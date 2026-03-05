"""
Database layer for BirdWatch
SQLite-based storage for detections and settings
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager

log = logging.getLogger("birdwatch.db")
DB_PATH = os.environ.get("DB_PATH", "birdwatch.db")


# ═══════════════════════════════════════════════════════════════════════════════
# Connection
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════════

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS detections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                common_name TEXT    NOT NULL,
                scientific_name TEXT NOT NULL,
                confidence  REAL    NOT NULL,
                audio_file  TEXT,
                latitude    REAL,
                longitude   REAL,
                week        INTEGER,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_detections_timestamp
                ON detections(timestamp);
            CREATE INDEX IF NOT EXISTS idx_detections_common_name
                ON detections(common_name);
            CREATE INDEX IF NOT EXISTS idx_detections_confidence
                ON detections(confidence);

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        _seed_default_settings(conn)
    log.info("Database initialized")


def _seed_default_settings(conn):
    defaults = {
        "latitude": "52.0",
        "longitude": "5.0",
        "min_confidence": "0.25",
        "sensitivity": "1.0",
        "segment_seconds": "15",
        "sample_rate": "48000",
        "mic_device_index": "",
        "recordings_path": "recordings",
        "max_disk_pct": "95.0",
        "auto_start": "true",
        "station_name": "BirdWatch Station",
        "birdweather_token": "",
        "birdweather_enabled": "false",
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Detections
# ═══════════════════════════════════════════════════════════════════════════════

def insert_detection(
    timestamp: str,
    common_name: str,
    scientific_name: str,
    confidence: float,
    audio_file: str = None,
    latitude: float = None,
    longitude: float = None,
    week: int = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO detections
               (timestamp, common_name, scientific_name, confidence, audio_file, latitude, longitude, week)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, common_name, scientific_name, confidence, audio_file, latitude, longitude, week)
        )
        return cur.lastrowid


def get_detections(
    limit=50, offset=0,
    species=None, date_from=None, date_to=None, min_confidence=0.0
) -> list:
    filters = ["confidence >= ?"]
    params = [min_confidence]

    if species:
        filters.append("(common_name LIKE ? OR scientific_name LIKE ?)")
        params += [f"%{species}%", f"%{species}%"]
    if date_from:
        filters.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        filters.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")

    where = " AND ".join(filters)
    params += [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM detections
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?""",
            params
        ).fetchall()
    return [dict(r) for r in rows]


def count_detections(species=None, date_from=None, date_to=None, min_confidence=0.0) -> int:
    filters = ["confidence >= ?"]
    params = [min_confidence]

    if species:
        filters.append("(common_name LIKE ? OR scientific_name LIKE ?)")
        params += [f"%{species}%", f"%{species}%"]
    if date_from:
        filters.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        filters.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")

    where = " AND ".join(filters)
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM detections WHERE {where}", params
        ).fetchone()
    return row["cnt"] if row else 0


