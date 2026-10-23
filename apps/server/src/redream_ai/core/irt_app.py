from typing import List, Tuple, AsyncGenerator, Optional
import asyncio
import contextlib
import re
from .models import Conversation, Stage, ChatInput, ChatResponse
import hashlib
import logging
import json
from .prompts import (SYSTEM_PROMPT_TEMPLATES_DE, SYSTEM_PROMPT_TEMPLATES_EN,
                      NIGHTMARE_SUMMARY_PROMPT_DE, NIGHTMARE_SUMMARY_PROMPT_EN,
                      SCOPE_BOUNDARY_DE, SCOPE_BOUNDARY_EN, HELPLINE_REFERENCE,
                      INTRO_MESSAGE_DE, INTRO_MESSAGE_EN,
                      SAFETY_NOTE_INTRO_DE, SAFETY_NOTE_INTRO_EN,
                      SAFETY_NOTE_STYLE_DE, SAFETY_NOTE_STYLE_EN,
                      RECORDING_ENOUGH_NOTE_DE, RECORDING_ENOUGH_NOTE_EN,
                      RECORDING_NO_DREAM_YET_NOTE_DE, RECORDING_NO_DREAM_YET_NOTE_EN,
                      REWRITING_ENOUGH_NOTE_DE, REWRITING_ENOUGH_NOTE_EN,
                      REWRITING_ONE_MORE_NOTE_DE, REWRITING_ONE_MORE_NOTE_EN,
                      REWRITING_OPENER_NOTE_DE, REWRITING_OPENER_NOTE_EN,
                      REWRITING_NOTHING_YET_NOTE_DE, REWRITING_NOTHING_YET_NOTE_EN,
                      LANGUAGE_SWITCH_NOTE_DE, LANGUAGE_SWITCH_NOTE_EN,
                      LANGUAGE_CHECK_PROMPT, REPLAY_CHECK_PROMPT, TALK_GATE_PROMPT)

# The LLM endpoint comes from redream_ai.config (LLM_*, environment or .env). The
# test conftests pin a throwaway endpoint as env vars, which win over any .env.
from ..config import settings

# Tracing decorators: no-op stand-ins (reDreamOSS has no tracing backend).
from ..observability import langfuse_context, observe  # noqa: E402

# The language comes from the client (language_override). The backend switches
# it between turns on the AI client's language_check (resolve_language_switch below).

# Routing/response agents are built from the LLM_* settings (see agent.py).
from .agent import routing_agent, response_agent  # noqa: E402

# Get logger instances
logger = logging.getLogger(__name__)

# The endpoint is checked at startup (the server's lifespan logs readiness), not as
# a hard raise at import time — so the module imports cleanly for tests/offline dev.
# When LLM_* is incomplete, `routing_agent`/`response_agent` are None (agent.py
# warns) and api.chat raises 503. (Reconciles the §8 import-time asymmetry noted in
# the brief.)


def _build_safety_critic():
    """The SafetyCritic, or None (fail-open: the chatbot works without it).

    None when SAFETY_CRITIC_ENABLED is false (warned, so a disabled critic is
    never silent) or when it cannot be built.
    """
    if not settings.safety_critic_enabled:
        logger.warning("SAFETY_CRITIC_ENABLED is false — rewriting turns run without the SafetyCritic.")
        return None
    try:
        from ..clinical_safety.critic import SafetyCritic
        critic = SafetyCritic()
    except Exception as e:
        logger.warning(f"SafetyCritic initialization failed: {e}. Safety checks disabled.")
        return None
    logger.info("SafetyCritic initialized successfully")
    return critic


safety_critic = _build_safety_critic()

