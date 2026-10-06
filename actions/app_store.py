"""
actions/app_store.py — find and install apps from the OS app store by voice.

    "Jarvis, download WhatsApp"      → looks it up, asks you to CONFIRM on the HUD,
                                        then installs it.

Per platform
    macOS    Mac App Store via the `mas` CLI (brew install mas). If `mas` is missing
             or Apple refuses the CLI install, the app's App Store page is opened so
             you only press "Get". Apps that are not in the App Store at all (Chrome,
             Spotify, VS Code ...) fall back to Homebrew casks.
    Windows  winget — Microsoft Store source first, then the normal winget catalogue.
    Linux    Flatpak (Flathub), then snap, then apt.

Installing software runs code from the internet and cannot be cleanly undone, so
the install is parked behind core/confirm.py: the model can't approve it itself,
you press CONFIRM on the HUD. Searching needs no confirmation.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

_SYSTEM = platform.system()          # "Windows" | "Darwin" | "Linux"

if _SYSTEM == "Darwin":
    # Apps started from Finder / launchd get PATH=/usr/bin:/bin:/usr/sbin:/sbin,
    # so `brew` and `mas` (in /opt/homebrew/bin or /usr/local/bin) were "not
    # found" and the whole Homebrew fallback silently never ran.
    import os as _os
    _extra = [d for d in ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin")
              if d not in _os.environ.get("PATH", "").split(":")]
    if _extra:
        _os.environ["PATH"] = ":".join(_extra + [_os.environ.get("PATH", "")]).strip(":")
_INSTALL_TIMEOUT = 1800              # big apps take a while
_SEARCH_TIMEOUT = 25


# ── small helpers ────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = _SEARCH_TIMEOUT) -> tuple[int, str]:
    """Run a command, return (returncode, combined output). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out"
    except Exception as e:
        return 1, str(e)


def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _clean_query(name: str) -> str:
    """'download the WhatsApp app' → 'WhatsApp'."""
    q = re.sub(r"\b(the|app|application|from|app store|store|please)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", q).strip() or name.strip()


# ── macOS ────────────────────────────────────────────────────────────────────
#
# Real installs, in order of preference:
#   1. `mas install`            (fast, no clicking; Apple restricts it for apps
#                                that were never on your Apple ID)
#   2. App Store "Get" click    (AppleScript / System Events — needs the
#                                Accessibility permission for the app running
#                                Jarvis, and you may see a Touch ID prompt)
#   3. Homebrew cask            (a real download, no login, if a cask exists)
# The result is verified by looking for the app on disk, not by trusting an
# exit code.

_BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
_last_hint = [""]        # why the last GUI attempt failed, for the spoken reply

_CLICK_SCRIPT = """
on run argv
    set maxTries to (item 1 of argv) as integer
    set wanted to {"Get", "GET", "Install", "INSTALL", "Download", "DOWNLOAD"}
    tell application "System Events"
        tell process "App Store"
            set frontmost to true
            repeat maxTries times
                try
                    set els to entire contents of window 1
                    repeat with el in els
                        try
                            if role of el is "AXButton" then
                                set n to ""
                                try
                                    set n to name of el
                                end try
                                if n is missing value then set n to ""
                                if n is "" then
                                    try
                                        set n to description of el
                                    end try
                                    if n is missing value then set n to ""
                                end if
                                if n is "Open" or n is "OPEN" then return "installed"
                                if wanted contains n then
                                    click el
                                    return "clicked " & n
                                end if
                            end if
                        end try
                    end repeat
                end try
                delay 1
            end repeat
        end tell
    end tell
    return "notfound"
end run
"""


def _brew() -> str | None:
    return shutil.which("brew") or next((b for b in _BREW_PATHS if shutil.which(b)), None)


def _mac_find_installed(app: dict) -> str | None:
    """Path of the installed app, or None. Checks the App Store id first."""
    import glob
    import os
    code, out = _run(["mdfind", f"kMDItemAppStoreAdamID == {app['id']}"], timeout=15)
    if code == 0 and out.strip():
        return out.splitlines()[0]
    want = re.sub(r"[^a-z0-9]", "", app["name"].lower())
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        for path in glob.glob(os.path.join(base, "*.app")):
            have = re.sub(r"[^a-z0-9]", "", os.path.basename(path)[:-4].lower())
            if want and (have == want or have.startswith(want) or want.startswith(have) and len(have) > 3):
                return path
    return None


