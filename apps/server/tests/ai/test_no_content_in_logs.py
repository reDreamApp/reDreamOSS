"""No chat content in the logs, not even at DEBUG.

Prompts, segments and classifier answers carry what people wrote about their
dreams (health data). The logs say what happened (model, sizes, labels from
the fixed code list), never what was written. Found in the S20 live run: the
prompt logger wrote every prompt with the whole chat at INFO.
"""

from __future__ import annotations

import json
import logging
import types

import pytest

from redream_ai.clinical_safety.classifier import ClinicalClassifier
from redream_ai.clinical_safety.critic import SafetyCritic
from redream_ai.core import agent

SECRET = "Zephyrlantern"
USER_TEXT = f"I lift the {SECRET} and the path behind the wolf lights up"
CONTEXT = f"A wolf chased me past the {SECRET} tree"


class _ScriptedAgent:
    def __init__(self, content):
        self._content = content

    async def generate(self, prompt, **kwargs):
        return self._content, {"input": 1, "output": 1, "total": 2}, None


@pytest.fixture
def all_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def _assert_no_content(caplog: pytest.LogCaptureFixture) -> None:
    text = caplog.text
    assert caplog.records, "the code under test logged nothing; the check would be empty"
    assert SECRET not in text
    assert SECRET.upper() not in text


async def test_agent_logs_sizes_not_prompts(all_logs) -> None:
    llm = agent.Agent("m-small", f"system with {SECRET}")

    class _Completions:
        async def create(self, **kwargs):
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content=f"reply about {SECRET}"),
                        logprobs=None,
                    )
                ],
                usage=None,
            )

    llm.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))

    content, _, _ = await llm.generate(USER_TEXT)

    assert SECRET in content
    assert "llm.request model=m-small" in all_logs.text
    _assert_no_content(all_logs)


@pytest.mark.parametrize(
    "answer",
    [
        "CONFRONT",
        f"I think {SECRET} means CONFRONT",  # unknown code, fuzzy-matched
        f"{SECRET} {SECRET}",  # unmatchable: UNCLASSIFIED
    ],
)
async def test_classifier_logs_labels_not_text(all_logs, answer) -> None:
    classifier = ClinicalClassifier()
    classifier.agent = _ScriptedAgent(answer)

    await classifier.classify_intent(USER_TEXT, context=CONTEXT, return_logprobs=True)

    _assert_no_content(all_logs)


async def test_critic_logs_segment_types_not_segments(all_logs) -> None:
    classifier = ClinicalClassifier()
    classifier.agent = _ScriptedAgent("CONFRONT")
    critic = SafetyCritic(classifier=classifier)
    critic.segmenter = _ScriptedAgent(
        json.dumps([{"text": USER_TEXT, "type": "ACTION", "context": CONTEXT}])
    )

    result = await critic.evaluate_intervention(USER_TEXT, nightmare_context=CONTEXT)

    assert result.is_safe
    assert "['ACTION']" in all_logs.text
    _assert_no_content(all_logs)
