import logging
from datetime import datetime
from database import get_db, get_schedule_settings
from report_generator import generate_alert_report, save_report_to_db
from whatsapp import send_whatsapp_report

logger = logging.getLogger(__name__)


def run_full_process(send_whatsapp_override=None):
    """
    Full automated process:
    1. Fetch alerts for last 24h filtered by active alert_settings
    2. Mark them as noticed in our DB
    3. Generate grouped report
    4. Optionally send WhatsApp
    5. Log to report_log
    Returns: report dict
    """
    logger.info("Starting full process (check + mark + report)")
    conn = get_db()
    try:
        sched = get_schedule_settings(conn)
        whatsapp_enabled = send_whatsapp_override
        if whatsapp_enabled is None:
            whatsapp_enabled = bool(sched and sched["whatsapp_enabled"])
        whatsapp_number = sched["whatsapp_number"] if sched else ""

        report = generate_alert_report(conn, hours_back=24, mark_noticed=False)

        sent_wa = False
        if whatsapp_enabled:
            phone = whatsapp_number or None
            sent_wa = send_whatsapp_report(report, phone)

        save_report_to_db(conn, report, sent_whatsapp=sent_wa)

        logger.info(
            f"Full process done: {report['total_alerts']} alerts, "
            f"{report['total_units']} units, WhatsApp={'sent' if sent_wa else 'skipped'}"
        )
        return report

    except Exception as e:
        logger.error(f"Full process error: {e}", exc_info=True)
        raise
    finally:
        conn.close()


def should_run_today(sched) -> bool:
    """Check if today is an active day per schedule settings."""
    if not sched or not sched["is_enabled"]:
        return False
    active_days = [int(d) for d in sched["active_days"].split(",") if d.strip()]
    # Python weekday: Mon=0..Sun=6; GSI uses 0=Sun..6=Sat
    python_day = datetime.now().weekday()
    # Convert: Sun=0 in GSI = 6 in Python
    gsi_day = (python_day + 1) % 7
    return gsi_day in active_days