@observe(as_type="trace", capture_input=False, capture_output=False)
async def process_chat_message(
    chat_input: ChatInput,
    conversation: Conversation,
    safety_enabled: bool = True,
) -> ChatResponse:
    """Process a chat message and return complete response"""
    try:
        # Update the trace level input/output
        langfuse_context.update_current_trace(
            name=f"Chat Session: {chat_input.session_id[:8]}",
            session_id=chat_input.session_id,
            user_id=chat_input.user_id,
            tags=conversation.stages,
            input=chat_input.message,
            output=None
        )
        
        # Update observation level input/output
        langfuse_context.update_current_observation(
            name="Process Message",
            input=chat_input.message,
            output=None,
            metadata={
                "type": "chat_processing",
                "is_streaming": False,
                "user_id": chat_input.user_id
            }
        )
        
        # Add user message first so stage determination sees it in history
        # Use current conversation language or override for initial tagging
        initial_language = chat_input.language_override or conversation.language
        conversation.add_message(chat_input.message, "user", language=initial_language)

        # Language is controlled by override (from UI) or previous conversation setting
        detected_language = chat_input.language_override or conversation.language or "en"
        stage = await determine_stage_async(chat_input.message, conversation)
        stage, summary_held_back = await _hold_summary_without_rewrite(stage, conversation)
        conversation.language = detected_language

        # Generate nightmare summary at recording→rewriting transition
        if stage == "rewriting" and conversation.nightmare_summary is None:
            conversation.nightmare_summary = await generate_nightmare_summary(conversation)

        # Safety evaluation during rewriting stage
        safety_result = None
        if safety_enabled and stage == "rewriting":
            safety_result = await evaluate_safety(chat_input.message, conversation)

        response, usage = await get_response_async(
            stage, chat_input.message, conversation,
            safety_constraint=await _stage_note(
                stage, safety_result, conversation, detected_language, summary_held_back,
                chat_input.language_switched
            )
        )
        conversation.add_message(response, "assistant", stage, language=detected_language)

        response_obj = ChatResponse(
            session_id=chat_input.session_id,
            stage=stage,
            response=response,
            stages=conversation.stages,
            usage=usage,
            language=detected_language,
            safety=safety_result.to_dict() if safety_result else None
        )
        
        # Update both trace and observation output
        langfuse_context.update_current_trace(
            output=response
        )
        langfuse_context.update_current_observation(
            output=response,
            usage=usage
        )
        
        return response_obj
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        raise

@observe(as_type="trace", capture_input=False, capture_output=False)
async def process_chat_message_stream(
    chat_input: ChatInput,
    conversation: Conversation,
    safety_enabled: bool = True,
) -> AsyncGenerator[str, None]:
    """Process a chat message and yield streaming response"""
    try:
        # Update trace level with session, user, input
        langfuse_context.update_current_trace(
            name=f"Streaming Chat Session: {chat_input.session_id[:8]}",
            session_id=chat_input.session_id,
            user_id=chat_input.user_id,
            tags=conversation.stages,
            input=chat_input.message
        )
        
        langfuse_context.update_current_observation(
            name="Process Stream Message",
            input=chat_input.message,
            output=None,
            metadata={
                "type": "chat_processing",
                "is_streaming": True
            }
        )
        
        # Add user message first so stage determination sees it in history
        initial_language = chat_input.language_override or conversation.language
        conversation.add_message(chat_input.message, "user", language=initial_language)

        # Language is controlled by override (from UI) or previous conversation setting
        detected_language = chat_input.language_override or conversation.language or "en"
        stage = await determine_stage_async(chat_input.message, conversation)
        stage, summary_held_back = await _hold_summary_without_rewrite(stage, conversation)
        conversation.language = detected_language

        # Generate nightmare summary at recording→rewriting transition
        if stage == "rewriting" and conversation.nightmare_summary is None:
            conversation.nightmare_summary = await generate_nightmare_summary(conversation)

        # Safety evaluation during rewriting stage
        safety_result = None
        if safety_enabled and stage == "rewriting":
            safety_result = await evaluate_safety(chat_input.message, conversation)

        history = conversation.get_history_as_string()

        is_english = (conversation.language == "en")
        templates = SYSTEM_PROMPT_TEMPLATES_EN if is_english else SYSTEM_PROMPT_TEMPLATES_DE
        intro_message = INTRO_MESSAGE_EN if is_english else INTRO_MESSAGE_DE

        # Prepend intro message to the history string used for the prompt
        history_for_prompt = intro_message + "\\n" + history if history else intro_message
        
        prompt_template = templates[stage]
        full_prompt = f"\n\nConversation history:\n{history_for_prompt}"
        
        full_response = ""
        final_usage = {}
        stage_note = await _stage_note(
            stage, safety_result, conversation, detected_language, summary_held_back,
            chat_input.language_switched
        )
        # aclosing (here and in get_response_stream_async): closing this stream
        # closes the provider response right away, not whenever GC gets to it.
        async with contextlib.aclosing(get_response_stream_async(
            stage, full_prompt, conversation, detected_language,
            safety_constraint=stage_note
        )) as response_stream:
            async for chunk, chunk_usage in response_stream:
                if chunk:
                    full_response += chunk
                    yield f"data: {json.dumps({'content': chunk, 'stages': conversation.stages, 'language': detected_language})}\n\n"
                if chunk_usage:
                    final_usage = chunk_usage
        
        conversation.add_message(full_response, "assistant", stage, language=detected_language)
        
        # Update trace output at the end
        langfuse_context.update_current_trace(
            output=full_response
        )
        langfuse_context.update_current_observation(
            output=full_response,
            usage=final_usage
        )
        
        # Surface the response-generation token usage to the wire adapter: the
        # chat_stream adapter consumes this frame (never forwards it) and reports
        # the numbers in its `final` frame, mirroring chat's ChatResponse.usage.
        if final_usage:
            yield f"data: {json.dumps({'usage': final_usage})}\n\n"

        if safety_result:
            yield f"data: {json.dumps({'safety': safety_result.to_dict()})}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

