#!/usr/bin/env python3
"""Compute winter dark-spell persistence (and related lived-volatility metrics)
for ALL sunshine stations, from real daily observations 1991-2020.

Pre-registered definition (see docs/experiments/2026-06-28_daily-volatility.md):
  dark day        = daily sunshine < THRESHOLD hours
  season          = Nov-Mar (low-light season)
  L_y             = longest consecutive dark-day run within one winter season
  dark_spell_CVaR80 = mean of the worst 20% of L_y across all winters
Robustness columns: thresholds {1,2,3}h, and split periods 1991-2005 / 2006-2020.

Output: data/dark_spell_metrics.csv (raw daily cached under data/jma_daily/).
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from daily_volatility import _session, fetch_station  # reuse fetch + cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("dark_spell_all")

DB = Path(__file__).resolve().parent.parent / "data" / "jma_solar.db"
OUT = Path(__file__).resolve().parent.parent / "data" / "dark_spell_metrics.csv"
WINTER_MONTHS = {11, 12, 1, 2, 3}


def _season(y: int, m: int) -> int:
    return y + 1 if m in (11, 12) else y


def _spells(series: dict[str, float], thr: float, y0: int = 1991, y1: int = 2020) -> list[int]:
    """Longest consecutive dark-run per winter season (Nov-Mar), filtered to [y0,y1]."""
    by: dict[int, list[tuple[str, float]]] = {}
    for k, v in series.items():
        y, m = int(k[:4]), int(k[5:7])
        if m in WINTER_MONTHS:
            sy = _season(y, m)
            if y0 <= sy <= y1:
                by.setdefault(sy, []).append((k, v))
    out = []
    for sy, items in by.items():
        if len(items) < 120:  # need most of a Nov-Mar season
            continue
        items.sort()
        run = best = 0
        for _, v in items:
            dark = (not np.isnan(v)) and v < thr
            run = run + 1 if dark else 0
            best = max(best, run)
        out.append(best)
    return out


def _cvar80(Ls: list[int]) -> float:
    if len(Ls) < 5:
        return float("nan")
    arr = np.sort(np.array(Ls, float))
    k = max(1, int(np.ceil(0.2 * len(arr))))
    return float(np.mean(arr[-k:]))  # worst 20% = longest spells


def metrics(series: dict[str, float]) -> dict[str, float]:
    items = sorted(series.items())
    vals = np.array([v for _, v in items], float)
    mo = np.array([int(k[5:7]) for k, _ in items])
    w = np.isin(mo, list(WINTER_MONTHS))
    wv = vals[w]
    yr = np.array([int(k[:4]) for k, _ in items])
    seas = np.where(np.isin(mo, [11, 12]), yr + 1, yr)
    seas_means = [np.nanmean(vals[w & (seas == s)]) for s in sorted(set(seas[w]))
                  if np.isfinite(vals[w & (seas == s)]).sum() > 120]
    cv = float(np.nanstd(seas_means) / np.nanmean(seas_means) * 100) if seas_means else float("nan")
    return dict(
        winter_mean=float(np.nanmean(wv)),
        cv=cv,
        dark2_pct=float(np.nanmean(wv < 2.0) * 100),
        cvar_t1=_cvar80(_spells(series, 1.0)),
        cvar_t2=_cvar80(_spells(series, 2.0)),
        cvar_t3=_cvar80(_spells(series, 3.0)),
        cvar_early=_cvar80(_spells(series, 2.0, 1991, 2005)),
        cvar_late=_cvar80(_spells(series, 2.0, 2006, 2020)),
    )


def main() -> None:
    conn = sqlite3.connect(str(DB))
    stations = conn.execute(
        "SELECT DISTINCT s.stid, s.name, s.latitude FROM stations s "
        "JOIN daily_data d ON s.stid=d.stid "
        "WHERE d.sunshine_normal IS NOT NULL AND s.latitude IS NOT NULL "
        "ORDER BY s.stid"
    ).fetchall()
    log.info("Computing dark-spell metrics for %d sunshine stations", len(stations))
    s = _session()
    rows = []
    for i, (stid, name, lat) in enumerate(stations):
        try:
            series = fetch_station(s, stid)
            nok = sum(1 for v in series.values() if np.isfinite(v))
            if nok < 3000:
                log.warning("[%d/%d] %s %s: only %d valid days, skip", i + 1, len(stations), stid, name, nok)
                continue
            m = metrics(series)
            rows.append((stid, name, lat, nok, m))
            log.info("[%d/%d] %s %s: winter=%.2fh cvar2=%.1f cv=%.1f",
                     i + 1, len(stations), stid, name, m["winter_mean"], m["cvar_t2"], m["cv"])
        except Exception as e:
            log.warning("[%d/%d] %s %s: error %s", i + 1, len(stations), stid, name, e)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("stid,name,lat,n_days,winter_mean_h,interannual_cv_pct,dark2_pct,"
                "spell_cvar80_t1,spell_cvar80_t2,spell_cvar80_t3,spell_cvar80_1991_2005,spell_cvar80_2006_2020\n")
        for stid, name, lat, nok, m in rows:
            f.write(f"{stid},{name},{lat},{nok},{m['winter_mean']:.3f},{m['cv']:.2f},{m['dark2_pct']:.2f},"
                    f"{m['cvar_t1']:.2f},{m['cvar_t2']:.2f},{m['cvar_t3']:.2f},"
                    f"{m['cvar_early']:.2f},{m['cvar_late']:.2f}\n")
    log.info("Wrote %s (%d stations)", OUT, len(rows))
    print("DONE", len(rows))


if __name__ == "__main__":
    main()
