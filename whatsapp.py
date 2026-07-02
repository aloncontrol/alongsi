import logging
import requests
from config import (
    GREENAPI_INSTANCE_ID, GREENAPI_API_TOKEN, GREENAPI_PHONE
)

logger = logging.getLogger(__name__)


class GreenAPIWhatsApp:
    BASE_URL = "https://api.green-api.com"

    def __init__(self, instance_id=None, api_token=None, phone=None):
        self.instance_id = instance_id or GREENAPI_INSTANCE_ID
        self.api_token = api_token or GREENAPI_API_TOKEN
        self.default_phone = phone or GREENAPI_PHONE

    def _url(self, method):
        return f"{self.BASE_URL}/waInstance{self.instance_id}/{method}/{self.api_token}"

    def send_text(self, message: str, phone: str = None) -> bool:
        """Send plain text message via Green API."""
        chat_id = self._format_phone(phone or self.default_phone)
        if not chat_id:
            logger.error("WhatsApp: no phone number configured")
            return False

        try:
            resp = requests.post(
                self._url("sendMessage"),
                json={"chatId": chat_id, "message": message},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("idMessage"):
                logger.info(f"WhatsApp sent OK: {data['idMessage']}")
                return True
            logger.error(f"WhatsApp send failed: {data}")
            return False
        except requests.RequestException as e:
            logger.error(f"WhatsApp request error: {e}")
            return False

    def send_report_summary(self, report_data: dict, phone: str = None) -> bool:
        """Send a formatted report summary as WhatsApp message."""
        lines = [
            f"📊 *דוח התראות GSI Monitor*",
            f"🕐 {report_data.get('generated_at', '')[:16]}",
            f"",
            f"סה\"כ התראות: *{report_data.get('total_alerts', 0)}*",
            f"בקרים עם תקלות: *{report_data.get('total_units', 0)}*",
            f"",
        ]

        for group in report_data.get("groups", []):
            name = group.get("name") or "ללא קבוצה"
            count = group.get("alert_count", 0)
            units = group.get("unit_count", 0)
            lines.append(f"🌍 *{name}*: {count} תקלות | {units} בקרים")
            for unit_entry in group.get("units", [])[:5]:  # max 5 per group
                lines.append(f"  • {unit_entry['name']}: {unit_entry['count']} תקלות")

        lines.append("")
        lines.append("_ALONGSI Monitor - Alon Control Systems_")

        return self.send_text("\n".join(lines), phone)

    def _format_phone(self, phone: str) -> str:
        """Convert phone number to Green API chatId format (972XXXXXXXXX@c.us)."""
        if not phone:
            return ""
        # Strip non-digits
        digits = "".join(c for c in phone if c.isdigit())
        # Israeli numbers: 05X -> 9725X
        if digits.startswith("0") and len(digits) == 10:
            digits = "972" + digits[1:]
        if not digits.endswith("@c.us"):
            return digits + "@c.us"
        return digits

    def test_connection(self) -> bool:
        """Check if Green API instance is authorized."""
        try:
            resp = requests.get(
                self._url("getStateInstance"),
                timeout=10
            )
            resp.raise_for_status()
            state = resp.json().get("stateInstance", "")
            logger.info(f"Green API state: {state}")
            return state == "authorized"
        except requests.RequestException as e:
            logger.error(f"Green API connection test failed: {e}")
            return False


def send_whatsapp_report(report_data: dict, phone: str = None) -> bool:
    """Convenience function — create client and send."""
    client = GreenAPIWhatsApp()
    if not client.instance_id or not client.api_token:
        logger.warning("WhatsApp not configured (missing GREENAPI credentials)")
        return False
    return client.send_report_summary(report_data, phone)
