import json
import logging
import re
import time
from typing import TypedDict

import requests
from bs4 import BeautifulSoup

from crawler.config import (
    INDEX_URL,
    RAW_HTML_DIR,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    STATIONS_JSON,
    STATION_URL,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


class StationInfo(TypedDict):
    stid: str
    name: str
    prid: str
    kansoku: str
    latitude: float | None
    longitude: float | None
    elevation: float | None
    title_raw: str
    observation_ended: str | None


def _create_session() -> requests.Session:
    """Create a session with JMA cookies by visiting index.php."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ja,en;q=0.9",
    })
    logger.info("Establishing session with %s", INDEX_URL)
    resp = session.get(INDEX_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    logger.info("Session established. Cookies: %s", list(session.cookies.keys()))
    return session


def _parse_dms(dms_str: str) -> float | None:
    """Parse degrees-minutes string like '34度46.7分' to decimal degrees."""
    m = re.match(r"(\d+)度([\d.]+)分", dms_str)
    if not m:
        return None
    degrees = int(m.group(1))
    minutes = float(m.group(2))
    return round(degrees + minutes / 60, 6)


def _parse_title(title: str) -> dict[str, str | float | None]:
    """Parse the title attribute from a station element.

    Example title:
    '地点名：大島泉津 カナ:オオシマセンヅ 北緯：34度46.7分 東経：139度25.3分 標高：49m 2016年11月30日に観測終了'
    """
    result: dict[str, str | float | None] = {
        "latitude": None,
        "longitude": None,
        "elevation": None,
        "observation_ended": None,
    }

    lat_match = re.search(r"北緯[：:](\d+度[\d.]+分)", title)
    if lat_match:
        result["latitude"] = _parse_dms(lat_match.group(1))

    lon_match = re.search(r"東経[：:](\d+度[\d.]+分)", title)
    if lon_match:
        result["longitude"] = _parse_dms(lon_match.group(1))

    elev_match = re.search(r"標高[：:]([\d.]+)m", title)
    if elev_match:
        result["elevation"] = float(elev_match.group(1))

    end_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日に観測終了", title)
    if end_match:
        result["observation_ended"] = (
            f"{end_match.group(1)}-{int(end_match.group(2)):02d}-{int(end_match.group(3)):02d}"
        )

    return result


def _fetch_prefectures(session: requests.Session) -> list[str]:
    """Fetch all prefecture/region IDs (prid) from JMA station endpoint."""
    logger.info("Fetching prefecture list from %s", STATION_URL)
    resp = session.post(STATION_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    html = resp.text
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_HTML_DIR / "prefectures.html").write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    prids: list[str] = []
    for inp in soup.select('input[name="prid"]'):
        prid = inp.get("value", "")
        if prid and prid not in prids:
            prids.append(str(prid))

    logger.info("Found %d prefecture/region IDs", len(prids))
    return prids


def _fetch_stations_for_prid(
    session: requests.Session, prid: str
) -> list[StationInfo]:
    """Fetch stations for a given prid and parse them."""
    resp = session.post(
        STATION_URL,
        data={"pd": prid},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    html = resp.text
    RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_HTML_DIR / f"pref_{prid}.html").write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    stations: list[StationInfo] = []
    seen_in_page: set[str] = set()

    for div in soup.select("div.station"):
        stid_input = div.select_one('input[name="stid"]')
        stname_input = div.select_one('input[name="stname"]')
        kansoku_input = div.select_one('input[name="kansoku"]')

        if not stid_input:
            continue

        stid = str(stid_input.get("value", ""))
        if not stid or stid in seen_in_page:
            continue
        seen_in_page.add(stid)

        stname = str(stname_input.get("value", "")) if stname_input else ""
        kansoku = str(kansoku_input.get("value", "")) if kansoku_input else ""
        title_raw = str(div.get("title", ""))

        parsed = _parse_title(title_raw)

        stations.append(StationInfo(
            stid=stid,
            name=stname,
            prid=prid,
            kansoku=kansoku,
            latitude=parsed["latitude"],  # type: ignore[arg-type]
            longitude=parsed["longitude"],  # type: ignore[arg-type]
            elevation=parsed["elevation"],  # type: ignore[arg-type]
            title_raw=title_raw,
            observation_ended=parsed["observation_ended"],  # type: ignore[arg-type]
        ))

    return stations


def discover_stations() -> list[StationInfo]:
    """Discover all stations with solar radiation data (kansoku[3]=='1').

    Returns deduplicated list of stations, also saved to data/stations.json.
    """
    session = _create_session()
    time.sleep(REQUEST_DELAY)

    prids = _fetch_prefectures(session)
    time.sleep(REQUEST_DELAY)

    all_stations: list[StationInfo] = []
    seen_stids: set[str] = set()

    for i, prid in enumerate(prids):
        logger.info("Fetching stations for prid=%s (%d/%d)", prid, i + 1, len(prids))
        stations = _fetch_stations_for_prid(session, prid)
        logger.info("  Found %d stations in prid=%s", len(stations), prid)

        for st in stations:
            if st["stid"] not in seen_stids:
                seen_stids.add(st["stid"])
                all_stations.append(st)

        time.sleep(REQUEST_DELAY)

    logger.info("Total unique stations: %d", len(all_stations))

    # Filter for solar radiation capability: kansoku[3] == '1'
    solar_stations = [
        st for st in all_stations
        if len(st["kansoku"]) > 3 and st["kansoku"][3] == "1"
    ]
    logger.info(
        "Stations with solar radiation (kansoku[3]=='1'): %d", len(solar_stations)
    )

    # Save to JSON
    STATIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(STATIONS_JSON, "w", encoding="utf-8") as f:
        json.dump(solar_stations, f, ensure_ascii=False, indent=2)
    logger.info("Saved station list to %s", STATIONS_JSON)

    return solar_stations
