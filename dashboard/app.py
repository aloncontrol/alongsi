import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
from functools import wraps
from collections import defaultdict

from database import (
    get_db, get_all_units, get_unit, get_latest_snapshots_all,
    get_latest_snapshots_filtered, get_all_units_filtered,
    get_latest_snapshot, get_unit_alerts, get_recent_alerts,
    get_system_alerts, get_unit_weather, get_snapshot_history,
    get_alert_settings, get_schedule_settings, save_alert_settings,
    save_schedule_settings, get_unit_groups, save_unit_groups_bulk,
    get_report_log, get_report_by_id, ALL_ALERT_CODES,
    get_primary_alerts_per_unit, get_all_battery_stats,
    get_all_users, create_user, update_user, delete_user, PERMISSIONS,
    get_all_projects, upsert_project, delete_project,
    get_user_project_ids, set_user_projects, get_unit_count_per_project,
    dismiss_unit_alerts,
    get_primary_active_alerts_per_unit, dismiss_active_alert
)
from alerts import get_alert_description, get_alert_category, CATEGORY_ICONS
from report_generator import generate_alert_report, save_report_to_db
from whatsapp import send_whatsapp_report
from config import COMPANY_NAME, SYSTEM_TITLE
from auth import is_allowed, send_otp, verify_otp, get_user, verify_password_login, has_password, set_password

app = Flask(__name__)
app.secret_key = "alongsi-gsi-monitor-2026"


def _base_ctx():
    """Common template context for all pages."""
    return {
        "company_name": COMPANY_NAME,
        "system_title": SYSTEM_TITLE,
        "current_permission": session.get("permission", "viewer"),
        "current_user_name": session.get("user_name", ""),
    }


def _get_project_ids():
    """Return project_ids for current user.
    Admin → None (meaning all projects).
    Others → list of assigned project_ids (empty list = no access)."""
    if session.get("permission") == "admin":
        return None
    phone = session.get("phone")
    if not phone:
        return []
    conn = get_db()
    try:
        from auth import get_user as _get_user
        user = _get_user(phone)
        if not user:
            return []
        return get_user_project_ids(conn, user["id"])
    finally:
        conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("index"))
    if request.method == "POST":
        phone = (request.form.get("phone") or "").strip()
        method = request.form.get("method", "otp")

        if not phone:
            flash("נא להזין מספר טלפון", "error")
            return render_template("login.html", **_base_ctx())
        if not is_allowed(phone):
            flash("מספר הטלפון אינו מורשה לגישה למערכת", "error")
            return render_template("login.html", **_base_ctx())

        if method == "password":
            password = (request.form.get("password") or "").strip()
            if not password:
                flash("נא להזין סיסמה", "error")
                return render_template("login.html", **_base_ctx())
            if not has_password(phone):
                flash("לא הוגדרה סיסמה למספר זה — השתמש בקוד WhatsApp", "error")
                return render_template("login.html", **_base_ctx())
            if not verify_password_login(phone, password):
                flash("סיסמה שגויה", "error")
                return render_template("login.html", **_base_ctx())
            # Password OK — log in directly
            user = get_user(phone)
            session["authenticated"] = True
            session["phone"] = phone
            session["user_name"] = user.get("name", "") if user else ""
            session["permission"] = user.get("permission", "viewer") if user else "viewer"
            return redirect(url_for("index"))

        # OTP flow
        sent = send_otp(phone)
        if not sent:
            flash("שגיאה בשליחת קוד — בדוק הגדרות Green API", "error")
            return render_template("login.html", **_base_ctx())
        session["pending_phone"] = phone
        return redirect(url_for("verify"))
    return render_template("login.html", **_base_ctx())


@app.route("/verify", methods=["GET", "POST"])
def verify():
    phone = session.get("pending_phone")
    if not phone:
        return redirect(url_for("login"))
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if verify_otp(phone, code):
            session.pop("pending_phone", None)
            session["authenticated"] = True
            session["phone"] = phone
            user = get_user(phone)
            session["permission"] = user["permission"] if user else "viewer"
            session["user_name"] = user["name"] if user else ""
            session.permanent = True
            return redirect(url_for("index"))
        flash("קוד שגוי או שפג תוקפו — נסה שוב", "error")
    return render_template("verify.html", phone=phone, **_base_ctx())


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Main dashboard (טיפול מיידי) ─────────────────────────────