@observe(name="determine_stage", as_type="generation", capture_input=False, capture_output=False)
async def determine_stage_async(user_input: str, conversation: Conversation) -> str:
    """Async version of determine_stage"""
    langfuse_context.update_current_observation(
        name="Stage Determination",
        input=user_input,
        output=None,  # Will be set later
        metadata={"type": "stage_determination"}
    )
    
    history = conversation.get_history_as_string()
    # Corrected prompt: ROUTING_PROMPT is handled by the agent's system_prompt
    prompt = f"<transcript>\n{history}\n</transcript>\n\nClassification:"
    
    stage_response, usage, _ = await routing_agent.generate(prompt)  # Unpack content, usage, and logprobs
    stage_str = stage_response.strip()
    logger.info(
        "Stage output: %s",
        stage_str if stage_str in {s.value for s in Stage} else f"({len(stage_str)} chars)",
    )
    
    try:
        stage = Stage(stage_str)
    except ValueError:
        # Unparseable routing output → stay in the current stage. On turn 1 the
        # stages list is empty, so default to RECORDING instead of IndexError-ing.
        if conversation.stages:
            stage = Stage(conversation.stages[-1])
        else:
            stage = Stage.RECORDING
        logger.warning(f"Invalid stage ({len(stage_str)} chars), defaulting to {stage.value}")

    # APP BUILD: 'rehearsal' is not part of the routed chat flow (it happens later
    # in the dream journal, not in chat). If the router ever emits it — the response
    # template still exists as a website-legacy artifact — treat it as the final
    # wrap-up. See ROUTING_PROMPT in prompts.py.
    if stage == Stage.REHEARSAL:
        logger.info("Rehearsal is not routed in the app build; treating as final")
        stage = Stage.FINAL

    stage = _keep_stage_order(stage, conversation)

    conversation.stages.append(stage.value)
    
    langfuse_context.update_current_observation(
        output=stage.value,
        metadata={"stage": stage.value},
        usage=usage  # Add usage data to the observation
    )
    
    return stage.value

# A session cannot leave recording on the user's first message: the guide
# answers at least once in recording before any rewriting starts.
MIN_USER_MESSAGES_BEFORE_REWRITING = 2

_ROUTED_AFTER_RECORDING = (Stage.REWRITING.value, Stage.SUMMARY.value, Stage.FINAL.value)


