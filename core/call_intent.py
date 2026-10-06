"""Deterministic call-command classifier for TUK.

Turns a raw speech-to-text transcript into CALL_ANSWER / CALL_REJECT /
CALL_HANGUP -- or None when the sentence is not a call command. It is plain
string work (no model, no I/O, microseconds), so it can run on every
transcript chunk of the live voice path before the conversational LLM has
finished deciding what to say.

Supports English, Singlish (romanised Sinhala), Sinhala script and mixed
code-switching ("call eka answer karanna", "මේ කෝල් එක answer කරන්න").

Two strengths of match:

  strong -- the sentence names a call AND an action ("answer this call",
            "call eka ganna", "කෝල් එක ගන්න"). Safe to act on by itself.
  weak   -- only an action with nothing else in the sentence ("answer it",
            "meka answer karanna", "eka ganna", "pick up"). Only meaningful
            when a call is actually ringing, so the caller must confirm the
            call state before acting on it ("it" = the current call).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

CALL_ANSWER = "CALL_ANSWER"
CALL_REJECT = "CALL_REJECT"
CALL_HANGUP = "CALL_HANGUP"

_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
_PUNCT = re.compile(r"[^\w\u0D80-\u0DFF]+", re.UNICODE)


def normalize(text: str) -> str:
    """Lower-case, strip zero-width joiners/punctuation, collapse spaces."""
    t = unicodedata.normalize("NFKC", text or "").translate(_ZW).lower()
    return _PUNCT.sub(" ", t).strip()


def _set(*words: str) -> frozenset[str]:
    return frozenset(normalize(w) for w in words if normalize(w))


# --------------------------------------------------------------- vocabulary
_NOUNS = _set(
    "call", "calls", "cal", "col", "kol", "koll", "kall", "coll", "phone", "phn", "ring",
    "ringing", "caller",
    "කෝල්", "කෝල", "කෝලය", "කොල්", "කෝල්එක", "ඇමතුම", "ඇමතුමට", "ඇමතුමක්", "ඇමතුමක",
    "ඇමතුම්", "ඇමතුමෙ", "ඇමතුමේ", "ෆෝන්", "ෆෝන්එක",
)

_ANSWER = _set(
    # English + common ASR slips
    "answer", "anser", "ancer", "answr", "answar", "ansar", "answered", "answering", "ans",
    "accept", "accepted", "accepting", "receive", "received", "attend", "pick", "picked",
    "picking", "take", "taking",
    # Singlish
    "ganna", "gannawa", "gannako", "gannna", "gnna", "gannaa", "gane", "aran", "aranawa",
    "arang", "aragena", "pilithuru", "pilituru", "uththara", "uttara",
    # Sinhala script
    "ගන්න", "ගන්නවා", "ගන්නකෝ", "ගනින්", "අරන්", "අරං", "අරගෙන", "පිළිතුරු", "පිලිතුරු",
    "උත්තර", "උත්තරය",
)

_REJECT = _set(
    "reject", "rejected", "rejecting", "decline", "declined", "dismiss", "ignore", "refuse",
    "ප්‍රතික්ෂේප", "ප්රතික්ෂේප",
)

_HANGUP = _set(
    "hangup", "hang", "end", "ended", "cut", "disconnect", "terminate", "drop",
    "kapanna", "kapan", "kapala", "navaththanna", "nawattanna", "awasan",
    "කපන්න", "කපලා", "නවත්තන්න", "නවත්වන්න", "අවසන්",
)

# Words that never make a sentence "about something else".
_FILLER = _set(
    "it", "this", "that", "the", "a", "an", "my", "me", "for", "please", "pls", "plz", "bro",
    "tuk", "hey", "hi", "ok", "okay", "now", "can", "could", "would", "will", "you", "kindly",
    "just", "up", "incoming", "current", "one", "to", "and", "then", "yes", "on", "off", "down",
    "eka", "ek", "meka", "me", "mage", "ona", "oya", "karanna", "karan", "kranna", "krnna", "krn",
    "karapan", "karanawa", "denna", "dennko", "denawa", "ban", "bn", "machan", "machang",
    "ado", "aiye", "aiyo", "mama", "mata", "eka", "ekak", "ekata", "dan", "dang", "ikmanata",
    "ikmanin", "poddak", "hari", "ne", "nang", "koh", "aney",
    "මේ", "මේක", "ඒක", "එක", "එන", "කරන්න", "කරපන්", "කරන්නකෝ", "දෙන්න", "මට", "පොඩ්ඩක්",
    "බන්", "අයියේ", "මචං", "දැන්", "ඉක්මනට", "ඉක්මනින්", "මගේ", "කරලා", "වගේ", "ඔයා",
)

_NEGATION = _set(
    "dont", "don", "not", "never", "no", "cant", "cannot", "wont", "stop", "epa", "epaa",
    "එපා", "නෑ", "නැහැ", "එපාමයි", "ඕන", "නෙමෙයි",
)

# "what/how/why ..." sentences are questions *about* answering, not commands.
_QUESTION_START = _set(
    "what", "why", "how", "when", "where", "who", "which", "whats", "hows", "should", "if",
    "explain", "tell", "ඇයි", "කොහොමද", "මොකද", "මොකක්ද",
)

# "call ekak ganna" = "make a call" (indefinite), not "take the call".
_INDEFINITE = _set("ekak", "ekakata", "එකක්", "එකක")

_FUZZY_TARGETS = ("answer", "accept", "receive")


def _is_latin(tok: str) -> bool:
    return tok.isascii() and tok.isalpha()


def _canon(tok: str, vocab_extra: frozenset[str] = frozenset()) -> str:
    """Repair obvious ASR slips on long Latin tokens ('anwser' -> 'answer').
    Only the three English answer-verbs are fuzzed; Singlish words are listed
    explicitly so 'gonna' can never be mistaken for 'ganna'."""
    if len(tok) < 5 or not _is_latin(tok) or tok in _ANSWER or tok in _NOUNS or tok in _FILLER:
        return tok
    hit = difflib.get_close_matches(tok, _FUZZY_TARGETS, n=1, cutoff=0.8)
    return hit[0] if hit else tok


@dataclass(frozen=True)
class Detection:
    intent: str          # CALL_ANSWER | CALL_REJECT | CALL_HANGUP
    strong: bool         # names a call explicitly (safe without call context)
    text: str            # normalised transcript


def _tokens(text: str) -> list[str]:
    toks = normalize(text).split()
    out: list[str] = []
    for t in toks:
        out.append(_canon(t))
    # "call එක"/"call eka" glue forms: split "කෝල්එක" is handled via vocab.
    return out


def classify(text: str) -> Optional[Detection]:
    """Return a Detection for call commands, else None."""
    toks = _tokens(text)
    if not toks or len(toks) > 14:          # a command, not a paragraph
        return None
    tset = set(toks)

    if tset & _NEGATION:
        return None
    if toks[0] in _QUESTION_START:
        return None
    if tset & _INDEFINITE and tset & _NOUNS:
        return None

    has_noun = bool(tset & _NOUNS)
    has_ans = bool(tset & _ANSWER)
    has_rej = bool(tset & _REJECT)
    has_hang = bool(tset & _HANGUP)

    if has_ans and not has_rej and not has_hang:
        intent = CALL_ANSWER
    elif has_rej and not has_hang:
        intent = CALL_REJECT
    elif has_hang and not has_rej:
        intent = CALL_HANGUP
    elif has_rej and has_hang:
        intent = CALL_REJECT
    else:
        return None

    if has_noun:
        return Detection(intent, True, " ".join(toks))

    # No explicit call noun: only a bare command ("answer it", "eka ganna",
    # "pick up") qualifies, and only as a weak, context-dependent match.
    verbs = _ANSWER | _REJECT | _HANGUP
    residual = [t for t in toks if t not in verbs and t not in _FILLER]
    if residual:
        return None
    return Detection(intent, False, " ".join(toks))


_RETRY = frozenset(normalize(x) for x in (
    "try again", "try it again", "again", "once more", "one more time", "check again",
    "please try again", "try again bro", "do it again", "retry",
    "ayeth", "ayeth try karanna", "ayeth karanna", "thawath parak", "thawa parak",
    "ayeth ekak", "ayeth balanna",
    "ආයෙත්", "ආයෙත් try කරන්න", "නැවත උත්සාහ කරන්න", "නැවත උත්සාහ කරන්නකෝ", "තවත් වතාවක්",
    "ආයෙමත් බලන්න", "නැවත බලන්න",
))


def is_retry_phrase(text: str) -> bool:
    """True for a bare 'try again' (only meaningful right after a call command)."""
    return normalize(text) in _RETRY