_CATEGORY_ORDER = [
    "ספיקה וזרימה", "דישון", "סוללה וחשמל", "חומרה",
    "תקשורת", "חיישנים וכניסות", "מערכת", "מזג אוויר",
]

@app.route("/")
@login_required
def index():
    conn = get_db()
    pids = _get_project_ids()
    snapshots = get_latest_snapshots_filtered(conn, pids)
    system_alerts = get_system_alerts(conn, limit=10, unacknowledged_only=True)
    primary_alerts = get_primary_active_alerts_per_unit(conn, pids)
    battery_stats = get_all_battery_stats(conn)
    all_projects = get_all_projects(conn)
    conn.close()

    total = len(snapshots)
    connected = sum(1 for s in snapshots if s["connection_status"] == 1)
    disconnected = total - connected
    with_alerts = sum(1 for s in snapshots if (s["alerts_count"] or 0) > 0)
    irrigating = sum(1 for s in snapshots if (s["output_state"] or 0) > 0)

    # Enrich each snapshot with primary fault category
    enriched = []
    for s in snapshots:
        d = dict(s)
        code = primary_alerts.get(d["control_unit_id"])
        d["primary_alert_code"] = code
        d["primary_alert_name"] = get_alert_description(code) if code else None
        d["primary_alert_category"] = get_alert_category(code) if code else None

        # Disconnected controllers override → תקשורת
        if d["connection_status"] != 1:
            d["primary_alert_category"] = "תקשורת"
            d["primary_alert_name"] = "ניתוק תקשורת"
        # device_alarm set but no specific alert in history → show generic "תקלת מכשיר"
        elif d.get("device_alarm") and (d["device_alarm"] or 0) != 0 and not code:
            d["primary_alert_category"] = "חומרה"
            d["primary_alert_name"] = "התראת מכשיר"

        # Attach battery stats
        batt = battery_stats.get(d["control_unit_id"])
        d["noon_avg"] = batt["noon_avg"] if batt else None
        d["midnight_avg"] = batt["midnight_avg"] if batt else None
        # Count active valves from output_state bitmask
        d["active_valves"] = bin(d.get("output_state") or 0).count('1')
        enriched.append(d)

    # Sort: units with alerts first (by category order), then by alerts_count desc,
    # then disconnected, then OK
    def _sort_key(d):
        has_alerts = 1 if (d["alerts_count"] or 0) > 0 else 0
        cat = d["primary_alert_category"]
        cat_idx = _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else len(_CATEGORY_ORDER)
        count = -(d["alerts_count"] or 0)
        connected_ok = 0 if d["connection_status"] == 1 else 1
        return (1 - has_alerts, cat_idx, count, connected_ok)

    enriched.sort(key=_sort_key)

    # Build fault categories for the dashboard fault section
    fault_categories = {}
    for cat in _CATEGORY_ORDER:
        fault_categories[cat] = {"name": cat, "icon": CATEGORY_ICONS.get(cat, "⚠️"), "units": []}
    for d in enriched:
        cat = d["primary_alert_category"]
        if cat and cat in fault_categories:
            fault_categories[cat]["units"].append(d)
    cat_list = [c for c in fault_categories.values() if c["units"]]
    total_faults = sum(1 for d in enriched if d["primary_alert_category"])

    # Per-project summary for the Projects tab
    project_map = {p["project_id"]: p["project_name"] for p in all_projects}
    proj_stats = {}
    for d in enriched:
        pid = d.get("project_id")
        if pid not in proj_stats:
            proj_stats[pid] = {"id": pid,
                               "name": project_map.get(pid, f"פרויקט {pid}"),
                               "total": 0, "irrigating": 0,
                               "disconnected": 0, "faults": 0}
        st = proj_stats[pid]
        st["total"] += 1
        if (d.get("output_state") or 0) > 0:
            st["irrigating"] += 1
        if d["connection_status"] != 1:
            st["disconnected"] += 1
        if d.get("primary_alert_category") and d["connection_status"] == 1:
            st["faults"] += 1

    # All projects in DB (auto-synced from GSI), enriched with live stats
    all_db_projects = {p["project_id"]: p["project_name"] for p in all_projects}
    for pid, name in all_db_projects.items():
        if pid not in proj_stats:
            proj_stats[pid] = {"id": pid, "name": name,
                               "total": 0, "irrigating": 0,
                               "disconnected": 0, "faults": 0}

    projects_list = sorted(proj_stats.values(), key=lambda p: p["name"])

    # Active project: from ?pid= param only (0 = show project list)
    try:
        active_pid = int(request.args.get("pid", 0))
    except (ValueError, TypeError):
        active_pid = 0
    if active_pid and active_pid not in proj_stats:
        active_pid = 0

    active_project_name = proj_stats[active_pid]["name"] if active_pid and active_pid in proj_stats else ""

    return render_template("dashboard.html",
        active_page="dashboard",
        snapshots=enriched,
        total=total, connected=connected,
        disconnected=disconnected, total_alerts=with_alerts,
        irrigating=irrigating,
        system_alerts=system_alerts,
        fault_categories=cat_list,
        total_faults=total_faults,
        projects_summary=projects_list,
        active_pid=active_pid,
        active_project_name=active_project_name,
        json=json,
        **_base_ctx()
    )