def _keep_stage_order(stage: Stage, conversation: Conversation) -> Stage:
    """Hold the router's verdict to the order a session moves through.

    The router is a model and can guess wrong; the order is not negotiable:
    no step is skipped (final needs a summary, a summary needs a rewriting
    turn), rewriting cannot start on the user's first message, and once
    rewriting has begun a turn never falls back to recording.
    """
    previous = conversation.stages

    # (The website build's rehearsal-before-final guard is dropped here since
    # rehearsal isn't routed.)
    if stage == Stage.FINAL and Stage.SUMMARY.value not in previous:
        logger.info("Redirecting to summary stage as no summary has been generated yet")
        stage = Stage.SUMMARY

    if stage == Stage.SUMMARY and Stage.REWRITING.value not in previous:
        logger.info("Redirecting to rewriting stage as nothing has been rewritten yet")
        stage = Stage.REWRITING

    if stage == Stage.REWRITING and Stage.REWRITING.value not in previous:
        user_messages = sum(1 for m in conversation.messages if m.role == "user")
        if user_messages < MIN_USER_MESSAGES_BEFORE_REWRITING:
            logger.info("Staying in recording: rewriting cannot start on the first message")
            stage = Stage.RECORDING

    if stage == Stage.RECORDING and Stage.REWRITING.value in previous:
        # Fall back to the newest stage this build routes to.
        routed = [s for s in previous if s in _ROUTED_AFTER_RECORDING]
        logger.info("Staying in %s: a session does not go back to recording", routed[-1])
        stage = Stage(routed[-1])

    return stage


@observe(name="generate_nightmare_summary", as_type="generation")
async def generate_nightmare_summary(conversation: Conversation) -> Optional[str]:
    """Generate a comprehensive summary of the nightmare from the recording phase.

    Called once at the recording→rewriting transition. Extracts user messages
    from before the first rewriting-stage response and summarizes them.
    """
    try:
        is_english = (conversation.language == "en")
        summary_prompt = NIGHTMARE_SUMMARY_PROMPT_EN if is_english else NIGHTMARE_SUMMARY_PROMPT_DE

        # Collect user messages from recording phase (before first rewriting assistant message)
        recording_messages = []
        for msg in conversation.messages:
            if msg.role == "assistant" and msg.stage == "rewriting":
                break
            if msg.role == "user":
                recording_messages.append(msg.content)

        if not recording_messages:
            logger.warning("No recording-phase user messages found for nightmare summary")
            return None

        recording_text = "\n".join(recording_messages)
        prompt = f"Patient's nightmare description:\n{recording_text}"

        summary, usage, _ = await response_agent.generate(prompt, system_prompt=summary_prompt)

        logger.info(f"Generated nightmare summary ({len(summary)} chars)")
        # An empty answer is no summary: None makes the next turn try again.
        return summary.strip() or None

    except Exception as e:
        logger.error(f"Failed to generate nightmare summary: {e}")
        return None


# Longer messages go to the critic unchecked: they carry dream content.
_TALK_MAX_WORDS = 10
# Verdicts for short replies, keyed by a hash so that no user text is kept.
_talk_verdicts: dict[str, bool] = {}
_TALK_VERDICTS_MAX = 512


async def _is_plain_talk(text: str) -> bool:
    """True when a short message says nothing about the dream ("yes", "that's all").

    Such a message gives the critic nothing to judge, but its classifier still
    answers, mostly with "replay" (10 of 13 plain replies in the 2026-10-14
    runs). Any doubt or failure counts as dream content, so the critic runs.
    """
    text = text.strip()
    if not text or len(text.split()) > _TALK_MAX_WORDS:
        return False
    if not any(ch.isalpha() for ch in text):
        return True  # only symbols or emoji
    key = hashlib.sha256(text.lower().encode()).hexdigest()
    if key in _talk_verdicts:
        return _talk_verdicts[key]
    try:
        answer, _, _ = await routing_agent.generate(f"Message: {text}", system_prompt=TALK_GATE_PROMPT)
    except Exception as e:
        logger.error(f"Talk check failed (the critic runs): {e}")
        return False
    verdict = answer.strip().upper().startswith("TALK")
    if len(_talk_verdicts) >= _TALK_VERDICTS_MAX:
        _talk_verdicts.clear()
    _talk_verdicts[key] = verdict
    return verdict


