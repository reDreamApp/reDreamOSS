"""Structured extraction of the summary-stage prose into wire `SummaryStructured` (ADR 0003).

The ported IRT summary stage emits labeled prose (Title:/Rewritten Dream:/Original Dream:)
for the user to read. This module runs a small JSON-mode pass over that prose to ALSO emit
the structured fields the backend persists into `dreams` — without string-matching the
labels in the backend (the `_parse_summary` antipattern ADR 0003 retired). JSON mode is
requested only when LLM_JSON_MODE is on; fenced or prose-wrapped JSON parses either way.

Failure is graceful: returns None, and the prose still reaches the user (structured=None
means "not extracted", the backend simply doesn't get a Dream row this turn).

Note: the ported flow has no `intensity` stage (see project decision), so `intensity` is
left None here — the 0-10 rating is not collected as a routed conversational step.
"""

from __future__ import annotations

import logging

from .config import settings
from .core import agent as core_agent
from .llm.json_parse import parse_json_object
from .models import Language, SummaryStructured

log = logging.getLogger(__name__)

_EXTRACT_SYSTEM_PROMPT = (
    "You extract structured fields from an ALREADY-WRITTEN dream summary. "
    "Return ONLY a JSON object with exactly these three string keys: "
    '"title", "rewritten_summary", "original_summary". '
    "Copy the text under each labeled section of the summary VERBATIM — do not rewrite, "
    "translate, shorten, merge, reorder, or invent anything. Keep the rewritten summary "
    "self-contained with no comparison to the original."
)

_JSON_OBJECT = {"type": "json_object"}


async def extract_summary_structured(
    prose_summary: str, language: Language
) -> SummaryStructured | None:
    """Extract structured summary fields from the summary-stage prose. None on failure."""
    agent = core_agent.response_agent
    if agent is None:
        log.warning("response_agent unavailable; skipping structured summary extraction")
        return None
    try:
        content, _usage, _ = await agent.generate(
            prose_summary,
            system_prompt=_EXTRACT_SYSTEM_PROMPT,
            response_format=_JSON_OBJECT if settings.llm_json_mode else None,
        )
        data = parse_json_object(content)
        original = str(data["original_summary"])
        return SummaryStructured(
            title=str(data["title"]),
            rewritten_summary=str(data["rewritten_summary"]),
            original_summary=original,
            # Backend reads `dream_summary` first for the original_dream column; mirror it.
            dream_summary=original,
            # No routed intensity stage in the ported flow.
            intensity=None,
        )
    except Exception as exc:  # graceful: prose still shown, backend just gets no Dream row
        log.warning("structured summary extraction failed (structured=None): %s", exc)
        return None
