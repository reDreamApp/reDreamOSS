import os
import math
import asyncio
from typing import Literal, AsyncGenerator, Tuple, Optional, List
from dataclasses import dataclass, field
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from groq import AsyncGroq
import logging
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

ProviderType = Literal["groq", "openai", "scaleway", "openrouter", "mistral"]

@dataclass
class ModelConfig:
    name: str
    provider: ProviderType


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

# Models that use max_completion_tokens instead of max_tokens
# Includes both direct API names (gpt-5) and OpenRouter-style names (openai/gpt-5)
NEW_API_MODELS = ("gpt-5", "o1", "o3", "openai/gpt-5", "openai/o1", "openai/o3")


class Agent:
    def __init__(
        self,
        model_config: ModelConfig,
        system_prompt: str,
        temperature: float = 0.5,
        max_tokens: int = 1024,
        fallback_model_config: Optional[ModelConfig] = None,
    ):
        self.model = model_config.name
        self.provider = model_config.provider
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Check if this model uses the new API parameter names
        self._uses_new_api = self._model_uses_new_api(self.model)
        self.client = self._build_client(model_config.provider)

        # Optional cross-provider fallback. When the primary model fails (transient
        # errors after retries, or a hard error such as model-not-found / 4xx — e.g.
        # a deprecated model hitting EOL), generate()/generate_stream() transparently
        # retry the same prompt on this fallback model + provider.
        self.fallback_model: Optional[str] = None
        self.fallback_provider: Optional[ProviderType] = None
        self.fallback_client = None
        self._fallback_uses_new_api = False
        if fallback_model_config is not None:
            self.fallback_model = fallback_model_config.name
            self.fallback_provider = fallback_model_config.provider
            self._fallback_uses_new_api = self._model_uses_new_api(self.fallback_model)
            self.fallback_client = self._build_client(fallback_model_config.provider)

    @staticmethod
    def _model_uses_new_api(model_name: str) -> bool:
        return any(model_name.startswith(prefix) for prefix in NEW_API_MODELS)

    @staticmethod
    def _build_client(provider: ProviderType):
        """Construct the OpenAI-compatible (or Groq) client for a provider."""
        if provider == "groq":
            api_key = os.getenv('GROQ_API_KEY')
            if not api_key:
                raise ValueError("GROQ_API_KEY not found in environment")
            return AsyncGroq(api_key=api_key)
        elif provider == "scaleway":
            api_key = os.getenv('SCALEWAY_API_KEY')
            if not api_key:
                raise ValueError("SCALEWAY_API_KEY not found in environment")
            # The bare URL resolves to the organization's default project. A key
            # whose rights are limited to another project must name it in the URL.
            project_id = os.getenv('SCALEWAY_PROJECT_ID')
            base_url = f"https://api.scaleway.ai/{project_id}/v1" if project_id else "https://api.scaleway.ai/v1"
            return AsyncOpenAI(api_key=api_key, base_url=base_url)
        elif provider == "openrouter":
            api_key = os.getenv('OPENROUTER_API_KEY')
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY not found in environment")
            return AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        elif provider == "mistral":
            api_key = os.getenv('MISTRAL_API_KEY')
            if not api_key:
                raise ValueError("MISTRAL_API_KEY not found in environment")
            return AsyncOpenAI(api_key=api_key, base_url="https://api.mistral.ai/v1")
        else:
            api_key = os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment")
            return AsyncOpenAI(api_key=api_key)

    def _build_request_kwargs(self, model, uses_new_api, provider, prompt, logprobs, top_logprobs, stream=False, system_prompt=None, response_format=None):
        """Assemble the chat.completions.create kwargs for a given model/provider.

        ``system_prompt`` overrides ``self.system_prompt`` for this call only — used
        to pass a per-request composed system prompt instead of mutating the shared
        agent (concurrency-safety fix; the same override feeds the fallback path).
        ``response_format`` (e.g. {"type": "json_object"}) requests structured output
        — used by the summary structured-extraction pass (ADR 0003).
        """
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        request_kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": effective_system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": self.temperature,
        }
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        if stream:
            request_kwargs["stream"] = True
        # Use appropriate token limit parameter based on model
        if uses_new_api:
            request_kwargs["max_completion_tokens"] = self.max_tokens
        else:
            request_kwargs["max_tokens"] = self.max_tokens
        if logprobs:
            request_kwargs["logprobs"] = True
            if top_logprobs is not None:
                request_kwargs["top_logprobs"] = top_logprobs
            # OpenRouter: ensure the request is routed to a provider that supports logprobs
            if provider == "openrouter":
                request_kwargs["extra_body"] = {"provider": {"require_parameters": True}}
        return request_kwargs

    async def _create_with_retries(self, client, request_kwargs):
        """Call chat.completions.create, retrying transient network/server errors.

        Scaleway's endpoint occasionally drops connections under sustained
        sequential load (seen in consistency-mode benchmarks).
        """
        TRANSIENT_EXCS = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
        max_attempts = 4
        delay = 1.0
        for attempt in range(max_attempts):
            try:
                return await client.chat.completions.create(**request_kwargs)
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
        logprobs: bool = False,
        top_logprobs: Optional[int] = None,
        system_prompt: Optional[str] = None,
        response_format: Optional[dict] = None,
    ) -> Tuple[str, dict, Optional[LogprobsResult]]:
        """
        Generate a response from the LLM, transparently falling back to a second
        model/provider if the primary fails.

        Args:
            prompt: The user prompt to send
            logprobs: Whether to request log probabilities for each token
            top_logprobs: If logprobs=True, how many top alternatives per token (1-20)
            system_prompt: Per-call system prompt override (defaults to self.system_prompt).
                Pass the composed prompt here instead of mutating self.system_prompt so
                concurrent requests can't clobber each other.
            response_format: Optional structured-output spec, e.g. {"type": "json_object"}
                (used by the summary structured-extraction pass, ADR 0003).

        Returns:
            Tuple of (response_text, usage_dict, logprobs_result or None)
        """
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        try:
            log_prompt(effective_system_prompt, prompt, self.model)

            primary_kwargs = self._build_request_kwargs(
                self.model, self._uses_new_api, self.provider, prompt, logprobs, top_logprobs,
                system_prompt=effective_system_prompt, response_format=response_format
            )
            try:
                response = await self._create_with_retries(self.client, primary_kwargs)
            except Exception as primary_err:
                if self.fallback_client is None:
                    raise
                logger.warning(
                    f"Primary model '{self.model}' ({self.provider}) failed: {primary_err}. "
                    f"Falling back to '{self.fallback_model}' ({self.fallback_provider})."
                )
                fallback_kwargs = self._build_request_kwargs(
                    self.fallback_model, self._fallback_uses_new_api, self.fallback_provider,
                    prompt, logprobs, top_logprobs, system_prompt=effective_system_prompt,
                    response_format=response_format
                )
                response = await self._create_with_retries(self.fallback_client, fallback_kwargs)

            usage = {
                "input": response.usage.prompt_tokens,
                "output": response.usage.completion_tokens,
                "total": response.usage.total_tokens
            }

            logprobs_result = None
            if logprobs and response.choices[0].logprobs:
                logprobs_result = self._parse_logprobs(response.choices[0].logprobs)

            return response.choices[0].message.content, usage, logprobs_result

        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            raise
    
    def _parse_logprobs(self, logprobs_data) -> LogprobsResult:
        """Parse the logprobs from API response into our dataclass."""
        result = LogprobsResult()
        
        if hasattr(logprobs_data, 'content') and logprobs_data.content:
            for token_info in logprobs_data.content:
                top_alternatives = None
                if hasattr(token_info, 'top_logprobs') and token_info.top_logprobs:
                    top_alternatives = [
                        {"token": alt.token, "logprob": alt.logprob}
                        for alt in token_info.top_logprobs
                    ]
                
                result.tokens.append(TokenLogprob(
                    token=token_info.token,
                    logprob=token_info.logprob,
                    top_logprobs=top_alternatives
                ))
        
        return result

    async def generate_stream(self, prompt: str, system_prompt: Optional[str] = None) -> AsyncGenerator[Tuple[str, Optional[dict], Optional[LogprobsResult]], None]:
        """Generate a streaming response, falling back to the secondary model if the
        primary fails to open the stream. Note: logprobs not available in streaming mode.

        ``system_prompt`` overrides self.system_prompt for this call only (see generate)."""
        try:
            usage = {"input": 0, "output": 0, "total": 0}

            primary_kwargs = self._build_request_kwargs(
                self.model, self._uses_new_api, self.provider, prompt,
                logprobs=False, top_logprobs=None, stream=True, system_prompt=system_prompt
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
                    self.fallback_model, self._fallback_uses_new_api, self.fallback_provider,
                    prompt, logprobs=False, top_logprobs=None, stream=True, system_prompt=system_prompt
                )
                stream = await self._create_with_retries(self.fallback_client, fallback_kwargs)

            async for chunk in stream:
                if chunk_usage := getattr(chunk, "usage", None):
                    usage = {
                        "input": chunk_usage.prompt_tokens,
                        "output": chunk_usage.completion_tokens,
                        "total": chunk_usage.total_tokens
                    }
                if chunk.choices and (content := chunk.choices[0].delta.content):
                    yield content, None, None

            yield "", usage, None
        except Exception as e:
            logger.error(f"Error in stream generation: {str(e)}")
            raise

