"""Always-on mode: silent login start + wake word on + tray icon.

Kept tiny on purpose — it only flips the switches that already exist:
  * login start (core.startup) launched with --wake-daemon (GUI starts only after TUK is heard)
  * wake word flag (memory.config_manager) so local 'TUK' wake gate is armed at boot
  * a persisted always_on flag the UI reads to decide hide-to-tray behaviour
"""
from __future__ import annotations

import json

from core.startup import is_login_start, set_login_start
from memory import config_manager as cm


def is_enabled() -> bool:
    return bool(cm.load_api_keys().get("always_on", False)) and is_login_start()


def _write_flag(value: bool) -> None:
    cm.ensure_config_dir()
    data: dict = {}
    if cm.CONFIG_FILE.exists():
        try:
            data = json.loads(cm.CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["always_on"] = bool(value)
    cm.CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def enable() -> tuple[bool, str]:
    if not set_login_start(True, background=True):
        return False, "Could not register TUK to start at login."
    _write_flag(True)
    cm.save_wake_word_enabled(True)
    return True, "Always-on enabled."


def disable() -> tuple[bool, str]:
    ok = set_login_start(False)
    _write_flag(False)
    return ok, "Always-on disabled."
