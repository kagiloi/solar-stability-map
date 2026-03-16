import json
import logging
from pathlib import Path
from typing import Any

from crawler.config import (
    CSV_HEADER_ROWS,
    DB_PATH,
    EXPECTED_DATA_ROWS,
    RAW_CSV_DIR,
    STATIONS_JSON,
)
from crawler.db import get_connection, init_db, insert_daily_rows, insert_station

logger = logging.getLogger(__name__)


def _parse_float(val: str) -> float | None:
    """Parse a CSV value to float, returning None for empty/missing."""
    val = val.strip()
    if not val or val == "--" or val == "×":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_int(val: str) -> int | None:
    """Parse a CSV value to int, returning None for empty/missing."""
    val = val.strip()
    if not val or val == "--":
        return None
    try:
        return int(val)
    except ValueError:
        return None


def _parse_date(date_str: str) -> str:
    """Convert '2025/M/D' to 'MM-DD' format."""
    parts = date_str.strip().split("/")
    if len(parts) != 3:
        return date_str.strip()
    month = int(parts[1])
    day = int(parts[2])
    return f"{month:02d}-{day:02d}"


def _detect_column_count(lines: list[str]) -> int:
    """Detect CSV column count from the first data row."""
    for line in lines[CSV_HEADER_ROWS:]:
        cols = line.split(",")
        if cols[0].strip().startswith("20"):
            return len(cols)
    return 0


def _parse_csv_file(csv_path: Path, stid: str) -> list[tuple[Any, ...]]:
    """Parse a single CSV file and return rows for daily_data table.

    Handles two formats:
    - 13 columns: official stations with 現象なし情報 columns
    - 11 columns: AMeDAS stations without 現象なし情報 columns
    """
    text = csv_path.read_text(encoding="utf-8")
    lines = text.strip().split("\n")

    if len(lines) <= CSV_HEADER_ROWS:
        logger.warning("  %s: Not enough lines (got %d)", stid, len(lines))
        return []

    ncols = _detect_column_count(lines)

    # Column index mapping: (sunshine_actual, sunshine_quality, sunshine_normal,
    #                        solar_actual, solar_quality, solar_normal, solar_normal_quality)
    if ncols >= 13:
        # 13-col: has 現象なし情報 for sunshine actual and sunshine normal
        idx = (1, 3, 5, 8, 9, 11, 12)
    elif ncols >= 11:
        # 11-col: no 現象なし情報 columns
        idx = (1, 2, 4, 6, 7, 9, 10)
    else:
        logger.warning("  %s: Unexpected column count %d", stid, ncols)
        return []

    data_lines = lines[CSV_HEADER_ROWS:]
    rows: list[tuple[Any, ...]] = []

    for line in data_lines:
        cols = line.split(",")
        if len(cols) < ncols:
            continue

        date_raw = cols[0].strip()
        if not date_raw.startswith("20"):
            continue

        date_mmdd = _parse_date(date_raw)
        sunshine_hours = _parse_float(cols[idx[0]])
        sunshine_quality = _parse_int(cols[idx[1]])
        sunshine_normal = _parse_float(cols[idx[2]])
        solar_radiation = _parse_float(cols[idx[3]])
        solar_quality = _parse_int(cols[idx[4]])
        solar_normal = _parse_float(cols[idx[5]])
        solar_normal_quality = _parse_int(cols[idx[6]])

        rows.append((
            stid,
            date_mmdd,
            sunshine_hours,
            sunshine_quality,
            sunshine_normal,
            solar_radiation,
            solar_quality,
            solar_normal,
            solar_normal_quality,
        ))

    return rows