# Words that are common in one language and not words of the other. Left out
# on purpose: words of both ("in", "so", "man"), and look-alikes with another
# meaning ("was", "die", "will", "war", "also", "am", "an", "hat", "still",
# "not", "her", "um", "er", "im"). English contractions are single words
# ("don't", "i'm"), as the tokenizer keeps the apostrophe.
_GERMAN_WORDS = frozenset("""
    ich du sie wir ihr es sich mein meine meinen meinem meiner meines
    dein deine deinen deinem deiner mir mich dich dir uns
    nicht kein keine keinen keinem keiner keines nichts niemand alles
    und oder aber auch noch sehr schon nur jetzt dann dass weil doch mal mehr immer
    wenn wie ist sind bin bist habe hab hast haben hatte waren wird wurde würde
    kann kannst möchte brauche geht gibt bitte
    ein eine einen einem einer der das dem des im vom zum zur über für mit auf zu
    von nach bei aus
""".split())
_ENGLISH_WORDS = frozenset("""
    i me my myself you your we they she he him his it this that the a of to with for
    from and but or if is are be been were had have has do does did
    can could would want wanna need feel know think speak understand help
    don't dont didn't didnt doesn't isn't wasn't can't cant won't
    i'm i've i'll it's let's
    please very already just about how what when where why who there then nobody
    on at up out
""".split())
# The other language's name, in either language; "deutsch" and "englisch"
# also inside compounds ("Hochdeutsch", "Schulenglisch").
_OTHER_LANGUAGE_NAME = {
    "en": re.compile(r"deutsch\w*|englisch\w*|\bgermans?\b", re.IGNORECASE),
    "de": re.compile(r"englisch\w*|\benglish\w*|\bgermans?\b", re.IGNORECASE),
}
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")
_LANGUAGE_CHECK_TIMEOUT_S = 8.0
_LANGUAGE_NAMES = {"en": "English", "de": "German"}
# Answers keyed by a hash of language and text, so that no user text is kept.
_language_verdicts: dict[str, Optional[str]] = {}
_LANGUAGE_VERDICTS_MAX = 512


def _may_switch_language(text: str, current: str) -> bool:
    """Cheap check before the model: does the message name or look like the other language?"""
    if _OTHER_LANGUAGE_NAME[current].search(text):
        return True
    words = [w.lower() for w in _WORD.findall(text.replace("\u2019", "'"))]
    if len(words) < 3:
        return False
    other, own = (_GERMAN_WORDS, _ENGLISH_WORDS) if current == "en" else (_ENGLISH_WORDS, _GERMAN_WORDS)
    other_hits = [w for w in words if w in other]
    own_hits = sum(1 for w in words if w in own)
    # One word of the other language is enough when none of the current one
    # appears: short sentences in distress ("Ich will sterben") must reach the
    # model, so the crisis check and the helpline get the user's language.
    return (len(set(other_hits)) >= 2 and len(other_hits) > own_hits) or (
        bool(other_hits) and own_hits == 0
    )


async def resolve_language_switch(message: str, current: str) -> Optional[str]:
    """The language the chat continues in when this message switches it ("en" / "de"), else None.

    Only messages that pass the word check above reach the small model (the
    routing agent, as for _is_plain_talk). It names the message's language
    (EN / DE / OTHER); the chat switches when that is the other of the two. A
    timeout, an error, OTHER or any other answer keeps the language, and so
    does the current language.
    """
    text = message.strip()
    if current not in _LANGUAGE_NAMES or not text or not _may_switch_language(text, current):
        return None
    key = hashlib.sha256(f"{current}\n{text.lower()}".encode()).hexdigest()
    if key in _language_verdicts:
        return _language_verdicts[key]
    try:
        answer, _, _ = await asyncio.wait_for(
            routing_agent.generate(text, system_prompt=LANGUAGE_CHECK_PROMPT),
            timeout=_LANGUAGE_CHECK_TIMEOUT_S,
        )
    except Exception as e:  # includes the timeout
        logger.error(f"Language check failed (the language stays): {type(e).__name__}")
        return None
    # Exactly one of the three words; "DE/EN" or "EN or DE" keep the language.
    match = re.fullmatch(r"\s*(EN|DE|OTHER)[\s.!]*", (answer or "").upper())
    if match is None:
        logger.warning("Language check gave no usable answer (the language stays)")
        return None
    verdict = match.group(1).lower()
    switched_to = verdict if verdict in _LANGUAGE_NAMES and verdict != current else None
    if len(_language_verdicts) >= _LANGUAGE_VERDICTS_MAX:
        _language_verdicts.clear()
    _language_verdicts[key] = switched_to
    return switched_to


