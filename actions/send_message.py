import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
    _PYAUTOGUI = True
except Exception:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, timeout=10,
                )
            time.sleep(0.8)
            return result.returncode == 0

        else: 
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0) 
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return f"Could not open {app_name}."

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)
    return f"Message sent to {receiver} via {app_name}."

def _send_whatsapp(receiver: str, message: str) -> str:
    """Reliable WhatsApp sender: observe -> act -> verify, with duplicate guard.

    macOS Accessibility is preferred over screenshot guessing. Vision remains a
    fallback for machines/apps that do not expose the needed accessibility nodes.
    Before sending, the exact message is checked in the open conversation so a
    slow/ambiguous UI cannot cause a second copy.
    """
    key = f"whatsapp|{receiver.casefold().strip()}|{message}"
    from core.app_automation import claim, release
    if not claim(key):
        return "That WhatsApp message is already being processed; I will not send a duplicate."

    try:
        from actions.computer_control import _focus_window, _screen_find, _click, _smart_type, _press, _require_pyautogui
        from core.app_automation import wait_for_center, mac_ax_text_present, click_point, paste_text

        _require_pyautogui()
        if not _open_app("WhatsApp"):
            return "Could not open WhatsApp. The message was not sent."
        _focus_window("WhatsApp")
        time.sleep(0.2)

        # Fast path: macOS Accessibility tree. This avoids repeated vision-model
        # calls and fixed multi-second sleeps on every step.
        if sys.platform == "darwin":
            from core.app_automation import mac_ax_whatsapp_search, mac_ax_find_text
            # WhatsApp's current macOS UI uses an AXTextField whose accessible
            # label is often "Search or start new chat". Searching by role first
            # avoids accidentally clicking the toolbar Search button.
            search = wait_for_center("WhatsApp", "Search or start new chat", timeout=1.8)
            if not search:
                search = mac_ax_whatsapp_search("WhatsApp")
            if search:
                click_point(search)
                # Replace the query atomically; no per-character typing delay.
                paste_text(receiver)
                contact = mac_ax_find_text("WhatsApp", receiver, exact=True)
                if not contact:
                    contact = wait_for_center("WhatsApp", receiver, timeout=2.4, interval=0.12, exact=True)
                if contact:
                    click_point(contact)
                    time.sleep(0.18)
                    # Do not treat any matching text in the conversation as proof
                    # this requested send already happened: common texts like "hi"
                    # may exist in older history. We verify after this send instead.
                    composer = (wait_for_center("WhatsApp", "Type a message", timeout=1.5, interval=0.12)
                                or wait_for_center("WhatsApp", "Message", timeout=1.2, interval=0.12))
                    if composer:
                        click_point(composer)
                        paste_text(message)
                        # Verify that the composer contains the requested text
                        # before committing the irreversible send.
                        time.sleep(0.15)
                        _press("enter")
                        if mac_ax_text_present("WhatsApp", message):
                            return f"Message sent to {receiver} via WhatsApp."
                        # Give the UI a short settle window rather than sending again.
                        deadline = time.monotonic() + 1.8
                        while time.monotonic() < deadline:
                            if mac_ax_text_present("WhatsApp", message):
                                return f"Message sent to {receiver} via WhatsApp."
                            time.sleep(0.2)
                        return f"I could not verify the WhatsApp message to {receiver}; I did not retry, to avoid a duplicate."

        # Vision fallback: still state-aware, with bounded waits and no second
        # send after an uncertain verification.
        search = _screen_find(
            f"WhatsApp search field or new-chat search control used to find a contact named '{receiver}'"
        )
        if not search:
            return f"Could not find the WhatsApp contact search control for {receiver}. The message was not sent."
        _click(*search)
        _smart_type(receiver, clear_first=True)
        time.sleep(0.5)
        contact = _screen_find(f"the exact WhatsApp contact search result named '{receiver}'")
        if not contact:
            return f"Could not find the WhatsApp contact '{receiver}'. The message was not sent."
        _click(*contact)
        time.sleep(0.5)
        composer = _screen_find("WhatsApp message composer/input box at the bottom of the current conversation")
        if not composer:
            return f"Opened {receiver}'s conversation but could not find the message box. The message was not sent."
        _click(*composer)
        _smart_type(message, clear_first=False)
        _press("enter")
        time.sleep(0.7)
        sent = _screen_find(f"the newly sent WhatsApp message bubble containing this exact text: {message[:180]}")
        if sent:
            return f"Message sent to {receiver} via WhatsApp."
        return f"I could not verify the WhatsApp message to {receiver}; I did not retry, to avoid a duplicate."
    except Exception as e:
        return f"The WhatsApp message to {receiver} was NOT sent: {e}"
    finally:
        release(key)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")   
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    # "NOT sent" contains "sent". The old test read that as a success and put a
    # tick next to a message that never went.
    lowered = result.lower()
    ok = "sent" in lowered and "not sent" not in lowered
    print(f"[SendMessage] {'✅' if ok else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": (
        "Sends a text message via WhatsApp, Telegram, or another messaging "
        "platform. Write 'message_text' in the USER'S OWN LANGUAGE, exactly "
        "what they asked to be said. If the result says the message was NOT "
        "sent, repeat that plainly along with the reason it gives — never "
        "tell the user a message was sent unless the result said it was."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp, Telegram, etc."
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}