def parse_all() -> tuple[int, int]:
    """Parse all downloaded CSVs and store in SQLite.

    Returns (stations_processed, total_rows_inserted).
    """
    if not STATIONS_JSON.exists():
        raise FileNotFoundError(
            f"Station list not found at {STATIONS_JSON}. Run --discover first."
        )

    with open(STATIONS_JSON, encoding="utf-8") as f:
        stations: list[dict[str, Any]] = json.load(f)

    init_db(DB_PATH)
    conn = get_connection(DB_PATH)

    stations_processed = 0
    total_rows = 0

    try:
        # Insert station metadata
        for st in stations:
            insert_station(
                conn,
                stid=st["stid"],
                name=st["name"],
                prid=st["prid"],
                latitude=st.get("latitude"),
                longitude=st.get("longitude"),
                elevation=st.get("elevation"),
                kansoku=st["kansoku"],
                title_raw=st.get("title_raw", ""),
                observation_ended=st.get("observation_ended"),
            )
        conn.commit()
        logger.info("Inserted %d stations into DB", len(stations))

        # Parse and insert daily data
        for i, st in enumerate(stations):
            stid = st["stid"]
            csv_path = RAW_CSV_DIR / f"{stid}.csv"

            if not csv_path.exists():
                logger.warning("[%d/%d] %s: CSV not found, skipping", i + 1, len(stations), stid)
                continue

            rows = _parse_csv_file(csv_path, stid)
            if rows:
                count = insert_daily_rows(conn, rows)
                total_rows += count
                stations_processed += 1
                if count < EXPECTED_DATA_ROWS:
                    logger.warning(
                        "[%d/%d] %s (%s): only %d rows (expected %d)",
                        i + 1, len(stations), stid, st.get("name", ""), count, EXPECTED_DATA_ROWS,
                    )
                else:
                    logger.info(
                        "[%d/%d] %s (%s): %d rows",
                        i + 1, len(stations), stid, st.get("name", ""), count,
                    )
            else:
                logger.warning("[%d/%d] %s: No data rows parsed", i + 1, len(stations), stid)

            # Commit per station
            conn.commit()

    finally:
        conn.close()

    logger.info(
        "Parse complete: %d stations, %d total rows",
        stations_processed, total_rows,
    )
    return stations_processed, total_rows


def verify() -> None:
    """Run verification checks on the database."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found at {DB_PATH}. Run --parse first.")

    conn = get_connection(DB_PATH)
    try:
        # Station count
        row = conn.execute("SELECT COUNT(*) FROM stations").fetchone()
        station_count = row[0] if row else 0
        logger.info("Total stations: %d", station_count)

        # Stations with fewer than 365 days
        incomplete = conn.execute(
            """
            SELECT s.stid, s.name, COUNT(d.date) as day_count
            FROM stations s
            LEFT JOIN daily_data d ON s.stid = d.stid
            GROUP BY s.stid
            HAVING day_count < ?
            ORDER BY day_count
            """,
            (EXPECTED_DATA_ROWS,),
        ).fetchall()

        if incomplete:
            logger.warning("Stations with < %d days:", EXPECTED_DATA_ROWS)
            for stid, name, count in incomplete:
                logger.warning("  %s (%s): %d days", stid, name, count)
        else:
            logger.info("All stations have %d days of data.", EXPECTED_DATA_ROWS)

        # Solar normal value range
        solar_range = conn.execute(
            """
            SELECT MIN(solar_normal), MAX(solar_normal), AVG(solar_normal)
            FROM daily_data
            WHERE solar_normal IS NOT NULL
            """
        ).fetchone()
        if solar_range:
            logger.info(
                "Solar normal range: min=%.2f, max=%.2f, avg=%.2f MJ/m2",
                solar_range[0] or 0, solar_range[1] or 0, solar_range[2] or 0,
            )

        # Sunshine normal value range
        sun_range = conn.execute(
            """
            SELECT MIN(sunshine_normal), MAX(sunshine_normal), AVG(sunshine_normal)
            FROM daily_data
            WHERE sunshine_normal IS NOT NULL
            """
        ).fetchone()
        if sun_range:
            logger.info(
                "Sunshine normal range: min=%.2f, max=%.2f, avg=%.2f hours",
                sun_range[0] or 0, sun_range[1] or 0, sun_range[2] or 0,
            )

        # Sample: top 5 stations by average solar normal
        top_solar = conn.execute(
            """
            SELECT s.stid, s.name, ROUND(AVG(d.solar_normal), 2) as avg_solar
            FROM stations s
            JOIN daily_data d ON s.stid = d.stid
            WHERE d.solar_normal IS NOT NULL
            GROUP BY s.stid
            ORDER BY avg_solar DESC
            LIMIT 5
            """
        ).fetchall()
        if top_solar:
            logger.info("Top 5 stations by average solar normal (MJ/m2):")
            for stid, name, avg_solar in top_solar:
                logger.info("  %s (%s): %.2f", stid, name, avg_solar)

        # Total data rows
        total = conn.execute("SELECT COUNT(*) FROM daily_data").fetchone()
        logger.info("Total daily_data rows: %d", total[0] if total else 0)

    finally:
        conn.close()
