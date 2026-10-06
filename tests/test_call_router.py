"""Live-pipeline tests. The _call_* methods are lifted out of the REAL main.py
(AST) and run against a stub assistant, so these tests fail if main.py stops
routing call commands deterministically -- they are not tests of a side module
that nothing calls."""
import ast
import asyncio
import queue
import unittest
from pathlib import Path
from unittest import mock

import actions.call_control as cc
from actions.call_control import CallState
from core.call_router import CallVoiceGate, call_lang
from tests.test_call_control import FakePhone

MAIN = Path(__file__).resolve().parent.parent / "main.py"
_WANTED = ("_call_on_transcript", "_call_run", "_call_suppress_chat_reply",
           "_call_finish_turn", "_call_speak", "_call_typed",
           "_call_text_mode", "_on_text_command")


class _SyncThread:
    """threading.Thread stand-in that runs the target inline (deterministic tests)."""
    def __init__(self, target=None, args=(), **kw): self._t, self._a = target, args
    def start(self): self._t(*self._a)


class _Threading:
    Thread = _SyncThread


def _lift_methods():
    src = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = {"asyncio": asyncio, "threading": _Threading, "call_lang": call_lang, "print": lambda *a, **k: None}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name in _WANTED:
                    found[f.name] = f
    assert set(found) == set(_WANTED), f"main.py is missing call routing methods: {set(_WANTED) - set(found)}"
    mod = ast.Module(body=list(found.values()), type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), str(MAIN), "exec"), ns)
    return {k: ns[k] for k in _WANTED}


class _Ui:
    def __init__(self): self.logs = []
    def write_log(self, m): self.logs.append(m)
    def set_state(self, s): pass


class _Session:
    def __init__(self): self.sent = []
    async def send_client_content(self, turns, turn_complete): self.sent.append(turns["parts"][0]["text"])


class _Visemes:
    def reset(self): pass


class Assistant:
    """Minimal stand-in for the TUK assistant object in main.py."""
    def __init__(self, controller):
        self._call_gate = CallVoiceGate(controller_factory=lambda: controller)
        self._call_task = self._call_det = self._call_debounce = None
        self._interrupted = False
        self.audio_in_queue = queue.Queue()
        self.ui, self.session, self._visemes = _Ui(), _Session(), _Visemes()
        self._session_log, self._asst_name, self._play_cursor = [], "TUK", 0.0
        self._loop, self._wake_enabled, self._awake = object(), False, True
    def set_speaking(self, v): pass


for _n, _f in _lift_methods().items():
    setattr(Assistant, _n, _f)


def run_turn(ctl, *chunks):
    """Simulate one voice turn: transcript chunks arrive, then turn_complete."""
    async def go():
        a = Assistant(ctl)
        a.audio_in_queue.put(b"model-audio-for-wrong-chat-reply")
        buf = []
        for c in chunks:
            buf.append(c)
            a._call_on_transcript(" ".join(buf))
            await asyncio.sleep(0)
        handled = await a._call_finish_turn(" ".join(buf))
        return a, handled
    return asyncio.run(go())


