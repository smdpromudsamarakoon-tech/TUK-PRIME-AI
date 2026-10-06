"""Health check action for TUK."""
from core.health import run_health_checks
def health_check(parameters=None, player=None, speak=None, **_):
    rows=run_health_checks()
    text="\n".join(("✓ " if r["ok"] else "✗ ")+r["name"]+" — "+r["detail"]+
                   ((" | Fix: "+r["fix"]) if not r["ok"] and r["fix"] else "") for r in rows)
    if player:
        try: player.write_log("SYS: Health check\n"+text)
        except Exception: pass
    return text
TOOL={"name":"health_check","description":"Checks TUK microphone, speakers, Gemini API key, internet, Accessibility and package-manager readiness.","parameters":{"type":"OBJECT","properties":{}},"handler":health_check}
