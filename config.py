import os

# GSI API Configuration
GSI_BASE_URL = "https://gsi.galcon-smart.com"
GSI_PROJECT_ID = 8378           # legacy alias (single project)
GSI_PROJECT_IDS = [8378]        # list of all monitored project IDs
GSI_TOKEN = os.environ.get("GSI_TOKEN", "")  # Auto-refreshed on startup via login

# GSI Login Credentials (used for automatic token refresh)
GSI_USERNAME = os.environ.get("GSI_USERNAME", "alon@alon-control.co.il")
GSI_PASSWORD = os.environ.get("GSI_PASSWORD", "R03l06s08@")

# Polling interval in minutes
POLL_INTERVAL_MINUTES = 15

# Database
DB_PATH = os.path.join(os.path.dirname(__file__), "gsi_monitor.db")

# Dashboard
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 5000

# Alert thresholds
ALERT_DISCONNECT_HOURS = 24  # Alert if no connection for X hours
ALERT_LOW_SIGNAL = 10  # Signal strength threshold
ALERT_PAYMENT_DAYS = 30  # Days before payment expiry to alert

# Green API (WhatsApp)
GREENAPI_INSTANCE_ID = os.environ.get("GREENAPI_INSTANCE_ID", "7107570977")
GREENAPI_API_TOKEN   = os.environ.get("GREENAPI_API_TOKEN", "5041838f9f134c6a965e0334011b191da62db08219e241c18e")
GREENAPI_PHONE       = os.environ.get("GREENAPI_PHONE", "")  # e.g. 0501234567

# Authorized phone numbers for login (Israeli format: 05XXXXXXXX)
ALLOWED_PHONES = [
    os.environ.get("ALLOWED_PHONE_1", ""),  # set via env or add directly below
    "0528664374",
]
ALLOWED_PHONES = [p for p in ALLOWED_PHONES if p]  # remove empty

# Company / branding
COMPANY_NAME = "אלון מערכות"
SYSTEM_TITLE = "מערכת ניטור ובקרה"

# API Endpoints
API_ENDPOINTS = {
    "unit_list": "/api/api/project/{project_id}/UnitList",
    "unit_info": "/api/api/unit/{unit_id}?ProjectID={project_id}",
    "unit_settings": "/api/api/unit/{unit_id}/settings?ProjectID={project_id}",
    "unit_programs": "/api/api/unit/{unit_id}/programslist",
    "unit_irrigation": "/api/api/unit/{unit_id}/IrrigationSettings",
    "unit_alerts": "/api/API/Unit/{unit_id}/Alerts?Cols=RecordDate_DESC",
    "unit_weather": "/api/api/unit/{unit_id}/GetWathearUnit?lat={lat}&lon={lon}",
    "unit_alert_settings": "/api/api/unit/{unit_id}/AlertsSettings?SendDefaults=0&Type=0",
    "user_info": "/api/Admin/User/LoginInfo",
    "project_info": "/api/api/project/{project_id}/Info",
    "cached_alerts": "/api/api/project/{project_id}/CachAlerts",
    "unit_logs": "/api/api/Unit/{unit_id}/Logs?StartTicks={start_ticks}&EndTicks={end_ticks}&request",
}
