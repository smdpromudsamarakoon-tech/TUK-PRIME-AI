"""Local TUK wake gate using faster-whisper.

While TUK is asleep, microphone audio stays on-device and is only used to
recognise the standalone wake word "TUK". No sleep-gate audio is sent to Gemini.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Callable

SAMPLE_RATE = 16000
# Whisper-tiny almost never writes the made-up word "TUK" as "tuk": it hears
# tuck / tock / tok / took-ish sounds. Accepting only the exact spelling was the
# main reason the wake word "sometimes" did nothing. "took" is deliberately NOT
# accepted (far too common in normal speech).
WAKE_RE = re.compile(r"(?<![a-z])(?:tuk|tuck|tuc|tuke|tukk|tock|toc|tok|tuk-tuk)(?:'s)?(?![a-z])",
                     re.IGNORECASE)
WAKE_PROMPT = "TUK. Hey TUK."      # biases the recogniser toward the spelling "TUK"


def is_installed() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


def is_ready() -> bool:
    return is_installed()


class TukWakeDetector:
    def __init__(self, on_detect: Callable[[], None], logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._logger = logger
        self._notify = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=96)
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready = False
        self._model = None
        self._recent = bytearray()
        self._last_transcribe = 0.0
        self._busy = False
        self._cooldown_until = 0.0

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self) -> bool:
        if self._running:
            return True
        if not is_installed():
            self._notify("TUK wake needs faster-whisper. Run: pip install -r requirements.txt")
            return False
        try:
            from faster_whisper import WhisperModel
            self._logger("TUK wake: loading local tiny English model…")
            self._model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        except Exception as e:
            self._logger(f"TUK wake: model load failed — {e}")
            self._notify(f"TUK wake unavailable — {e}")
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TUKWakeThread")
        self._thread.start()
        self._logger("TUK wake: listening locally for 'TUK'.")
        return True

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        self._ready = False
        self._model = None
        self._recent.clear()

    def feed(self, frame_int16) -> None:
        if not self._running:
            return
        try:
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                # Inference runs on the same thread that drains this queue. When
                # the queue filled up during a transcription, put_nowait() threw
                # away the NEWEST audio and kept the stale audio, so a wake word
                # spoken while the model was busy was lost. Drop the oldest.
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                self._queue.put_nowait(data)
        except Exception:
            pass

    def _loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                continue
            try:
                self._recent.extend(item.tobytes())
                max_bytes = int(SAMPLE_RATE * 2.0 * 2)
                if len(self._recent) > max_bytes:
                    del self._recent[:-max_bytes]
                now = time.monotonic()
                if now < self._cooldown_until or self._busy or len(self._recent) < SAMPLE_RATE * 2:
                    continue
                if now - self._last_transcribe < 0.8:
                    continue
                self._last_transcribe = now
                self._busy = True
                try:
                    import numpy as np
                    audio = np.frombuffer(bytes(self._recent), dtype=np.int16).astype(np.float32) / 32768.0
                    segments, _info = self._model.transcribe(
                        audio,
                        language="en",
                        beam_size=1,
                        best_of=1,
                        temperature=0.0,
                        vad_filter=True,
                        condition_on_previous_text=False,
                        initial_prompt=WAKE_PROMPT,
                        no_speech_threshold=0.5,
                    )
                    text = " ".join(s.text.strip() for s in segments).strip()
                    if WAKE_RE.search(text):
                        self._cooldown_until = time.monotonic() + 1.5
                        self._recent.clear()
                        try:
                            self._on_detect()
                        except Exception as e:
                            self._logger(f"TUK wake callback failed — {e}")
                    else:
                        keep = int(SAMPLE_RATE * 1.0 * 2)
                        if len(self._recent) > keep:
                            del self._recent[:-keep]
                finally:
                    self._busy = False
            except Exception as e:
                self._logger(f"TUK wake inference error — {e}")
