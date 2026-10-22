"""The JSON object in a model answer.

Generic models wrap JSON in markdown fences or prose ("Here is the result:
{...}") even when told to return bare JSON, and JSON mode (LLM_JSON_MODE) is
not available on every endpoint. Some servers also leave the model's
reasoning (<think>...</think>) in the content.

The crisis check reads its verdict through this, so an ambiguous answer
raises instead of guessing: an example object in the prose or the reasoning
must never stand in for the real one.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
# Only the marker: in a one-line fence (```json {...}```) the object follows it.
_OPENING_FENCE = re.compile(r"^```[\w-]*")


def _drop_reasoning(text: str) -> str:
    """The answer after a reasoning block. Some servers send only the closing
    tag; an unclosed block (cut off) leaves no answer."""
    _, closed, answer = text.rpartition(_THINK_CLOSE)
    if closed:
        return answer
    return text.split(_THINK_OPEN, 1)[0]


def _balanced_spans(text: str) -> list[str]:
    """Every top-level `{...}` span, in order. Braces inside JSON strings do not
    count; an object nested in a span is never a span of its own."""
    spans: list[str] = []
    depth = 0
    start = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"' and depth > 0:
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append(text[start : i + 1])
    return spans


def _loads(span: str) -> dict[str, Any] | None:
    """The span as JSON; a `{...}` span that parses is always an object."""
    try:
        value: dict[str, Any] = json.loads(span)
    except json.JSONDecodeError:
        return None
    return value


def parse_json_object(text: str) -> dict[str, Any]:
    """The one JSON object in `text`.

    Reasoning, code fences and surrounding prose are skipped, and so is a
    `{...}` in the prose that is not JSON. Raises ValueError unless exactly
    one top-level object parses: none (an unclosed, truncated object
    included, and never an object picked out of the inside of a broken one),
    several (an example before the answer), or an answer that is a JSON
    array.
    """
    answer = _OPENING_FENCE.sub("", _drop_reasoning(text).strip()).lstrip()
    if answer.startswith("["):
        raise ValueError("the model answer is a JSON array, not an object")
    objects = [obj for span in _balanced_spans(answer) if (obj := _loads(span)) is not None]
    if len(objects) != 1:
        raise ValueError(
            f"{len(objects)} JSON objects in the model answer ({len(text)} chars), need one"
        )
    return objects[0]
