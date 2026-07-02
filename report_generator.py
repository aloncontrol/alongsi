import logging
from datetime import datetime, time as dtime
from collections import defaultdict
from database import (
    get_alerts_in_window, get_active_alert_codes,
    get_schedule_settings, mark_alerts_noticed, save_report
)
from alerts import get_alert_description, get_alert_category, CATEGORY_ICONS

logger = logging.getLogger(__name__)

UNGROUPED = "ללא קבוצה"


def _in_time_window(record_date_str: str, start: str, end: str) -> bool:
    """Check if a datetime string falls within a time window (handles overnight ranges)."""
    if not start or not end:
        return True
    try:
        dt = datetime.fromisoformat(record_date_str)
        t = dt.time()
        sh, sm = [int(x) for x in start.split(":")]
        eh, em = [int(x) for x in end.split(":")]
        t_start = dtime(sh, sm)
        t_end = dtime(eh, em)
        if t_start <= t_end:
            return t_start <= t <= t_end
        else:  # overnight: e.g. 17:00 -> 05:00
            return t >= t_start or t <= t_end
    except Exception:
        return True


def generate_alert_report(conn, hours_back=24, mark_noticed=False) -> dict:
    """
    Generate a grouped alert report.
    Returns dict with: groups, total_alerts, total_units, generated_at
    """
    sched = get_schedule_settings(conn)
    active_codes = get_active_alert_codes(conn)

    # Smart rule params
    leak_start = sched["leak_hour_start"] if sched else "17:00"
    leak_end = sched["leak_hour_end"] if sched else "05:00"
    flow_min_count = int(sched["flow_min_count"]) if sched else 3
    flow_hours = int(sched["flow_hours_window"]) if sched else 48

    raw_alerts = get_alerts_in_window(conn, hours_back, active_codes)

    # Apply smart rules
    filtered = []
    flow_tracker = defaultdict(list)  # unit_id -> [record_date]

    for a in raw_alerts:
        code = a["alert_code"]
        category = get_alert_category(code)

        # Leak: time window filter
        if category == "ספיקה וזרימה" and code in (4, 5):
            if not _in_time_window(a["record_date"] or "", leak_start, leak_end):
                continue

        # Flow (ספיקה): frequency filter
        if category == "ספיקה וזרימה" and code in (1, 2) and flow_min_count > 0:
            flow_tracker[a["control_unit_id"]].append(a["record_date"])
            continue  # decide later

        filtered.append(a)

    # Apply flow frequency rule
    for unit_id, dates in flow_tracker.items():
        if len(dates) >= flow_min_count:
            # Include all flow alerts for this unit
            for a in raw_alerts:
                if a["control_unit_id"] == unit_id and a["alert_code"] in (1, 2):
                    filtered.append(a)

    # Group by area
    groups_map = defaultdict(lambda: {"alerts": [], "units": set()})
    for a in filtered:
        group = a["group_name"] or UNGROUPED
        groups_map[group]["alerts"].append(a)
        groups_map[group]["units"].add(a["control_unit_id"])

    # Build output structure
    groups = []
    for group_name in sorted(groups_map.keys()):
        g = groups_map[group_name]
        unit_summary = defaultdict(list)
        for a in g["alerts"]:
            uid = a["control_unit_id"]
            name = a["unit_name"] or a["u_name"] or f"Unit {uid}"
            unit_summary[uid].append({
                "name": name,
                "alert_code": a["alert_code"],
                "alert_name": get_alert_description(a["alert_code"]),
                "category": get_alert_category(a["alert_code"]),
                "record_date": a["record_date"],
                "station_name": a["station_name"],
                "actual_flow": a["actual_flow"],
                "water_quantity": a["water_quantity"],
            })

        units_list = []
        for uid, unit_alerts in unit_summary.items():
            units_list.append({
                "id": uid,
                "name": unit_alerts[0]["name"],
                "count": len(unit_alerts),
                "alerts": unit_alerts
            })
        units_list.sort(key=lambda u: u["name"])

        groups.append({
            "name": group_name,
            "alert_count": len(g["alerts"]),
            "unit_count": len(g["units"]),
            "units": units_list
        })

    total_alerts = sum(g["alert_count"] for g in groups)
    total_units = sum(g["unit_count"] for g in groups)

    if mark_noticed and filtered:
        mark_alerts_noticed(conn, [a["id"] for a in filtered])

    # Build summary data for charts
    alerts_by_category = defaultdict(int)
    for g in groups:
        for u in g["units"]:
            for a in u["alerts"]:
                cat = a.get("category") or "אחר"
                alerts_by_category[cat] += 1

    alerts_by_group = {g["name"]: g["alert_count"] for g in groups}

    report = {
        "generated_at": datetime.now().isoformat(),
        "hours_back": hours_back,
        "total_alerts": total_alerts,
        "total_units": total_units,
        "groups": groups,
        "category_icons": CATEGORY_ICONS,
        "alerts_by_category": dict(alerts_by_category),
        "alerts_by_group": alerts_by_group,
    }
    return report


def save_report_to_db(conn, report: dict, sent_whatsapp=False) -> int:
    """Persist report to report_log. Returns report ID."""
    html = render_report_html(report)
    return save_report(
        conn,
        alert_count=report["total_alerts"],
        unit_count=report["total_units"],
        sent_whatsapp=sent_whatsapp,
        report_html=html
    )


def render_report_html(report: dict) -> str:
    """Render the report as a standalone HTML string."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    import os
    tpl_dir = os.path.join(os.path.dirname(__file__), "dashboard", "templates")
    env = Environment(
        loader=FileSystemLoader(tpl_dir),
        autoescape=select_autoescape(["html"])
    )
    tpl = env.get_template("alert_report_print.html")
    return tpl.render(report=report)