# Model configurations
MODELS = {
    "GROQ_70B": ModelConfig(name="llama-3.3-70b-versatile", provider="groq"),
    "GROQ_8B": ModelConfig(name="llama-3.1-8b-instant", provider="groq"),
    "GPT4": ModelConfig(name="gpt-4", provider="openai"),
    # GPT-5.2 model for high-quality synthetic data generation
    "GPT5": ModelConfig(name="gpt-5.2", provider="openai"),
    # --- Scaleway Generative APIs (primary path) ---
    # Response generation primary
    "MISTRAL_MEDIUM": ModelConfig(name="mistral-medium-3.5-128b", provider="scaleway"),
    # Routing, safety segmenter, and safety classifier primary. The classifier needs
    # logprobs, which Scaleway supports.
    "MISTRAL_SMALL": ModelConfig(name="mistral-small-3.2-24b-instruct-2506", provider="scaleway"),
    # --- Direct Mistral API (fallback path) ---
    # Response fallback. Deliberately a FLOATING alias (user decision 2026-07-02,
    # recorded exception to ADR 0010's pinning rule): the rarely-hit fallback
    # tracks Mistral's current medium so it stays close to the pinned Scaleway
    # primary (mistral-medium-3.5) instead of aging out on a fixed version.
    "MISTRAL_MEDIUM_DIRECT": ModelConfig(name="mistral-medium-latest", provider="mistral"),
    "MISTRAL_LARGE": ModelConfig(name="mistral-large-2512", provider="mistral"),        # unused at runtime
    "MINISTRAL_8B": ModelConfig(name="ministral-8b-2512", provider="mistral"),          # routing/segmenter fallback
    "MISTRAL_SMALL_DIRECT": ModelConfig(name="mistral-small-2603", provider="mistral"), # classifier fallback (supports logprobs)
    # --- OpenRouter mirrors (unused at runtime; OpenRouter does not support logprobs) ---
    "OR_MISTRAL_SMALL": ModelConfig(name="mistralai/mistral-small-3.2-24b-instruct", provider="openrouter"),
    "OR_GPT5": ModelConfig(name="openai/gpt-5.2", provider="openrouter"),
    "OR_GPT5_4": ModelConfig(name="openai/gpt-5.4", provider="openrouter"),
}

