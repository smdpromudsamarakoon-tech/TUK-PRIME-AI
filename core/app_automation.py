"""Modern, state-aware desktop app automation helpers.

macOS uses the Accessibility tree first (fast and deterministic), with the
existing vision finder as a fallback.  The important rule is OBSERVE -> ACT ->
VERIFY; never blindly press Enter because a fixed sleep elapsed.
"""
from __future__ import annotations

import platform
import subprocess
import time
from typing import Optional, Tuple

try:
    import pyautogui
except Exception:  # pragma: no cover
    pyautogui = None

try:
    import pyperclip
except ImportError:  # pragma: no cover
    pyperclip = None


def _mac() -> bool:
    return platform.system() == "Darwin"


def _run_osascript(script: str, timeout: float = 4.0) -> str:
    p = subprocess.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "AppleScript failed").strip())
    return (p.stdout or "").strip()


def mac_ax_center(process: str, needle: str, *, exact: bool = False) -> Optional[Tuple[int, int]]:
    """Return the screen center of an accessible UI element matching needle."""
    if not _mac():
        return None
    # AppleScript string escaping.
    n = needle.replace("\\", "\\\\").replace('"', '\\"')
    mode = "exact" if exact else "contains"
    script = f'''\
set needleText to "{n}"
set matchMode to "{mode}"
tell application "System Events"
    tell process "{process}"
        set frontmost to true
        set foundX to 0
        set foundY to 0
        set foundIt to false
        my walkUI(window 1)
        if foundIt then return (foundX as text) & "," & (foundY as text)
    end tell
end tell
return ""

on walkUI(container)
    tell application "System Events"
        repeat with u in UI elements of container
            if foundIt then exit repeat
            try
                set nm to (name of u as text)
            on error
                set nm to ""
            end try
            try
                set ds to (description of u as text)
            on error
                set ds to ""
            end try
            set hit to false
            if matchMode is "exact" then
                if nm is needleText or ds is needleText then set hit to true
            else
                if nm contains needleText or ds contains needleText then set hit to true
            end if
            if hit then
                try
                    set p to position of u
                    set sz to size of u
                    set foundX to (item 1 of p) + ((item 1 of sz) div 2)
                    set foundY to (item 2 of p) + ((item 2 of sz) div 2)
                    set foundIt to true
                end try
            end if
            if not foundIt then
                try
                    my walkUI(u)
                end try
            end if
        end repeat
    end tell
end walkUI
'''
    try:
        out = _run_osascript(script)
        if "," not in out:
            return None
        x, y = out.split(",", 1)
        return int(float(x)), int(float(y))
    except Exception:
        return None


