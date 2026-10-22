import math
import asyncio
import contextlib
from typing import AsyncGenerator, Tuple, Optional, List
from dataclasses import dataclass, field
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
import logging
from ..config import LLM_REQUIRED, settings
from ..llm.chat_request import answer_text, build_chat_request, create_chat_completion
from .prompts import ROUTING_PROMPT
# Create a specific logger for prompts
prompt_logger = logging.getLogger("prompts")
logger = logging.getLogger(__name__)

# Add a method to log prompts
def log_prompt(system_prompt: str, user_prompt: str, model: str):
    prompt_logger.info(
        "\n=== API REQUEST ===\n"
        f"Model: {model}\n"
        f"System Prompt: {system_prompt}\n"
        f"User Prompt: {user_prompt}\n"
        "=================="
    )

def build_client() -> AsyncOpenAI:
    """The one way to reach the chat LLM: the OpenAI-compatible endpoint from
    LLM_BASE_URL / LLM_API_KEY. Every client gets LLM_TIMEOUT_SECONDS; the SDK
    default (600 s) would outlast the backend's own budget."""
    if not settings.llm_configured:
        raise ValueError(f"{LLM_REQUIRED} must be set")
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
    )


def _usage_dict(usage) -> dict:
    """Provider-reported token counts; zeros when the endpoint reports none."""
    if usage is None:
        return {"input": 0, "output": 0, "total": 0}
    return {
        "input": usage.prompt_tokens or 0,
        "output": usage.completion_tokens or 0,
        "total": usage.total_tokens or 0,
    }


@dataclass
class TokenLogprob:
    """Log probability information for a single token."""
    token: str
    logprob: float
    top_logprobs: Optional[List[dict]] = None


@dataclass 
class LogprobsResult:
    """Container for logprobs data from a completion."""
    tokens: List[TokenLogprob] = field(default_factory=list)
    
    @property
    def mean_logprob(self) -> float:
        """Average log probability across all tokens."""
        if not self.tokens:
            return 0.0
        return sum(t.logprob for t in self.tokens) / len(self.tokens)
    
    @property
    def total_logprob(self) -> float:
        """Sum of all log probabilities (log of sequence probability)."""
        return sum(t.logprob for t in self.tokens)
    
    @property
    def confidence(self) -> float:
        """Convert mean logprob to a 0-1 confidence score."""
        return math.exp(self.mean_logprob) if self.tokens else 0.0