async def _describes_a_change(user_input: str, nightmare_summary: Optional[str]) -> bool:
    """Second opinion on a "replay" verdict: does the message differ from the original?

    False on any doubt or failure, so the critic's verdict stands.
    """
    if not nightmare_summary:
        return False
    try:
        answer, _, _ = await routing_agent.generate(
            f"[ORIGINAL DREAM]\n{nightmare_summary}\n\n[MESSAGE]\n{user_input}",
            system_prompt=REPLAY_CHECK_PROMPT,
        )
    except Exception as e:
        logger.error(f"Replay check failed (the critic's verdict stands): {e}")
        return False
    return answer.strip().upper().startswith(("CHANGED", "NONE"))


async def _is_false_alarm(result, user_input: str, nightmare_summary: Optional[str]) -> bool:
    """True when everything the critic flagged is a closing remark or a mistaken "replay".

    Two things its classifier gets wrong often enough to derail a session
    (2026-10-14 runs): "That is the whole new version." after a good rewrite,
    and a rewrite that keeps people or things from the original. Every other
    verdict (violence, waking up, feeling nothing, ...) stands as it is.
    """
    flagged = [s for s in result.segments if s.node_type == "maladaptive"]
    if not flagged:
        # The message was flagged as a whole.
        return result.node_id == "TRAUMA_REPLAY" and await _describes_a_change(user_input, nightmare_summary)
    changed: Optional[bool] = None
    for segment in flagged:
        if await _is_plain_talk(segment.text):
            continue
        if segment.node_id != "TRAUMA_REPLAY":
            return False
        if changed is None:
            changed = await _describes_a_change(user_input, nightmare_summary)
        if not changed:
            return False
    return True


def _safety_note(safety_result, is_english: bool) -> Optional[str]:
    """The critic's note for the reply prompt, framed so that it is not quoted."""
    if safety_result is None or not safety_result.constraint:
        return None
    if safety_result.is_safe:
        return safety_result.constraint
    intro = SAFETY_NOTE_INTRO_EN if is_english else SAFETY_NOTE_INTRO_DE
    style = SAFETY_NOTE_STYLE_EN if is_english else SAFETY_NOTE_STYLE_DE
    return intro + "\n\n" + safety_result.constraint + "\n\n" + style


def _rewriting_answers(conversation: Conversation) -> List[str]:
    """What the user has said since the guide opened the rewriting stage."""
    opener = next(
        (i for i, m in enumerate(conversation.messages) if m.role == "assistant" and m.stage == "rewriting"),
        None,
    )
    if opener is None:
        return []
    return [m.content for m in conversation.messages[opener + 1:] if m.role == "user"]


async def _has_new_version(conversation: Conversation) -> bool:
    """True once the user has said anything about the new version of the dream."""
    for answer in _rewriting_answers(conversation):
        if not await _is_plain_talk(answer):
            return True
    return False


async def _hold_summary_without_rewrite(stage: str, conversation: Conversation) -> Tuple[str, bool]:
    """Keep a session in rewriting while there is nothing to summarise.

    A user who only answers "yes" gets routed to the summary sooner or later,
    and the summary then has to make up a new version (2026-10-14 runs).
    Returns the stage for this turn and whether the summary was held back.
    """
    if stage != Stage.SUMMARY.value or Stage.SUMMARY.value in conversation.stages[:-1]:
        return stage, False
    if await _has_new_version(conversation):
        return stage, False
    logger.info("Staying in rewriting: no new version has been described yet")
    conversation.stages[-1] = Stage.REWRITING.value
    return Stage.REWRITING.value, True


