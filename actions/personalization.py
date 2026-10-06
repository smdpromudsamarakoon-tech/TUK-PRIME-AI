"""Small, explicit personalization controls for the TUK assistant."""
from __future__ import annotations

def personalize_action(parameters: dict, player=None, **_) -> str:
    action = str(parameters.get("action") or "show").strip().lower()
    try:
        from memory.config_manager import get_preferred_address, save_preferred_address
        if action in {"set", "change", "update"}:
            value = str(parameters.get("preferred_address") or "").strip()
            if not value:
                return "Tell me the short name or form of address you want me to use."
            saved = save_preferred_address(value)
            result = f"I will address you as {saved} when it feels natural. You can change this preference any time."
        elif action in {"show", "get", "current"}:
            result = f"Your current preferred form of address is {get_preferred_address()}."
        else:
            return "Supported personalization actions are show and set."
        if player:
            try:
                player.write_log("SYS: Personalization preference updated." if action in {"set", "change", "update"} else "SYS: Personalization preference checked.")
            except Exception:
                pass
        return result
    except Exception as exc:
        print(f"[Personalization] Could not update preference: {exc}")
        return "I couldn't save that preference. Please check that TUK can write to its config file."

TOOL = {
    "name": "personalize_user",
    "description": (
        "Manage the user's preferred casual name or form of address. Use action=set when the user explicitly says "
        "'call me X', 'address me as X', or asks to change their nickname. Use action=show when they ask what name "
        "you use for them. Do not infer a nickname or change it without a clear request. This changes only the "
        "preferred form of address, not the user's legal/full name. Never open a browser."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "show or set"},
            "preferred_address": {"type": "STRING", "description": "Short name/nickname to use, only required for set"},
        },
        "required": ["action"],
    },
    "handler": personalize_action,
}
