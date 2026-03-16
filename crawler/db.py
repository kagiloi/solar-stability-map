import sqlite3
import logging
from pathlib import Path
from typing import Any

from crawler.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS stations (
    stid              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    prid              TEXT NOT NULL,
    latitude          REAL,
    longitude         REAL,
    elevation         REAL,
    kansoku           TEXT,
    title_raw         TEXT,
    observation_ended TEXT
);

CREATE TABLE IF NOT EXISTS daily_data (
    stid                    TEXT NOT NULL,
    date                    TEXT NOT NULL,
    sunshine_hours          REAL,
    sunshine_hours_quality  INTEGER,
    sunshine_normal         REAL,
    solar_radiation         REAL,
    solar_radiation_quality INTEGER,
    solar_normal            REAL,
    solar_normal_quality    INTEGER,
    PRIMARY KEY (stid, date),
    FOREIGN KEY (stid) REFERENCES stations(stid)
);

CREATE INDEX IF NOT EXISTS idx_daily_data_date ON daily_data(date);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        logger.info("Database initialized at %s", db_path)
    finally:
        conn.close()


def insert_station(
    conn: sqlite3.Connection,
    stid: str,
    name: str,
    prid: str,
    latitude: float | None,
    longitude: float | None,
    elevation: float | None,
    kansoku: str,
    title_raw: str,
    observation_ended: str | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO stations
            (stid, name, prid, latitude, longitude, elevation, kansoku, title_raw, observation_ended)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (stid, name, prid, latitude, longitude, elevation, kansoku, title_raw, observation_ended),
    )


def insert_daily_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[Any, ...]],
) -> int:
    """Insert daily data rows in a batch. Returns number of rows inserted."""
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_data
            (stid, date, sunshine_hours, sunshine_hours_quality, sunshine_normal,
             solar_radiation, solar_radiation_quality, solar_normal, solar_normal_quality)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def get_station_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM stations").fetchone()
    return row[0] if row else 0


def get_daily_data_count(conn: sqlite3.Connection, stid: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM daily_data WHERE stid = ?", (stid,)
    ).fetchone()
    return row[0] if row else 0