# ── Fault-type dashboard ─────────────────────────────────────

@app.route("/faults")
@login_required
def faults_dashboard():
    conn = get_db()
    pids = _get_project_ids()
    snapshots = get_latest_snapshots_filtered(conn, pids)
    primary_alerts = get_primary_active_alerts_per_unit(conn, pids)
    conn.close()

    # Enrich snapshots
    enriched = []
    for s in snapshots:
        d = dict(s)
        code = primary_alerts.get(d["control_unit_id"])
        d["primary_alert_code"] = code
        d["primary_alert_name"] = get_alert_description(code) if code else None
        d["primary_alert_category"] = get_alert_category(code) if code else None
        if d["connection_status"] != 1:
            d["primary_alert_category"] = "תקשורת"
            d["primary_alert_name"] = "ניתוק תקשורת"
        elif d.get("device_alarm") and (d["device_alarm"] or 0) != 0 and not code:
            d["primary_alert_category"] = "חומרה"
            d["primary_alert_name"] = "התראת מכשיר"
        enriched.append(d)

    # Group by category
    categories = {}
    for cat in _CATEGORY_ORDER:
        categories[cat] = {
            "name": cat,
            "icon": CATEGORY_ICONS.get(cat, "⚠️"),
            "units": []
        }
    no_fault = {"name": "ללא תקלות", "icon": "✅", "units": []}

    for d in enriched:
        cat = d["primary_alert_category"]
        if cat and cat in categories:
            categories[cat]["units"].append(d)
        elif not cat or (d["alerts_count"] or 0) == 0:
            no_fault["units"].append(d)
        else:
            categories.setdefault(cat, {"name": cat, "icon": "⚠️", "units": []})["units"].append(d)

    # Build ordered list, skip empty fault categories
    cat_list = [c for c in categories.values() if c["units"]]
    cat_list.append(no_fault)

    total = len(enriched)
    total_faults = sum(1 for d in enriched if d["primary_alert_category"])

    return render_template("faults_dashboard.html",
        active_page="faults",
        categories=cat_list,
        total=total,
        total_faults=total_faults,
        **_base_ctx()
    )


# ── Unit detail ───────────────────────────────────────────────

@app.route("/unit/<int:unit_id>")
@login_required
def unit_detail(unit_id):
    conn = get_db()
    unit = get_unit(conn, unit_id)
    snapshot = get_latest_snapshot(conn, unit_id)
    alerts = get_unit_alerts(conn, unit_id, limit=50)
    weather = get_unit_weather(conn, unit_id)
    history = get_snapshot_history(conn, unit_id, hours=48)
    conn.close()

    valves   = json.loads(snapshot["valves_json"])   if snapshot and snapshot["valves_json"]   else []
    programs = json.loads(snapshot["programs_json"]) if snapshot and snapshot["programs_json"] else []

    return render_template("unit_detail.html",
        active_page="dashboard",
        unit=unit, snapshot=snapshot,
        valves=valves, programs=programs,
        alerts=alerts, weather=weather, history=history,
        get_alert_description=get_alert_description,
        json=json,
        **_base_ctx()
    )


# ── Alert report (דיווח התראות שוטפות) ───────────────────────

@app.route("/alert-report")
@login_required
def alert_report():
    conn = get_db()
    sched = get_schedule_settings(conn)
    conn.close()
    return render_template("alert_report.html",
        active_page="alert-report",
        report=None, sched=sched,
        **_base_ctx()
    )


