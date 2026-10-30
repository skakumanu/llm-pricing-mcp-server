"""MCP Tool: View or delete the one price alert an alert_token manages (v1.81.0)."""
from typing import Any, Dict

from src.services.pricing_alerts import ALERT_TOKEN_REFUSAL_MESSAGE, get_pricing_alert_service

_ACTIONS = ("view", "delete")

# One refusal for every token failure, identical for both actions and whether or not
# any alert exists. Callers get a fresh copy so nothing can mutate the template.
_REFUSAL: Dict[str, Any] = {
    "success": False,
    "error_code": "invalid_alert_token",
    "error": ALERT_TOKEN_REFUSAL_MESSAGE,
}


def _refusal() -> Dict[str, Any]:
    return dict(_REFUSAL)


class ManagePriceAlertTool:
    """Self-service view/delete of one price alert, authorised by its alert_token.

    PUBLIC (no caller_access): the alert_token is the credential, so no API key is
    needed. No alert id is accepted; the token alone selects the alert.
    """

    # Never surface exception text (it could quote arguments) through ToolManager.
    redact_errors = True

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return {
                "success": False,
                "error_code": "invalid_arguments",
                "error": "action must be 'view' or 'delete'",
            }
        token = arguments.get("alert_token")

        try:
            svc = get_pricing_alert_service()
        except RuntimeError:
            return {"success": False, "error": "Price alerts are not available right now"}

        try:
            if action == "view":
                alert = await svc.get_by_token(token)
                if alert is None:
                    return _refusal()
                return {"success": True, "action": "view", "alert": alert}
            alert_id = await svc.delete_by_token(token)
            if alert_id is None:
                return _refusal()
            return {"success": True, "action": "delete", "deleted": True, "alert_id": alert_id}
        except Exception as exc:
            return {
                "success": False,
                "error": "Failed to manage the price alert",
                "error_type": type(exc).__name__,
            }