DEFAULT_ROUTING_MODEL_KEY = "MISTRAL_SMALL"
DEFAULT_RESPONSE_MODEL_KEY = "MISTRAL_MEDIUM"
# Cross-provider fallbacks (direct Mistral API) used when the Scaleway primary fails.
ROUTING_FALLBACK_MODEL_KEY = "MINISTRAL_8B"
RESPONSE_FALLBACK_MODEL_KEY = "MISTRAL_MEDIUM_DIRECT"

# Create agent instances (will fail if API keys are not set)
try:
    # Routing: Mistral Small on Scaleway, falling back to Ministral 8B on Mistral.
    routing_agent = Agent(
        MODELS[DEFAULT_ROUTING_MODEL_KEY], ROUTING_PROMPT, temperature=0.1,
        fallback_model_config=MODELS[ROUTING_FALLBACK_MODEL_KEY],
    )
    # Responses: Mistral Medium 3.5 on Scaleway, falling back to mistral-medium-latest on Mistral.
    response_agent = Agent(
        MODELS[DEFAULT_RESPONSE_MODEL_KEY], "your_response_prompt", temperature=0.1,
        fallback_model_config=MODELS[RESPONSE_FALLBACK_MODEL_KEY],
    )
except ValueError:
    # API keys not set - agents will be None until .env is configured
    routing_agent = None
    response_agent = None
    logger.warning("API keys not found. Agent instances not created. Set SCALEWAY_API_KEY and MISTRAL_API_KEY in .env file.")
