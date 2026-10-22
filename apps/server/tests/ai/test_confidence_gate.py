"""§5 rewrite-safety confidence gate — the None-vs-0.0 regression trap.

Brief §5 (CHATBOT_INTEGRATION_BRIEF.md): in the clinical rewrite-safety
classifier (redream_ai.clinical_safety — NOT the ADR 0005 crisis classifier in
redream_ai.safety), `confidence = None` means logprobs were unavailable (e.g.
the direct-Mistral fallback answered) and must NOT be gated — the label is
trusted. `confidence = 0.0` is a genuine failure (fuzzy match / unparseable
code) and routes the segment to UNCLASSIFIED (fail closed). If a change ever
collapses None to 0.0, every fallback-answered classification becomes
UNCLASSIFIED and the bot asks for clarification forever.
"""

from __future__ import annotations

import math

import pytest

from redream_ai.clinical_safety.classifier import ClassificationResult, ClinicalClassifier
from redream_ai.clinical_safety.critic import SafetyCritic
from redream_ai.core.agent import LogprobsResult, TokenLogprob


class _ScriptedAgent:
    """Stands in for the classifier's Agent: returns a scripted (text, usage, logprobs)."""

    def __init__(self, content, logprobs=None):
        self._content = content
        self._logprobs = logprobs
        self.calls = []

    async def generate(self, prompt, logprobs=False, top_logprobs=None, **kwargs):
        self.calls.append({"prompt": prompt, "logprobs": logprobs, "top_logprobs": top_logprobs})
        return self._content, {"input": 5, "output": 1, "total": 6}, self._logprobs


def _confront_logprobs():
    return LogprobsResult(
        tokens=[
            TokenLogprob(
                "CON",
                -0.1,
                top_logprobs=[
                    {"token": "CON", "logprob": -0.1},
                    {"token": "HID", "logprob": -2.5},
                ],
            ),
            TokenLogprob("FRONT", -0.3),
        ]
    )


@pytest.fixture
def classifier():
    return ClinicalClassifier()


# --- classifier: None vs 0.0 semantics -------------------------------------------


async def test_valid_code_with_logprobs_has_real_confidence(classifier):
    classifier.agent = _ScriptedAgent("CONFRONT", logprobs=_confront_logprobs())

    node_id, result = await classifier.classify_intent("I fight back", return_logprobs=True)

    assert node_id == "BEHAVIORAL_MASTERY"
    assert result.confidence == pytest.approx(math.exp(-0.2))
    assert result.mean_logprob == pytest.approx(-0.2)
    assert result.margin == pytest.approx(math.exp(-0.1) - math.exp(-2.5))
    # The classifier must always request logprobs (with top-5 alternatives)
    assert classifier.agent.calls[0]["logprobs"] is True
    assert classifier.agent.calls[0]["top_logprobs"] == 5


async def test_missing_logprobs_yields_none_confidence_not_zero(classifier):
    # e.g. the direct-Mistral fallback answered without logprobs → confidence must
    # be None (unknown), NEVER 0.0 (which would mean "genuine failure" and reroute
    # the segment to UNCLASSIFIED downstream).
    classifier.agent = _ScriptedAgent("CONFRONT", logprobs=None)

    node_id, result = await classifier.classify_intent("I fight back", return_logprobs=True)

    assert node_id == "BEHAVIORAL_MASTERY"
    assert result.confidence is None
    assert result.mean_logprob is None
    assert result.margin is None


async def test_fuzzy_matched_code_forces_zero_confidence(classifier):
    # "FIGHTING" is not a CODE_MAP code; the fuzzy keyword match recovers
    # BEHAVIORAL_MASTERY but the confidence is forced to a genuine 0.0 — even
    # though logprobs WERE available for the garbled response.
    classifier.agent = _ScriptedAgent("FIGHTING", logprobs=_confront_logprobs())

    node_id, result = await classifier.classify_intent("I fight back", return_logprobs=True)

    assert node_id == "BEHAVIORAL_MASTERY"
    assert result.confidence == 0.0
    assert result.confidence is not None


async def test_unmatchable_code_routes_to_unclassified_with_zero_confidence(classifier):
    classifier.agent = _ScriptedAgent("QQQQ", logprobs=None)

    node_id, result = await classifier.classify_intent("gibberish", return_logprobs=True)

    assert node_id == "UNCLASSIFIED"
    assert result.code == "UNCLASSIFIED"
    # Total failure is a REAL zero, never None — the critic must gate it.
    assert result.confidence == 0.0


# --- critic: the gate itself (confidence is not None and confidence <= 0.0) ------


class _FixedClassifier:
    """Duck-typed ClinicalClassifier returning one fixed (node_id, confidence)."""

    def __init__(self, node_id, confidence):
        self._node_id = node_id
        self._confidence = confidence

    async def classify_intent(self, user_text, context=None, return_logprobs=False):
        result = ClassificationResult(
            node_id=self._node_id,
            code="X",
            confidence=self._confidence,
            raw_response="X",
            mean_logprob=None,
        )
        return self._node_id, result


def _critic(node_id, confidence):
    # skip_segmentation=True bypasses the LLM segmenter: the whole input becomes a
    # single ACTION segment, so the test isolates classification + gate + triage.
    return SafetyCritic(
        classifier=_FixedClassifier(node_id, confidence), skip_segmentation=True
    )


async def test_none_confidence_skips_gate_and_trusts_adaptive_label():
    result = await _critic("BEHAVIORAL_MASTERY", None).evaluate_intervention("I fight back")

    assert result.is_safe is True
    assert result.node_id == "BEHAVIORAL_MASTERY"
    assert result.segments[0].node_id == "BEHAVIORAL_MASTERY"  # NOT rerouted
    assert result.confidence is None
    assert "VALIDATE" in (result.constraint or "")


async def test_none_confidence_skips_gate_and_trusts_maladaptive_label():
    result = await _critic("AVOIDANCE", None).evaluate_intervention("I hide under the bed")

    assert result.is_safe is False
    assert result.node_id == "AVOIDANCE"  # trusted, not rerouted to UNCLASSIFIED
    assert result.severity == "HIGH"
    assert "[SAFETY SYSTEM - INTERVENTION REQUIRED]" in (result.constraint or "")


async def test_zero_confidence_reroutes_to_unclassified_and_fails_closed():
    # A genuine 0.0 (fuzzy match / total failure) must be gated even when the
    # label itself looked adaptive.
    result = await _critic("BEHAVIORAL_MASTERY", 0.0).evaluate_intervention("I fight back")

    assert result.is_safe is False
    assert result.node_id == "UNCLASSIFIED"
    assert result.segments[0].node_id == "UNCLASSIFIED"  # rerouted
    assert result.severity == "UNCERTAIN"
    assert "[SAFETY SYSTEM - CLARIFICATION NEEDED]" in (result.constraint or "")


async def test_tiny_positive_confidence_passes_the_gate():
    # Gate is `<= 0.0`, strictly: any positive confidence must pass untouched.
    result = await _critic("BEHAVIORAL_MASTERY", 1e-9).evaluate_intervention("I fight back")

    assert result.is_safe is True
    assert result.node_id == "BEHAVIORAL_MASTERY"
    assert result.confidence == pytest.approx(1e-9)
