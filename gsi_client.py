import re
import time
import logging
import requests
from datetime import datetime, timedelta
from config import GSI_BASE_URL, GSI_TOKEN, GSI_PROJECT_ID, API_ENDPOINTS, GSI_USERNAME, GSI_PASSWORD

logger = logging.getLogger(__name__)

LOGIN_URL = "https://gsi.galcon-smart.com/api/Auth/Login"


class GSIClient:
    def __init__(self, token=None, base_url=None, project_id=None):
        self.base_url = base_url or GSI_BASE_URL
        self.project_id = project_id or GSI_PROJECT_ID
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://gsi.galcon-smart.com",
            "Referer": "https://gsi.galcon-smart.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        self._request_delay = 0.3  # seconds between requests

        # Use provided token or login to get a fresh one
        if token:
            self.token = token
        elif GSI_TOKEN:
            self.token = GSI_TOKEN
        else:
            self.token = self._login()

        self._set_auth_header()

    def _set_auth_header(self):
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _login(self):
        """Login to GSI API and return a fresh token."""
        try:
            logger.info(f"Logging in to GSI as {GSI_USERNAME}...")
            resp = self.session.post(
                LOGIN_URL,
                json={"UserName": GSI_USERNAME, "Password": GSI_PASSWORD},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("Body", {}).get("AccountToken")
            if token:
                logger.info("GSI login successful — token acquired")
                return token
            else:
                logger.error(f"Login response missing token: {data}")
                return None
        except Exception as e:
            logger.error(f"GSI login failed: {e}")
            return None

    def _refresh_token(self):
        """Get a new token and update session headers."""
        new_token = self._login()
        if new_token:
            self.token = new_token
            self._set_auth_header()
            return True
        return False

    def _url(self, endpoint_key, **kwargs):
        kwargs.setdefault("project_id", self.project_id)
        template = API_ENDPOINTS[endpoint_key]
        return self.base_url + template.format(**kwargs)

    def _get(self, endpoint_key, **kwargs):
        url = self._url(endpoint_key, **kwargs)
        time.sleep(self._request_delay)
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 401:
                logger.warning(f"401 on GET {url} — refreshing token")
                if self._refresh_token():
                    resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"GET {url} failed: {e}")
            return None

    def _post(self, endpoint_key, body=None, **kwargs):
        url = self._url(endpoint_key, **kwargs)
        time.sleep(self._request_delay)
        try:
            resp = self.session.post(url, json=body or {}, timeout=30)
            if resp.status_code == 401:
                logger.warning(f"401 on POST {url} — refreshing token")
                if self._refresh_token():
                    resp = self.session.post(url, json=body or {}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"POST {url} failed: {e}")
            return None

    def get_cached_alerts(self):
        """Get currently ACTIVE alerts for the entire project (CachAlerts endpoint).
        Returns list of dicts with at least ControlUnitID and AlertCode."""
        data = self._post("cached_alerts", body={})
        if data is None:
            return []
        # Normalise various response shapes
        if isinstance(data, list):
            if len(data) == 1 and isinstance(data[0], dict) and "Body" in data[0]:
                body = data[0]["Body"]
            else:
                body = data
        elif isinstance(data, dict):
            body = data.get("Body", data)
        else:
            body = []
        if isinstance(body, list):
            return body
        logger.warning(f"Unexpected CachAlerts response shape: {type(body)}")
        return []

    def get_unit_list(self):
        """Get list of all controllers in the project (simple list, may be incomplete).

        NOTE: This endpoint (/UnitList) only returns a subset of units.
        Prefer get_project_info() which returns the full list via POST /Info.
        """
        data = self._get("unit_list")
        if data is None:
            return []
        # API returns flat list or nested Body
        if isinstance(data, list):
            if len(data) == 1 and isinstance(data[0], dict) and "Body" in data[0]:
                return data[0]["Body"]
            return data
        if isinstance(data, dict) and "Body" in data:
            return data["Body"] if isinstance(data["Body"], list) else []
        return []

    def get_project_info(self, page_size=100):
        """Get full list of all units in the project via POST /Info endpoint.

        This is the correct endpoint used by the GSI web app for the unit list page.
        Returns a list of unit summaries (each with ControlUnitID, Name, Status, SN, etc.)
        The field for unit name is 'Name' (not 'UnitName' as in get_unit_list()).
        Returns empty list on failure.
        """
        body = {
            "PageSize": page_size,
            "PageNumber": 1,
            "Search": "",
            "SortType": "UnitType",
            "SortDirection": "ASC"
        }
        data = self._post("project_info", body=body)
        if data is None:
            logger.warning("get_project_info: POST /Info returned None — falling back to UnitList")
            return self.get_unit_list()

        if isinstance(data, dict):
            response_body = data.get("Body", {})
            if isinstance(response_body, dict):
                units = response_body.get("Response", [])
                total = response_body.get("TotalCount", len(units))
                logger.info(f"get_project_info: {len(units)}/{total} units from /Info endpoint")
                return units if isinstance(units, list) else []

        logger.warning(f"get_project_info: unexpected response shape: {type(data)}")
        return self.get_unit_list()

    def get_unit_info(self, unit_id):
        """Get full unit data: Config, Valves, Programs, WaterMeter, etc."""
        data = self._get("unit_info", unit_id=unit_id)
        if data and isinstance(data, dict):
            return data.get("Body", data)
        return None

    def get_unit_settings(self, unit_id):
        """Get unit settings."""
        data = self._get("unit_settings", unit_id=unit_id)
        if data and isinstance(data, dict):
            return data.get("Body", data)
        return None

    def get_unit_programs(self, unit_id):
        """Get irrigation programs for a unit."""
        data = self._get("unit_programs", unit_id=unit_id)
        if data and isinstance(data, dict):
            return data.get("Body", data)
        return data

    def get_unit_alerts(self, unit_id):
        """Get alerts for a unit."""
        data = self._get("unit_alerts", unit_id=unit_id)
        if data and isinstance(data, dict):
            return data.get("Body", [])
        return []

    @staticmethod
    def _parse_coord(val):
        """Parse coordinate value - handles decimal (32.5) and DMS (32°33'44.5) formats."""
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        # Try decimal first
        try:
            return float(s)
        except (ValueError, TypeError):
            pass
        # Try DMS format: e.g. "34°33'55.6" or "34°33'55.6N"
        import re as _re
        m = _re.match(r"(\d+)[°º]\s*(\d+)'\s*([\d.]+)\"?\s*([NSEW]?)", s)
        if m:
            deg, mins, secs, hem = m.groups()
            dec = float(deg) + float(mins) / 60 + float(secs) / 3600
            if hem in ('S', 'W'):
                dec = -dec
            return dec
        logger.warning(f"Cannot parse coordinate: {val!r}")
        return None

    def get_unit_weather(self, unit_id, lat, lon):
        """Get weather forecast for a unit's location."""
        if not lat or not lon:
            return None
        # Normalise coordinates (handle DMS format like "34°33'55.6")
        lat_dec = self._parse_coord(lat)
        lon_dec = self._parse_coord(lon)
        if lat_dec is None or lon_dec is None:
            logger.warning(f"Unit {unit_id}: invalid coordinates lat={lat!r} lon={lon!r}")
            return None
        return self._get("unit_weather", unit_id=unit_id, lat=lat_dec, lon=lon_dec)

    def get_unit_irrigation_settings(self, unit_id):
        """Get irrigation settings for a unit."""
        data = self._get("unit_irrigation", unit_id=unit_id)
        if data and isinstance(data, dict):
            return data.get("Body", data)
        return None

    def get_user_info(self):
        """Get logged-in user info."""
        data = self._get("user_info")
        if data and isinstance(data, dict):
            return data.get("Body", data)
        return None

    def get_battery_averages(self, unit_id, days=7):
        """
        Fetch general logs for unit, parse Code=12 battery rows,
        return dict with noon_avg and midnight_avg voltages over last N days.
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - days * 24 * 3600 * 1000
        data = self._post("unit_logs", body={"PageNumber": 1, "PageSize": 100},
                          unit_id=unit_id, start_ticks=start_ms, end_ticks=now_ms)
        if not data:
            return None
        body = data.get("Body", {}) or {}
        rows = body.get("Response", []) or []

        noon_vals, mid_vals = [], []
        for row in rows:
            if row.get("Code") != 12:
                continue
            msg = row.get("Message", "") or ""
            n = re.search(r"Noon:([\d.]+)v", msg)
            m = re.search(r"Midnight:([\d.]+)v", msg)
            if n:
                noon_vals.append(float(n.group(1)))
            if m:
                mid_vals.append(float(m.group(1)))

        return {
            "noon_avg": round(sum(noon_vals) / len(noon_vals), 2) if noon_vals else None,
            "midnight_avg": round(sum(mid_vals) / len(mid_vals), 2) if mid_vals else None,
            "noon_latest": noon_vals[0] if noon_vals else None,
            "midnight_latest": mid_vals[0] if mid_vals else None,
            "days_sampled": len(noon_vals),
        }

    def test_connection(self):
        """Test if the API connection and token are valid."""
        try:
            user = self.get_user_info()
            if user:
                name = f"{user.get('FirstName', '')} {user.get('LastName', '')}".strip()
                logger.info(f"Connected as: {name} ({user.get('Email', 'N/A')})")
                return True
            return False
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
