"""Small user-triggered routines/macros for TUK."""
from __future__ import annotations
import platform, subprocess, webbrowser
def _open(name):
    if platform.system()=="Darwin": subprocess.Popen(["open","-a",name])
    elif platform.system()=="Windows": subprocess.Popen(["cmd","/c","start","",name])
    else: subprocess.Popen([name])
def routine(parameters=None, player=None, speak=None, **_):
    p=parameters or {}; name=str(p.get("name","")).strip().lower()
    if name in ("good morning","morning"):
        webbrowser.open("https://news.google.com/"); webbrowser.open("https://calendar.google.com/")
        return "Good morning routine started: news and calendar opened."
    if name in ("work mode","work"):
        for app in (p.get("apps") or []):
            try: _open(str(app))
            except Exception: pass
        return "Work mode started."
    return "Available routines: Good morning and Work mode."
TOOL={"name":"routine","description":"Runs named routines/macros such as Good morning or Work mode.","parameters":{"type":"OBJECT","properties":{"name":{"type":"STRING"},"apps":{"type":"ARRAY","items":{"type":"STRING"}}},"required":["name"]},"handler":routine}