async def _rewriting_note(
    safety_result, conversation: Conversation, is_english: bool, summary_held_back: bool
) -> Optional[str]:
    note = _safety_note(safety_result, is_english)
    if note:
        return note
    nothing_yet = REWRITING_NOTHING_YET_NOTE_EN if is_english else REWRITING_NOTHING_YET_NOTE_DE
    if summary_held_back:
        return nothing_yet
    latest = next((m.content for m in reversed(conversation.messages) if m.role == "user"), "")
    if not await _is_plain_talk(latest):
        return None
    answers = _rewriting_answers(conversation)
    if not answers:
        # The stage opens with this reply, on a message without any change in it.
        return REWRITING_OPENER_NOTE_EN if is_english else REWRITING_OPENER_NOTE_DE
    # The user added nothing: one more question the first time, then no more.
    plain = [await _is_plain_talk(answer) for answer in answers]
    if all(plain):
        return nothing_yet
    if sum(plain) < 2:
        return REWRITING_ONE_MORE_NOTE_EN if is_english else REWRITING_ONE_MORE_NOTE_DE
    return REWRITING_ENOUGH_NOTE_EN if is_english else REWRITING_ENOUGH_NOTE_DE


async def _recording_note(conversation: Conversation, is_english: bool) -> Optional[str]:
    # The same for the telling of the dream: three times nothing new since it
    # began (a "yes" to "anything else?" deserves its follow-up question), or a
    # second message before any dream was told.
    answers = [m.content for m in conversation.messages if m.role == "user"]
    if len(answers) < 2 or not await _is_plain_talk(answers[-1]):
        return None
    plain = [await _is_plain_talk(answer) for answer in answers]
    if all(plain):
        return RECORDING_NO_DREAM_YET_NOTE_EN if is_english else RECORDING_NO_DREAM_YET_NOTE_DE
    if sum(plain[plain.index(False) + 1:]) < 3:
        return None
    return RECORDING_ENOUGH_NOTE_EN if is_english else RECORDING_ENOUGH_NOTE_DE


async def _stage_note(
    stage: str, safety_result, conversation: Conversation, language: str, summary_held_back: bool = False,
    language_switched: bool = False
) -> Optional[str]:
    """What is added to the stage prompt for this turn, if anything.

    The critic's note on a flagged rewrite, or a reminder to stop asking a
    user who has nothing more to add. The model does not keep count by itself
    (2026-10-14 runs: up to 13 questions in a row to "yes" and "no" answers).
    On the turn the backend switched the chat's language, the switch note
    comes last.
    """
    is_english = language == "en"
    note = None
    if stage == Stage.REWRITING.value:
        note = await _rewriting_note(safety_result, conversation, is_english, summary_held_back)
    elif stage == Stage.RECORDING.value:
        note = await _recording_note(conversation, is_english)
    if not language_switched:
        return note
    switch_note = LANGUAGE_SWITCH_NOTE_EN if is_english else LANGUAGE_SWITCH_NOTE_DE
    return note + "\n\n" + switch_note if note else switch_note


@observe(name="safety_evaluation", as_type="generation")
async def evaluate_safety(user_input: str, conversation: Conversation):
    """Run SafetyCritic on user input during the rewriting stage.

    Returns SafetyResult or None if the critic is unavailable or fails.
    Fail-open: the chatbot still has system prompt safety instructions.
    """
    if safety_critic is None:
        return None

    try:
        if await _is_plain_talk(user_input):
            logger.info("Safety evaluation skipped: the message is plain conversation")
            return None
        result = await safety_critic.evaluate_intervention(
            user_input,
            nightmare_context=conversation.nightmare_summary
        )
        logger.info(f"Safety evaluation: is_safe={result.is_safe}, node={result.node_id}, severity={result.severity}")
        if not result.is_safe and await _is_false_alarm(result, user_input, conversation.nightmare_summary):
            logger.info(f"Safety evaluation dropped as a false alarm: node={result.node_id}")
            return None
        return result
    except Exception as e:
        logger.error(f"Safety evaluation failed (proceeding without constraint): {e}")
        return None


