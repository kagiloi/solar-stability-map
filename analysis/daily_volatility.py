#!/usr/bin/env python3
"""Fetch REAL daily observed sunshine (JMA obsdl) 1991-2020 for a focus set of
stations and compute lived-volatility metrics — the things the 30-yr *normals*
smooth away, which is what the "rate of change / delta" hypothesis actually needs.

NASA POWER couldn't do this (its ~100km cells average out local day-to-day cloud
variation). Station-resolution daily observations can.

Caches raw per-station/year CSVs under data/jma_daily/ so re-runs don't re-crawl.
Outputs data/daily_volatility.csv + data/plots/daily_volatility.png.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crawler.config import (
    INDEX_URL, TABLE_URL, USER_AGENT, CSV_POST_PARAMS, REQUEST_TIMEOUT,
    CSV_ENCODING, REQUEST_DELAY,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("daily_vol")

for _c in ["Hiragino Sans", "YuGothic", "Noto Sans CJK JP"]:
    if any(f.name == _c for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _c
        break

CACHE = Path(__file__).resolve().parent.parent / "data" / "jma_daily"
OUT_CSV = Path(__file__).resolve().parent.parent / "data" / "daily_volatility.csv"
OUT_PLOT = Path(__file__).resolve().parent.parent / "data" / "plots" / "daily_volatility.png"

Y0, Y1 = 1991, 2020
DARK_HOURS = 2.0  # a "dark day" = < 2h of bright sunshine (essentially sunless)

# group, name, obsdl stid
STATIONS = [
    ("盆地", "松本", "s47618"), ("盆地", "諏訪", "s47620"),
    ("盆地", "軽井沢", "s47622"), ("盆地", "飯田", "s47637"),
    ("道東", "帯広", "s47417"), ("道東", "釧路", "s47418"), ("道東", "根室", "s47420"),
    ("太平洋", "静岡", "s47656"), ("太平洋", "名古屋", "s47636"),
    ("日本海", "金沢", "s47605"), ("日本海", "新潟", "s47604"),
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"})
    s.get(INDEX_URL, timeout=REQUEST_TIMEOUT)
    return s


def _post(s: requests.Session, stid: str, y0: int, y1: int) -> str | None:
    p = dict(CSV_POST_PARAMS)
    p["stationNumList"] = json.dumps([stid])
    p["elementNumList"] = '[["401",""]]'  # sunshine hours only
    p["ymdList"] = json.dumps([str(y0), str(y1), "1", "12", "1", "31"])
    for attempt in range(1, 4):
        try:
            r = s.post(TABLE_URL, data=p, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            if r.content[:20].startswith(b"<!DOCTYPE") or "html" in r.headers.get("Content-Type", ""):
                log.warning("    HTML (limit/session?) y=%d-%d attempt %d", y0, y1, attempt)
                s.get(INDEX_URL, timeout=REQUEST_TIMEOUT); time.sleep(REQUEST_DELAY)
                continue
            return r.content.decode(CSV_ENCODING)
        except (requests.RequestException, UnicodeDecodeError) as e:
            log.warning("    err %s attempt %d", e, attempt); time.sleep(2 ** attempt)
    return None


def fetch_station(s: requests.Session, stid: str) -> dict[str, float]:
    """Return {YYYY-MM-DD: sunshine_hours} for Y0..Y1, cached per chunk."""
    out: dict[str, float] = {}
    # Try 10-year chunks (obsdl limits total cells); fall back per-year.
    for c0 in range(Y0, Y1 + 1, 10):
        c1 = min(c0 + 9, Y1)
        cache = CACHE / f"{stid}_{c0}_{c1}.csv"
        txt = cache.read_text(encoding="utf-8") if cache.exists() else None
        if txt is None:
            txt = _post(s, stid, c0, c1)
            if txt and txt.count("\n") > 100:
                CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(txt, encoding="utf-8")
                time.sleep(REQUEST_DELAY)
            else:  # chunk failed → per-year
                txt = None
                for y in range(c0, c1 + 1):
                    yc = CACHE / f"{stid}_{y}_{y}.csv"
                    t = yc.read_text(encoding="utf-8") if yc.exists() else _post(s, stid, y, y)
                    if t and not yc.exists():
                        CACHE.mkdir(parents=True, exist_ok=True)
                        yc.write_text(t, encoding="utf-8"); time.sleep(REQUEST_DELAY)
                    if t:
                        _parse_into(t, out)
                continue
        _parse_into(txt, out)
    return out


def _parse_into(txt: str, out: dict[str, float]) -> None:
    for line in txt.splitlines():
        if not line[:2].isdigit() or "/" not in line[:11]:
            continue
        parts = line.split(",")
        ymd = parts[0].strip()
        val = parts[1].strip() if len(parts) > 1 else ""
        try:
            y, m, d = ymd.split("/")
            key = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            out[key] = float(val) if val != "" else np.nan
        except ValueError:
            continue


def winter_metrics(series: dict[str, float]) -> dict[str, float]:
    """Lived-volatility metrics from a daily sunshine series."""
    items = sorted(series.items())
    dates = [k for k, _ in items]
    vals = np.array([series[k] for k in dates], float)
    yr = np.array([int(k[:4]) for k in dates])
    mo = np.array([int(k[5:7]) for k in dates])
    is_w = np.isin(mo, [12, 1, 2])
    season = np.where(mo == 12, yr + 1, yr)  # Dec → next year's winter

    # interannual winter reliability
    seas_means = []
    for sy in sorted(set(season[is_w])):
        sel = is_w & (season == sy)
        v = vals[sel]
        if np.isfinite(v).sum() > 60:
            seas_means.append(np.nanmean(v))
    seas_means = np.array(seas_means)
    cv = float(np.nanstd(seas_means) / np.nanmean(seas_means) * 100)

    wv = vals[is_w]
    dvol = float(np.nanmean(np.abs(np.diff(wv))))           # winter day-to-day swing
    winter_mean = float(np.nanmean(wv))
    dark_frac = float(np.nanmean(wv < DARK_HOURS) * 100)    # % winter days essentially sunless

    # consecutive dark-day spells per winter → mean & worst max-run
    max_runs = []
    for sy in sorted(set(season[is_w])):
        v = vals[is_w & (season == sy)]
        run = best = 0
        for x in v:
            run = run + 1 if (np.isfinite(x) and x < DARK_HOURS) else 0
            best = max(best, run)
        max_runs.append(best)
    return dict(
        winter_mean=winter_mean, cv=cv, dvol=dvol, dark_frac=dark_frac,
        spell_mean=float(np.mean(max_runs)), spell_worst=float(np.max(max_runs)),
    )


def main() -> None:
    s = _session()
    rows = []
    for grp, name, stid in STATIONS:
        log.info("Fetching %s (%s)...", name, stid)
        series = fetch_station(s, stid)
        n_ok = sum(1 for v in series.values() if np.isfinite(v))
        log.info("  %s: %d days (%d valid)", name, len(series), n_ok)
        if n_ok < 3000:
            log.warning("  %s: too few valid days, skipping", name); continue
        m = winter_metrics(series)
        rows.append((grp, name, m))

    print("\n=== 実日別 日照時間 1991-2020 の冬(DJF) volatility (station解像度) ===")
    print(f"{'群':<6}{'地点':<8}{'冬日照h':>7}{'当外れCV%':>9}{'日々|Δ|':>8}{'暗い日%':>7}{'連続暗:平均':>11}{'最悪':>5}")
    for grp, name, m in rows:
        print(f"{grp:<6}{name:<8}{m['winter_mean']:>7.2f}{m['cv']:>9.1f}{m['dvol']:>8.2f}"
              f"{m['dark_frac']:>7.1f}{m['spell_mean']:>11.1f}{m['spell_worst']:>5.0f}")
    print(f"\n暗い日=日照<{DARK_HOURS}h。連続暗=その冬の最長連続暗日数(平均/30冬中の最悪)")

    with open(OUT_CSV, "w", encoding="utf-8") as f:
        f.write("group,name,winter_mean_h,interannual_cv_pct,winter_daily_swing,dark_day_pct,spell_mean_days,spell_worst_days\n")
        for grp, name, m in rows:
            f.write(f"{grp},{name},{m['winter_mean']:.3f},{m['cv']:.2f},{m['dvol']:.3f},"
                    f"{m['dark_frac']:.2f},{m['spell_mean']:.2f},{m['spell_worst']:.0f}\n")
    log.info("Wrote %s", OUT_CSV)

    # plot: winter brightness vs worst consecutive-dark spell, sized by interannual CV
    fig, ax = plt.subplots(figsize=(11, 8))
    colors = {"盆地": "#4caf82", "道東": "#6c9cff", "太平洋": "#e0a030", "日本海": "#e05555"}
    for grp, name, m in rows:
        ax.scatter(m["spell_worst"], m["winter_mean"], s=80 + m["cv"] * 30,
                   c=colors.get(grp, "#999"), alpha=0.8, edgecolor="#222")
        ax.annotate(f"{name}", (m["spell_worst"], m["winter_mean"]), fontsize=9,
                    xytext=(5, 4), textcoords="offset points")
    for grp, c in colors.items():
        ax.scatter([], [], c=c, label=grp)
    ax.set_xlabel(f"最悪の連続暗日数 (1991-2020で最長, 日照<{DARK_HOURS}h)", fontsize=11)
    ax.set_ylabel("冬(DJF)平均日照時間 [h/day]", fontsize=11)
    ax.set_title("実日別の冬の光環境: 明るさ × 連続曇天リスク (点の大きさ=年々の当たり外れCV)\n左上=理想(明るく連続曇天が短い)", fontsize=12)
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); OUT_PLOT.parent.mkdir(parents=True, exist_ok=True); fig.savefig(str(OUT_PLOT), dpi=150)
    log.info("Wrote %s", OUT_PLOT)
    print("\nDONE")


if __name__ == "__main__":
    main()