def _mac_wait_installed(app: dict, seconds: int) -> str | None:
    import time
    end = time.time() + seconds
    while time.time() < end:
        path = _mac_find_installed(app)
        if path:
            return path
        time.sleep(4)
    return None


def _mac_click_get(app: dict) -> tuple[bool, str]:
    """Open the app's App Store page and press Get / Install for the user.

    Returns (True, "") only if a button was really clicked (or the app turned
    out to be installed already). Before, it returned True even when nothing
    was clicked, so TUK then sat for 15 minutes waiting for a download that
    never started.
    """
    import time
    _run(["open", f"macappstore://apps.apple.com/app/id{app['id']}"])
    time.sleep(4)
    clicked_any = False
    for attempt in range(3):                       # Get → (Install) → done
        code, out = _run(["osascript", "-e", _CLICK_SCRIPT,
                          "30" if attempt == 0 else "6"], timeout=90)
        low = out.lower()
        if ("assistive access" in low or "-25211" in low or "-1719" in low
                or "-1743" in low or "not allowed" in low or "not authorized" in low):
            return False, ("macOS blocked the click. Turn on Accessibility AND Automation for TUK "
                           "(System Settings → Privacy & Security → Accessibility / Automation), "
                           "or press Get in the App Store window that I opened.")
        if "installed" in low:
            return True, ""
        if "clicked" not in low:
            break
        clicked_any = True
        time.sleep(3)
        if _mac_find_installed(app):
            break
    if not clicked_any:
        return False, ("I opened the App Store page but couldn't press Get myself. "
                       "Press Get there and it will download.")
    return True, ""


def _mac_install_store(app: dict) -> str:
    name = app["name"]
    if _mac_find_installed(app):
        return f"{name} is already installed."

    # 1) Use `mas` only when it is already installed.
    # Never bootstrap `mas` with Homebrew here: `brew install mas` can trigger
    # a terminal/admin-password prompt. TUK must never collect or automate
    # the user's macOS password.
    mas = shutil.which("mas")
    if mas:
        code, _ = _run([mas, "install", app["id"]], timeout=_INSTALL_TIMEOUT)
        if code == 0 and _mac_wait_installed(app, 30):
            return f"Installed {name} from the Mac App Store."

    # 2) Open the real App Store and let macOS handle authentication.
    # If authentication is required, macOS may offer Touch ID automatically
    # (with the normal password fallback controlled by macOS).
    ok, msg = _mac_click_get(app)
    if ok:
        path = _mac_wait_installed(app, 900)        # download + Touch ID prompt
        if path:
            return f"Installed {name} from the Mac App Store."
    else:
        _last_hint[0] = msg

    return ""                                       # caller falls back to Homebrew



def _mac_brew_cask(query: str) -> dict | None:
    brew = _brew()
    if not brew:
        return None
    code, out = _run([brew, "search", "--casks", query], timeout=60)
    if code != 0:
        return None
    names = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("==>")]
    if not names:
        return None
    q = re.sub(r"\s+", "-", query.lower())
    best = q if q in names else names[0]
    return {"id": best, "name": best, "brew": brew}


def _mac_install_cask(cask: dict) -> str:
    code, out = _run([cask["brew"], "install", "--cask", cask["id"]], timeout=_INSTALL_TIMEOUT)
    if code == 0:
        return f"Installed {cask['name']} with Homebrew."
    return f"Homebrew could not install {cask['name']}: {out[-160:]}"


def _mac_run(app: dict | None, query: str) -> str:
    """Full macOS install chain: App Store first, Homebrew cask as the safety net."""
    if app:
        done = _mac_install_store(app)
        if done:
            return done
    cask = _mac_brew_cask(app["name"] if app else query) or _mac_brew_cask(query)
    if cask:
        return _mac_install_cask(cask)
    hint = _last_hint[0] or "Install Homebrew (brew.sh) so I have a fallback, and try again."
    return f"I could not finish installing {app['name'] if app else query}. {hint}"


