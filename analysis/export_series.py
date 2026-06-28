#!/usr/bin/env python3
"""Export per-station daily series for the web workbench's Compare view.

Produces web/series.json:
  { "meta": {...}, "stations": { stid: {
        name, lat, lon,
        solar: [365] | null,   # daily NORMAL 全天日射量 (MJ/m2/day), Jan1..Dec31 (no Feb29)
        sun:   [365] | null,   # daily NORMAL 日照時間 (h/day)
        wdark: [151] | null,   # winter dark-day PROBABILITY climatology, Nov1..Mar31
  } } }

- solar / sun are the published 30-year daily normals straight from the crawler DB
  (data/jma_solar.db -> daily_data.solar_normal / sunshine_normal).
- wdark is DERIVED from the real daily observations 1991-2020 cached under
  data/jma_daily/ : for each winter calendar day, the fraction of years whose
  observed sunshine was < 2h (essentially sunless). The raw daily obs stay
  gitignored (license/volume); only this aggregate climatology is shipped.

Reads the daily cache files directly (glob) -> no network access.
"""
from __future__ import annotations

import glob
import json
import math
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jma_solar.db"
DAILY = ROOT / "data" / "jma_daily"
OUT = ROOT / "web" / "series.json"

DARK_HOURS = 2.0

# Winter calendar order Nov 1 .. Mar 31 (151 days, Feb 29 excluded).
WINTER_DAYS: list[tuple[int, int]] = (
    [(11, d) for d in range(1, 31)]
    + [(12, d) for d in range(1, 32)]
    + [(1, d) for d in range(1, 32)]
    + [(2, d) for d in range(1, 29)]
    + [(3, d) for d in range(1, 32)]
)


def load_normals() -> dict[str, dict]:
    """{stid: {name, lat, lon, solar:[365]|None, sun:[365]|None}} from the DB normals."""
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    stations: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT stid, name, latitude, longitude FROM stations"
    ):
        stations[r["stid"]] = {
            "name": r["name"],
            "lat": r["latitude"],
            "lon": r["longitude"],
            "_solar": {},
            "_sun": {},
        }
    for r in conn.execute(
        "SELECT stid, date, solar_normal, sunshine_normal FROM daily_data"
    ):
        st = stations.get(r["stid"])
        if st is None:
            continue
        if r["solar_normal"] is not None:
            st["_solar"][r["date"]] = r["solar_normal"]
        if r["sunshine_normal"] is not None:
            st["_sun"][r["date"]] = r["sunshine_normal"]
    conn.close()

    # Build ordered 365-day arrays (date keys are 'MM-DD', sort = Jan1..Dec31).
    # Exclude any leap day so the array stays exactly 365 (JS assumes a fixed
    # non-leap day-of-year index and meta.normal_days = 365).
    out: dict[str, dict] = {}
    for stid, st in stations.items():
        solar_days = sorted(d for d in st["_solar"] if d != "02-29")
        sun_days = sorted(d for d in st["_sun"] if d != "02-29")
        rec: dict = {"name": st["name"], "lat": st["lat"], "lon": st["lon"]}
        rec["solar"] = (
            [round(st["_solar"][d], 2) for d in solar_days]
            if len(solar_days) >= 365 else None
        )
        rec["sun"] = (
            [round(st["_sun"][d], 2) for d in sun_days]
            if len(sun_days) >= 365 else None
        )
        # only keep stations that have at least one normal series
        if rec["solar"] is not None or rec["sun"] is not None:
            out[stid] = rec
    return out


def _parse_daily(stid: str) -> dict[tuple[int, int], list[float]]:
    """Read all cached daily CSVs for a station -> {(month,day): [values across years]}.

    Skips missing (empty) values. Feb 29 is folded in but never used by WINTER_DAYS.
    """
    by_md: dict[tuple[int, int], list[float]] = {}
    for path in sorted(glob.glob(str(DAILY / f"{stid}_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line[:2].isdigit() or "/" not in line[:11]:
                    continue
                parts = line.split(",")
                ymd = parts[0].strip()
                val = parts[1].strip() if len(parts) > 1 else ""
                if val == "":
                    continue
                try:
                    _y, m, d = (int(x) for x in ymd.split("/"))
                    v = float(val)
                except ValueError:
                    continue
                by_md.setdefault((m, d), []).append(v)
    return by_md


def winter_dark_prob(stid: str) -> list[float] | None:
    """P(observed sunshine < 2h) per winter calendar day, Nov1..Mar31, from real obs."""
    by_md = _parse_daily(stid)
    if not by_md:
        return None
    out: list[float] = []
    total_obs = 0
    for md in WINTER_DAYS:
        vals = by_md.get(md, [])
        if vals:
            dark = sum(1 for v in vals if v < DARK_HOURS)
            out.append(round(dark / len(vals), 3))
            total_obs += len(vals)
        else:
            out.append(None)  # no observations for this calendar day
    # need a reasonably complete winter record to be meaningful
    if total_obs < 151 * 10:  # ~<10 yrs avg coverage
        return None
    return out


def main() -> None:
    stations = load_normals()
    n_wdark = 0
    for stid, rec in stations.items():
        wd = winter_dark_prob(stid)
        rec["wdark"] = wd
        if wd is not None:
            n_wdark += 1

    payload = {
        "meta": {
            "dark_hours": DARK_HOURS,
            "winter_days": [f"{m:02d}-{d:02d}" for m, d in WINTER_DAYS],
            "normal_days": 365,
            "note": (
                "solar/sun = JMA 30-yr daily normals (MJ/m2, h). "
                "wdark = P(daily sunshine < 2h) climatology from real obs 1991-2020 "
                "(Nov1..Mar31). null = series unavailable for that station."
            ),
        },
        "stations": stations,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = OUT.stat().st_size / 1024
    n_solar = sum(1 for r in stations.values() if r["solar"])
    n_sun = sum(1 for r in stations.values() if r["sun"])
    print(
        f"DONE {OUT}  stations={len(stations)} "
        f"solar={n_solar} sun={n_sun} wdark={n_wdark}  {size_kb:.0f} KB"
    )


if __name__ == "__main__":
    main()
