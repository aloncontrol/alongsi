import logging
from datetime import datetime
from gsi_client import GSIClient
from database import (
    get_db, init_db, upsert_unit, insert_snapshot,
    insert_alerts, insert_weather, get_latest_snapshot,
    upsert_battery_stats, rebuild_active_alerts_from_snapshots
)
from alerts import AlertEngine

logger = logging.getLogger(__name__)


class DataCollector:
    def __init__(self, project_id=None):
        from config import GSI_PROJECT_ID
        self.project_id = project_id or GSI_PROJECT_ID
        self.client = GSIClient(project_id=self.project_id)
        self.alert_engine = AlertEngine()
        init_db()

    def collect_all(self):
        """Main collection cycle - fetch all units for this project."""
        start = datetime.now()
        logger.info(f"Starting data collection for project {self.project_id} at {start.isoformat()}")

        # Use get_project_info (POST /Info) which returns ALL units (vs UnitList which misses some)
        unit_list = self.client.get_project_info()
        if not unit_list:
            logger.error(f"Failed to fetch project info for project {self.project_id}")
            return

        logger.info(f"Found {len(unit_list)} units in project {self.project_id} (via /Info endpoint)")

        # Build set of unit IDs returned by the API
        api_unit_ids = {u.get("ControlUnitID") for u in unit_list if u.get("ControlUnitID")}

        # Find all known units (active OR recently-inactive) not in the current API list.
        # "Recently-inactive" = had a snapshot in the last 30 days (not truly gone).
        conn0 = get_db()
        prev_known = conn0.execute(
            """SELECT DISTINCT u.control_unit_id, u.unit_name
               FROM units u
               WHERE u.project_id=?
                 AND (u.is_active=1
                      OR EXISTS (
                          SELECT 1 FROM unit_snapshots s
                          WHERE s.control_unit_id=u.control_unit_id
                            AND s.captured_at >= datetime('now', '-30 days')
                      ))
            """,
            (self.project_id,)
        ).fetchall()
        orphaned = [
            {"ControlUnitID": r["control_unit_id"], "UnitName": r["unit_name"]}
            for r in prev_known
            if r["control_unit_id"] not in api_unit_ids
        ]
        if orphaned:
            logger.info(f"Found {len(orphaned)} previously-known units not in current API list — will re-check")

        # NOTE: We do NOT blanket-reset is_active=0 here.
        # Units stay active until proven gone, to avoid a transient "all inactive" window.
        # Primary units get is_active=1 via upsert_unit().
        # Orphaned units that fail will be explicitly marked inactive below.
        conn0.close()

        success_count = 0
        error_count = 0
        collected_ids = set()

        # Collect units returned by the API (primary list)
        # Note: /Info uses 'Name' field; /UnitList uses 'UnitName' — handle both
        for unit_entry in unit_list:
            unit_id = unit_entry.get("ControlUnitID")
            unit_name = unit_entry.get("Name") or unit_entry.get("UnitName", "Unknown")
            try:
                self._collect_unit(unit_id, unit_name)
                success_count += 1
                collected_ids.add(unit_id)
            except Exception as e:
                logger.error(f"Error collecting unit {unit_name} ({unit_id}): {e}")
                error_count += 1

        # Re-check orphaned units: if still API-accessible, keep them active
        orphan_ok = 0
        orphan_gone = 0
        for unit_entry in orphaned:
            unit_id = unit_entry.get("ControlUnitID")
            unit_name = unit_entry.get("Name") or unit_entry.get("unit_name") or unit_entry.get("UnitName", "Unknown")
            try:
                self._collect_unit(unit_id, unit_name)
                collected_ids.add(unit_id)
                orphan_ok += 1
            except Exception as e:
                # Unit no longer accessible — mark explicitly inactive
                logger.debug(f"Orphaned unit {unit_name} ({unit_id}) no longer accessible: {e}")
                try:
                    conn_gone = get_db()
                    conn_gone.execute(
                        "UPDATE units SET is_active=0 WHERE control_unit_id=?", (unit_id,)
                    )
                    conn_gone.commit()
                    conn_gone.close()
                except Exception:
                    pass
                orphan_gone += 1
        if orphaned:
            logger.info(f"Orphaned units: {orphan_ok} still active, {orphan_gone} gone")

        # Rebuild active_alerts from device_alarm flags in latest snapshots
        try:
            conn_c = get_db()
            count = rebuild_active_alerts_from_snapshots(conn_c, self.project_id)
            conn_c.close()
            logger.info(f"Active alerts rebuilt: {count} units with open faults")
        except Exception as e:
            logger.error(f"Failed to rebuild active alerts: {e}")

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(
            f"Collection complete [project {self.project_id}]: "
            f"{success_count} OK, {error_count} errors, {elapsed:.1f}s elapsed"
        )

    def _collect_unit(self, unit_id, unit_name):
        """Collect all data for a single unit."""
        conn = get_db()
        try:
            # Get full unit info
            unit_data = self.client.get_unit_info(unit_id)
            if not unit_data:
                logger.warning(f"No data for unit {unit_name} ({unit_id})")
                return

            # Get previous snapshot for change detection
            prev_snapshot = get_latest_snapshot(conn, unit_id)

            # Store unit master data (stamp with project_id)
            upsert_unit(conn, unit_data, project_id=self.project_id)

            # Store snapshot
            insert_snapshot(conn, unit_id, unit_data)

            # Fetch and store alerts
            alerts = self.client.get_unit_alerts(unit_id)
            if alerts:
                insert_alerts(conn, unit_id, alerts)

            # Fetch and store weather
            config = unit_data.get("Config", {})
            lat = config.get("Map_Latitude")
            lon = config.get("Map_Longitude")
            if lat and lon:
                weather = self.client.get_unit_weather(unit_id, lat, lon)
                if weather:
                    insert_weather(conn, unit_id, weather)

            conn.commit()

            # Run alert checks
            self.alert_engine.check_unit(conn, unit_id, unit_data, prev_snapshot)
            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def run_battery_collection():
    """Collect battery stats for all projects (runs separately, once per day)."""
    from config import GSI_PROJECT_IDS
    ok_total, err_total = 0, 0
    for project_id in GSI_PROJECT_IDS:
        client = GSIClient(project_id=project_id)
        # Get current unit list from API (not from DB — avoids stale/removed units)
        unit_list = client.get_project_info()
        if unit_list:
            units = [{"control_unit_id": u.get("ControlUnitID"),
                      "unit_name": u.get("Name") or u.get("UnitName", str(u.get("ControlUnitID")))}
                     for u in unit_list if u.get("ControlUnitID")]
            logger.info(f"Battery collection [project {project_id}]: {len(units)} units from API")
        else:
            # Fallback to DB units for this project
            conn = get_db()
            try:
                units = conn.execute(
                    "SELECT control_unit_id, unit_name FROM units WHERE project_id=?",
                    (project_id,)
                ).fetchall()
            finally:
                conn.close()
            logger.info(f"Battery collection [project {project_id}] (DB fallback): {len(units)} units")
        ok, err = 0, 0
        for u in units:
            try:
                stats = client.get_battery_averages(u["control_unit_id"])
                if stats and (stats["noon_avg"] or stats["midnight_avg"]):
                    conn2 = get_db()
                    upsert_battery_stats(conn2, u["control_unit_id"], stats)
                    conn2.close()
                    ok += 1
            except Exception as e:
                logger.error(f"Battery fetch failed for {u['unit_name']}: {e}")
                err += 1
        logger.info(f"Battery [project {project_id}] done: {ok} OK, {err} errors")
        ok_total += ok
        err_total += err
    logger.info(f"Battery collection complete: {ok_total} OK, {err_total} errors")