def _mac_pick(query: str, results: list[dict]) -> dict | None:
    if not results:
        return None
    q = query.lower()
    for r in results:                       # exact-ish name match beats ranking
        if r["name"].lower() == q or r["name"].lower().startswith(q + " "):
            return r
    return results[0]


def _mac_search(query: str) -> list[dict]:
    """Ask Apple's public iTunes Search API — no login, no extra packages."""
    url = ("https://itunes.apple.com/search?" +
           urllib.parse.urlencode({"term": query, "entity": "macSoftware", "limit": 5}))
    try:
        with urllib.request.urlopen(url, timeout=_SEARCH_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for it in data.get("results", []):
        out.append({
            "id":    str(it.get("trackId", "")),
            "name":  it.get("trackName", ""),
            "price": float(it.get("price") or 0.0),
            "label": it.get("formattedPrice") or "Free",
        })
    return [o for o in out if o["id"]]


def _mac_plan(query: str):
    """→ (title, detail, run) or (None, message, None)."""
    app = _mac_pick(query, _mac_search(query))
    if app and app["price"] > 0:                     # never buy on a voice command
        _run(["open", f"macappstore://apps.apple.com/app/id{app['id']}"])
        return (None, f"{app['name']} costs {app['label']}, so I won't buy it for you. "
                      f"I opened its App Store page.", None)
    if app:
        return (f"Install {app['name']}", "Free · Mac App Store",
                lambda: _mac_run(app, query))
    cask = _mac_brew_cask(query)
    if cask:
        return (f"Install {cask['name']}", "Homebrew cask (not in the App Store)",
                lambda: _mac_run(None, query))
    return (None, f"I couldn't find '{query}' in the Mac App Store"
                  + ("" if _brew() else " and Homebrew isn't installed for a second look") + ".", None)


# ── Windows ──────────────────────────────────────────────────────────────────

_WG_COMMON = ["--accept-source-agreements"]


def _winget_search(query: str, source: str) -> dict | None:
    code, out = _run(["winget", "search", query, "--source", source, *_WG_COMMON],
                     timeout=60)
    if code != 0:
        return None
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    sep = next((i for i, l in enumerate(lines) if set(l.strip()) == {"-"}), None)
    if sep is None or sep == 0 or sep + 1 >= len(lines):
        return None
    header = lines[sep - 1]
    id_col = header.find("Id")
    if id_col < 0:
        return None
    ver_col = header.find("Version")
    rows = []
    for l in lines[sep + 1:]:
        name = l[:id_col].strip()
        pid = (l[id_col:ver_col] if ver_col > id_col else l[id_col:]).strip().split()[0] if l[id_col:].strip() else ""
        if name and pid:
            rows.append({"name": name, "id": pid, "source": source})
    q = query.lower()
    for r in rows:
        if r["name"].lower() == q:
            return r
    return rows[0] if rows else None


def _win_install(app: dict) -> str:
    code, out = _run(["winget", "install", "--id", app["id"], "--exact",
                      "--source", app["source"], "--silent",
                      "--accept-package-agreements", *_WG_COMMON],
                     timeout=_INSTALL_TIMEOUT)
    if code == 0:
        return f"Installed {app['name']}."
    return f"winget could not install {app['name']}: {out[-160:]}"


def _win_plan(query: str):
    if not shutil.which("winget"):
        return (None, "winget isn't available. Update 'App Installer' from the Microsoft Store first.", None)
    app = _winget_search(query, "msstore") or _winget_search(query, "winget")
    if not app:
        return (None, f"I couldn't find '{query}' in the Microsoft Store or winget.", None)
    src = "Microsoft Store" if app["source"] == "msstore" else "winget"
    return (f"Install {app['name']}", f"{src} · {app['id']}", lambda: _win_install(app))


# ── Linux ────────────────────────────────────────────────────────────────────

def _flatpak_search(query: str) -> dict | None:
    code, out = _run(["flatpak", "search", "--columns=application,name", query])
    if code != 0:
        return None
    for l in out.splitlines():
        parts = l.split("\t") if "\t" in l else l.split(None, 1)
        if len(parts) == 2 and "." in parts[0] and not l.lower().startswith("application"):
            return {"id": parts[0].strip(), "name": parts[1].strip()}
    return None


def _linux_plan(query: str):
    if shutil.which("flatpak"):
        app = _flatpak_search(query)
        if app:
            def _run_fp():
                code, out = _run(["flatpak", "install", "-y", "flathub", app["id"]],
                                 timeout=_INSTALL_TIMEOUT)
                return (f"Installed {app['name']}." if code == 0
                        else f"Flatpak could not install {app['name']}: {out[-160:]}")
            return (f"Install {app['name']}", f"Flathub · {app['id']}", _run_fp)
    if shutil.which("snap"):
        name = re.sub(r"\s+", "-", query.lower())
        code, _ = _run(["snap", "info", name])
        if code == 0:
            def _run_snap():
                cmd = ["pkexec", "snap", "install", name] if shutil.which("pkexec") else ["snap", "install", name]
                c, out = _run(cmd, timeout=_INSTALL_TIMEOUT)
                return f"Installed {name}." if c == 0 else f"snap could not install {name}: {out[-160:]}"
            return (f"Install {name}", "Snap Store", _run_snap)
    if shutil.which("apt-get") and shutil.which("pkexec"):
        name = re.sub(r"\s+", "-", query.lower())
        code, _ = _run(["apt-cache", "show", name])
        if code == 0:
            def _run_apt():
                c, out = _run(["pkexec", "apt-get", "install", "-y", name], timeout=_INSTALL_TIMEOUT)
                return f"Installed {name}." if c == 0 else f"apt could not install {name}: {out[-160:]}"
            return (f"Install {name}", "apt", _run_apt)
    return (None, f"I couldn't find '{query}' with Flatpak, snap or apt on this system.", None)


def _installed_apps() -> str:
    if _SYSTEM == "Darwin":
        code,out=_run(["system_profiler","SPApplicationsDataType"],timeout=45)
        return out if code==0 else "Could not list apps: "+out[-200:]
    if _SYSTEM=="Windows" and shutil.which("winget"):
        code,out=_run(["winget","list"],timeout=60)
        return out if code==0 else "Could not list apps: "+out[-200:]
    if _SYSTEM=="Linux" and shutil.which("flatpak"):
        code,out=_run(["flatpak","list","--app"],timeout=30)
        return out if code==0 else "Could not list apps: "+out[-200:]
    return "Installed-app listing is not available on this system."

def _uninstall(query: str) -> str:
    if _SYSTEM=="Windows" and shutil.which("winget"):
        code,out=_run(["winget","uninstall",query,"--accept-source-agreements"],timeout=_INSTALL_TIMEOUT)
        return out[-500:] if out else ("Uninstalled "+query+"." if code==0 else "Uninstall failed.")
    if _SYSTEM=="Darwin":
        import glob, os
        q=re.sub(r"[^a-z0-9]","",query.lower()); matches=[]
        for base in ("/Applications",os.path.expanduser("~/Applications")):
            for path in glob.glob(base+"/*.app"):
                n=re.sub(r"[^a-z0-9]","",os.path.basename(path)[:-4].lower())
                if q and (q in n or n in q): matches.append(path)
        if not matches: return "I couldn't find an installed app matching "+query+"."
        target=matches[0]
        # Verify the app still exists before asking Finder. Finder returns -1728
        # when the POSIX path disappeared or is not reachable.
        if not os.path.exists(target):
            return "The app path disappeared before uninstall: " + target

        # Use Finder reveal/delete with an escaped argv path. This avoids
        # failures caused by hard-coded /Applications paths and special chars.
        script = r"""on run argv
 set targetPath to item 1 of argv
 set targetFile to POSIX file targetPath as alias
 tell application "Finder"
  move targetFile to trash
 end tell
 return "moved"
end run"""
        code, out = _run(["osascript", "-e", script, target], timeout=30)
        if code == 0:
            return "Moved " + os.path.basename(target) + " to Trash."
        return "Couldn't move " + os.path.basename(target) + " to Trash: " + (out[-250:] or "macOS denied the operation.")
    if _SYSTEM=="Linux" and shutil.which("flatpak"):
        code,out=_run(["flatpak","uninstall","-y",query],timeout=_INSTALL_TIMEOUT)
        return out[-500:] if out else ("Uninstalled "+query+"." if code==0 else "Uninstall failed.")
    return "App uninstall is not available on this system."

def _update_all() -> str:
    if _SYSTEM=="Windows" and shutil.which("winget"):
        code,out=_run(["winget","upgrade","--all","--silent","--accept-source-agreements","--accept-package-agreements"],timeout=_INSTALL_TIMEOUT)
        return out[-800:] or ("All available updates started." if code==0 else "Update failed.")
    if _SYSTEM=="Darwin" and shutil.which("brew"):
        _run(["brew","update"],timeout=300); code,out=_run(["brew","upgrade","--cask"],timeout=_INSTALL_TIMEOUT)
        return out[-800:] or "Homebrew cask updates completed."
    if _SYSTEM=="Linux" and shutil.which("flatpak"):
        code,out=_run(["flatpak","update","-y"],timeout=_INSTALL_TIMEOUT); return out[-800:]
    return "System-wide app update is not available through a supported package manager."

# ── entry point ──────────────────────────────────────────────────────────────

_PLANNERS = {"Darwin": _mac_plan, "Windows": _win_plan, "Linux": _linux_plan}


def app_store(parameters=None, player=None, speak=None, **_ignored) -> str:
    params = parameters or {}
    raw = str(params.get("app_name", "")).strip()
    action = str(params.get("action", "install")).strip().lower()
    if action in ("list","list_installed","installed"):
        return _installed_apps()
    if action in ("uninstall", "remove", "delete", "erase"):
        from core import confirm
        return confirm.request(key="app_uninstall", title=f"Uninstall {raw}", detail="The app will be removed (macOS moves it to Trash).", run=lambda: _uninstall(_clean_query(raw)))
    if action in ("update_all", "update", "upgrade"):
        from core import confirm
        return confirm.request(key="app_update_all", title="Update all apps", detail="Update apps using the platform package manager.", run=_update_all)
    if action not in ("list", "list_installed", "installed", "update_all", "update", "upgrade") and not raw:
        return "Which app should I look for?"

    planner = _PLANNERS.get(_SYSTEM)
    if planner is None:
        return f"Installing apps isn't supported on {_SYSTEM}."

    query = _clean_query(raw)
    _log(player, f"[app_store] {action}: {query}")

    try:
        title, detail, run = planner(query)
    except Exception as e:
        return f"App search failed: {e}"

    if title is None:
        return detail                                   # a spoken explanation

    if action == "search":
        return f"Found: {title.removeprefix('Install ')} ({detail})."

    from core import confirm
    if confirm.pending_title():
        return f"Finish the pending confirmation '{confirm.pending_title()}' first."

    def _run_logged() -> str:
        _log(player, f"SYS: {title} — working, this can take a few minutes...")
        try:
            result = run()
        except Exception as e:                      # never lose the outcome
            result = f"The install failed: {e}"
        if speak:                                   # tell the user the outcome out loud
            try:
                speak("[SYSTEM] App install finished. Result: " + result +
                      " Tell the user this in one short sentence, in their language.")
            except Exception:
                pass
        return result

    return confirm.request(key="app_store", title=title, detail=detail, run=_run_logged)


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "app_store",
    "description": (
        "Manage desktop apps. ALWAYS use this tool when the user asks to download, "
        "install, uninstall, delete, remove, list installed apps, or update apps. "
        "For a new app use action='install' (also handles 'download'/'get'); for removing "
        "an app use action='uninstall'; for installed-app inventory use action='list'; "
        "for updates use action='update_all'. On macOS, free Mac App Store apps and "
        "Homebrew casks are supported where available. Installs and removals require "
        "the user to press CONFIRM on the HUD. Use open_app only to launch an installed app."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Official name of the app in English (e.g. 'WhatsApp', 'Spotify', 'VLC')",
            },
            "action": {
                "type": "STRING",
                "description": "install/download/get, search, uninstall/remove/delete, list/list_installed, or update_all/update",
            },
        },
        "required": [],
    },
    "handler": app_store,
}