def scope_boundary(is_english: bool) -> str:
    """The scope boundary in the prompt language, with HELPLINE_PROMPT_EN /
    HELPLINE_PROMPT_DE read now (not at import) in place of HELPLINE_REFERENCE.

    Plain replacement, never str.format: prompt and operator text may hold braces.
    """
    if is_english:
        return SCOPE_BOUNDARY_EN.replace(HELPLINE_REFERENCE, settings.helpline_prompt_en)
    return SCOPE_BOUNDARY_DE.replace(HELPLINE_REFERENCE, settings.helpline_prompt_de)


@observe(name="get_response", as_type="generation", capture_input=False, capture_output=False)
async def get_response_async(stage: str, user_input: str, conversation: Conversation, safety_constraint: Optional[str] = None) -> Tuple[str, dict]:
    """Async version of get_response"""
    langfuse_context.update_current_observation(
        name="Response Generation",
        input=user_input,
        output=None,  # Will be set later
        metadata={
            "type": "response_generation",
            "stage": stage
        }
    )
    
    history = conversation.get_history_as_string()

    is_english = (conversation.language == "en")
    templates = SYSTEM_PROMPT_TEMPLATES_EN if is_english else SYSTEM_PROMPT_TEMPLATES_DE
    intro_message = INTRO_MESSAGE_EN if is_english else INTRO_MESSAGE_DE

    # Prepend intro message to the history string used for the prompt
    history_for_prompt = intro_message + "\\n" + history if history else intro_message
    
    prompt_template = templates[stage]
    full_prompt = f"\n\nConversation history:\n{history_for_prompt}" # Use the modified history

    # Prepend the scope/boundary guardrail to every stage so the bot stays within
    # the IRT method (no diagnosis / no acting as a confidant; distress → redirect).
    boundary = scope_boundary(is_english)
    base_prompt = boundary + "\n\n" + prompt_template
    if safety_constraint:
        composed_system_prompt = base_prompt + "\n\n" + safety_constraint
    else:
        composed_system_prompt = base_prompt
    response, usage, _ = await response_agent.generate(full_prompt, system_prompt=composed_system_prompt)
    
    langfuse_context.update_current_observation(
        output=response,
        usage=usage
    )
    
    return response, usage

@observe(name="get_response_stream", as_type="generation", capture_input=False, capture_output=False)
async def get_response_stream_async(stage: str, full_prompt: str, conversation: Conversation, language: str, safety_constraint: Optional[str] = None) -> AsyncGenerator[Tuple[str, Optional[dict]], None]:
    """Async streaming version of get_response"""
    langfuse_context.update_current_observation(
        name="Response Generation",
        input=full_prompt,
        output=None,  # Will be set after streaming completes
        metadata={
            "type": "response_generation",
            "stage": stage,
            "language": language,
            "is_streaming": True
        }
    )
    
    is_english = (language == "en")
    templates = SYSTEM_PROMPT_TEMPLATES_EN if is_english else SYSTEM_PROMPT_TEMPLATES_DE
    prompt_template = templates[stage]
    # Prepend the scope/boundary guardrail (see get_response_async).
    boundary = scope_boundary(is_english)
    base_prompt = boundary + "\n\n" + prompt_template
    if safety_constraint:
        composed_system_prompt = base_prompt + "\n\n" + safety_constraint
    else:
        composed_system_prompt = base_prompt

    full_response = ""
    final_usage = {}
    async with contextlib.aclosing(
        response_agent.generate_stream(full_prompt, system_prompt=composed_system_prompt)
    ) as agent_stream:
        async for chunk, chunk_usage, _ in agent_stream:
            if chunk:
                full_response += chunk
            if chunk_usage:
                final_usage = chunk_usage
            yield chunk, chunk_usage
    
    # Update observation with final output and usage
    langfuse_context.update_current_observation(
        output=full_response,
        usage=final_usage
    )