class LivePipelineTests(unittest.TestCase):
    def setUp(self):
        for n, v in (("VERIFY_TIMEOUT_S", 0.0), ("VERIFY_POLL_S", 0.0)):
            p = mock.patch.object(cc, n, v); p.start(); self.addCleanup(p.stop)

    def spoken(self, a): return " ".join(a.session.sent)

    # -- Test 1: no call
    def test_no_call(self):
        a, handled = run_turn(FakePhone([CallState.NO_CALL]), "answer this call")
        self.assertTrue(handled)
        self.assertIn("I don't see an incoming call right now.", self.spoken(a))
        self.assertTrue(a.audio_in_queue.empty())             # wrong chat audio was dropped

    # -- Test 2: incoming call -> CallController.answer_call() really invoked
    def test_incoming_phone_call_answered_and_verified(self):
        ctl = FakePhone([CallState.INCOMING, CallState.CONNECTED])
        a, handled = run_turn(ctl, "answer this call")
        self.assertTrue(handled)
        self.assertEqual(ctl.calls, ["answer"])
        self.assertIn("Call answered.", self.spoken(a))
        self.assertFalse(a._interrupted)                       # speaking the result is allowed again

    # -- Test 3 / 4: Singlish + Sinhala
    def test_singlish_and_sinhala(self):
        for phrase in ("call eka answer karanna", "කෝල් එක ගන්න", "මේ කෝල් එක ගන්න"):
            with self.subTest(phrase):
                ctl = FakePhone([CallState.INCOMING, CallState.CONNECTED])
                a, handled = run_turn(ctl, phrase)
                self.assertTrue(handled)
                self.assertEqual(ctl.calls, ["answer"])

    # -- Test 5: context -- "answer it" resolves to the ringing call
    def test_answer_it_with_incoming_call(self):
        for phrase in ("answer it", "meka answer karanna", "eka ganna"):
            with self.subTest(phrase):
                # weak commands read state twice (gate check, then executor)
                ctl = FakePhone([CallState.INCOMING, CallState.INCOMING, CallState.CONNECTED])
                a, handled = run_turn(ctl, phrase)
                self.assertTrue(handled)
                self.assertEqual(ctl.calls, ["answer"])
                self.assertIn("Call answered.", self.spoken(a))

    def test_answer_it_without_a_call_is_left_to_normal_chat(self):
        ctl = FakePhone([CallState.NO_CALL])
        a, handled = run_turn(ctl, "answer it")
        self.assertFalse(handled)
        self.assertEqual(a.session.sent, [])
        self.assertEqual(ctl.calls, [])

    def test_partial_transcript_chunks(self):
        ctl = FakePhone([CallState.INCOMING, CallState.CONNECTED])
        a, handled = run_turn(ctl, "answer", "this", "call")
        self.assertTrue(handled)
        self.assertEqual(ctl.calls, ["answer"])               # acted exactly once

    # -- Test 6: WhatsApp personal call
    def test_whatsapp_personal_call_unsupported(self):
        ctl = cc.ChannelRouter(primary=FakePhone([CallState.INCOMING], channel="whatsapp"))
        a, handled = run_turn(ctl, "answer this call")
        self.assertTrue(handled)
        self.assertIn("WhatsApp call isn't available", self.spoken(a))
        self.assertNotIn("Call answered", self.spoken(a))

    def test_unverified_answer_never_reported_as_success(self):
        a, _ = run_turn(FakePhone([CallState.RINGING]), "answer this call")
        self.assertNotIn("Call answered", self.spoken(a))

    def test_normal_chat_untouched(self):
        a, handled = run_turn(FakePhone([CallState.INCOMING]), "what's the weather today")
        self.assertFalse(handled)
        self.assertEqual(a.session.sent, [])
        self.assertFalse(a.audio_in_queue.empty())            # model audio NOT suppressed


class ReplyShapeTests(unittest.TestCase):
    def setUp(self):
        for n, v in (("VERIFY_TIMEOUT_S", 0.0), ("VERIFY_POLL_S", 0.0)):
            p = mock.patch.object(cc, n, v); p.start(); self.addCleanup(p.stop)

    def test_single_reply_no_duplicate_log_line(self):
        a, _ = run_turn(FakePhone([CallState.NO_CALL]), "answer this call")
        # the spoken result is logged by the normal reply path; the router adds no 2nd copy
        self.assertFalse(any(m.startswith("TUK:") for m in a.ui.logs))
        self.assertEqual(len(a.session.sent), 1)

    def test_reply_language_follows_the_user(self):
        a, _ = run_turn(FakePhone([CallState.NO_CALL]), "answer this call")
        self.assertIn("in English only", a.session.sent[0])
        a, _ = run_turn(FakePhone([CallState.NO_CALL]), "කෝල් එක ගන්න")
        self.assertIn("in Sinhala only", a.session.sent[0])
        a, _ = run_turn(FakePhone([CallState.NO_CALL]), "call eka ganna")
        self.assertIn("in English only", a.session.sent[0])

    def test_failed_speak_falls_back_to_log(self):
        async def go():
            a = Assistant(FakePhone([CallState.NO_CALL]))
            async def boom(**k): raise RuntimeError("closed")
            a.session.send_client_content = boom
            a._call_on_transcript("answer this call"); await asyncio.sleep(0)
            await a._call_finish_turn("answer this call")
            return a
        a = asyncio.run(go())
        self.assertIn("TUK: I don't see an incoming call right now.", a.ui.logs)


class RetryTests(unittest.TestCase):
    def test_try_again_repeats_last_call_command_with_fresh_state(self):
        from core.call_intent import classify
        states = [CallState.NO_CALL, CallState.INCOMING, CallState.CONNECTED]
        ctl = FakePhone(states)
        g = CallVoiceGate(controller_factory=lambda: ctl)
        self.assertIsNone(g.classify("try again"))                  # nothing to retry yet
        with mock.patch.object(cc, "VERIFY_TIMEOUT_S", 0.0):
            self.assertEqual(g.resolve(classify("answer this call")).code, "NO_CALL")
            for phrase in ("try again", "ayeth try karanna", "ආයෙත් try කරන්න", "again"):
                det = g.classify(phrase)
                self.assertIsNotNone(det, phrase)
                self.assertEqual(det.intent, "CALL_ANSWER")
                self.assertTrue(det.strong)
            ctl.states = [CallState.INCOMING, CallState.CONNECTED]
            out = g.resolve(g.classify("try again"))
        self.assertEqual(out.code, "SUCCESS")                        # the call rang in between
        self.assertEqual(ctl.calls, ["answer"])

    def test_retry_expires(self):
        from core.call_intent import classify
        g = CallVoiceGate(controller_factory=lambda: FakePhone([CallState.NO_CALL]))
        g.resolve(classify("answer this call"))
        g._last_intent_at -= 500
        self.assertIsNone(g.classify("try again"))

    def test_unrelated_sentences_are_not_retries(self):
        from core.call_intent import classify
        g = CallVoiceGate(controller_factory=lambda: FakePhone([CallState.NO_CALL]))
        g.resolve(classify("answer this call"))
        for t in ("what's the weather", "try again later tonight please maybe", "open chrome"):
            self.assertIsNone(g.classify(t), t)


