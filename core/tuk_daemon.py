"""TUK background wake daemon for macOS/desktop.

The daemon owns the microphone while TUK's GUI is not running. It listens
locally for the word "TUK" and launches the GUI as a separate process.
This keeps the wake listener alive when the GUI is closed and avoids the
Qt/macOS tray/window lifetime crash path.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCKSIZE = 1024
_WAKE_WORDS = ("tuk", "tuck", "tuc", "tuke", "tukk", "tock", "toc", "tok")
_WAKE_PROMPT = "TUK. Hey TUK."
_STALL_SECONDS = 6.0          # no audio callback for this long → reopen the mic


def _config_file() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "api_keys.json"


def _always_on_enabled() -> bool:
    try:
        import json
        data = json.loads(_config_file().read_text(encoding="utf-8"))
        return bool(data.get("always_on", False))
    except Exception:
        return False


def _ui_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable), "--wake-ui"]
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), "--wake-ui"]


def _launch_ui() -> subprocess.Popen | None:
    try:
        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(_ui_command(), **kwargs)
    except Exception as exc:
        print(f"[TUK Wake] UI launch failed: {exc}", flush=True)
        return None


def run() -> int:
    if not _always_on_enabled():
        print("[TUK Wake] Always-on is disabled; daemon exiting.", flush=True)
        return 0

    try:
        import numpy as np
        import sounddevice as sd
        from faster_whisper import WhisperModel
    except Exception as exc:
        print(f"[TUK Wake] Missing dependency: {exc}", flush=True)
        return 2

    print("[TUK Wake] Loading local TUK wake model…", flush=True)
    try:
        model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    except Exception as exc:
        print(f"[TUK Wake] Model load failed: {exc}", flush=True)
        return 3

    audio_q: list[np.ndarray] = []
    lock = threading.Lock()
    last_check = 0.0
    cooldown = 0.0
    last_audio = [time.monotonic()]
    ui_proc: subprocess.Popen | None = None
    wake_re = re.compile(r"(?<![a-z])(?:%s)(?:'s)?(?![a-z])" % "|".join(_WAKE_WORDS), re.IGNORECASE)

    def callback(indata, frames, time_info, status):
        last_audio[0] = time.monotonic()
        if status:
            print(f"[TUK Wake] audio: {status}", flush=True)
        try:
            data = indata[:, 0].copy() if getattr(indata, "ndim", 1) > 1 else indata.copy()
            with lock:
                audio_q.append(data)
                # ~6 s of 1024-sample blocks. 24 blocks (1.5 s) meant that any
                # slow transcription silently dropped the audio in between.
                if len(audio_q) > 96:
                    del audio_q[:-96]
        except Exception:
            pass

    def _reset_portaudio() -> None:
        # After a headset connects/disconnects or the Mac sleeps, PortAudio keeps
        # a stale device list and the old stream just goes silent forever.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

    print("[TUK Wake] Listening locally for 'TUK'.", flush=True)
    recent = np.zeros(0, dtype=np.int16)
    try:
        while True:
            if not _always_on_enabled():
                print("[TUK Wake] Always-on disabled; stopping.", flush=True)
                return 0
            # Outer loop: (re)open the microphone. If the stream dies or stalls
            # the daemon used to stay "alive" but deaf until you rebooted.
            try:
                last_audio[0] = time.monotonic()
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=BLOCKSIZE,
                    callback=callback,
                ):
                    while True:
                        if not _always_on_enabled():
                            print("[TUK Wake] Always-on disabled; stopping.", flush=True)
                            return 0

                        if ui_proc is not None and ui_proc.poll() is not None:
                            ui_proc = None

                        if time.monotonic() - last_audio[0] > _STALL_SECONDS:
                            print("[TUK Wake] microphone stalled — reopening.", flush=True)
                            break

                        time.sleep(0.08)
                        now = time.monotonic()
                        if now < cooldown or now - last_check < 0.7:
                            continue

                        with lock:
                            if audio_q:
                                chunk = np.concatenate(audio_q)
                                audio_q.clear()
                            else:
                                chunk = None
                        if chunk is None:
                            continue

                        recent = np.concatenate((recent, chunk))
                        max_samples = int(SAMPLE_RATE * 2.0)
                        if recent.size > max_samples:
                            recent = recent[-max_samples:]
                        if recent.size < int(SAMPLE_RATE * 0.8):
                            continue

                        last_check = now
                        audio = recent.astype(np.float32) / 32768.0
                        try:
                            segments, _ = model.transcribe(
                                audio,
                                language="en",
                                beam_size=1,
                                best_of=1,
                                temperature=0.0,
                                vad_filter=True,
                                condition_on_previous_text=False,
                                initial_prompt=_WAKE_PROMPT,
                            )
                            text = " ".join(s.text.strip() for s in segments).strip()
                        except Exception as exc:
                            print(f"[TUK Wake] inference error: {exc}", flush=True)
                            continue

                        if not wake_re.search(text):
                            # keep the last second so a word split across two
                            # checks is not cut in half
                            keep = int(SAMPLE_RATE * 1.0)
                            if recent.size > keep:
                                recent = recent[-keep:]
                            continue

                        cooldown = time.monotonic() + 2.0
                        recent = np.zeros(0, dtype=np.int16)
                        if ui_proc is None or ui_proc.poll() is not None:
                            print("[TUK Wake] 'TUK' detected — launching TUK UI.", flush=True)
                            ui_proc = _launch_ui()
                        else:
                            print("[TUK Wake] 'TUK' detected — UI already running.", flush=True)
            except KeyboardInterrupt:
                return 0
            except Exception as exc:
                print(f"[TUK Wake] microphone error: {exc} — retrying in 3 s.", flush=True)
            with lock:
                audio_q.clear()
            recent = np.zeros(0, dtype=np.int16)
            time.sleep(3.0)
            _reset_portaudio()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"[TUK Wake] fatal error: {exc}", flush=True)
        return 4
