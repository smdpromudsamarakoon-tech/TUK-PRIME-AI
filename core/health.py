"""Cross-platform diagnostics for TUK."""
from __future__ import annotations
import platform, shutil, subprocess, urllib.request

def _item(name, ok, detail, fix=""):
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": fix}

def _mac_accessibility():
    try:
        p=subprocess.run(["osascript","-e",'tell application "System Events" to UI elements enabled'],
                         capture_output=True,text=True,timeout=3)
        if "true" in (p.stdout or "").lower():
            return _item("Accessibility",True,"Enabled")
    except Exception:
        pass
    return _item("Accessibility",False,"Not enabled",
                 "System Settings → Privacy & Security → Accessibility → allow TUK.")

def run_health_checks():
    out=[]
    try:
        import sounddevice as sd
        devs=sd.query_devices()
        out.append(_item("Microphone / speakers", bool(devs), "Audio devices detected",
                         "Open system Sound settings and select a working device."))
    except Exception as e:
        out.append(_item("Microphone / speakers",False,str(e)[:140],
                          "Open system Sound settings and select a working device."))
    from memory.config_manager import is_configured
    out.append(_item("Gemini API key",is_configured(),
                     "Key available in OS keychain" if is_configured() else "No valid key configured",
                     "Open Setup and add a Gemini API key."))
    try:
        urllib.request.urlopen("https://generativelanguage.googleapis.com",timeout=4)
        out.append(_item("Internet",True,"Connection available"))
    except Exception:
        out.append(_item("Internet",False,"Connection unavailable","Check Wi-Fi/VPN/firewall."))
    out.append(_mac_accessibility() if platform.system()=="Darwin"
               else _item("Accessibility",True,"Not required for basic operation"))
    managers=[x for x in ("brew","winget","flatpak","snap") if shutil.which(x)]
    out.append(_item("Package manager",bool(managers),", ".join(managers) if managers else "None detected",
                     "Install Homebrew, winget or Flatpak if app control needs it."))
    return out

def summary():
    return "\n".join(("✓ " if x["ok"] else "✗ ")+x["name"]+" — "+x["detail"] for x in run_health_checks())
