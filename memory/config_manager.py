import json
import os
import platform
import subprocess
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"
KEYRING_SERVICE = "Mark-TUK"
KEYRING_USER = "gemini_api_key"
try:
    import keyring
except Exception:
    keyring = None


def _macos_keychain_set(key: str) -> bool:
    if platform.system() != "Darwin":
        return False
    security = "/usr/bin/security"
    if not os.path.exists(security):
        return False
    account = os.environ.get("USER") or os.environ.get("USERNAME") or "TUK"
    try:
        p = subprocess.run(
            [security, "add-generic-password", "-a", account, "-s", KEYRING_SERVICE, "-w", key, "-U"],
            capture_output=True, text=True, timeout=8
        )
        return p.returncode == 0
    except Exception:
        return False


def _macos_keychain_get() -> str | None:
    if platform.system() != "Darwin":
        return None
    security = "/usr/bin/security"
    if not os.path.exists(security):
        return None
    account = os.environ.get("USER") or os.environ.get("USERNAME") or "TUK"
    try:
        p = subprocess.run(
            [security, "find-generic-password", "-a", account, "-s", KEYRING_SERVICE, "-w"],
            capture_output=True, text=True, timeout=8
        )
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    return None


def _keychain_set(key: str) -> None:
    # On macOS, use Apple's native Keychain CLI directly. This avoids Python
    # keyring backend/import problems (notably on newer Python versions).
    if _macos_keychain_set(key):
        return
    if keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
            stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if stored and stored.strip() == key:
                return
        except Exception:
            pass
    raise RuntimeError(
        "OS keychain support is unavailable. On macOS, TUK could not access "
        "Apple Keychain. On Windows/Linux, install/configure the OS credential "
        "store and keyring package, then try setup again."
    )


def _keychain_get() -> str | None:
    value = _macos_keychain_get()
    if value:
        return value
    if keyring is not None:
        try:
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or None
        except Exception:
            pass
    return None

LLM_KEYCHAIN_SERVICE = "Mark-TUK-LLM"

def save_llm_api_key(api_key: str) -> None:
    """Store a provider-agnostic chat API key in the OS credential store."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("API key is empty.")
    if platform.system() == "Darwin":
        security = "/usr/bin/security"
        account = os.environ.get("USER") or os.environ.get("USERNAME") or "TUK"
        p = subprocess.run([security, "add-generic-password", "-a", account, "-s", LLM_KEYCHAIN_SERVICE, "-w", key, "-U"], capture_output=True, text=True, timeout=8)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or "macOS Keychain could not save the API key")
        if get_llm_api_key() != key:
            raise RuntimeError("macOS Keychain did not confirm the saved API key")
        return
    if keyring is not None:
        keyring.set_password(LLM_KEYCHAIN_SERVICE, KEYRING_USER, key)
        if keyring.get_password(LLM_KEYCHAIN_SERVICE, KEYRING_USER) == key:
            return
    raise RuntimeError("OS credential store is unavailable for this API key")

def get_llm_api_key() -> str | None:
    if platform.system() == "Darwin":
        security = "/usr/bin/security"
        account = os.environ.get("USER") or os.environ.get("USERNAME") or "TUK"
        try:
            p = subprocess.run([security, "find-generic-password", "-a", account, "-s", LLM_KEYCHAIN_SERVICE, "-w"], capture_output=True, text=True, timeout=8)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except Exception:
            pass
    if keyring is not None:
        try:
            return keyring.get_password(LLM_KEYCHAIN_SERVICE, KEYRING_USER) or None
        except Exception:
            pass
    return None


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    """Store the Gemini secret in the OS keychain; never write it to JSON.

    The old implementation swallowed keychain failures and then told the UI that
    setup had completed. That left a configured-looking app with no secret, so
    the Live client received an empty key and entered the reconnect loop.
    """
    ensure_config_dir()
    key = (gemini_api_key or "").strip()
    if not key:
        raise ValueError("Gemini API key is empty.")

    _keychain_set(key)
    stored = _keychain_get()
    if not stored or stored.strip() != key:
        raise RuntimeError("The OS keychain did not confirm the Gemini API key.")

    # Only after the keychain write is verified is it safe to remove any legacy
    # plaintext copy from the JSON config.
    data = load_api_keys()
    data.pop("gemini_api_key", None)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Config] Failed to load config: {e}")
        return {}

    # One-time migration from old releases that stored the secret in JSON.
    legacy = data.get("gemini_api_key")
    if legacy:
        migrated = False
        try:
            existing = _keychain_get()
            if not existing:
                _keychain_set(str(legacy))
                existing = _keychain_get()
            migrated = bool(existing and existing.strip() == str(legacy).strip())
        except Exception as e:
            print(f"[Config] Keychain migration unavailable: {e}")

        # Do not delete the only copy if migration failed. This prevents an
        # upgrade from silently destroying a working installation. Once the
        # keychain is confirmed, remove the legacy plaintext value immediately.
        if migrated:
            data.pop("gemini_api_key", None)
            try:
                CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            except Exception:
                pass
    return data


def get_gemini_key() -> str | None:
    value = _keychain_get()
    if value:
        return value

    # Legacy installations may still have a plaintext key only when migration
    # could not complete. Load it through the same migration path rather than
    # indexing the JSON directly.
    try:
        legacy = load_api_keys().get("gemini_api_key")
        return str(legacy).strip() if legacy else None
    except Exception:
        return None


def is_configured() -> bool:
    cfg = load_api_keys()
    provider = str(cfg.get("llm_provider", "gemini-live") or "gemini-live").strip().lower()
    if provider == "ollama":
        return bool(cfg.get("llm_url") and cfg.get("llm_model"))
    if provider in {"openai-compatible", "openai", "custom", "openrouter", "groq", "together", "deepseek", "mistral"}:
        key = get_llm_api_key()
        return bool(key and len(key) > 1 and cfg.get("llm_url") and cfg.get("llm_model"))
    key = get_gemini_key()
    return bool(key and len(key) > 15)

def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "TUK") or "TUK"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def get_preferred_address() -> str:
    """Return the user's chosen casual form of address, defaulting to first name."""
    data = load_api_keys()
    preferred = str(data.get("preferred_address") or data.get("nickname") or "").strip()
    if preferred:
        return preferred[:48]
    full_name = str(data.get("user_name") or "").strip()
    return (full_name.split()[0] if full_name else "bro")[:48]


def save_preferred_address(value: str) -> str:
    """Persist a nickname/address preference while preserving unrelated settings."""
    cleaned = " ".join(str(value or "").split())[:48]
    if not cleaned:
        raise ValueError("Please provide a short name or form of address.")
    ensure_config_dir()
    data = load_api_keys()
    data["preferred_address"] = cleaned
    CONFIG_FILE.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return cleaned


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "TUK"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


HUD_STYLES = ("jarvis", "orb", "face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what MARK LIV shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "jarvis")).strip().lower()
    return v if v in HUD_STYLES else "jarvis"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "jarvis")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")