class TextOnlyProviderTests(unittest.TestCase):
    """Ollama / OpenAI-compatible mode: NO realtime session. This is the mode in
    which 'answer this call' used to fall straight through to the chat LLM."""
    def setUp(self):
        for n, v in (("VERIFY_TIMEOUT_S", 0.0), ("VERIFY_POLL_S", 0.0)):
            p = mock.patch.object(cc, n, v); p.start(); self.addCleanup(p.stop)

    def _typed(self, ctl, text):
        a = Assistant(ctl); a.session = None
        a.chat_calls = []
        a._on_text_command = lambda t, _skip_call=False: a.chat_calls.append((t, _skip_call))
        Assistant._on_text_command(a, text)           # the REAL main.py method
        return a

    def test_no_call_answers_honestly_and_never_reaches_llm(self):
        a = self._typed(FakePhone([CallState.NO_CALL]), "answer this call")
        self.assertIn("TUK: I don't see an incoming call right now.", a.ui.logs)
        self.assertEqual(a.chat_calls, [])

    def test_incoming_call_answered_in_text_mode(self):
        ctl = FakePhone([CallState.INCOMING, CallState.CONNECTED])
        a = self._typed(ctl, "call eka answer karanna")
        self.assertEqual(ctl.calls, ["answer"])
        self.assertIn("TUK: Call answered.", a.ui.logs)
        self.assertEqual(a.chat_calls, [])

    def test_whatsapp_unsupported_in_text_mode(self):
        ctl = cc.ChannelRouter(primary=FakePhone([CallState.INCOMING], channel="whatsapp"))
        a = self._typed(ctl, "කෝල් එක ගන්න")
        self.assertTrue(any("WhatsApp call isn't available" in m for m in a.ui.logs))

    def test_weak_command_without_call_falls_back_to_chat(self):
        a = self._typed(FakePhone([CallState.NO_CALL]), "answer it")
        self.assertEqual(a.chat_calls, [("answer it", True)])   # re-enters chat, router skipped
        self.assertEqual(a.ui.logs, [])

    def test_ordinary_text_is_untouched(self):
        a = Assistant(FakePhone([CallState.INCOMING])); a.session = None
        calls = []
        # not a call command -> the method proceeds past the gate (to the config / LLM
        # code, which we stop at by making the config read fail fast)
        with mock.patch("builtins.open", side_effect=RuntimeError("stop")):
            try: Assistant._on_text_command(a, "what's the weather")
            except Exception: pass
        self.assertEqual(a.ui.logs, [])


class WiringTests(unittest.TestCase):
    """The router must be reachable from the live entry points of main.py."""
    @classmethod
    def setUpClass(cls): cls.src = MAIN.read_text(encoding="utf-8")

    def test_voice_transcript_feeds_router(self):
        self.assertIn("self._call_on_transcript(", self.src)
        i = self.src.index("sc.input_transcription.text:")
        self.assertIn("_call_on_transcript", self.src[i:i + 600])

    def test_turn_complete_resolves_call_before_chat_logging(self):
        i = self.src.index("await self._call_finish_turn(")
        self.assertLess(i, self.src.index("self._last_out_logged = \"\"   # new exchange"))

    def test_typed_commands_use_same_gate_before_llm(self):
        j = self.src.index("def _on_text_command")
        body = self.src[j:j + 3000]
        self.assertIn("self._call_gate.classify(text)", body)
        self.assertLess(body.index("_call_gate.classify"), body.index("llm_provider"))
        self.assertIn("_call_text_mode", body)               # no-session (text provider) route

    def test_tool_reuses_router_outcome(self):
        self.assertIn('name == "call_control" and self._call_gate.recent_outcome()', self.src)


class GateTests(unittest.TestCase):
    def test_weak_command_ignored_without_call(self):
        from core.call_intent import classify
        g = CallVoiceGate(controller_factory=lambda: FakePhone([CallState.NO_CALL]))
        self.assertIsNone(g.resolve(classify("answer it")))

    def test_strong_command_always_gets_honest_answer(self):
        from core.call_intent import classify
        g = CallVoiceGate(controller_factory=lambda: FakePhone([CallState.NO_CALL]))
        self.assertEqual(g.resolve(classify("answer this call")).code, "NO_CALL")


if __name__ == "__main__":
    unittest.main()
