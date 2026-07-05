import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS units (
            control_unit_id INTEGER PRIMARY KEY,
            unit_name TEXT,
            serial_number TEXT,
            model TEXT,
            model_view TEXT,
            model_type TEXT,
            valve_num INTEGER,
            comm_type TEXT,
            address TEXT,
            description TEXT,
            phone TEXT,
            latitude REAL,
            longitude REAL,
            firmware_version TEXT,
            server_version TEXT,
            bootloader_version TEXT,
            season_start TEXT,
            season_end TEXT,
            expired INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS unit_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            control_unit_id INTEGER REFERENCES units(control_unit_id),
            connection_status INTEGER,
            controller_state INTEGER,
            signal_strength TEXT,
            water_factor INTEGER,
            input_state INTEGER,
            output_state INTEGER,
            device_alarm INTEGER,
            is_sync INTEGER,
            alerts_count INTEGER DEFAULT 0,
            valves_json TEXT,
            programs_json TEXT,
            raw_json TEXT,
            captured_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY,
            control_unit_id INTEGER REFERENCES units(control_unit_id),
            alert_code INTEGER,
            record_date TEXT,
            received_date TEXT,
            station_name TEXT,
            station_number INTEGER,
            program_name TEXT,
            program_number INTEGER,
            actual_flow REAL,
            nominal_flow REAL,
            water_quantity REAL,
            message TEXT,
            unit_name TEXT,
            noticed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS weather (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            control_unit_id INTEGER REFERENCES units(control_unit_id),
            forecast_date TEXT,
            description TEXT,
            temp_min_c REAL,
            temp_max_c REAL,
            humidity_avg REAL,
            wind_speed REAL,
            precipitation_mm REAL,
            rain_chance REAL,
            captured_at TEXT
        );

        CREATE TABLE IF NOT EXISTS system_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            control_unit_id INTEGER,
            unit_name TEXT,
            alert_type TEXT,
            message TEXT,
            severity TEXT,
            acknowledged INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_unit_time
            ON unit_snapshots(control_unit_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_alerts_unit_date
            ON alerts(control_unit_id, record_date);
        CREATE INDEX IF NOT EXISTS idx_system_alerts_time
            ON system_alerts(created_at);
        CREATE INDEX IF NOT EXISTS idx_weather_unit_date
            ON weather(control_unit_id, forecast_date);

        CREATE TABLE IF NOT EXISTS unit_battery_stats (
            control_unit_id INTEGER PRIMARY KEY REFERENCES units(control_unit_id),
            noon_avg REAL,
            midnight_avg REAL,
            noon_latest REAL,
            midnight_latest REAL,
            days_sampled INTEGER,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_settings (
            alert_code INTEGER PRIMARY KEY,
            alert_name TEXT,
            category TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS unit_groups (
            control_unit_id INTEGER PRIMARY KEY,
            group_name TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS schedule_settings (
            id INTEGER DEFAULT 1 PRIMARY KEY,
            time_1 TEXT DEFAULT '05:30',
            time_2 TEXT DEFAULT '',
            active_days TEXT DEFAULT '0,1,2,3,4,5,6',
            is_enabled INTEGER DEFAULT 1,
            whatsapp_enabled INTEGER DEFAULT 0,
            whatsapp_number TEXT DEFAULT '',
            leak_hour_start TEXT DEFAULT '17:00',
            leak_hour_end TEXT DEFAULT '05:00',
            flow_min_count INTEGER DEFAULT 3,
            flow_hours_window INTEGER DEFAULT 48,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS report_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT,
            alert_count INTEGER,
            unit_count INTEGER,
            sent_whatsapp INTEGER DEFAULT 0,
            report_html TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            permission TEXT DEFAULT 'viewer',
            last_login TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS projects (
            project_id   INTEGER PRIMARY KEY,
            project_name TEXT NOT NULL,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_projects (
            user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(project_id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, project_id)
        );

        CREATE TABLE IF NOT EXISTS active_alerts (
            project_id      INTEGER NOT NULL,
            control_unit_id INTEGER NOT NULL,
            alert_code      INTEGER NOT NULL,
            message         TEXT,
            record_date     TEXT,
            updated_at      TEXT,
            PRIMARY KEY (project_id, control_unit_id, alert_code)
        );
    """)
    conn.commit()

    # Migrations: add columns that may not exist in older DBs
    migrations = [
        "ALTER TABLE units ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE units ADD COLUMN project_id INTEGER",
        "ALTER TABLE users ADD COLUMN password_hash TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # Column already exists

    conn.close()
    # Seed lookup tables (defined later in this file)
    _seed_defaults()


def _seed_defaults():
    from config import ALLOWED_PHONES
    conn = get_db()
    init_alert_settings(conn)
    init_schedule_settings(conn)
    _seed_projects(conn)
    # Seed first admin from config if users table is empty
    if ALLOWED_PHONES:
        seed_initial_user(conn, ALLOWED_PHONES[0], "מנהל")
    conn.close()


def _seed_projects(conn):
    """Auto-register projects from config if they don't exist yet."""
    from config import GSI_PROJECT_IDS
    for pid in GSI_PROJECT_IDS:
        conn.execute("""
            INSERT OR IGNORE INTO projects (project_id, project_name)
            VALUES (?, ?)
        """, (pid, f"פרויקט {pid}"))
    conn.commit()


def upsert_unit(conn, unit_data, project_id=None):
    config = unit_data.get("Config", {})
    conn.execute("""
        INSERT INTO units (control_unit_id, unit_name, serial_number, model, model_view,
            valve_num, comm_type, address, description, phone, latitude, longitude,
            firmware_version, server_version, bootloader_version,
            season_start, season_end, updated_at, is_active, project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(control_unit_id) DO UPDATE SET
            unit_name=excluded.unit_name, serial_number=excluded.serial_number,
            model=excluded.model, model_view=excluded.model_view,
            valve_num=excluded.valve_num, comm_type=excluded.comm_type,
            address=excluded.address, description=excluded.description,
            phone=excluded.phone, latitude=excluded.latitude, longitude=excluded.longitude,
            firmware_version=excluded.firmware_version, server_version=excluded.server_version,
            bootloader_version=excluded.bootloader_version,
            season_start=excluded.season_start, season_end=excluded.season_end,
            updated_at=excluded.updated_at, is_active=1,
            project_id=COALESCE(excluded.project_id, project_id)
    """, (
        config.get("ControlUnitID"),
        config.get("UnitName"),
        config.get("SN"),
        config.get("ModelVersion"),
        config.get("ModelView"),
        config.get("ValveNum"),
        config.get("CommTypeID"),
        config.get("Address"),
        config.get("Description"),
        config.get("Phone"),
        config.get("Map_Latitude"),
        config.get("Map_Longitude"),
        config.get("CommUnit_AppVersion"),
        None,  # server_version comes from dashboard table
        config.get("BootLoaderVersion"),
        config.get("SeasonStartDate"),
        config.get("SeasonEndDate"),
        datetime.now().isoformat(),
        project_id
    ))


def insert_snapshot(conn, unit_id, unit_data):
    config = unit_data.get("Config", {})
    valves = unit_data.get("Valves", [])
    programs = unit_data.get("Programs", [])
    now = datetime.now().isoformat()

    conn.execute("""
        INSERT INTO unit_snapshots (control_unit_id, connection_status, controller_state,
            signal_strength, water_factor, input_state, output_state, device_alarm,
            is_sync, valves_json, programs_json, raw_json, captured_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        unit_id,
        1 if config.get("ConnectionStatus") else 0,
        config.get("ControllerState"),
        config.get("Signal"),
        config.get("WaterFactor"),
        config.get("InputsState"),
        config.get("OutputState"),
        config.get("DeviceAlarm"),
        1 if config.get("IsSync") else 0,
        json.dumps(valves, ensure_ascii=False),
        json.dumps(programs, ensure_ascii=False),
        json.dumps(unit_data, ensure_ascii=False),
        now
    ))


def insert_alerts(conn, unit_id, alerts_list):
    for alert in alerts_list:
        conn.execute("""
            INSERT OR IGNORE INTO alerts (id, control_unit_id, alert_code, record_date,
                received_date, station_name, station_number, program_name, program_number,
                actual_flow, nominal_flow, water_quantity, message, unit_name, noticed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert.get("ID"),
            unit_id,
            alert.get("AlertCode"),
            alert.get("RecordDate"),
            alert.get("ReceivedDate"),
            alert.get("StationName"),
            alert.get("StationNumber"),
            alert.get("ProgramName"),
            alert.get("ProgramNumber"),
            alert.get("ActualFlow"),
            alert.get("NominalFlow"),
            alert.get("WaterQuant"),
            alert.get("Message"),
            alert.get("UnitName"),
            1 if alert.get("Noticed") else 0
        ))


def insert_weather(conn, unit_id, weather_data):
    forecasts = weather_data.get("Body", {}).get("forecastList") or []
    now = datetime.now().isoformat()
    for fc in forecasts:
        conn.execute("""
            INSERT INTO weather (control_unit_id, forecast_date, description,
                temp_min_c, temp_max_c, humidity_avg, wind_speed, precipitation_mm,
                rain_chance, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            unit_id,
            fc.get("date"),
            fc.get("description"),
            fc.get("Temp_Celsius", {}).get("Min"),
            fc.get("Temp_Celsius", {}).get("Max"),
            fc.get("Humidity", {}).get("Avg"),
            fc.get("WindSpeed", {}).get("Avg"),
            fc.get("Prec_mm", {}).get("Avg"),
            fc.get("Prec_Percent", {}).get("Avg"),
            now
        ))


def insert_system_alert(conn, unit_id, unit_name, alert_type, message, severity="warning"):
    conn.execute("""
        INSERT INTO system_alerts (control_unit_id, unit_name, alert_type, message, severity)
        VALUES (?, ?, ?, ?, ?)
    """, (unit_id, unit_name, alert_type, message, severity))


def get_all_units(conn):
    return conn.execute("SELECT * FROM units ORDER BY unit_name").fetchall()


def get_unit(conn, unit_id):
    return conn.execute("SELECT * FROM units WHERE control_unit_id = ?", (unit_id,)).fetchone()


def get_latest_snapshot(conn, unit_id):
    return conn.execute("""
        SELECT * FROM unit_snapshots WHERE control_unit_id = ?
        ORDER BY captured_at DESC LIMIT 1
    """, (unit_id,)).fetchone()


def get_latest_snapshots_all(conn):
    return conn.execute("""
        SELECT s.*, u.unit_name, u.model_view, u.serial_number, u.address,
               u.comm_type, u.firmware_version, u.latitude, u.longitude,
               u.project_id, u.valve_num
        FROM unit_snapshots s
        JOIN units u ON s.control_unit_id = u.control_unit_id
        WHERE s.id IN (
            SELECT MAX(id) FROM unit_snapshots GROUP BY control_unit_id
        )
        AND u.is_active = 1
        ORDER BY u.unit_name
    """).fetchall()


def get_latest_snapshots_filtered(conn, project_ids=None):
    """Return latest snapshots filtered by project_ids list. None = all projects (admin)."""
    if project_ids is None:
        return get_latest_snapshots_all(conn)
    if not project_ids:
        return []
    placeholders = ",".join("?" * len(project_ids))
    return conn.execute(f"""
        SELECT s.*, u.unit_name, u.model_view, u.serial_number, u.address,
               u.comm_type, u.firmware_version, u.latitude, u.longitude,
               u.project_id, u.valve_num
        FROM unit_snapshots s
        JOIN units u ON s.control_unit_id = u.control_unit_id
        WHERE s.id IN (
            SELECT MAX(id) FROM unit_snapshots GROUP BY control_unit_id
        )
        AND u.is_active = 1
        AND u.project_id IN ({placeholders})
        ORDER BY u.unit_name
    """, project_ids).fetchall()


def get_all_units_filtered(conn, project_ids=None):
    """Return units filtered by project_ids list. None = all (admin)."""
    if project_ids is None:
        return get_all_units(conn)
    if not project_ids:
        return []
    placeholders = ",".join("?" * len(project_ids))
    return conn.execute(
        f"SELECT * FROM units WHERE project_id IN ({placeholders}) ORDER BY unit_name",
        project_ids
    ).fetchall()


# Alert codes that indicate a fault has been RESOLVED — never display as active fault
RESOLUTION_ALERT_CODES = {5, 24, 33, 41, 71}


def get_primary_alerts_per_unit(conn, days=30):
    """Return dict of {unit_id: alert_code} — lowest (highest priority) OPEN code
    per unit, from the last N days. Excludes noticed alerts and resolution codes."""
    rows = conn.execute("""
        SELECT a.control_unit_id, MIN(a.alert_code) AS alert_code
        FROM alerts a
        INNER JOIN (
            SELECT control_unit_id, MAX(created_at) AS max_created
            FROM alerts
            WHERE record_date >= datetime('now', '-' || ? || ' days')
              AND noticed = 0
            GROUP BY control_unit_id
        ) latest ON a.control_unit_id = latest.control_unit_id
                 AND a.created_at = latest.max_created
        WHERE a.record_date >= datetime('now', '-' || ? || ' days')
          AND a.noticed = 0
        GROUP BY a.control_unit_id
    """, (days, days)).fetchall()
    # Filter out units whose only active code is a resolution code
    result = {}
    for r in rows:
        code = r["alert_code"]
        if code not in RESOLUTION_ALERT_CODES:
            result[r["control_unit_id"]] = code
    return result


def dismiss_unit_alerts(conn, unit_id):
    """Mark all unnoticed alerts for a unit as noticed (dismiss active faults)."""
    conn.execute(
        "UPDATE alerts SET noticed=1 WHERE control_unit_id=? AND noticed=0",
        (unit_id,)
    )
    conn.commit()


def get_unit_alerts(conn, unit_id, limit=50):
    return conn.execute("""
        SELECT * FROM alerts WHERE control_unit_id = ?
        ORDER BY record_date DESC LIMIT ?
    """, (unit_id, limit)).fetchall()


def get_recent_alerts(conn, limit=100):
    return conn.execute("""
        SELECT * FROM alerts ORDER BY record_date DESC LIMIT ?
    """, (limit,)).fetchall()


def get_system_alerts(conn, limit=100, unacknowledged_only=False):
    if unacknowledged_only:
        return conn.execute("""
            SELECT * FROM system_alerts WHERE acknowledged = 0
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    return conn.execute("""
        SELECT * FROM system_alerts ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()


def get_unit_weather(conn, unit_id):
    return conn.execute("""
        SELECT * FROM weather WHERE control_unit_id = ?
        ORDER BY captured_at DESC, forecast_date ASC LIMIT 5
    """, (unit_id,)).fetchall()


def get_snapshot_history(conn, unit_id, hours=48):
    return conn.execute("""
        SELECT * FROM unit_snapshots WHERE control_unit_id = ?
        AND captured_at >= datetime('now', '-' || ? || ' hours')
        ORDER BY captured_at ASC
    """, (unit_id, hours)).fetchall()


# ── Alert settings ────────────────────────────────────────────

ALL_ALERT_CODES = [
    # ספיקה וזרימה
    (1,  "ספיקה נמוכה",                         "ספיקה וזרימה", 1),
    (2,  "ספיקה גבוהה",                         "ספיקה וזרימה", 1),
    (3,  "אי זרימת מים",                        "ספיקה וזרימה", 1),
    (4,  "דליפת מים - התחלה",                   "ספיקה וזרימה", 1),
    (5,  "דליפת מים - סיום",                    "ספיקה וזרימה", 1),
    (6,  "שטיפת מסנן מתמשכת",                   "ספיקה וזרימה", 0),
    # דישון
    (10, "אי זרימת דשן",                        "דישון", 1),
    (11, "השהיית יחידה עקב דשן לא מבוקר",      "דישון", 1),
    (12, "התראת מרכז דישון",                    "דישון", 1),
    (13, "דישון לא הסתיים",                     "דישון", 1),
    (14, "התראת EC",                            "דישון", 1),
    (15, "התראת pH",                            "דישון", 1),
    (16, "התראת EC קיצוני",                     "דישון", 0),
    (17, "התראת pH קיצוני",                     "דישון", 0),
    # סוללה וחשמל
    (20, "מתח סוללה נמוך",                      "סוללה וחשמל", 1),
    (21, "סוללה ריקה",                          "סוללה וחשמל", 1),
    (22, "תקלת טעינת קבל",                      "סוללה וחשמל", 1),
    (23, "אין מתח AC",                          "סוללה וחשמל", 0),
    (24, "חזר מתח AC",                          "סוללה וחשמל", 0),
    # חומרה
    (30, "סולנואיד/חיווט",                      "חומרה", 1),
    (31, "קצר",                                 "חומרה", 1),
    (32, "שגיאת זיכרון בקר",                    "חומרה", 1),
    (33, "מגופים תקינים",                       "חומרה", 1),
    # תקשורת
    (40, "שגיאת תקשורת - קוד 17",               "תקשורת", 1),
    (41, "תקשורת חזרה",                         "תקשורת", 1),
    (42, "אין תגובה",                           "תקשורת", 1),
    (43, "תקלת תקשורת - שרת",                   "תקשורת", 0),
    # חיישנים וכניסות
    (50, "התראת חיישן",                         "חיישנים וכניסות", 1),
    (51, "כניסה כבתה",                          "חיישנים וכניסות", 1),
    (52, "כניסה הופעלה",                        "חיישנים וכניסות", 1),
    (53, "התראה מתנאי לוגי",                    "חיישנים וכניסות", 1),
    # מערכת
    (60, "שגיאה בכתיבת יומנים והתראות",         "מערכת", 1),
    (61, "סנכרון זמן בבקר נכשל",               "מערכת", 1),
    (62, "תכנות שגוי",                          "מערכת", 1),
    (63, "שגיאה בהגדרת מגוף",                   "מערכת", 1),
    (64, "שגיאה בכתיבת CNF",                   "מערכת", 1),
    (65, "הורדת גירסת בקר מרחוק נכשל",         "מערכת", 1),
    # מזג אוויר
    (70, "יחידה מושהית עקב סיכוי לגשם",        "מזג אוויר", 1),
    (71, "יחידה פעילה עקב אי סיכוי לגשם",      "מזג אוויר", 1),
]


def init_alert_settings(conn):
    """Seed alert_settings table if empty."""
    count = conn.execute("SELECT COUNT(*) FROM alert_settings").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_settings (alert_code, alert_name, category, is_active) VALUES (?,?,?,?)",
            ALL_ALERT_CODES
        )
        conn.commit()


def init_schedule_settings(conn):
    """Ensure a single schedule_settings row exists."""
    conn.execute(
        "INSERT OR IGNORE INTO schedule_settings (id) VALUES (1)"
    )
    conn.commit()


def get_alert_settings(conn):
    return conn.execute(
        "SELECT * FROM alert_settings ORDER BY alert_code"
    ).fetchall()


def get_active_alert_codes(conn):
    rows = conn.execute(
        "SELECT alert_code FROM alert_settings WHERE is_active=1"
    ).fetchall()
    return {r["alert_code"] for r in rows}


def save_alert_settings(conn, active_codes: set):
    conn.execute("UPDATE alert_settings SET is_active=0")
    if active_codes:
        conn.executemany(
            "UPDATE alert_settings SET is_active=1 WHERE alert_code=?",
            [(c,) for c in active_codes]
        )
    conn.commit()


def get_schedule_settings(conn):
    return conn.execute(
        "SELECT * FROM schedule_settings WHERE id=1"
    ).fetchone()


def save_schedule_settings(conn, data: dict):
    conn.execute("""
        UPDATE schedule_settings SET
            time_1=?, time_2=?, active_days=?, is_enabled=?,
            whatsapp_enabled=?, whatsapp_number=?,
            leak_hour_start=?, leak_hour_end=?,
            flow_min_count=?, flow_hours_window=?,
            updated_at=?
        WHERE id=1
    """, (
        data.get("time_1", "05:30"),
        data.get("time_2", ""),
        data.get("active_days", "0,1,2,3,4,5,6"),
        1 if data.get("is_enabled") else 0,
        1 if data.get("whatsapp_enabled") else 0,
        data.get("whatsapp_number", ""),
        data.get("leak_hour_start", "17:00"),
        data.get("leak_hour_end", "05:00"),
        int(data.get("flow_min_count", 3)),
        int(data.get("flow_hours_window", 48)),
        datetime.now().isoformat()
    ))
    conn.commit()


# ── Unit groups ───────────────────────────────────────────────

def get_unit_groups(conn):
    """Return dict: control_unit_id -> group_name."""
    rows = conn.execute("SELECT control_unit_id, group_name FROM unit_groups").fetchall()
    return {r["control_unit_id"]: r["group_name"] for r in rows}


def save_unit_group(conn, unit_id, group_name):
    conn.execute("""
        INSERT INTO unit_groups (control_unit_id, group_name, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(control_unit_id) DO UPDATE SET
            group_name=excluded.group_name, updated_at=excluded.updated_at
    """, (unit_id, group_name.strip(), datetime.now().isoformat()))


def save_unit_groups_bulk(conn, groups: dict):
    """groups: {unit_id: group_name}"""
    now = datetime.now().isoformat()
    conn.executemany("""
        INSERT INTO unit_groups (control_unit_id, group_name, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(control_unit_id) DO UPDATE SET
            group_name=excluded.group_name, updated_at=excluded.updated_at
    """, [(uid, name.strip(), now) for uid, name in groups.items()])
    conn.commit()


# ── Report log ────────────────────────────────────────────────

def save_report(conn, alert_count, unit_count, sent_whatsapp, report_html):
    cursor = conn.execute("""
        INSERT INTO report_log (generated_at, alert_count, unit_count, sent_whatsapp, report_html)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), alert_count, unit_count,
          1 if sent_whatsapp else 0, report_html))
    conn.commit()
    return cursor.lastrowid


def get_report_log(conn, limit=50):
    return conn.execute("""
        SELECT id, generated_at, alert_count, unit_count, sent_whatsapp
        FROM report_log ORDER BY generated_at DESC LIMIT ?
    """, (limit,)).fetchall()


def get_report_by_id(conn, report_id):
    return conn.execute(
        "SELECT * FROM report_log WHERE id=?", (report_id,)
    ).fetchone()


# ── Battery stats ─────────────────────────────────────────────

def upsert_battery_stats(conn, unit_id, stats: dict):
    """Insert or update battery stats for a unit."""
    from datetime import datetime
    conn.execute("""
        INSERT INTO unit_battery_stats
            (control_unit_id, noon_avg, midnight_avg, noon_latest, midnight_latest, days_sampled, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(control_unit_id) DO UPDATE SET
            noon_avg=excluded.noon_avg,
            midnight_avg=excluded.midnight_avg,
            noon_latest=excluded.noon_latest,
            midnight_latest=excluded.midnight_latest,
            days_sampled=excluded.days_sampled,
            updated_at=excluded.updated_at
    """, (unit_id, stats.get("noon_avg"), stats.get("midnight_avg"),
          stats.get("noon_latest"), stats.get("midnight_latest"),
          stats.get("days_sampled"), datetime.now().isoformat()))
    conn.commit()


def get_all_battery_stats(conn):
    """Return dict of {unit_id: battery_stats_row} for all units."""
    rows = conn.execute("SELECT * FROM unit_battery_stats").fetchall()
    return {r["control_unit_id"]: dict(r) for r in rows}


# ── User management ──────────────────────────────────────────

PERMISSIONS = ["admin", "user", "viewer"]

def get_all_users(conn):
    return conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()

def get_user_by_phone(conn, phone):
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("972") and len(digits) == 12:
        digits = "0" + digits[3:]
    return conn.execute("SELECT * FROM users WHERE phone=? AND is_active=1", (digits,)).fetchone()

def create_user(conn, phone, name, permission="viewer"):
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("972") and len(digits) == 12:
        digits = "0" + digits[3:]
    conn.execute(
        "INSERT OR IGNORE INTO users (phone, name, permission, created_at) VALUES (?,?,?,?)",
        (digits, name, permission, datetime.now().isoformat())
    )
    conn.commit()

def update_user(conn, user_id, name, permission, is_active):
    conn.execute(
        "UPDATE users SET name=?, permission=?, is_active=? WHERE id=?",
        (name, permission, is_active, user_id)
    )
    conn.commit()

def delete_user(conn, user_id):
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()

def update_last_login(conn, phone):
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("972") and len(digits) == 12:
        digits = "0" + digits[3:]
    conn.execute(
        "UPDATE users SET last_login=? WHERE phone=?",
        (datetime.now().isoformat(), digits)
    )
    conn.commit()

def seed_initial_user(conn, phone, name="מנהל"):
    """Seed first admin user if table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        create_user(conn, phone, name, "admin")


# ── Project management ────────────────────────────────────────

def get_all_projects(conn):
    return conn.execute("SELECT * FROM projects ORDER BY project_name").fetchall()

def upsert_project(conn, project_id, project_name, is_active=1):
    conn.execute("""
        INSERT INTO projects (project_id, project_name, is_active)
        VALUES (?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            project_name=excluded.project_name,
            is_active=excluded.is_active
    """, (project_id, project_name, is_active))
    conn.commit()

def delete_project(conn, project_id):
    conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))
    conn.commit()

def get_user_project_ids(conn, user_id):
    """Return list of project_ids assigned to a user."""
    rows = conn.execute(
        "SELECT project_id FROM user_projects WHERE user_id=?", (user_id,)
    ).fetchall()
    return [r["project_id"] for r in rows]

def set_user_projects(conn, user_id, project_ids):
    """Replace all project assignments for a user."""
    conn.execute("DELETE FROM user_projects WHERE user_id=?", (user_id,))
    if project_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO user_projects (user_id, project_id) VALUES (?, ?)",
            [(user_id, pid) for pid in project_ids]
        )
    conn.commit()

def get_unit_count_per_project(conn):
    """Return dict {project_id: active_unit_count}."""
    rows = conn.execute("""
        SELECT project_id, COUNT(*) as cnt
        FROM units WHERE is_active=1 AND project_id IS NOT NULL
        GROUP BY project_id
    """).fetchall()
    return {r["project_id"]: r["cnt"] for r in rows}


# ── Active alerts (live from CachAlerts API) ──────────────────

def replace_active_alerts(conn, project_id, alerts_list):
    """Replace all active alerts for a project with a fresh list (from API or rebuild)."""
    conn.execute("DELETE FROM active_alerts WHERE project_id=?", (project_id,))
    now = datetime.now().isoformat()
    for a in alerts_list:
        unit_id = a.get("ControlUnitID") or a.get("controlUnitId") or a.get("UnitId")
        code    = a.get("AlertCode") or a.get("alertCode") or a.get("Code")
        if not unit_id or not code:
            continue
        conn.execute("""
            INSERT OR REPLACE INTO active_alerts
                (project_id, control_unit_id, alert_code, message, record_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            project_id, unit_id, code,
            a.get("Message") or a.get("message"),
            a.get("RecordDate") or a.get("recordDate"),
            now
        ))
    conn.commit()




# Alert codes that OPEN a persistent fault and stay active until a specific closing code.
# All other codes are "transient" events — they don't need a resolution.
# Mapping: opening_code → closing_code
PERSISTENT_FAULT_PAIRS = {
    4:  5,   # דליפת מים - התחלה → סיום
    23: 24,  # אין מתח AC → חזר מתח AC
    30: 33,  # סולנואיד/חיווט → מגופים תקינים
    31: 33,  # קצר → מגופים תקינים
    40: 41,  # שגיאת תקשורת → תקשורת חזרה
    70: 71,  # יחידה מושהית עקב גשם → יחידה פעילה
}


def rebuild_active_alerts_from_snapshots(conn, project_id):
    """
    Rebuild active_alerts using two real-time sources from the latest snapshot:
      1. Units with device_alarm != 0  (hardware alarm bitmask — most reliable)
      2. Units currently disconnected (connection_status=0)
    Both sources reflect the unit's state RIGHT NOW, not historical inference.
    Falls back to generic code 0 when no alert record exists.
    """
    now = datetime.now().isoformat()
    conn.execute("DELETE FROM active_alerts WHERE project_id=?", (project_id,))

    res_codes = ",".join(str(c) for c in RESOLUTION_ALERT_CODES)

    candidate_ids = set()

    # Source 1: device_alarm != 0  (hardware alarm bitmask — most reliable)
    alarmed = conn.execute("""
        SELECT s.control_unit_id
        FROM unit_snapshots s
        INNER JOIN (
            SELECT control_unit_id, MAX(id) AS max_id
            FROM unit_snapshots GROUP BY control_unit_id
        ) latest ON s.id = latest.max_id
        JOIN units u ON s.control_unit_id = u.control_unit_id
        WHERE u.is_active=1 AND u.project_id=?
          AND s.device_alarm IS NOT NULL AND s.device_alarm != 0
    """, (project_id,)).fetchall()
    for r in alarmed:
        candidate_ids.add(r["control_unit_id"])

    # Source 2: disconnected units (connection_status=0)
    disconnected = conn.execute("""
        SELECT s.control_unit_id
        FROM unit_snapshots s
        INNER JOIN (
            SELECT control_unit_id, MAX(id) AS max_id
            FROM unit_snapshots GROUP BY control_unit_id
        ) latest ON s.id = latest.max_id
        JOIN units u ON s.control_unit_id = u.control_unit_id
        WHERE u.is_active=1 AND u.project_id=?
          AND s.connection_status = 0
    """, (project_id,)).fetchall()
    for r in disconnected:
        candidate_ids.add(r["control_unit_id"])

    # For each candidate, pick the best alert code to display
    for uid in candidate_ids:
        # 1st: most recent enabled, non-resolution alert in last 90 days
        alert_row = conn.execute(f"""
            SELECT a.alert_code, a.message, a.record_date
            FROM alerts a
            JOIN alert_settings s ON a.alert_code = s.alert_code
            WHERE a.control_unit_id = ?
              AND a.alert_code NOT IN ({res_codes})
              AND s.is_active = 1
              AND a.record_date >= datetime('now', '-90 days')
            ORDER BY a.record_date DESC LIMIT 1
        """, (uid,)).fetchone()

        if not alert_row:
            # 2nd: any non-resolution alert ever
            alert_row = conn.execute(f"""
                SELECT alert_code, message, record_date
                FROM alerts
                WHERE control_unit_id=? AND alert_code NOT IN ({res_codes})
                ORDER BY record_date DESC LIMIT 1
            """, (uid,)).fetchone()

        code  = alert_row["alert_code"]  if alert_row else 0
        msg   = alert_row["message"]     if alert_row else "תקלה פעילה (DeviceAlarm)"
        rdate = alert_row["record_date"] if alert_row else now

        conn.execute("""
            INSERT OR REPLACE INTO active_alerts
                (project_id, control_unit_id, alert_code, message, record_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (project_id, uid, code, msg, rdate, now))

    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM active_alerts WHERE project_id=?", (project_id,)
    ).fetchone()[0]


def get_primary_active_alerts_per_unit(conn, project_ids=None):
    """Return dict {unit_id: alert_code} from the live active_alerts table.
    project_ids=None means all projects (admin)."""
    if project_ids is None:
        rows = conn.execute("""
            SELECT control_unit_id, MIN(alert_code) AS alert_code
            FROM active_alerts
            GROUP BY control_unit_id
        """).fetchall()
    elif not project_ids:
        return {}
    else:
        placeholders = ",".join("?" * len(project_ids))
        rows = conn.execute(f"""
            SELECT control_unit_id, MIN(alert_code) AS alert_code
            FROM active_alerts
            WHERE project_id IN ({placeholders})
            GROUP BY control_unit_id
        """, project_ids).fetchall()
    return {r["control_unit_id"]: r["alert_code"] for r in rows}


def dismiss_active_alert(conn, unit_id):
    """Remove active alerts for a unit (user dismissed the fault)."""
    conn.execute("DELETE FROM active_alerts WHERE control_unit_id=?", (unit_id,))
    conn.commit()


# ── Alert queries for reporting ───────────────────────────────

def get_alerts_in_window(conn, hours_back=24, active_codes=None):
    """Fetch alerts from the last N hours, optionally filtered by active alert codes."""
    query = """
        SELECT a.*, u.unit_name as u_name, ug.group_name,
               asett.alert_name, asett.category
        FROM alerts a
        LEFT JOIN units u ON a.control_unit_id = u.control_unit_id
        LEFT JOIN unit_groups ug ON a.control_unit_id = ug.control_unit_id
        LEFT JOIN alert_settings asett ON a.alert_code = asett.alert_code
        WHERE a.record_date >= datetime('now', '-' || ? || ' hours')
        AND a.noticed = 0
    """
    params = [hours_back]
    rows = conn.execute(query, params).fetchall()
    if active_codes is not None:
        rows = [r for r in rows if r["alert_code"] in active_codes]
    return rows


def mark_alerts_noticed(conn, alert_ids):
    if not alert_ids:
        return
    placeholders = ",".join("?" * len(alert_ids))
    conn.execute(
        f"UPDATE alerts SET noticed=1 WHERE id IN ({placeholders})",
        list(alert_ids)
    )
    conn.commit()


if __name__ == "__main__":
    conn = get_db()
    init_db()
    init_alert_settings(conn)
    init_schedule_settings(conn)
    conn.close()
    print(f"Database initialized at {DB_PATH}")