def sync_projects_from_gsi():
    """Fetch the full project list from GSI and upsert names into the DB.

    This is metadata-only (no unit data collected). It keeps the projects
    table up-to-date with whatever the GSI user account has access to.
    Returns the list of project dicts [{project_id, project_name}, ...].
    """
    try:
        from gsi_client import GSIClient
        from database import upsert_project
        client = GSIClient()
        projects = client.get_user_projects()
        if projects:
            conn = get_db()
            for p in projects:
                upsert_project(conn, p["project_id"], p["project_name"])
            conn.close()
            logger.info(f"sync_projects_from_gsi: synced {len(projects)} projects to DB")
        return projects
    except Exception as e:
        logger.warning(f"sync_projects_from_gsi failed: {e}")
        return []


def run_collection():
    """Entry point for scheduled collection.

    1. Syncs project list from GSI to DB (metadata / names only — fast).
    2. Collects unit data only for projects that are actively monitored:
       - projects in GSI_PROJECT_IDS (config), OR
       - projects in DB that have at least one unit already collected
         (i.e. the user has explicitly triggered a collection for them before).

    This avoids attempting to collect all thousands of projects that may
    exist in a GSI admin account.
    """
    from config import GSI_PROJECT_IDS

    # Step 1: sync project names from GSI → DB (metadata only)
    sync_projects_from_gsi()

    # Step 2: determine which projects to actively collect
    monitored = set(GSI_PROJECT_IDS)

    # Add any project that already has unit data in the DB
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT project_id FROM units WHERE project_id IS NOT NULL"
        ).fetchall()
        conn.close()
        for r in rows:
            monitored.add(r["project_id"])
    except Exception as e:
        logger.warning(f"Could not read active projects from DB: {e}")

    logger.info(f"run_collection: collecting {len(monitored)} monitored projects")
    for project_id in sorted(monitored):
        collector = DataCollector(project_id=project_id)
        collector.collect_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_collection()
