"""The parameters of a chat-completions request, for any model on any endpoint.

Every chat call is built and sent here: the core agents (replies, routing, the
small checks, the summary extraction, the rewrite-safety segmenter and
classifier) and `OpenAICompatibleLlmClient` (the crisis check).

`build_chat_request` shapes the parameters for the model. A reasoning model
(`is_reasoning_model`) gets no temperature, max_completion_tokens in place of
max_tokens (raised by LLM_REASONING_EXTRA_TOKENS: the reasoning counts against
it), and LLM_REASONING_EFFORT; every other model gets what it is given.

`create_chat_completion` sends the request. When the endpoint answers 400 or
422 and the error names one of the parameters below as unsupported, it adjusts
that parameter and retries, up to three times per call. A body that lists
several errors (a validator's 422) is read error by error: the words of the
rejection and the parameter's name must come from the same one. max_tokens and
max_completion_tokens are renamed into each other, never back into a name the
endpoint already rejected for the model (that one is then left out). Once the
endpoint has got past an adjustment (it answered, or rejected another
parameter with another error) the adjustment is remembered for that endpoint
and model, so later calls go out adjusted at once. Any other 400 or 422
(content policy, context length, unknown model, a value out of range) is
raised as it is, and so is the same error coming back after an adjustment
(request text echoed in the error that only read like a rejection): an
adjustment that did not help is not remembered.

A reasoning model whose answer comes back empty because the token limit ran
out is logged once per model, for a stream at its end. `answer_text` reads the
answer out of a message's or a stream delta's content.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection
from typing import Any

import openai

from ..config import settings

log = logging.getLogger(__name__)

# OpenAI's reasoning families, bare or vendor-prefixed. Any other reasoning
# model is listed in LLM_REASONING_MODELS.
REASONING_MODEL_PREFIXES = (
    "gpt-5", "o1", "o3", "o4", "openai/gpt-5", "openai/o1", "openai/o3", "openai/o4",
)  # fmt: skip

# A parameter an endpoint may reject, and the one that takes its value
# (None: the parameter is left out).
_REPLACEMENT: dict[str, str | None] = {
    "temperature": None,
    "top_p": None,
    "max_tokens": "max_completion_tokens",
    "max_completion_tokens": "max_tokens",
    "response_format": None,
    "reasoning_effort": None,
}
_MAX_ADAPTATIONS = 3
# The answers that can mean "this parameter is not taken": OpenAI's and most
# others' 400, and the 422 of an endpoint whose request schema forbids unknown
# fields (Mistral: pydantic's extra_forbidden).
_ADAPTED_ERRORS = (openai.BadRequestError, openai.UnprocessableEntityError)
# The name as a whole identifier: "max_tokens" never matches inside
# "max_completion_tokens" or "max_tokens_to_sample".
_NAMED = {name: re.compile(rf"(?<!\w){name}(?!\w)", re.IGNORECASE) for name in _REPLACEMENT}
# How endpoints say they do not take a parameter (or the value given for it),
# as opposed to a value that is out of range or a prompt that is too long.
_NOT_ACCEPTED = re.compile(
    r"unsupported|not supported|does not support|unknown|unrecogni[sz]ed|not allowed"
    r"|not permitted|unexpected|extra_forbidden",
    re.IGNORECASE,
)

# The fields of an error body that describe the error. The others can repeat
# the request or the model's output (Groq's failed_generation, the input a
# validator echoes), so a parameter name there says nothing.
_DESCRIBING = ("error", "errors", "message", "msg", "detail", "loc", "param", "code", "type")

# (base_url, model) -> the parameters that endpoint rejected for that model.
_adjusted: dict[tuple[str, str], set[str]] = {}
# (model, parameter) pairs already logged.
_warned: set[tuple[str, str]] = set()
# (model, whether a token limit was sent) already logged for running out of tokens.
_warned_out_of_tokens: set[tuple[str, bool]] = set()


def is_reasoning_model(model: str) -> bool:
    """Listed in LLM_REASONING_MODELS, or one of OpenAI's reasoning families."""
    return model in settings.reasoning_models or model.startswith(REASONING_MODEL_PREFIXES)


