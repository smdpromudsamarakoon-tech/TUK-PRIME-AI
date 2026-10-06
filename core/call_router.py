"""Deterministic voice-path glue for call commands.

main.py feeds every speech-to-text chunk through ``CallVoiceGate.classify``.
A confident CALL_* match never reaches the conversational LLM as chat: the
gate resolves it against the real call state, executes + verifies it through
``actions.call_control.execute_call_command``, and hands back an honest
sentence for TUK to speak.

Pure logic, no audio / session objects -- so the whole decision path is unit
testable without Gemini or a microphone.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import re

from core.call_intent import (CALL_ANSWER, CALL_HANGUP, CALL_REJECT, Detection, classify,
                              is_retry_phrase)
from actions.call_control import (
    RINGING_STATES, CallController, CallOutcome, CallState, _log, execute_call_command,
    get_controller,
)

# A weak match ("answer it", "eka ganna") is only a call command when the call
# state makes it one: "it" resolves to the call that is actually ringing.
_WEAK_STATES = {
    CALL_ANSWER: RINGING_STATES,
    CALL_REJECT: RINGING_STATES,
    CALL_HANGUP: RINGING_STATES | {CallState.CONNECTED},
}


_SINHALA = re.compile("[\u0D80-\u0DFF]")
RETRY_WINDOW_S = 90.0


def call_lang(text: str) -> str:
    """Language the user wrote in, so the spoken result matches it. Sinhala
    script -> Sinhala; everything else (English, Singlish) -> English."""
    return "Sinhala" if _SINHALA.search(text or "") else "English"


class CallVoiceGate:
    def __init__(self, controller_factory: Callable[[], CallController] = get_controller,
                 executor: Callable[..., CallOutcome] = execute_call_command):
        self._factory = controller_factory
        self._exec = executor
        self._last: Optional[CallOutcome] = None
        self._last_at = 0.0
        self._last_intent: Optional[str] = None
        self._last_intent_at = 0.0

    # -- cheap, synchronous: safe to call on every transcript chunk --------
    def classify(self, text: str) -> Optional[Detection]:
        det = classify(text)
        if det is not None:
            return det
        # "try again" / "ayeth" shortly after a call command repeats that command.
        # The call state is re-read from the provider every time, never reused.
        if (self._last_intent and time.monotonic() - self._last_intent_at <= RETRY_WINDOW_S
                and is_retry_phrase(text)):
            return Detection(self._last_intent, True, "retry")
        return None

    # -- blocking (HTTP / polling): run in an executor thread ---------------
    def resolve(self, det: Detection) -> Optional[CallOutcome]:
        """Strong match: always executes (and answers honestly even when there
        is no call). Weak match: executes only while a call is ringing/active,
        otherwise returns None so the sentence goes to normal conversation."""
        try:
            ctl = self._factory()
            if not det.strong:
                st = ctl.get_call_state().state
                if st not in _WEAK_STATES[det.intent]:
                    _log("CALL_STATE_CHECK", state=st.value, note="weak command ignored, no matching call")
                    return None
            outcome = self._exec(det.intent, ctl, command=det.text)
        except Exception as e:                      # never a fake success
            _log("CALL_ANSWER_RESULT", result="ERROR", error=str(e))
            outcome = CallOutcome(det.intent, "FAILED",
                                  "I couldn't answer the call because the phone control "
                                  "connection isn't available.")
        self._last, self._last_at = outcome, time.monotonic()
        self._last_intent, self._last_intent_at = det.intent, self._last_at
        return outcome

    def recent_outcome(self, max_age: float = 20.0) -> Optional[CallOutcome]:
        """Outcome already produced for the current utterance -- lets the LLM
        tool path reuse it instead of acting twice on one request."""
        if self._last and time.monotonic() - self._last_at <= max_age:
            return self._last
        return None

    def consume_recent(self) -> None:
        self._last = None