@app.route("/alert-report/run", methods=["POST"])
@login_required
def alert_report_run():
    mark_noticed = request.form.get("mark_noticed") == "1"
    send_wa = request.form.get("send_whatsapp") == "1"

    conn = get_db()
    sched = get_schedule_settings(conn)
    try:
        report = generate_alert_report(conn, hours_back=24, mark_noticed=mark_noticed)
        sent_wa = False
        if send_wa:
            phone = sched["whatsapp_number"] if sched else ""
            sent_wa = send_whatsapp_report(report, phone or None)
            if sent_wa:
                flash("הדוח נשלח בהצלחה בוואטסאפ ✅", "success")
            else:
                flash("שליחת וואטסאפ נכשלה - בדוק הגדרות Green API", "error")
        save_report_to_db(conn, report, sent_whatsapp=sent_wa)
    finally:
        conn.close()

    return render_template("alert_report.html",
        active_page="alert-report",
        report=report, sched=sched,
        **_base_ctx()
    )


# ── Reports log ───────────────────────────────────────────────

@app.route("/reports")
@login_required
def reports():
    conn = get_db()
    report_log = get_report_log(conn, limit=50)
    conn.close()
    return render_template("reports.html",
        active_page="reports",
        report_log=report_log,
        **_base_ctx()
    )


@app.route("/reports/<int:report_id>")
@login_required
def report_view(report_id):
    conn = get_db()
    row = get_report_by_id(conn, report_id)
    conn.close()
    if not row:
        flash("דוח לא נמצא", "error")
        return redirect(url_for("reports"))
    return render_template("report_view.html",
        active_page="reports",
        report_id=report_id,
        report_html=row["report_html"] or "",
        **_base_ctx()
    )


# ── Settings ──────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    conn = get_db()

    if request.method == "POST":
        # Save alert type toggles
        active_codes = {int(c) for c in request.form.getlist("active_codes")}
        save_alert_settings(conn, active_codes)

        # Save schedule + smart rules
        active_days = ",".join(request.form.getlist("active_days"))
        save_schedule_settings(conn, {
            "time_1":            request.form.get("time_1", "05:30"),
            "time_2":            request.form.get("time_2", ""),
            "active_days":       active_days or "0,1,2,3,4,5,6",
            "is_enabled":        "is_enabled" in request.form,
            "whatsapp_enabled":  "whatsapp_enabled" in request.form,
            "whatsapp_number":   request.form.get("whatsapp_number", ""),
            "leak_hour_start":   request.form.get("leak_hour_start", "17:00"),
            "leak_hour_end":     request.form.get("leak_hour_end", "05:00"),
            "flow_min_count":    request.form.get("flow_min_count", 3),
            "flow_hours_window": request.form.get("flow_hours_window", 48),
        })
        flash("ההגדרות נשמרו בהצלחה", "success")
        conn.close()
        # Reload scheduler cron jobs with new times (if main.py is running)
        try:
            import main as _main
            _main.reload_schedule()
        except Exception:
            pass
        return redirect(url_for("settings"))

    alert_rows = get_alert_settings(conn)
    sched = get_schedule_settings(conn)
    conn.close()

    # Group alerts by category preserving order
    categories = defaultdict(list)
    cat_order = ["ספיקה וזרימה", "דישון", "סוללה וחשמל", "חומרה",
                 "תקשורת", "חיישנים וכניסות", "מערכת", "מזג אוויר"]
    for row in alert_rows:
        categories[row["category"]].append(row)
    ordered = {cat: categories[cat] for cat in cat_order if cat in categories}
    for cat in categories:
        if cat not in ordered:
            ordered[cat] = categories[cat]

    active_count = sum(1 for r in alert_rows if r["is_active"])

    return render_template("settings.html",
        active_page="settings",
        categories=ordered,
        sched=sched,
        icons=CATEGORY_ICONS,
        active_count=active_count,
        total_count=len(alert_rows),
        **_base_ctx()
    )


# ── Groups ────────────────────────────────────────────────────

