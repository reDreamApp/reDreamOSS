"""Live eval for the rewriting-stage opener + summary→final routing.

Covers the 2026-07-15 tester feedback (Ramon):
1. The rewriting stage used to open with a leaked fill-in-the-blank template
   ("You mentioned feeling [emotion] when [situation] happened…") that also
   locked the user onto the bot-picked moment. New behavior: the bot asks the
   USER to pick the moment and says the whole dream is changeable.
2. Summaries sometimes printed twice — one cause is the router re-emitting
   "summary" after the user confirms they're satisfied. Routing rule 5 now
   pins that to "final".

Hits the REAL LLM providers (SCALEWAY_API_KEY + MISTRAL_API_KEY from
apps/server/.env). Run on every edit of core/prompts.py:

    cd apps/server && uv run python evals/rewriting_regression.py
"""

# ruff: noqa: E501 — conversation fixtures read better as one line per turn

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


# Agents are built at import time from env — load keys BEFORE importing the core.
_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from redream_ai.core import irt_app  # noqa: E402
from redream_ai.core.models import ChatInput, Conversation  # noqa: E402

RUNS = 3  # live model at temperature>0 — check stability across repeats

# Template-leak markers that must never appear in a rewriting opener.
FORBIDDEN = [
    "[emotion]",
    "[situation]",
    "[Emotion]",
    "[Situation]",
    "You mentioned feeling",
    "following format",
    "Du hast erwähnt, dass du dich",
    "folgendes Format",
]

DOG_DREAM_EN = [
    (
        "assistant",
        "Hi! I'm here to help you work through a nightmare. Would you like to tell me about a dream that has been bothering you?",
        "recording",
    ),
    (
        "user",
        "I dreamt I was walking home from school and when I got home I found my dog lying dead in the garden.",
        None,
    ),
    ("assistant", "I'm sorry, that sounds distressing. How did the dream end?", "recording"),
    ("user", "I just stood there crying until I woke up. I felt so helpless.", None),
    (
        "assistant",
        "Thank you for sharing that. Would you like to proceed with rewriting your dream?",
        "recording",
    ),
]

DOG_DREAM_DE = [
    (
        "assistant",
        "Hallo! Ich begleite dich durch diese Übung. Magst du mir von einem Traum erzählen, der dich belastet?",
        "recording",
    ),
    (
        "user",
        "Ich habe geträumt, dass ich von der Schule nach Hause laufe und meinen Hund tot im Garten finde.",
        None,
    ),
    (
        "assistant",
        "Das tut mir leid, das klingt belastend. Wie ist der Traum ausgegangen?",
        "recording",
    ),
    (
        "user",
        "Ich stand nur weinend da, bis ich aufgewacht bin. Ich habe mich völlig hilflos gefühlt.",
        None,
    ),
    (
        "assistant",
        "Danke, dass du das geteilt hast. Möchtest du mit dem Umschreiben deines Traums fortfahren?",
        "recording",
    ),
]

SUMMARY_EN = (
    "Title: The Garden\n\nRewritten Dream: You walk home from school and your dog "
    "runs toward you, healthy and happy. You play together in the garden and feel "
    "relieved and safe.\n\nOriginal Dream: You walked home from school and found "
    "your dog lying dead in the garden. You stood there crying and felt helpless."
    "\n\nAre you satisfied with the generated summary?"
)


def build_conversation(turns, language, nightmare_summary=None) -> Conversation:
    conv = Conversation(session_id="eval", language=language)
    for role, content, stage in turns:
        conv.add_message(content, role, stage=stage, language=language)
    conv.stages = [t[2] for t in turns if t[0] == "assistant" and t[2]]
    conv.nightmare_summary = nightmare_summary
    return conv


async def eval_opener(label: str, turns, language: str, confirm: str) -> int:
    """User confirms the move to rewriting; inspect the stage + opener text."""
    failures = 0
    for run in range(RUNS):
        conv = build_conversation(turns, language, nightmare_summary="(pre-made summary)")
        resp = await irt_app.process_chat_message(
            ChatInput(session_id="eval", message=confirm, language_override=language),
            conv,
            safety_enabled=False,
        )
        leaks = [f for f in FORBIDDEN if f in resp.response]
        ok = resp.stage == "rewriting" and not leaks and "?" in resp.response
        if not ok:
            failures += 1
        print(
            f"{'PASS' if ok else 'FAIL'}  {label} run{run + 1}  stage={resp.stage}"
            + (f"  LEAKED={leaks}" if leaks else "")
        )
        print(f"      opener: {resp.response.strip()[:400]}\n")
    return failures


async def eval_routing(label: str, turns, language: str, reply: str, want: str) -> int:
    failures = 0
    for run in range(RUNS):
        conv = build_conversation(turns, language)
        conv.add_message(reply, "user", language=language)
        stage = await irt_app.determine_stage_async(reply, conv)
        ok = stage == want
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}  {label} run{run + 1}  routed={stage} (want {want})")
    return failures


async def main() -> int:
    if not (os.environ.get("SCALEWAY_API_KEY") and os.environ.get("MISTRAL_API_KEY")):
        print("SCALEWAY_API_KEY / MISTRAL_API_KEY missing — cannot run live eval")
        return 2

    failures = 0
    failures += await eval_opener("opener-en", DOG_DREAM_EN, "en", "Yes, let's rewrite it.")
    failures += await eval_opener("opener-de", DOG_DREAM_DE, "de", "Ja, gerne.")

    summary_turns = [
        *DOG_DREAM_EN,
        ("user", "Yes, let's rewrite it.", None),
        ("assistant", "Which part of the dream would you like to change?", "rewriting"),
        ("user", "My dog is alive and runs to greet me when I come home.", None),
        (
            "assistant",
            "That sounds like a complete rewritten dream to me. Would you like me to turn it into the summary now?",
            "rewriting",
        ),
        ("user", "Yes please.", None),
        ("assistant", SUMMARY_EN, "summary"),
    ]
    failures += await eval_routing("route-confirm→final", summary_turns, "en", "yes", "final")
    failures += await eval_routing(
        "route-change→summary",
        summary_turns,
        "en",
        "Can you change the title to something else?",
        "summary",
    )

    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
