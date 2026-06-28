#!/usr/bin/env python3
"""Off-winter (Apr-Oct) ACUTE dark-spell persistence for all stations, from real
daily observations 1991-2020. This is the v3 'acute_spell' axis: the genuinely
orthogonal, live-validated mechanism (e.g. Tokyo 2026-06 梅雨, 3 consecutive 0.0h
days) that the WINTER dark-spell metric (Nov-Mar) misses by construction.

Definition (pre-registered, season-disjoint from the Dec15-Feb15 winter floor):
  dark day  = daily sunshine < 2.0 h
  season    = Apr 1 .. Oct 31 (one calendar year; NO Nov overlap with the winter gate)
  L_y       = longest consecutive dark-day run within one off-winter season
  acute_spell_cvar80 = mean of the worst 20% of L_y across years

Reads the cached daily CSVs under data/jma_daily/ directly (no network).
Output: data/acute_spell_metrics.csv (derived only; raw obs stay gitignored).
"""
from __future__ import annotations

import glob
import sqlite3
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jma_solar.db"
DAILY = ROOT / "data" / "jma_daily"
OUT = ROOT / "data" / "acute_spell_metrics.csv"

DARK_HOURS = 2.0
OFF_WINTER = {4, 5, 6, 7, 8, 9, 10}  # Apr-Oct, season-disjoint from Dec15-Feb15 floor


def _read_daily(stid: str) -> dict[str, float]:
    """{YYYY-MM-DD: sunshine_hours} from cached CSVs (missing days skipped)."""
    out: dict[str, float] = {}
    for path in sorted(glob.glob(str(DAILY / f"{stid}_*.csv"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line[:2].isdigit() or "/" not in line[:11]:
                    continue
                parts = line.split(",")
                val = parts[1].strip() if len(parts) > 1 else ""
                if val == "":
                    continue
                try:
                    y, m, d = (int(x) for x in parts[0].split("/"))
                    out[f"{y:04d}-{m:02d}-{d:02d}"] = float(val)
                except ValueError:
                    continue
    return out


def _cvar80(runs: list[int]) -> float:
    if len(runs) < 5:
        return float("nan")
    arr = np.sort(np.array(runs, float))
    k = max(1, int(np.ceil(0.2 * len(arr))))
    return float(np.mean(arr[-k:]))


def acute_spell(series: dict[str, float]) -> float:
    by_year: dict[int, list[tuple[str, float]]] = {}
    for k, v in series.items():
        m = int(k[5:7])
        if m in OFF_WINTER:
            by_year.setdefault(int(k[:4]), []).append((k, v))
    runs: list[int] = []
    for _y, items in by_year.items():
        if len(items) < 150:  # need most of an Apr-Oct season (~214 days)
            continue
        items.sort()
        run = best = 0
        for _, v in items:
            dark = (not np.isnan(v)) and v < DARK_HOURS
            run = run + 1 if dark else 0
            best = max(best, run)
        runs.append(best)
    return _cvar80(runs)


def main() -> None:
    conn = sqlite3.connect(str(DB))
    stations = conn.execute(
        "SELECT DISTINCT s.stid, s.name FROM stations s JOIN daily_data d ON s.stid=d.stid "
        "WHERE d.sunshine_normal IS NOT NULL AND s.latitude IS NOT NULL ORDER BY s.stid"
    ).fetchall()
    conn.close()

    rows = []
    for stid, name in stations:
        series = _read_daily(stid)
        nok = sum(1 for v in series.values() if not np.isnan(v))
        if nok < 3000:
            continue
        cvar = acute_spell(series)
        if np.isnan(cvar):
            continue
        rows.append((stid, name, cvar))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("stid,name,acute_spell_cvar80\n")
        for stid, name, cvar in rows:
            f.write(f"{stid},{name},{cvar:.2f}\n")
    print(f"DONE {OUT} ({len(rows)} stations)")


if __name__ == "__main__":
    main()