@app.route("/groups", methods=["GET", "POST"])
@login_required
def groups():
    conn = get_db()

    if request.method == "POST":
        new_groups = {}
        for key, val in request.form.items():
            if key.startswith("group_") and val.strip():
                uid = int(key.replace("group_", ""))
                new_groups[uid] = val.strip()
        save_unit_groups_bulk(conn, new_groups)
        flash("הקבוצות נשמרו בהצלחה", "success")
        conn.close()
        return redirect(url_for("groups"))

    pids = _get_project_ids()
    units = get_all_units_filtered(conn, pids)
    # Attach model_view from snapshots
    snaps = {s["control_unit_id"]: s for s in get_latest_snapshots_filtered(conn, pids)}
    units_with_model = []
    for u in units:
        uid = u["control_unit_id"]
        snap = snaps.get(uid)
        units_with_model.append({
            "control_unit_id": uid,
            "unit_name": u["unit_name"],
            "model_view": snap["model_view"] if snap else u["model_view"],
            "address": u["address"],
        })
    grp_map = get_unit_groups(conn)
    conn.close()

    return render_template("groups.html",
        active_page="groups",
        units=units_with_model,
        groups=grp_map,
        **_base_ctx()
    )


# ── User management ──────────────────────────────────────────

@app.route("/users", methods=["GET", "POST"])
@login_required
def users_page():
    conn = get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            phone = (request.form.get("phone") or "").strip()
            name  = (request.form.get("name")  or "").strip()
            perm  = request.form.get("permission", "viewer")
            if phone and name:
                create_user(conn, phone, name, perm)
                flash(f"משתמש {name} נוסף בהצלחה", "success")
            else:
                flash("נא למלא שם ומספר טלפון", "error")

        elif action == "edit":
            uid   = int(request.form.get("user_id"))
            name  = (request.form.get("name")  or "").strip()
            perm  = request.form.get("permission", "viewer")
            active = 1 if request.form.get("is_active") else 0
            update_user(conn, uid, name, perm, active)
            flash("המשתמש עודכן בהצלחה", "success")

        elif action == "set_password":
            phone = (request.form.get("phone") or "").strip()
            pwd   = (request.form.get("new_password") or "").strip()
            if phone and len(pwd) >= 6:
                set_password(phone, pwd)
                flash("הסיסמה עודכנה בהצלחה", "success")
            else:
                flash("סיסמה חייבת להכיל לפחות 6 תווים", "error")

        elif action == "set_projects":
            uid = int(request.form.get("user_id"))
            project_ids = [int(p) for p in request.form.getlist("project_ids")]
            set_user_projects(conn, uid, project_ids)
            flash("פרויקטי המשתמש עודכנו בהצלחה", "success")

        elif action == "delete":
            uid = int(request.form.get("user_id"))
            delete_user(conn, uid)
            flash("המשתמש נמחק", "success")

        conn.close()
        return redirect(url_for("users_page"))

    users = get_all_users(conn)
    all_projects = get_all_projects(conn)
    # Build map of user_id → [project_id, ...]
    user_project_map = {}
    for u in users:
        user_project_map[u["id"]] = get_user_project_ids(conn, u["id"])
    conn.close()

    perm_labels = {"admin": "מנהל", "user": "משתמש", "viewer": "צופה בלבד"}

    return render_template("users.html",
        active_page="users",
        users=users,
        permissions=PERMISSIONS,
        perm_labels=perm_labels,
        all_projects=all_projects,
        user_project_map=user_project_map,
        **_base_ctx()
    )


# ── Projects management ───────────────────────────────────────

@app.route("/projects", methods=["GET", "POST"])
@login_required
def projects_page():
    if session.get("permission") != "admin":
        flash("גישה מוגבלת למנהלים בלבד", "error")
        return redirect(url_for("index"))

    conn = get_db()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "add":
            try:
                pid  = int(request.form.get("project_id"))
                name = (request.form.get("project_name") or "").strip()
                if pid and name:
                    upsert_project(conn, pid, name)
                    flash(f"פרויקט {name} נוסף בהצלחה", "success")
                else:
                    flash("נא למלא מזהה ושם פרויקט", "error")
            except (ValueError, TypeError):
                flash("מזהה פרויקט חייב להיות מספר", "error")

        elif action == "edit":
            try:
                pid  = int(request.form.get("project_id"))
                name = (request.form.get("project_name") or "").strip()
                if name:
                    upsert_project(conn, pid, name)
                    flash("הפרויקט עודכן בהצלחה", "success")
            except (ValueError, TypeError):
                flash("שגיאה בעדכון הפרויקט", "error")

        elif action == "delete":
            try:
                pid = int(request.form.get("project_id"))
                delete_project(conn, pid)
                flash("הפרויקט נמחק", "success")
            except (ValueError, TypeError):
                flash("שגיאה במחיקת הפרויקט", "error")

        conn.close()
        return redirect(url_for("projects_page"))

    all_projects = get_all_projects(conn)
    unit_counts  = get_unit_count_per_project(conn)
    conn.close()

    return render_template("projects.html",
        active_page="projects",
        projects=all_projects,
        unit_counts=unit_counts,
        **_base_ctx()
    )


