"""Cross-platform login-start registration for TUK.

One implementation shared by the AUTO-START and ALWAYS-ON buttons, so the
launcher file names and arguments can never drift apart again.

    set_login_start(True)                  -> start at login, normal window
    set_login_start(True, background=True) -> start at login, silent (tray only)
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

_MAC_LABEL = "com.tuk.assistant"


def _entry(background: bool) -> list[str]:
    if getattr(sys, "frozen", False):
        cmd = [os.path.abspath(sys.executable)]
    else:
        exe = sys.executable
        if platform.system() == "Windows":          # no console window at login
            pyw = Path(exe).with_name("pythonw.exe")
            if pyw.exists():
                exe = str(pyw)
        cmd = [exe, str(Path(__file__).resolve().parent.parent / "main.py")]
    if background:
        # Always-on uses a separate wake daemon. The daemon owns the mic and
        # launches the GUI only after the local TUK wake word is detected.
        cmd.append("--wake-daemon")
    return cmd


def _mac_bundle() -> str | None:
    """TUK.app path, when installed. Running through the app (not bare Python)
    is what lets macOS grant/keep the microphone permission at login."""
    if platform.system() != "Darwin":
        return None
    cands = [os.environ.get("TUK_APP_BUNDLE", ""),
             "/Applications/TUK.app",
             str(Path.home() / "Applications/TUK.app")]
    for c in cands:
        if c and (Path(c) / "Contents/MacOS/TUK").exists():
            return c
    return None


def _mac_plist() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{_MAC_LABEL}.plist"


def _linux_desktop() -> Path:
    return Path.home() / ".config/autostart/tuk.desktop"


def _quote(a: str) -> str:
    return '"' + a.replace('"', '\\"') + '"'


def is_login_start() -> bool:
    try:
        s = platform.system()
        if s == "Darwin":
            return _mac_plist().exists()
        if s == "Windows":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(k, "TUK")
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(k)
        return _linux_desktop().exists() or (Path.home() / ".config/autostart/TUK.desktop").exists()
    except Exception:
        return False


def set_login_start(enabled: bool = True, background: bool = False) -> bool:
    """Register / unregister TUK to start at login. Returns True on success."""
    try:
        s = platform.system()
        if s == "Darwin":
            p = _mac_plist()
            uid = os.getuid()
            target = f"gui/{uid}/{_MAC_LABEL}"
            # Re-register the agent immediately. Merely writing the plist is
            # not enough on modern macOS; launchd may keep the old definition.
            try:
                subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(p)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            except Exception:
                pass
            if enabled:
                p.parent.mkdir(parents=True, exist_ok=True)
                bundle = _mac_bundle()
                if bundle:
                    # Start through LaunchServices so TUK.app (with its mic usage
                    # description) is the responsible process. -W waits for it,
                    # -n always starts a fresh instance.
                    argv = ["/usr/bin/open", "-W", "-n", "-a", bundle, "--args"]
                    if background:
                        argv.append("--wake-daemon")
                    keep = ("<key>KeepAlive</key><true/>"
                            "<key>ThrottleInterval</key><integer>10</integer>")
                else:
                    argv = _entry(background)
                    keep = ("<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>"
                            "<key>ThrottleInterval</key><integer>5</integer>")
                args = "".join(f"<string>{escape(a)}</string>" for a in argv)
                log_dir = Path.home() / "Library" / "Logs" / "TUK"
                log_dir.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>'
                    f"<key>Label</key><string>{_MAC_LABEL}</string>"
                    f"<key>ProgramArguments</key><array>{args}</array>"
                    "<key>RunAtLoad</key><true/>"
                    f"{keep}"
                    "<key>ProcessType</key><string>Interactive</string>"
                    "<key>LimitLoadToSessionType</key><string>Aqua</string>"
                    f"<key>StandardOutPath</key><string>{escape(str(log_dir / 'wake-daemon.log'))}</string>"
                    f"<key>StandardErrorPath</key><string>{escape(str(log_dir / 'wake-daemon-error.log'))}</string>"
                    "</dict></plist>\n", encoding="utf-8")
                try:
                    result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(p)],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
                    if result.returncode != 0:
                        print(f"[Startup] launchctl bootstrap: {result.stderr.strip()}")
                        return False
                except Exception as exc:
                    print(f"[Startup] launchctl unavailable: {exc}")
                    return False
            else:
                p.unlink(missing_ok=True)
            return True
        if s == "Windows":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(k, "TUK", 0, winreg.REG_SZ,
                                      " ".join(_quote(a) for a in _entry(background)))
                else:
                    try:
                        winreg.DeleteValue(k, "TUK")
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(k)
            return True
        # Linux — remove both historical spellings so the toggle state stays honest.
        d = Path.home() / ".config/autostart"
        for name in ("tuk.desktop", "TUK.desktop"):
            (d / name).unlink(missing_ok=True)
        if enabled:
            d.mkdir(parents=True, exist_ok=True)
            _linux_desktop().write_text(
                "[Desktop Entry]\nType=Application\nName=TUK\n"
                "Exec=" + " ".join(_quote(a) for a in _entry(background)) + "\n"
                "Terminal=false\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
        return True
    except Exception as e:
        print(f"[Startup] {e}")
        return False