class Agent:
    def __init__(
        self,
        model: str,
        system_prompt: str,
        temperature: float = 0.5,
        max_tokens: int = 1024,
        fallback_model: Optional[str] = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = build_client()

        # Optional fallback model on the same endpoint. When the primary model fails
        # (transient errors after retries, or a hard error such as model-not-found /
        # 4xx — e.g. a deprecated model hitting EOL), generate()/generate_stream()
        # transparently retry the same prompt on it. Nothing in the app configures one.
        self.fallback_model: Optional[str] = fallback_model
        self.fallback_client = self.client if fallback_model is not None else None

    def _build_request_kwargs(self, model, prompt, stream=False, system_prompt=None, response_format=None):
        """Assemble the chat.completions.create kwargs for a given model.

        Never asks for logprobs: not every OpenAI-compatible endpoint supports them.
        A reasoning model gets no temperature, max_completion_tokens (the
        limit plus LLM_REASONING_EXTRA_TOKENS) and LLM_REASONING_EFFORT
        (redream_ai.llm.chat_request).

        ``system_prompt`` overrides ``self.system_prompt`` for this call only — used
        to pass a per-request composed system prompt instead of mutating the shared
        agent (concurrency-safety fix; the same override feeds the fallback path).
        ``response_format`` (e.g. {"type": "json_object"}) requests structured output
        — used by the summary structured-extraction pass (ADR 0003).
        """
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        request_kwargs = build_chat_request(
            model,
            [
                {"role": "system", "content": effective_system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format=response_format,
        )
        if stream:
            request_kwargs["stream"] = True
        return request_kwargs

    async def _create_with_retries(self, client, request_kwargs):
        """Call chat.completions.create, retrying transient network/server errors.

        Some endpoints occasionally drop connections under sustained
        sequential load (seen in consistency-mode benchmarks). A parameter the
        endpoint rejects is adjusted by create_chat_completion.
        """
        TRANSIENT_EXCS = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
        max_attempts = 4
        delay = 1.0
        for attempt in range(max_attempts):
            try:
                return await create_chat_completion(client, request_kwargs)
            except TRANSIENT_EXCS as e:
                if attempt == max_attempts - 1:
                    raise
                logger.warning(f"Transient API error (attempt {attempt+1}/{max_attempts}): {e}. Retrying in {delay}s.")
                await asyncio.sleep(delay)
                delay *= 2
        # Unreachable: the loop either returns on success or raises on the final attempt.
        raise RuntimeError("retry loop exhausted without returning or raising")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[dict] = None,
    ) -> Tuple[str, dict, Optional[LogprobsResult]]:
        """
        Generate a response from the LLM, transparently falling back to a second
        model if one is set and the primary fails.

        Args:
            prompt: The user prompt to send
            system_prompt: Per-call system prompt override (defaults to self.system_prompt).
                Pass the composed prompt here instead of mutating self.system_prompt so
                concurrent requests can't clobber each other.
            response_format: Optional structured-output spec, e.g. {"type": "json_object"}
                (used by the summary structured-extraction pass, ADR 0003).

        Returns:
            Tuple of (response_text, usage_dict, None). The third slot is where the
            logprobs used to go; they are no longer requested, so it is always None.
            A missing content is "" and missing usage counts are zeros.
        """
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        try:
            log_prompt(effective_system_prompt, prompt, self.model)

            primary_kwargs = self._build_request_kwargs(
                self.model, prompt,
                system_prompt=effective_system_prompt, response_format=response_format
            )
            try:
                response = await self._create_with_retries(self.client, primary_kwargs)
            except Exception as primary_err:
                if self.fallback_client is None:
                    raise
                logger.warning(
                    f"Primary model '{self.model}' failed: {primary_err}. "
                    f"Falling back to '{self.fallback_model}'."
                )
                fallback_kwargs = self._build_request_kwargs(
                    self.fallback_model,
                    prompt, system_prompt=effective_system_prompt,
                    response_format=response_format
                )
                response = await self._create_with_retries(self.fallback_client, fallback_kwargs)

            usage = _usage_dict(getattr(response, "usage", None))
            return answer_text(response.choices[0].message.content), usage, None

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            raise

    async def generate_stream(self, prompt: str, system_prompt: Optional[str] = None) -> AsyncGenerator[Tuple[str, Optional[dict], Optional[LogprobsResult]], None]:
        """Generate a streaming response, falling back to the secondary model if the
        primary fails to open the stream. Note: logprobs not available in streaming mode.

        ``system_prompt`` overrides self.system_prompt for this call only (see generate)."""
        try:
            usage = {"input": 0, "output": 0, "total": 0}

            primary_kwargs = self._build_request_kwargs(
                self.model, prompt, stream=True, system_prompt=system_prompt
            )
            try:
                stream = await self._create_with_retries(self.client, primary_kwargs)
            except Exception as primary_err:
                if self.fallback_client is None:
                    raise
                logger.warning(
                    f"Primary model '{self.model}' stream failed: {primary_err}. "
                    f"Falling back to '{self.fallback_model}'."
                )
                fallback_kwargs = self._build_request_kwargs(
                    self.fallback_model,
                    prompt, stream=True, system_prompt=system_prompt
                )
                stream = await self._create_with_retries(self.fallback_client, fallback_kwargs)

            # Closes the provider's HTTP response as soon as this generator is
            # closed (client gone mid-stream), not whenever GC gets to it. A failing
            # close is swallowed: it must never replace GeneratorExit/CancelledError.
            # (A reasoning model's answer left empty by the token limit is
            # logged by the stream itself: chat_request.)
            try:
                async for chunk in stream:
                    if chunk_usage := getattr(chunk, "usage", None):
                        usage = _usage_dict(chunk_usage)
                    # Usage-only or keep-alive chunks come with no choices (or no delta).
                    delta = getattr(chunk.choices[0], "delta", None) if chunk.choices else None
                    if content := answer_text(getattr(delta, "content", None)):
                        yield content, None, None
            finally:
                with contextlib.suppress(Exception):
                    await stream.close()

            yield "", usage, None
        except Exception as e:
            logger.error(f"Error in stream generation: {str(e)}")
            raise

def build_agents() -> Tuple[Optional[Agent], Optional[Agent]]:
    """routing_agent and response_agent from the LLM_* settings; (None, None) until
    the endpoint is configured. No fallback model: one provider serves every role."""
    if not settings.llm_configured:
        logger.warning(f"{LLM_REQUIRED} not all set — IRT agents unavailable until configured; chat turns fail.")
        return None, None
    # Routing: the small model.
    routing = Agent(settings.llm_small_model, ROUTING_PROMPT, temperature=0.1)
    # Responses: the main model.
    response = Agent(settings.llm_model, "your_response_prompt", temperature=0.1)
    return routing, response


# Create agent instances (None until LLM_* is configured)
routing_agent, response_agent = build_agents()