# ── Stub pages ────────────────────────────────────────────────

@app.route("/fertilization")
@login_required
def fertilization():
    return render_template("stub.html", active_page="fertilization",
        title="מדשנות", icon="🌿", **_base_ctx())

@app.route("/irrigation")
@login_required
def irrigation():
    return render_template("stub.html", active_page="irrigation",
        title="תכנון השקיה", icon="💧", **_base_ctx())

@app.route("/shutdowns")
@login_required
def shutdowns():
    return render_template("stub.html", active_page="shutdowns",
        title="השבתות", icon="🔒", **_base_ctx())


# ── Legacy alerts page ────────────────────────────────────────

@app.route("/alerts")
@login_required
def alerts_page():
    conn = get_db()
    gsi_alerts = get_recent_alerts(conn, limit=200)
    system_alerts = get_system_alerts(conn, limit=200)
    conn.close()
    return render_template("alerts.html",
        active_page="dashboard",
        gsi_alerts=gsi_alerts,
        system_alerts=system_alerts,
        get_alert_description=get_alert_description,
        **_base_ctx()
    )


# ── JSON APIs ─────────────────────────────────────────────────

@app.route("/api/snapshots")
@login_required
def api_snapshots():
    conn = get_db()
    snapshots = get_latest_snapshots_filtered(conn, _get_project_ids())
    conn.close()
    return jsonify([dict(s) for s in snapshots])


@app.route("/api/unit/<int:unit_id>/history")
@login_required
def api_unit_history(unit_id):
    hours = request.args.get("hours", 48, type=int)
    conn = get_db()
    history = get_snapshot_history(conn, unit_id, hours)
    conn.close()
    return jsonify([dict(h) for h in history])


@app.route("/api/alerts/acknowledge/<int:alert_id>", methods=["POST"])
@login_required
def acknowledge_alert(alert_id):
    conn = get_db()
    conn.execute("UPDATE system_alerts SET acknowledged=1 WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/unit/<int:unit_id>/dismiss-alerts", methods=["POST"])
@login_required
def dismiss_alerts(unit_id):
    """Remove unit from active_alerts (user confirmed fault is closed)."""
    conn = get_db()
    dismiss_active_alert(conn, unit_id)
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/gsi/projects")
@login_required
def api_gsi_projects():
    """Fetch all available GSI projects for the current user (live from GSI API)."""
    try:
        from gsi_client import GSIClient
        client = GSIClient()
        projects = client.get_user_projects()
        return jsonify({"ok": True, "projects": projects})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/project/add", methods=["POST"])
