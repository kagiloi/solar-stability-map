import json
import logging
import time
from pathlib import Path

import requests

from crawler.config import (
    CSV_ENCODING,
    CSV_POST_PARAMS,
    ELEMENT_NUM_LIST,
    EXPECTED_DATA_ROWS,
    INDEX_URL,
    MAX_RETRIES,
    RAW_CSV_DIR,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF_BASE,
    STATIONS_JSON,
    TABLE_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


def _create_session() -> requests.Session:
    """Create a session with JMA cookies."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en;q=0.9",
    })
    resp = session.get(INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    logger.info("Session established for download.")
    return session


def _validate_csv(path: Path) -> bool:
    """Check if a CSV file looks valid (has enough data rows starting with '20')."""
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.strip().split("\n")
        data_lines = [l for l in lines if l.strip().startswith("20")]
        return len(data_lines) >= EXPECTED_DATA_ROWS
    except Exception:
        return False


def _download_one(
    session: requests.Session, stid: str, output_dir: Path
) -> bool:
    """Download CSV for a single station. Returns True on success."""
    csv_path = output_dir / f"{stid}.csv"
    tmp_path = output_dir / f"{stid}.csv.tmp"

    # Skip if already downloaded and valid
    if csv_path.exists() and _validate_csv(csv_path):
        logger.info("  Skipping %s (already exists and valid)", stid)
        return True

    params = dict(CSV_POST_PARAMS)
    params["stationNumList"] = json.dumps([stid])
    params["elementNumList"] = ELEMENT_NUM_LIST

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(
                TABLE_URL,
                data=params,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")

            # If we got HTML instead of CSV, session may have expired
            if "text/html" in content_type or resp.content[:20].startswith(b"<!DOCTYPE"):
                logger.warning(
                    "  %s attempt %d: Got HTML instead of CSV, refreshing session",
                    stid, attempt,
                )
                session.get(INDEX_URL, timeout=REQUEST_TIMEOUT)
                time.sleep(REQUEST_DELAY)
                continue

            # Decode cp932 → UTF-8
            text = resp.content.decode(CSV_ENCODING)

            # Write to tmp, validate, then rename
            tmp_path.write_text(text, encoding="utf-8")

            if _validate_csv(tmp_path):
                tmp_path.rename(csv_path)
                logger.info("  Downloaded %s (%d bytes)", stid, len(text))
                return True
            else:
                logger.warning(
                    "  %s attempt %d: CSV validation failed (not enough data rows)",
                    stid, attempt,
                )
                tmp_path.unlink(missing_ok=True)

        except (requests.RequestException, UnicodeDecodeError, OSError) as e:
            logger.warning("  %s attempt %d failed: %s", stid, attempt, e)
            tmp_path.unlink(missing_ok=True)

        if attempt < MAX_RETRIES:
            backoff = RETRY_BACKOFF_BASE ** attempt
            logger.info("  Retrying in %.1fs...", backoff)
            time.sleep(backoff)

    logger.error("  FAILED to download %s after %d attempts", stid, MAX_RETRIES)
    return False


def download_all() -> tuple[int, int]:
    """Download CSVs for all stations in stations.json.

    Returns (success_count, failure_count).
    """
    if not STATIONS_JSON.exists():
        raise FileNotFoundError(
            f"Station list not found at {STATIONS_JSON}. Run --discover first."
        )

    with open(STATIONS_JSON, encoding="utf-8") as f:
        stations: list[dict[str, str]] = json.load(f)

    logger.info("Downloading CSVs for %d stations", len(stations))

    RAW_CSV_DIR.mkdir(parents=True, exist_ok=True)
    session = _create_session()
    time.sleep(REQUEST_DELAY)

    success = 0
    failure = 0

    for i, st in enumerate(stations):
        stid = st["stid"]
        logger.info("[%d/%d] Downloading %s (%s)", i + 1, len(stations), stid, st.get("name", ""))

        if _download_one(session, stid, RAW_CSV_DIR):
            success += 1
        else:
            failure += 1

        time.sleep(REQUEST_DELAY)

    logger.info("Download complete: %d success, %d failed", success, failure)
    return success, failure
