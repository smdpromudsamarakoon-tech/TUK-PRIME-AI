"""CALL_* intent classification: English, Singlish, Sinhala, ASR slips, false positives."""
import unittest

from core.call_intent import CALL_ANSWER, CALL_HANGUP, CALL_REJECT, classify

STRONG_ANSWER = [
    # English
    "answer this call", "answer the call", "pick up the call", "accept the call",
    "take the call", "answer incoming call", "Can you answer this call for me?",
    "bro answer the call", "Answer this call.", "anwser the call",
    # Singlish
    "call eka answer karanna", "me call eka answer karanna", "call eka aranawa",
    "call eka ganna", "call eka pick karanna", "call eka aran", "call eka ganna bro",
    # Sinhala / mixed
    "මේ කෝල් එක answer කරන්න", "කෝල් එක answer කරන්න", "මේ කෝල් එක ගන්න", "කෝල් එක ගන්න",
    "එන කෝල් එක answer කරන්න", "මේ ඇමතුම ගන්න", "ඇමතුමට පිළිතුරු දෙන්න", "call එක ගන්න",
    "එන call එක ගන්න",
]
WEAK_ANSWER = [
    "answer it", "pick up", "please pick up", "meka answer karanna", "eka ganna",
    "answer karanna", "මේක answer කරන්න", "take it",
]
NOT_COMMANDS = [
    "answer this question", "call mom", "make a call", "what is the answer",
    "pick up the milk", "don't answer the call", "how do I answer a call", "take it easy",
    "call eka karanna", "mata call ekak ganna oni", "I'm gonna call you", "what time is it",
    "call me nethu", "open chrome", "කෝල් එක ගන්න එපා", "", "   ",
]


class CallIntentTests(unittest.TestCase):
    def test_strong_answer_phrases(self):
        for t in STRONG_ANSWER:
            with self.subTest(t):
                d = classify(t)
                self.assertIsNotNone(d, t)
                self.assertEqual(d.intent, CALL_ANSWER)
                self.assertTrue(d.strong)

    def test_weak_answer_phrases_need_call_context(self):
        for t in WEAK_ANSWER:
            with self.subTest(t):
                d = classify(t)
                self.assertIsNotNone(d, t)
                self.assertEqual(d.intent, CALL_ANSWER)
                self.assertFalse(d.strong)

    def test_non_commands_are_not_intercepted(self):
        for t in NOT_COMMANDS:
            with self.subTest(t):
                self.assertIsNone(classify(t), t)

    def test_reject_and_hangup(self):
        self.assertEqual(classify("reject this call").intent, CALL_REJECT)
        self.assertEqual(classify("decline the call").intent, CALL_REJECT)
        self.assertEqual(classify("hang up").intent, CALL_HANGUP)
        self.assertEqual(classify("end the call").intent, CALL_HANGUP)
        self.assertEqual(classify("call eka kapanna").intent, CALL_HANGUP)


if __name__ == "__main__":
    unittest.main()