def mac_ax_center_role(process: str, *, roles=(), labels=(), exact=False) -> Optional[Tuple[int, int]]:
    """Fast macOS AX lookup using role + accessible text."""
    if not _mac():
        return None
    def esc(x: str) -> str:
        return x.replace('\\', '\\\\').replace('"', '\\"')
    roles_as = '{' + ','.join('"' + esc(x) + '"' for x in roles) + '}'
    labels_as = '{' + ','.join('"' + esc(x) + '"' for x in labels) + '}'
    mode = 'true' if exact else 'false'
    script = f'''\
set wantedRoles to {roles_as}
set wantedLabels to {labels_as}
set exactMode to {mode}
set foundX to 0
set foundY to 0
set foundIt to false
tell application "System Events"
    tell process "{esc(process)}"
        set frontmost to true
        repeat with w in windows
            my walkUI(w)
            if foundIt then exit repeat
        end repeat
    end tell
end tell
if foundIt then return (foundX as text) & "," & (foundY as text)
return ""

on matchesText(txt)
    if (count of wantedLabels) is 0 then return true
    repeat with wanted in wantedLabels
        if exactMode then
            if txt is (wanted as text) then return true
        else if txt contains (wanted as text) then return true
        end if
    end repeat
    return false
end matchesText

on walkUI(container)
    tell application "System Events"
        repeat with u in UI elements of container
            if foundIt then exit repeat
            set roleText to ""
            set allText to ""
            try
                set roleText to role of u as text
            end try
            try
                set allText to (name of u as text)
            end try
            try
                set allText to allText & " " & (description of u as text)
            end try
            try
                set allText to allText & " " & (value of u as text)
            end try
            try
                set allText to allText & " " & (title of u as text)
            end try
            set roleOK to ((count of wantedRoles) is 0)
            repeat with wantedRole in wantedRoles
                if roleText is (wantedRole as text) then set roleOK to true
            end repeat
            if roleOK and my matchesText(allText) then
                try
                    set p to position of u
                    set sz to size of u
                    if (item 1 of sz) > 1 and (item 2 of sz) > 1 then
                        set foundX to (item 1 of p) + ((item 1 of sz) div 2)
                        set foundY to (item 2 of p) + ((item 2 of sz) div 2)
                        set foundIt to true
                    end if
                end try
            end if
            if not foundIt then
                try
                    my walkUI(u)
                end try
            end if
        end repeat
    end tell
end walkUI
'''
    try:
        out = _run_osascript(script)
        if "," not in out:
            return None
        x, y = out.split(",", 1)
        return int(float(x)), int(float(y))
    except Exception:
        return None


def mac_ax_whatsapp_search(process: str = "WhatsApp") -> Optional[Tuple[int, int]]:
    labels = ("Search or start new chat", "Search or start a new chat", "Search", "Find a chat")
    return mac_ax_center_role(process, roles=("AXTextField", "AXSearchField"), labels=labels)


def mac_ax_find_text(process: str, text: str, *, exact: bool = True) -> Optional[Tuple[int, int]]:
    return mac_ax_center_role(process, roles=("AXStaticText", "AXButton", "AXCell", "AXRow", "AXLink", "AXTextField"), labels=(text,), exact=exact)

def mac_ax_text_present(process: str, needle: str) -> bool:
    """Fast accessibility-tree check for visible text, used for verification."""
    if not _mac():
        return False
    n = needle.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''\
set needleText to "{n}"
tell application "System Events"
    tell process "{process}"
        set foundIt to false
        my walkText(window 1)
    end tell
end tell
return foundIt as text

on walkText(container)
    tell application "System Events"
        repeat with u in UI elements of container
            if foundIt then exit repeat
            try
                set nm to (name of u as text)
            on error
                set nm to ""
            end try
            try
                set val to (value of u as text)
            on error
                set val to ""
            end try
            if nm contains needleText or val contains needleText then
                set foundIt to true
            else
                try
                    my walkText(u)
                end try
            end if
        end repeat
    end tell
end walkText
'''
    try:
        return _run_osascript(script).lower() == "true"
    except Exception:
        return False


def click_point(point: Tuple[int, int]) -> None:
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is not installed")
    pyautogui.click(*point)


def paste_text(text: str) -> None:
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is not installed")
    if pyperclip is not None:
        pyperclip.copy(text)
        pyautogui.hotkey("command" if _mac() else "ctrl", "v")
    else:
        pyautogui.write(text, interval=0.015)


def wait_for_center(process: str, needle: str, timeout: float = 8.0,
                    interval: float = 0.25, exact: bool = False) -> Optional[Tuple[int, int]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        point = mac_ax_center(process, needle, exact=exact)
        if point:
            return point
        time.sleep(interval)
    return None

# Small process-local idempotency guard.  The stronger protection is the
# conversation check in send_message: if the exact text already appears in the
# open chat, we never type/send it again.
_ACTIVE = set()

def claim(key: str) -> bool:
    if key in _ACTIVE:
        return False
    _ACTIVE.add(key)
    return True

def release(key: str) -> None:
    _ACTIVE.discard(key)
