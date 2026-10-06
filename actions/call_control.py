"""TUK call control.

    TUK Core -> CallController -> Adapter -> telephony provider

* ``CallController`` is the provider-neutral interface the rest of TUK talks to.
* ``PhoneBridgeAdapter`` talks to a local phone/SIP bridge (state file for
  reading, ``TUK_CALL_BRIDGE_URL`` for acting).
* ``WhatsAppAdapter`` exists so personal WhatsApp voice calls are *recognised*
  and answered honestly ("not controllable"); it never fakes an answer and
  never uses unofficial account automation. WhatsApp Business messaging is a
  separate feature (actions/send_message.py).
* ``ChannelRouter`` picks the adapter by the channel of the live call, so a
  future adapter is one ``register()`` call.

``execute_call_command`` is the single execution path. It is shared by the
deterministic voice router (core/call_router.py) and by the LLM tool below, and
it enforces:  detect call -> execute -> VERIFY -> respond.  Success is only
ever reported when the provider's own state afterwards says CONNECTED.

Bridge contract
---------------
State file  ~/.tuk/call_bridge/state.json  (written by the bridge)::

    {"state": "incoming|ringing|connected|ended|none",   # preferred
     "incoming": true,                                    # legacy boolean
     "channel": "phone|whatsapp|...", "caller": "+94..."}

HTTP (optional, needed to actually act) ``TUK_CALL_BRIDGE_URL``::

    POST {"action": "status|answer|reject|hangup"}
    -> {"ok": true, "state": "connected", "channel": "phone", "caller": "..."}
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

_STATE = Path.home() / ".tuk" / "call_bridge" / "state.json"

# Verification: after acting, poll the provider until it confirms (or give up).
VERIFY_TIMEOUT_S = 3.0
VERIFY_POLL_S = 0.3

MSG_NO_CALL = "I don't see an incoming call right now."
MSG_NO_ACTIVE = "I don't see an active call right now."
MSG_NO_BRIDGE_SUFFIX = (" No phone-control connection is set up either, "
                        "so I couldn't pick one up even if it rang.")
MSG_UNSUPPORTED_WA = ("I can recognize the command, but direct control of this "
                      "WhatsApp call isn't available through the current integration.")
MSG_NO_CONNECTION = ("I couldn't answer the call because the phone control "
                     "connection isn't available.")


# ----------------------------------------------------------------- logging
def _log(event: str, **kv) -> None:
    """Structured one-line log: [CALL] event=CALL_STATE_CHECK state=INCOMING"""
    parts = " ".join(f'{k}={v!r}' if isinstance(v, str) and " " in v else f"{k}={v}"
                     for k, v in kv.items())
    print(f"[CALL] event={event} {parts}".rstrip())


# ------------------------------------------------------------------ models
class CallState(str, Enum):
    NO_CALL = "NO_CALL"
    INCOMING = "INCOMING"
    RINGING = "RINGING"
    CONNECTED = "CONNECTED"
    ENDED = "ENDED"
    UNKNOWN = "UNKNOWN"


RINGING_STATES = {CallState.INCOMING, CallState.RINGING}


@dataclass(frozen=True)
class CallInfo:
    state: CallState = CallState.NO_CALL
    channel: str = "none"
    caller: str = ""
    provider_available: bool = False
    detail: str = ""


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    supported: bool = True
    message: str = ""


@dataclass(frozen=True)
class CallOutcome:
    intent: str            # CALL_ANSWER | CALL_REJECT | CALL_HANGUP | CALL_STATUS
    code: str              # SUCCESS | NO_CALL | UNSUPPORTED_CHANNEL | FAILED | NOT_VERIFIED | ...
    message: str           # what TUK should say (final, already honest)
    state_before: CallState = CallState.UNKNOWN
    verified_state: Optional[CallState] = None

    @property
    def success(self) -> bool:
        return self.code == "SUCCESS"

    def tool_text(self) -> str:
        return (f"{self.intent}_RESULT code={self.code}. Say exactly this to the user "
                f"(in their language) and nothing more: \"{self.message}\"")


# --------------------------------------------------------------- interface
class CallController(ABC):
    """Provider-neutral call control. Implementations must never report ok=True
    unless the provider accepted the action."""

    @abstractmethod
    def get_call_state(self) -> CallInfo: ...

    @abstractmethod
    def answer_call(self) -> ActionResult: ...

    @abstractmethod
    def reject_call(self) -> ActionResult: ...

    @abstractmethod
    def hangup_call(self) -> ActionResult: ...

    def get_caller_info(self) -> dict:
        info = self.get_call_state()
        return {"channel": info.channel, "caller": info.caller}


# ------------------------------------------------- phone bridge (local/SIP)
_STATE_WORDS = {
    "incoming": CallState.INCOMING, "ringing": CallState.RINGING,
    "ring": CallState.RINGING, "connected": CallState.CONNECTED,
    "active": CallState.CONNECTED, "answered": CallState.CONNECTED,
    "in_progress": CallState.CONNECTED, "in-call": CallState.CONNECTED,
    "ended": CallState.ENDED, "disconnected": CallState.ENDED,
    "none": CallState.NO_CALL, "idle": CallState.NO_CALL, "no_call": CallState.NO_CALL,
}


def _read_state() -> Optional[dict]:
    """The bridge's state file, or None if there is no bridge writing one."""
    try:
        data = json.loads(_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _bridge_request(action: str) -> dict:
    url = os.getenv("TUK_CALL_BRIDGE_URL", "").strip()
    if not url:
        return {"ok": False, "supported": False,
                "message": "No supported call-control bridge is connected."}
    try:
        payload = json.dumps({"action": action}).encode()
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "TUK-Call-Control/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if isinstance(data, dict) else {"ok": False, "message": "Invalid bridge response."}
    except Exception as e:
        return {"ok": False, "supported": True, "message": f"Call bridge error: {e}"}


def _info_from(data: Optional[dict], provider_available: bool) -> CallInfo:
    if not data:
        return CallInfo(CallState.NO_CALL, provider_available=provider_available)
    channel = str(data.get("channel") or "phone").lower()
    caller = str(data.get("caller") or "").strip()
    raw = str(data.get("state") or "").strip().lower()
    if raw:
        state = _STATE_WORDS.get(raw, CallState.UNKNOWN)
    else:  # legacy {"incoming": bool}
        state = CallState.INCOMING if data.get("incoming") else CallState.NO_CALL
    if state in (CallState.NO_CALL, CallState.ENDED) and channel == "none":
        caller = ""
    return CallInfo(state, channel, caller, provider_available)


class PhoneBridgeAdapter(CallController):
    """Reads call state from the bridge; acts through TUK_CALL_BRIDGE_URL."""

    def _acting_url(self) -> str:
        return os.getenv("TUK_CALL_BRIDGE_URL", "").strip()

    def get_call_state(self) -> CallInfo:
        data = _read_state()
        if data is None and self._acting_url():
            resp = _bridge_request("status")
            if resp.get("ok") or "state" in resp:
                data = resp
            else:
                return CallInfo(CallState.UNKNOWN, provider_available=True,
                                detail=str(resp.get("message", "")))
        return _info_from(data, provider_available=data is not None or bool(self._acting_url()))

    def _act(self, action: str) -> ActionResult:
        if not self._acting_url():
            return ActionResult(False, supported=False,
                                message="No supported call-control bridge is connected.")
        resp = _bridge_request(action)
        return ActionResult(bool(resp.get("ok")), bool(resp.get("supported", True)),
                            str(resp.get("message", "")))

    def answer_call(self) -> ActionResult:
        return self._act("answer")

    def reject_call(self) -> ActionResult:
        return self._act("reject")

    def hangup_call(self) -> ActionResult:
        return self._act("hangup")


class WhatsAppAdapter(CallController):
    """Personal WhatsApp voice calls cannot be controlled through any official
    API, and TUK does not use unofficial automation. This adapter exists so the
    channel is recognised and refused honestly; a real integration would
    replace it via ChannelRouter.register('whatsapp', ...)."""

    supported = False

    def get_call_state(self) -> CallInfo:
        return CallInfo(CallState.UNKNOWN, "whatsapp", provider_available=False)

    def _no(self) -> ActionResult:
        return ActionResult(False, supported=False, message=MSG_UNSUPPORTED_WA)

    def answer_call(self) -> ActionResult: return self._no()
    def reject_call(self) -> ActionResult: return self._no()
    def hangup_call(self) -> ActionResult: return self._no()


class ChannelRouter(CallController):
    """Single controller TUK talks to; dispatches actions by live-call channel."""

    def __init__(self, primary: Optional[CallController] = None):
        self._primary = primary or PhoneBridgeAdapter()
        self._adapters: dict[str, CallController] = {"whatsapp": WhatsAppAdapter()}

    def register(self, channel: str, adapter: CallController) -> None:
        self._adapters[channel.lower()] = adapter

    def _for(self, info: CallInfo) -> CallController:
        return self._adapters.get(info.channel, self._primary)

    def get_call_state(self) -> CallInfo:
        info = self._primary.get_call_state()
        if info.channel in self._adapters and not getattr(self._adapters[info.channel], "supported", True):
            return CallInfo(info.state, info.channel, info.caller, False, info.detail)
        return info

    def answer_call(self) -> ActionResult:
        return self._for(self.get_call_state()).answer_call()

    def reject_call(self) -> ActionResult:
        return self._for(self.get_call_state()).reject_call()

    def hangup_call(self) -> ActionResult:
        return self._for(self.get_call_state()).hangup_call()


_controller: Optional[CallController] = None


def get_controller() -> CallController:
    global _controller
    if _controller is None:
        _controller = ChannelRouter()
    return _controller


# ------------------------------------------------------- execute + verify
def _verify(controller: CallController, wanted: set[CallState]) -> CallState:
    """Poll the provider until it reports a wanted state (or time runs out)."""
    deadline = time.monotonic() + VERIFY_TIMEOUT_S
    state = controller.get_call_state().state
    while state not in wanted and time.monotonic() < deadline:
        time.sleep(VERIFY_POLL_S)
        state = controller.get_call_state().state
    return state


def execute_call_command(intent: str, controller: Optional[CallController] = None,
                         command: str = "") -> CallOutcome:
    """detect call -> execute -> verify -> honest outcome. Never raises."""
    controller = controller or get_controller()
    intent = intent if intent.startswith("CALL_") else f"CALL_{intent.upper()}"
    try:
        _log("CALL_COMMAND_RECEIVED", command=command)
        _log("CALL_INTENT_DETECTED", intent=intent)
        info = controller.get_call_state()
        _log("CALL_STATE_CHECK", state=info.state.value, channel=info.channel,
             provider=info.provider_available)
        return _execute(intent, controller, info)
    except Exception as e:  # a crash must never turn into a fake success
        _log("CALL_ANSWER_RESULT", result="ERROR", error=str(e))
        return CallOutcome(intent, "FAILED", MSG_NO_CONNECTION)


def _execute(intent: str, ctl: CallController, info: CallInfo) -> CallOutcome:
    st = info.state
    ringing = st in RINGING_STATES

    # --- no call / unknown ----------------------------------------------
    if intent == "CALL_ANSWER" or intent == "CALL_REJECT":
        gone_msg = MSG_NO_CALL
    else:
        gone_msg = MSG_NO_ACTIVE

    if info.channel == "whatsapp" and (ringing or st == CallState.CONNECTED or st == CallState.UNKNOWN):
        # Personal WhatsApp voice call: recognised, but not controllable.
        _log("CALL_ANSWER_RESULT", result="UNSUPPORTED_CHANNEL", channel="whatsapp")
        return CallOutcome(intent, "UNSUPPORTED_CHANNEL", MSG_UNSUPPORTED_WA, st)

    if st in (CallState.NO_CALL, CallState.ENDED):
        msg = gone_msg if info.provider_available else gone_msg + MSG_NO_BRIDGE_SUFFIX
        _log("CALL_ANSWER_RESULT", result="NO_CALL")
        return CallOutcome(intent, "NO_CALL" if info.provider_available else "NO_PROVIDER", msg, st)

    if st == CallState.UNKNOWN:
        _log("CALL_ANSWER_RESULT", result="UNKNOWN_STATE")
        return CallOutcome(intent, "UNKNOWN_STATE", MSG_NO_CONNECTION, st)

    # --- act ------------------------------------------------------------
    if intent == "CALL_ANSWER":
        if st == CallState.CONNECTED:
            return CallOutcome(intent, "ALREADY_CONNECTED", "That call is already connected.", st, st)
        _log("CALL_ANSWER_REQUESTED", channel=info.channel, caller=info.caller)
        res = ctl.answer_call()
        _log("CALL_ANSWER_RESULT", result="SUCCESS" if res.ok else "FAILED", detail=res.message)
        if not res.ok:
            msg = MSG_UNSUPPORTED_WA if "WhatsApp" in res.message else (
                MSG_NO_CONNECTION if not res.supported else
                "I couldn't answer the call. The phone control connection reported a problem.")
            return CallOutcome(intent, "FAILED", msg, st)
        final = _verify(ctl, {CallState.CONNECTED})
        _log("CALL_STATE_VERIFIED", verified_state=final.value)
        if final == CallState.CONNECTED:
            return CallOutcome(intent, "SUCCESS", "Call answered.", st, final)
        return CallOutcome(intent, "NOT_VERIFIED",
                           "I sent the answer request, but the phone didn't confirm the call "
                           "connected, so I can't say it was answered.", st, final)

    if intent == "CALL_REJECT":
        if st == CallState.CONNECTED:
            return CallOutcome(intent, "NOT_RINGING", "That call is already connected. "
                               "Say hang up if you want to end it.", st, st)
        res = ctl.reject_call()
        _log("CALL_ANSWER_RESULT", action="REJECT", result="SUCCESS" if res.ok else "FAILED")
        if not res.ok:
            return CallOutcome(intent, "FAILED", MSG_UNSUPPORTED_WA if "WhatsApp" in res.message
                               else "I couldn't decline the call because the phone control "
                                    "connection isn't available.", st)
        final = _verify(ctl, {CallState.ENDED, CallState.NO_CALL})
        _log("CALL_STATE_VERIFIED", verified_state=final.value)
        if final in (CallState.ENDED, CallState.NO_CALL):
            return CallOutcome(intent, "SUCCESS", "Call declined.", st, final)
        return CallOutcome(intent, "NOT_VERIFIED", "I sent the decline request, but the "
                           "phone didn't confirm the call ended.", st, final)

    # CALL_HANGUP (a ringing call is declined instead)
    res = ctl.hangup_call() if st == CallState.CONNECTED else ctl.reject_call()
    _log("CALL_ANSWER_RESULT", action="HANGUP", result="SUCCESS" if res.ok else "FAILED")
    if not res.ok:
        return CallOutcome(intent, "FAILED", MSG_UNSUPPORTED_WA if "WhatsApp" in res.message
                           else "I couldn't end the call because the phone control "
                                "connection isn't available.", st)
    final = _verify(ctl, {CallState.ENDED, CallState.NO_CALL})
    _log("CALL_STATE_VERIFIED", verified_state=final.value)
    if final in (CallState.ENDED, CallState.NO_CALL):
        return CallOutcome(intent, "SUCCESS", "Call ended.", st, final)
    return CallOutcome(intent, "NOT_VERIFIED", "I sent the hang-up request, but the phone "
                       "didn't confirm the call ended.", st, final)


# ---------------------------------------------------------- LLM tool entry
def call_control_action(parameters: dict, **_) -> str:
    """Tool handler (fallback path when the model itself picks the tool).
    Shares execute_call_command, so it can never claim an unverified success."""
    action = str((parameters or {}).get("action") or "status").strip().lower()
    if action not in {"status", "answer", "hangup", "reject"}:
        return "Unsupported call action."
    ctl = get_controller()
    if action == "status":
        info = ctl.get_call_state()
        if info.state in RINGING_STATES:
            who = f" from {info.caller}" if info.caller else ""
            return f"CALL_STATUS: incoming {info.channel} call detected{who}."
        if info.state == CallState.CONNECTED:
            return f"CALL_STATUS: a {info.channel} call is connected."
        return "CALL_STATUS: no incoming call is currently detected."
    intent = {"answer": "CALL_ANSWER", "reject": "CALL_REJECT", "hangup": "CALL_HANGUP"}[action]
    return execute_call_command(intent, ctl, command=f"tool:{action}").tool_text()


TOOL = {
    "name": "call_control",
    "description": (
        "Controls an ACTIVE INCOMING CALL when the user asks to answer, reject, hang up, or check a call: "
        "'answer this call', 'pick up', 'answer it', 'call eka answer karanna', 'කෝල් එක ගන්න', "
        "'reject this call', 'hang up'. ALWAYS call this tool for such requests -- never reply that you "
        "cannot answer calls, and never ask what call the user means. The tool checks the real call state, "
        "performs the action, verifies it, and returns the exact sentence to say; say that sentence and "
        "nothing else. Never claim a call was answered unless the tool says so."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "status | answer | reject | hangup"}
        },
        "required": ["action"],
    },
    "handler": call_control_action,
}
