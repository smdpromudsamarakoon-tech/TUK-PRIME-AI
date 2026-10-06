"""CallController behaviour: state detection, execution, VERIFICATION, honesty."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import actions.call_control as cc
from actions.call_control import (ActionResult, CallController, CallInfo, CallState,
                                  ChannelRouter, execute_call_command)


class FakePhone(CallController):
    """Scriptable provider. `states` is consumed on each get_call_state()."""
    def __init__(self, states, answer_ok=True, channel="phone", available=True):
        self.states, self.answer_ok, self.channel, self.available = list(states), answer_ok, channel, available
        self.calls = []

    def get_call_state(self):
        st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return CallInfo(st, self.channel, "+94771234567", self.available)

    def answer_call(self):
        self.calls.append("answer"); return ActionResult(self.answer_ok, True, "" if self.answer_ok else "boom")
    def reject_call(self):
        self.calls.append("reject"); return ActionResult(True)
    def hangup_call(self):
        self.calls.append("hangup"); return ActionResult(True)


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(cc, "VERIFY_TIMEOUT_S", 0.0); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(cc, "VERIFY_POLL_S", 0.0); p.start(); self.addCleanup(p.stop)

    def test_no_call_exact_message_and_no_action(self):
        ctl = FakePhone([CallState.NO_CALL])
        out = execute_call_command("CALL_ANSWER", ctl, "answer this call")
        self.assertEqual(out.message, "I don't see an incoming call right now.")
        self.assertEqual(ctl.calls, [])
        self.assertFalse(out.success)

    def test_no_bridge_at_all_is_honest(self):
        out = execute_call_command("CALL_ANSWER", FakePhone([CallState.NO_CALL], available=False))
        self.assertTrue(out.message.startswith("I don't see an incoming call right now."))
        self.assertIn("phone-control", out.message)
        self.assertFalse(out.success)

    def test_incoming_call_is_answered_and_verified(self):
        ctl = FakePhone([CallState.INCOMING, CallState.CONNECTED])
        out = execute_call_command("CALL_ANSWER", ctl, "answer this call")
        self.assertEqual(ctl.calls, ["answer"])              # answer_call() really invoked
        self.assertEqual(out.code, "SUCCESS")
        self.assertEqual(out.verified_state, CallState.CONNECTED)
        self.assertEqual(out.message, "Call answered.")

    def test_provider_ok_but_never_connects_is_not_success(self):
        ctl = FakePhone([CallState.RINGING])                  # stays RINGING forever
        out = execute_call_command("CALL_ANSWER", ctl)
        self.assertEqual(ctl.calls, ["answer"])
        self.assertEqual(out.code, "NOT_VERIFIED")
        self.assertNotIn("Call answered", out.message)

    def test_provider_failure_is_reported(self):
        out = execute_call_command("CALL_ANSWER", FakePhone([CallState.INCOMING], answer_ok=False))
        self.assertEqual(out.code, "FAILED")
        self.assertFalse(out.success)

    def test_whatsapp_personal_call_is_unsupported_never_faked(self):
        ctl = ChannelRouter(primary=FakePhone([CallState.INCOMING], channel="whatsapp"))
        out = execute_call_command("CALL_ANSWER", ctl, "answer this call")
        self.assertEqual(out.code, "UNSUPPORTED_CHANNEL")
        self.assertEqual(out.message, cc.MSG_UNSUPPORTED_WA)
        self.assertFalse(out.success)

    def test_crashing_provider_never_fakes_success(self):
        class Boom(FakePhone):
            def get_call_state(self): raise RuntimeError("x")
        out = execute_call_command("CALL_ANSWER", Boom([CallState.INCOMING]))
        self.assertEqual(out.code, "FAILED")

    def test_reject_and_hangup_verify_end_state(self):
        r = execute_call_command("CALL_REJECT", FakePhone([CallState.INCOMING, CallState.ENDED]))
        self.assertEqual(r.message, "Call declined.")
        h = execute_call_command("CALL_HANGUP", FakePhone([CallState.CONNECTED, CallState.NO_CALL]))
        self.assertEqual(h.message, "Call ended.")


class BridgeFileTests(unittest.TestCase):
    def _state(self, d, data):
        f = Path(d) / "state.json"; f.write_text(json.dumps(data), encoding="utf-8"); return f

    def test_tool_answer_without_call_never_claims_success(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cc, "_STATE", self._state(d, {"incoming": False})):
            cc._controller = None
            res = cc.call_control_action({"action": "answer"})
        self.assertIn("I don't see an incoming call right now.", res)
        self.assertNotIn("Call answered", res)

    def test_tool_status_reports_incoming(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(
                cc, "_STATE", self._state(d, {"state": "ringing", "channel": "phone", "caller": "+94"})):
            cc._controller = None
            self.assertIn("incoming phone call", cc.call_control_action({"action": "status"}))

    def test_tool_end_to_end_with_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._state(d, {"state": "incoming", "channel": "phone"})
            def fake_bridge(action):
                if action == "answer":
                    f.write_text(json.dumps({"state": "connected", "channel": "phone"}))
                return {"ok": True}
            with mock.patch.object(cc, "_STATE", f), mock.patch.object(cc, "_bridge_request", fake_bridge), \
                 mock.patch.dict("os.environ", {"TUK_CALL_BRIDGE_URL": "http://x"}), \
                 mock.patch.object(cc, "VERIFY_TIMEOUT_S", 0.0):
                cc._controller = None
                res = cc.call_control_action({"action": "answer"})
        self.assertIn("code=SUCCESS", res)
        self.assertIn("Call answered.", res)

    def test_file_only_bridge_cannot_act_and_says_so(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cc, "_STATE", self._state(d, {"state": "incoming"})), \
             mock.patch.dict("os.environ", {"TUK_CALL_BRIDGE_URL": ""}):
            cc._controller = None
            out = execute_call_command("CALL_ANSWER")
        self.assertEqual(out.code, "FAILED")
        self.assertEqual(out.message, cc.MSG_NO_CONNECTION)

    def test_tool_is_discoverable_by_action_loader(self):
        from core.action_loader import _validate
        rec = _validate(cc, "call_control.py")
        self.assertTrue(rec.valid, rec.error)       # the original bug: no 'handler' => rejected


if __name__ == "__main__":
    unittest.main()