def build_chat_request(
    model: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """The chat.completions.create parameters for `model`; the stream options
    are the caller's to add.

    A reasoning model gets no temperature, `max_tokens` plus
    LLM_REASONING_EXTRA_TOKENS as max_completion_tokens (its reasoning counts
    against that limit), and `reasoning_effort` or else LLM_REASONING_EFFORT
    (empty: none is sent, the provider's default applies).
    """
    request: dict[str, Any] = {"model": model, "messages": messages}
    if is_reasoning_model(model):
        if max_tokens is not None:
            request["max_completion_tokens"] = max_tokens + settings.llm_reasoning_extra_tokens
        if effort := reasoning_effort or settings.llm_reasoning_effort:
            request["reasoning_effort"] = effort
    else:
        request["temperature"] = temperature
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
    if response_format is not None:
        request["response_format"] = response_format
    return request


def answer_text(content: object) -> str:
    """The answer in a message's or a stream delta's content: the string, or,
    for a list of chunks (Mistral's reasoning models), its text chunks joined
    with the thinking chunks left out. "" when there is none."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            chunk["text"]
            for chunk in content
            if isinstance(chunk, dict)
            and chunk.get("type", "text") == "text"
            and isinstance(chunk.get("text"), str)
        )
    return ""


def _error_texts(body: object) -> list[str]:
    """The text of the fields of an error body that describe the error, error
    by error (the body as the SDK keeps it: the JSON's "error" value, the
    JSON, or the raw text when it is not JSON).

    Each object in a list is an error of its own (pydantic's `detail`, also
    inside Mistral's `message`; an `errors` list): a validator lists every
    failing field in one body, and a rejection's words in one error and a
    parameter's name in another say nothing. The rest of the body is one
    error, first.
    """
    own: list[str] = []
    listed: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key in _DESCRIBING:
                if key in value:
                    collect(value[key])
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    listed.extend(_error_texts(item))
                else:
                    collect(item)
        elif value is not None:
            own.append(str(value))

    collect(body)
    return [text for text in (" ".join(own), *listed) if text]


def _rejection(error: openai.APIStatusError, request: dict[str, Any]) -> tuple[str, str] | None:
    """The parameter of `request` that the error says the endpoint does not
    take (the first one named when an error names several), and the text of
    the error that says so."""
    for text in _error_texts(error.body):
        if not _NOT_ACCEPTED.search(text):
            continue
        named = [
            (match.start(), name)
            for name in _REPLACEMENT
            if name in request and (match := _NAMED[name].search(text))
        ]
        if named:
            return min(named)[1], text
    return None


def _without(text: str, parameter: str, value: object) -> str:
    """An error's text without what an echo of the request shows of an
    adjusted `parameter`: its name, and the pair name: value (also under the
    name that took the value)."""
    values = "|".join(re.escape(form) for form in {repr(value), json.dumps(value, default=str)})
    for name in filter(None, (parameter, _REPLACEMENT[parameter])):
        pair = rf"(?<!\w)['\"]?{name}['\"]?\s*:\s*(?:{values})(?!\w)"
        text = re.sub(rf"{pair}\s*,\s*|,\s*{pair}|{pair}", "", text)
    return _NAMED[parameter].sub("", text)


def _replacement(parameter: str, rejected: Collection[str]) -> str | None:
    """The parameter that takes `parameter`'s value; never one the endpoint
    rejected too (max_tokens and max_completion_tokens would ping-pong)."""
    replacement = _REPLACEMENT[parameter]
    return None if replacement in rejected else replacement


def _adjust(request: dict[str, Any], parameter: str, rejected: Collection[str]) -> dict[str, Any]:
    adjusted = dict(request)
    value = adjusted.pop(parameter)
    if (replacement := _replacement(parameter, rejected)) is not None:
        adjusted.setdefault(replacement, value)
    return adjusted


def _remember(key: tuple[str, str], parameter: str) -> None:
    rejected = _adjusted.setdefault(key, set())
    rejected.add(parameter)
    model = key[1]
    if (model, parameter) in _warned:
        return
    _warned.add((model, parameter))
    replacement = _replacement(parameter, rejected)
    log.warning(
        "llm.parameter_rejected model=%s parameter=%s — %s from now on",
        model,
        parameter,
        f"sending {replacement} instead" if replacement else "leaving it out",
    )


def _warn_if_out_of_tokens(
    request: dict[str, Any], finish_reason: object, *, answered: bool
) -> None:
    """Log once per model when a reasoning model's answer came back empty
    because it ran out of tokens (finish_reason "length"): the reasoning used
    them up. `request` is the one that went out, so the limit is named as it
    was sent; with no limit sent, LLM_REASONING_EXTRA_TOKENS cannot help and
    the warning says so. Nothing else from the request or the answer is logged.
    """
    model = str(request.get("model", ""))
    if answered or finish_reason != "length" or not is_reasoning_model(model):
        return
    limit = next(
        (name for name in ("max_completion_tokens", "max_tokens") if name in request), None
    )
    if (model, limit is not None) in _warned_out_of_tokens:
        return
    _warned_out_of_tokens.add((model, limit is not None))
    if limit is None:
        log.warning(
            "llm.reasoning_out_of_tokens model=%s — empty answer, finish_reason=length with no "
            "token limit sent: the reasoning used up the endpoint's own limit "
            "(LLM_REASONING_EXTRA_TOKENS only raises a limit that is sent; a lower "
            "LLM_REASONING_EFFORT leaves more room)",
            model,
        )
        return
    log.warning(
        "llm.reasoning_out_of_tokens model=%s — empty answer, finish_reason=length: the "
        "reasoning used up %s=%s; set LLM_REASONING_EXTRA_TOKENS higher (now %d)",
        model,
        limit,
        request[limit],
        settings.llm_reasoning_extra_tokens,
    )


def _check_answer(request: dict[str, Any], response: Any) -> None:
    """`_warn_if_out_of_tokens` for a complete (not streamed) response."""
    choices = getattr(response, "choices", None)
    if not choices:
        return
    content = getattr(getattr(choices[0], "message", None), "content", None)
    _warn_if_out_of_tokens(
        request,
        getattr(choices[0], "finish_reason", None),
        answered=bool(answer_text(content).strip()),
    )


class _WatchedStream:
    """The stream the SDK returned, its chunks passed on as they are; at its
    end, `_warn_if_out_of_tokens` for the request that went out. Everything
    else (close() above all) is the stream's own."""

    def __init__(self, stream: Any, request: dict[str, Any]) -> None:
        self._stream = stream
        self._chunks = aiter(stream)
        self._request = request
        self._finish_reason: object = None
        self._answered = False
        self._ended = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __aiter__(self) -> _WatchedStream:
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await anext(self._chunks)
        except StopAsyncIteration:
            if not self._ended:
                self._ended = True
                _warn_if_out_of_tokens(self._request, self._finish_reason, answered=self._answered)
            raise
        # Usage-only or keep-alive chunks come with no choices (or no delta);
        # the finish reason stays the one already seen.
        if choices := getattr(chunk, "choices", None):
            choice = choices[0]
            self._finish_reason = getattr(choice, "finish_reason", None) or self._finish_reason
            content = getattr(getattr(choice, "delta", None), "content", None)
            self._answered = self._answered or bool(answer_text(content).strip())
        return chunk


async def create_chat_completion(client: openai.AsyncOpenAI, request: dict[str, Any]) -> Any:
    """`client.chat.completions.create(**request)`, with the parameters the
    endpoint rejected for this model adjusted (see the module docstring).

    A stream is rejected when it is created, so streaming requests adapt the
    same way; the stream comes back watched for running out of tokens.
    `request` itself is never changed.
    """
    key = (str(getattr(client, "base_url", "")), str(request.get("model", "")))
    # Rejected for this model: remembered, then in this call.
    rejected = set(_adjusted.get(key, ()))
    for known in _REPLACEMENT:
        if known in rejected and known in request:
            request = _adjust(request, known, rejected)
    adaptations = 0
    # The last adjustment until the endpoint has got past it: the parameter,
    # the value it had, and the text of the error that rejected it.
    unconfirmed: tuple[str, Any, str] | None = None
    while True:
        try:
            response = await client.chat.completions.create(**request)
        except _ADAPTED_ERRORS as error:
            rejection = _rejection(error, request)
            if rejection is None:
                # Some other 400 or 422, possibly the one the last adjustment
                # was made for (text that only read like a rejection): that
                # adjustment is not remembered.
                raise
            parameter, text = rejection
            if unconfirmed is not None:
                last, value, last_text = unconfirmed
                if _without(text, last, value) == _without(last_text, last, value):
                    # The same error again, now read as naming another
                    # parameter: it was never about the last one (request
                    # text echoed back). Nothing is remembered.
                    raise
                # Rejected for another parameter: the endpoint got past the
                # last adjustment.
                _remember(key, last)
            if adaptations == _MAX_ADAPTATIONS:
                raise
            adaptations += 1
            unconfirmed = (parameter, request[parameter], text)
            rejected.add(parameter)
            request = _adjust(request, parameter, rejected)
        else:
            if unconfirmed is not None:
                _remember(key, unconfirmed[0])
            if request.get("stream"):
                return _WatchedStream(response, request)
            _check_answer(request, response)
            return response