@login_required
def api_project_add():
    """Add a project to monitoring: saves to DB and triggers an immediate collection."""
    if session.get("permission") not in ("admin", "user"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    data = request.get_json(force=True, silent=True) or {}
    pid  = data.get("project_id")
    name = (data.get("project_name") or "").strip()

    if not pid or not isinstance(pid, int):
        return jsonify({"ok": False, "error": "project_id required"}), 400

    try:
        conn = get_db()
        upsert_project(conn, pid, name or f"פרויקט {pid}")
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # Trigger collection for the new project in background
    def _collect_new():
        from collector import DataCollector
        try:
            dc = DataCollector(project_id=pid)
            dc.collect_all()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(f"Background collect for project {pid} failed: {exc}")

    import threading
    threading.Thread(target=_collect_new, daemon=True).start()

    return jsonify({"ok": True, "project_id": pid, "project_name": name})


@app.route("/api/project/remove", methods=["POST"])
@login_required
def api_project_remove():
    """Remove a project from monitoring (does not delete collected data)."""
    if session.get("permission") != "admin":
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    data = request.get_json(force=True, silent=True) or {}
    pid = data.get("project_id")
    if not pid:
        return jsonify({"ok": False, "error": "project_id required"}), 400

    from config import GSI_PROJECT_IDS
    if int(pid) in GSI_PROJECT_IDS:
        return jsonify({"ok": False, "error": "לא ניתן להסיר את הפרויקט הראשי"}), 400

    try:
        conn = get_db()
        delete_project(conn, int(pid))
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


@app.route("/api/process/run", methods=["POST"])
@login_required
def api_process_run():
    """Manual trigger of full process via API."""
    from process import run_full_process
    try:
        report = run_full_process(send_whatsapp_override=False)
        return jsonify({"ok": True, "total_alerts": report["total_alerts"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/map")
@login_required
def map_view():
    """Interactive map showing all controllers as coloured markers."""
    return render_template("map.html", active_page="map", **_base_ctx())


@app.route("/api/map/units")
@login_required
def api_map_units():
    """Return JSON list of all active units with coordinates, status, and alert info."""
    conn = get_db()
    pids = _get_project_ids()
    primary_alerts = get_primary_active_alerts_per_unit(conn, pids)

    if pids is None:
        rows = conn.execute("""
            SELECT u.control_unit_id, u.unit_name, u.latitude, u.longitude, u.address,
                   s.connection_status, s.device_alarm, s.controller_state, s.captured_at,
                   s.signal_strength
            FROM units u
            LEFT JOIN unit_snapshots s ON s.id = (
                SELECT MAX(id) FROM unit_snapshots WHERE control_unit_id=u.control_unit_id
            )
            WHERE u.is_active=1
            ORDER BY u.unit_name
        """).fetchall()
    else:
        if not pids:
            conn.close()
            return jsonify([])
        placeholders = ",".join("?" * len(pids))
        rows = conn.execute(f"""
            SELECT u.control_unit_id, u.unit_name, u.latitude, u.longitude, u.address,
                   s.connection_status, s.device_alarm, s.controller_state, s.captured_at,
                   s.signal_strength
            FROM units u
            LEFT JOIN unit_snapshots s ON s.id = (
                SELECT MAX(id) FROM unit_snapshots WHERE control_unit_id=u.control_unit_id
            )
            WHERE u.is_active=1 AND u.project_id IN ({placeholders})
            ORDER BY u.unit_name
        """, pids).fetchall()
    conn.close()

    units = []
    for r in rows:
        d = dict(r)
        uid = d["control_unit_id"]
        alert_code = primary_alerts.get(uid)
        conn_ok = d.get("connection_status") == 1
        has_alarm = (d.get("device_alarm") or 0) != 0

        if not conn_ok:
            status = "disconnected"
            color = "orange"
        elif has_alarm or alert_code is not None:
            status = "fault"
            color = "red"
        else:
            status = "ok"
            color = "green"

        units.append({
            "id": uid,
            "name": d["unit_name"] or f"Unit {uid}",
            "lat": d["latitude"],
            "lon": d["longitude"],
            "address": d["address"] or "",
            "connection_status": d.get("connection_status"),
            "device_alarm": d.get("device_alarm") or 0,
            "captured_at": d.get("captured_at") or "",
            "signal_strength": d.get("signal_strength") or "",
            "alert_code": alert_code,
            "alert_name": get_alert_description(alert_code) if alert_code is not None else None,
            "alert_category": get_alert_category(alert_code) if alert_code is not None else None,
            "status": status,
            "color": color,
        })

    return jsonify(units)


@app.route("/api/debug/info")
@login_required
def api_debug_info():
    """Debug endpoint — returns session state + unit counts."""
    conn = get_db()
    pids = _get_project_ids()
    snaps = get_latest_snapshots_filtered(conn, pids)
    active_alerts = get_primary_active_alerts_per_unit(conn, pids)

    units_breakdown = conn.execute("""
        SELECT is_active, project_id, COUNT(*) as cnt
        FROM units GROUP BY is_active, project_id ORDER BY is_active DESC
    """).fetchall()

    conn.close()
    return jsonify({
        "session": {
            "authenticated": session.get("authenticated"),
            "permission": session.get("permission"),
            "phone": session.get("phone"),
            "user_name": session.get("user_name"),
        },
        "project_ids": pids,
        "snapshot_count": len(snaps),
        "active_alert_units": len(active_alerts),
        "units_breakdown": [dict(r) for r in units_breakdown],
    })
