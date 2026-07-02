import logging
from database import insert_system_alert, get_latest_snapshot, ALL_ALERT_CODES
from config import ALERT_LOW_SIGNAL

logger = logging.getLogger(__name__)

# Build lookup: alert_code -> (name, category) from DB seed data
ALERT_CODE_MAP = {code: (name, cat) for code, name, cat, _ in ALL_ALERT_CODES}

# Also map legacy GSI alert codes (from API) to our category codes
# GSI uses different numeric codes than our internal ones
GSI_ALERT_CODES = {
    0:    "תקלה פעילה",   # generic — DeviceAlarm set but no specific code found
    1:    "ספיקה נמוכה",
    2:    "ספיקה גבוהה",
    3:    "אי זרימת מים",
    4:    "דליפת מים - התחלה",
    5:    "דליפת מים - סיום",
    6:    "שטיפת מסנן מתמשכת",
    10:   "אי זרימת דשן",
    11:   "השהיית יחידה עקב דשן לא מבוקר",
    12:   "התראת מרכז דישון",
    13:   "דישון לא הסתיים",
    14:   "התראת EC",
    15:   "התראת pH",
    16:   "התראת EC קיצוני",
    17:   "התראת pH קיצוני",
    20:   "מתח סוללה נמוך",
    21:   "סוללה ריקה",
    22:   "תקלת טעינת קבל",
    23:   "אין מתח AC",
    24:   "חזר מתח AC",
    30:   "סולנואיד/חיווט",
    31:   "קצר",
    32:   "שגיאת זיכרון בקר",
    33:   "מגופים תקינים",
    40:   "שגיאת תקשורת - קוד 17",
    41:   "תקשורת חזרה",
    42:   "אין תגובה",
    43:   "תקלת תקשורת - שרת",
    50:   "התראת חיישן",
    51:   "כניסה כבתה",
    52:   "כניסה הופעלה",
    53:   "התראה מתנאי לוגי",
    60:   "שגיאה בכתיבת יומנים והתראות",
    61:   "סנכרון זמן בבקר נכשל",
    62:   "תכנות שגוי",
    63:   "שגיאה בהגדרת מגוף",
    64:   "שגיאה בכתיבת CNF",
    65:   "הורדת גירסת בקר מרחוק נכשל",
    70:   "יחידה מושהית עקב סיכוי לגשם",
    71:   "יחידה פעילה עקב אי סיכוי לגשם",
    # Legacy GSI codes (as seen in real API responses)
    2000: "תקלת תקשורת",
    2001: "דליפת מים - התחלה",
    2002: "דליפת מים - סיום",
    2003: "חסימת ברז",
    2004: "קצר בברז",
    2005: "ברז לא נסגר",
    2006: "ברז לא נפתח",
    2007: "חיישן גשם פעיל",
    2008: "הפסקת חשמל",
    2009: "חזרת חשמל",
    2010: "ספירת מים עצרה",
    2011: "זרימה ללא תוכנית",
}

CATEGORY_ICONS = {
    "ספיקה וזרימה": "💧",
    "דישון":         "🧪",
    "סוללה וחשמל":   "🔋",
    "חומרה":         "🔧",
    "תקשורת":        "📡",
    "חיישנים וכניסות": "📊",
    "מערכת":         "⚙️",
    "מזג אוויר":     "🌧️",
}


def get_alert_description(alert_code):
    """Return Hebrew description for any alert code."""
    return GSI_ALERT_CODES.get(alert_code, f"התראה לא ידועה (קוד {alert_code})")


def get_alert_category(alert_code):
    """Return category for an alert code."""
    info = ALERT_CODE_MAP.get(alert_code)
    if info:
        return info[1]
    # Map legacy GSI codes to categories
    if 2000 <= alert_code <= 2011:
        return "תקשורת"
    return "אחר"


class AlertEngine:
    def check_unit(self, conn, unit_id, unit_data, prev_snapshot):
        config = unit_data.get("Config", {})
        unit_name = config.get("UnitName", f"Unit {unit_id}")

        self._check_connection(conn, unit_id, unit_name, config)
        self._check_signal(conn, unit_id, unit_name, config)
        self._check_device_alarm(conn, unit_id, unit_name, config)
        self._check_state_change(conn, unit_id, unit_name, config, prev_snapshot)

    def _check_connection(self, conn, unit_id, unit_name, config):
        if config.get("ConnectionStatus"):
            return
        if config.get("ControllerState") == 3:
            insert_system_alert(
                conn, unit_id, unit_name,
                "disconnected",
                f"בקר {unit_name} מנותק מהתקשורת",
                "critical"
            )
            logger.warning(f"ALERT: {unit_name} is disconnected")

    def _check_signal(self, conn, unit_id, unit_name, config):
        signal = config.get("Signal")
        if signal is None:
            return
        try:
            if int(signal) < ALERT_LOW_SIGNAL:
                insert_system_alert(
                    conn, unit_id, unit_name,
                    "low_signal",
                    f"עוצמת אות נמוכה בבקר {unit_name}: {signal}",
                    "warning"
                )
        except (ValueError, TypeError):
            pass

    def _check_device_alarm(self, conn, unit_id, unit_name, config):
        alarm = config.get("DeviceAlarm", 0)
        if not alarm:
            return
        parts = []
        if alarm & 0x01: parts.append("שגיאת חומרה")
        if alarm & 0x02: parts.append("שגיאת תקשורת")
        if alarm & 0x04: parts.append("שגיאת זיכרון")
        if alarm & 0x08: parts.append("תקלת תקשורת עם השרת")
        if alarm & 0x8000: parts.append("התראת מערכת")
        if parts:
            insert_system_alert(
                conn, unit_id, unit_name,
                "device_alarm",
                f"התראת מכשיר בבקר {unit_name}: {', '.join(parts)} (קוד: {alarm})",
                "warning"
            )

    def _check_state_change(self, conn, unit_id, unit_name, config, prev_snapshot):
        if prev_snapshot is None:
            return
        prev = prev_snapshot["connection_status"]
        now = 1 if config.get("ConnectionStatus") else 0
        if prev == 1 and now == 0:
            insert_system_alert(conn, unit_id, unit_name, "connection_lost",
                                f"בקר {unit_name} איבד חיבור", "critical")
            logger.warning(f"ALERT: {unit_name} connection LOST")
        elif prev == 0 and now == 1:
            insert_system_alert(conn, unit_id, unit_name, "connection_restored",
                                f"בקר {unit_name} חזר לתקשורת", "info")
            logger.info(f"INFO: {unit_name} connection restored")
