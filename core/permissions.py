"""One-time OS permission prompts for background use (macOS mainly)."""
from __future__ import annotations

import json
import platform

from memory import config_manager as cm


def _flag() -> bool:
    return bool(cm.load_api_keys().get("permissions_prompted", False))


def _set_flag() -> None:
    try:
        cm.ensure_config_dir()
        data = json.loads(cm.CONFIG_FILE.read_text(encoding="utf-8")) if cm.CONFIG_FILE.exists() else {}
        data["permissions_prompted"] = True
        cm.CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
    except Exception:
        pass


def request_first_run_permissions() -> None:
    """Ask for Accessibility once. The mic prompt appears by itself the first
    time audio is opened, so it is not duplicated here. Never raises."""
    if platform.system() != "Darwin" or _flag():
        return
    try:
        import ctypes, ctypes.util
        ax = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
        cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                          ctypes.c_void_p, ctypes.c_void_p]
        key = cf.CFStringCreateWithCString(None, b"AXTrustedCheckOptionPrompt", 0x08000100)
        true_ = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        keys = (ctypes.c_void_p * 1)(key)
        vals = (ctypes.c_void_p * 1)(true_.value)
        opts = cf.CFDictionaryCreate(None, keys, vals, 1, None, None)
        ax.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
        ax.AXIsProcessTrustedWithOptions(opts)
    except Exception as e:
        print(f"[Permissions] {e}")
    _set_flag()