def get_detection_by_id(detection_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM detections WHERE id = ?", (detection_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_detection(detection_id: int) -> bool:
    """Verwijder een detectie uit de database. Geeft True terug als succesvol."""
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM detections WHERE id = ?", (detection_id,))
    return cursor.rowcount > 0


def bulk_delete_before(date_str: str) -> int:
    """Verwijder alle detecties vóór de opgegeven datum (YYYY-MM-DD).
    Geeft het aantal verwijderde rijen terug."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM detections WHERE DATE(timestamp) < ?", (date_str,)
        )
    return cursor.rowcount


def export_detections_csv(date_from: str = None, date_to: str = None) -> list:
    """Haal alle detecties op tussen twee datums als lijst van dicts (voor CSV export)."""
    query = "SELECT id, timestamp, common_name, scientific_name, confidence, audio_file FROM detections WHERE 1=1"
    params = []
    if date_from:
        query += " AND DATE(timestamp) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(timestamp) <= ?"
        params.append(date_to)
    query += " ORDER BY timestamp ASC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_top_species(days=7, limit=10) -> list:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT common_name, scientific_name,
                      COUNT(*) as count,
                      MAX(confidence) as max_confidence,
                      AVG(confidence) as avg_confidence
               FROM detections
               WHERE timestamp >= ?
               GROUP BY common_name
               ORDER BY count DESC
               LIMIT ?""",
            (since, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_species() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT common_name, scientific_name
               FROM detections
               ORDER BY common_name"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_daily_stats(days=30) -> list:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DATE(timestamp) as date,
                      COUNT(*) as total_detections,
                      COUNT(DISTINCT common_name) as unique_species
               FROM detections
               WHERE timestamp >= ?
               GROUP BY DATE(timestamp)
               ORDER BY date""",
            (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_hourly_stats(days=7) -> list:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                      COUNT(*) as count
               FROM detections
               WHERE timestamp >= ?
               GROUP BY hour
               ORDER BY hour""",
            (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_species_hourly_heatmap(days=1, limit=30) -> dict:
    """Heatmap data: per soort het aantal waarnemingen per uur.
    Geeft terug: { species: [{name, total}, ...], hours: [0..23],
                   data: {species_name: {hour: count}} }
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()
    until = datetime.now().isoformat()   # nooit toekomstige uren tonen
    with get_conn() as conn:
        # Top soorten gesorteerd op totaal
        species_rows = conn.execute(
            """SELECT common_name, COUNT(*) as total
               FROM detections
               WHERE timestamp >= ? AND timestamp <= ?
               GROUP BY common_name
               ORDER BY total DESC
               LIMIT ?""",
            (since, until, limit)
        ).fetchall()

        # Per soort per uur
        detail_rows = conn.execute(
            """SELECT common_name,
                      CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                      COUNT(*) as count
               FROM detections
               WHERE timestamp >= ? AND timestamp <= ?
                 AND common_name IN (
                     SELECT common_name FROM detections
                     WHERE timestamp >= ? AND timestamp <= ?
                     GROUP BY common_name
                     ORDER BY COUNT(*) DESC
                     LIMIT ?
                 )
               GROUP BY common_name, hour""",
            (since, until, since, until, limit)
        ).fetchall()

    species = [{"name": r["common_name"], "total": r["total"]} for r in species_rows]
    data = {}
    for r in detail_rows:
        name = r["common_name"]
        if name not in data:
            data[name] = {}
        data[name][str(r["hour"])] = r["count"]

    return {"species": species, "data": data}


def get_summary_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as n FROM detections").fetchone()["n"]
        today = conn.execute(
            "SELECT COUNT(*) as n FROM detections WHERE DATE(timestamp) = DATE('now')"
        ).fetchone()["n"]
        species = conn.execute(
            "SELECT COUNT(DISTINCT common_name) as n FROM detections"
        ).fetchone()["n"]
        species_today = conn.execute(
            "SELECT COUNT(DISTINCT common_name) as n FROM detections WHERE DATE(timestamp) = DATE('now')"
        ).fetchone()["n"]
        last = conn.execute(
            "SELECT * FROM detections ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        best = conn.execute(
            """SELECT common_name, confidence FROM detections
               ORDER BY confidence DESC LIMIT 1"""
        ).fetchone()

    return {
        "total_detections": total,
        "detections_today": today,
        "total_species": species,
        "species_today": species_today,
        "last_detection": dict(last) if last else None,
        "best_detection": dict(best) if best else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════════════

def get_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {r["key"]: r["value"] for r in rows}
    # Parse booleans and numerics
    for k in ("auto_start", "birdweather_enabled"):
        if k in settings:
            settings[k] = settings[k].lower() == "true"
    for k in ("latitude", "longitude", "min_confidence", "sensitivity", "max_disk_pct"):
        if k in settings and settings[k]:
            try:
                settings[k] = float(settings[k])
            except ValueError:
                pass
    for k in ("segment_seconds", "sample_rate"):
        if k in settings:
            try:
                settings[k] = int(settings[k])
            except ValueError:
                pass
    if settings.get("mic_device_index") == "":
        settings["mic_device_index"] = None
    return settings


def save_settings(data: dict):
    with get_conn() as conn:
        for key, value in data.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value))
            )
    log.info(f"Settings saved: {list(data.keys())}")
