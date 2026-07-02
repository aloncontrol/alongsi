import sys
import os
import logging
import threading
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from config import POLL_INTERVAL_MINUTES, DASHBOARD_HOST, DASHBOARD_PORT
from database import init_db, get_db, get_schedule_settings
from collector import run_collection, run_battery_collection
from dashboard.app import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "alongsi.log"), encoding="utf-8")
    ]
)
logger = logging.getLogger("alongsi")

_scheduler: BackgroundScheduler = None


def _run_full_process_job():
    """Scheduled job wrapper — respects day-of-week filter."""
    from process import run_full_process, should_run_today
    conn = get_db()
    try:
        sched = get_schedule_settings(conn)
    finally:
        conn.close()

    if not should_run_today(sched):
        logger.info("Full process skipped — today is not an active day")
        return

    try:
        report = run_full_process()
        logger.info(f"Scheduled full process complete: {report['total_alerts']} alerts")
    except Exception as e:
        logger.error(f"Scheduled full process failed: {e}")


def _parse_hhmm(t: str):
    """Return (hour, minute) from 'HH:MM' string, or None if empty/invalid."""
    if not t or not t.strip():
        return None
    try:
        h, m = t.strip().split(":")
        return int(h), int(m)
    except Exception:
        return None


def _schedule_full_process(scheduler: BackgroundScheduler):
    """
    Load schedule_settings from DB and register cron jobs for full_process.
    Removes any previously registered full_process jobs before re-adding.
    """
    for job_id in ("full_process_1", "full_process_2"):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    conn = get_db()
    try:
        sched = get_schedule_settings(conn)
    finally:
        conn.close()

    if not sched or not sched["is_enabled"]:
        logger.info("Full process scheduler: disabled — no cron jobs added")
        return

    for job_id, time_key in (("full_process_1", "time_1"), ("full_process_2", "time_2")):
        parsed = _parse_hhmm(sched[time_key])
        if parsed:
            h, m = parsed
            scheduler.add_job(
                _run_full_process_job,
                CronTrigger(hour=h, minute=m),
                id=job_id,
                name=f"GSI Full Process ({sched[time_key]})",
                max_instances=1,
                replace_existing=True,
            )
            logger.info(f"Full process job scheduled at {sched[time_key]} (id={job_id})")


def main():
    logger.info("=" * 60)
    logger.info("ALONGSI - Galcon Smart Monitor v1.0")
    logger.info("=" * 60)

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Setup scheduler
    global _scheduler
    _scheduler = BackgroundScheduler()

    # Periodic data collection (every N minutes)
    _scheduler.add_job(
        run_collection,
        'interval',
        minutes=POLL_INTERVAL_MINUTES,
        id='gsi_collector',
        name='GSI Data Collector',
        max_instances=1
    )

    # Full process (check + mark + report + WhatsApp) at configured daily times
    _schedule_full_process(_scheduler)

    # Battery stats collection — once per day at 06:00
    _scheduler.add_job(
        run_battery_collection,
        CronTrigger(hour=6, minute=0),
        id='battery_collector',
        name='Battery Stats Collector',
        max_instances=1
    )

    _scheduler.start()
    logger.info(f"Scheduler started — collecting every {POLL_INTERVAL_MINUTES} minutes")

    # Run initial data collection in background so Flask starts immediately
    def _initial_collect():
        logger.info("Running initial data collection in background...")
        try:
            run_collection()
            logger.info("Initial collection complete")
        except Exception as e:
            logger.error(f"Initial collection failed: {e}")
        try:
            logger.info("Running initial battery collection...")
            run_battery_collection()
            logger.info("Initial battery collection complete")
        except Exception as e:
            logger.error(f"Initial battery collection failed: {e}")

    threading.Thread(target=_initial_collect, daemon=True).start()

    # Start Flask dashboard
    logger.info(f"Starting dashboard at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    try:
        app.run(
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            debug=False,
            use_reloader=False
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        _scheduler.shutdown()
        logger.info("ALONGSI stopped")


def reload_schedule():
    """Call this after saving schedule settings to re-apply cron jobs."""
    if _scheduler and _scheduler.running:
        _schedule_full_process(_scheduler)
        logger.info("Schedule reloaded from DB")


if __name__ == "__main__":
    main()
