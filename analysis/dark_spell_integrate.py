#!/usr/bin/env python3
"""Bias-safe integration of the winter dark-spell metric into the location screen.

Per the agreed method (docs/experiments/2026-06-28_daily-volatility.md): NO tuned
scalar weight. Instead — (1) robustness checks across the arbitrary analytic forks,
(2) a pre-registered risk band, (3) a Pareto frontier on the lived-volatility axes,
(4) an overfit test: do the candidate basins stay top-decile across ALL threshold /
split-period variants, or only for a cherry-picked one?

Reads data/dark_spell_metrics.csv (real daily) + web/data.json (v2 normals context).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
M = ROOT / "data" / "dark_spell_metrics.csv"
DJSON = ROOT / "web" / "data.json"

# Pre-registered, mechanism-anchored risk bands on the worst-winter dark spell
# (CVaR80 of the longest consecutive sub-2h-sunshine run, in days). NOT tuned to
# any station: a ~1.5-2 week sunless spell is the clinically concerning anchor.
GREEN_MAX = 5.0   # <=5 day worst spells: low risk
RED_MIN = 10.0    # >=10 day worst spells: high risk
BASINS = {"松本", "諏訪", "軽井沢", "飯田"}
WATCH = {"松本", "諏訪", "軽井沢", "飯田", "帯広", "釧路", "根室", "静岡", "名古屋", "金沢", "新潟", "甲府", "長野"}


def load():
    rows = list(csv.DictReader(open(M, encoding="utf-8")))
    for r in rows:
        for k in r:
            if k not in ("stid", "name"):
                r[k] = float(r[k]) if r[k] not in ("", "nan") else np.nan
    dj = {x["stid"]: x for x in json.load(open(DJSON))["sunshine"]}
    return rows, dj


def band(cvar):
    return "green" if cvar <= GREEN_MAX else ("red" if cvar >= RED_MIN else "yellow")


def pareto(rows, objs):
    """objs: list of (key, direction) where direction='up' good-high, 'down' good-low."""
    flags = []
    for i, ri in enumerate(rows):
        dom = False
        for j, rj in enumerate(rows):
            if i == j:
                continue
            better_eq = all((rj[k] >= ri[k]) if d == "up" else (rj[k] <= ri[k]) for k, d in objs)
            strictly = any((rj[k] > ri[k]) if d == "up" else (rj[k] < ri[k]) for k, d in objs)
            if better_eq and strictly:
                dom = True
                break
        flags.append(not dom)
    return flags


def good_set(rows, thr_key):
    """'good' = adequately bright (winter_mean >= median) AND short dark spells
    (bottom-decile on the given threshold's CVaR). Used for the overfit test."""
    wm = np.array([r["winter_mean_h"] for r in rows])
    cv = np.array([r[thr_key] for r in rows])
    bright = wm >= np.nanmedian(wm)
    short = cv <= np.nanpercentile(cv, 25)
    return {r["stid"] for r, b, s in zip(rows, bright, short) if b and s}


def main():
    rows, dj = load()
    valid = [r for r in rows if np.isfinite(r["spell_cvar80_t2"])]
    print(f"Stations: {len(valid)}\n")

    # --- 1. ROBUSTNESS: is the metric stable across the arbitrary forks? ---
    def sp(a, b):
        x = [r[a] for r in valid]; y = [r[b] for r in valid]
        return stats.spearmanr(x, y, nan_policy="omit")[0]
    print("=== 頑健性: dark-spell順位は閾値/期間の選択に頑健か (Spearman) ===")
    print(f"  CVaR80  t2 vs t1(閾値1h)      = {sp('spell_cvar80_t2','spell_cvar80_t1'):.3f}")
    print(f"  CVaR80  t2 vs t3(閾値3h)      = {sp('spell_cvar80_t2','spell_cvar80_t3'):.3f}")
    print(f"  CVaR80  1991-2005 vs 2006-2020 = {sp('spell_cvar80_1991_2005','spell_cvar80_2006_2020'):.3f}")
    print(f"  winter_floor(normals) vs dark-spell: {sp('winter_mean_h','spell_cvar80_t2'):.3f} (負=明るい冬ほど短い踊り場=非冗長な別軸)")

    # --- 2. RISK BANDS (pre-registered) ---
    for r in valid:
        r["band"] = band(r["spell_cvar80_t2"])
    from collections import Counter
    bc = Counter(r["band"] for r in valid)
    print(f"\n=== risk band (worst冬の連続暗 CVaR80, 閾値2h): green<= {GREEN_MAX} < yellow < {RED_MIN} <=red ===")
    print(f"  green={bc['green']}  yellow={bc['yellow']}  red={bc['red']}")
    reds = sorted([r for r in valid if r["band"] == "red"], key=lambda r: -r["spell_cvar80_t2"])
    print(f"  RED(高リスク)上位: " + ", ".join(f"{r['name']}({r['spell_cvar80_t2']:.0f}d)" for r in reds[:12]))

    # --- 3. OVERFIT TEST: do basins stay top across ALL variants? ---
    variants = ["spell_cvar80_t1", "spell_cvar80_t2", "spell_cvar80_t3",
                "spell_cvar80_1991_2005", "spell_cvar80_2006_2020"]
    sets = {v: good_set(valid, v) for v in variants}
    name_by = {r["stid"]: r["name"] for r in valid}
    print("\n=== 過適合検出: '明るい&踊り場短い' good集合に、仕様を変えても残るか ===")
    print(f"{'地点':<8}" + "".join(f"{v.replace('spell_cvar80_',''):>10}" for v in variants) + "  残存")
    for r in valid:
        if r["name"] in WATCH:
            marks = "".join(("  ✓     " if r["stid"] in sets[v] else "  -     ") for v in variants)
            n_in = sum(r["stid"] in sets[v] for v in variants)
            print(f"  {r['name']:<6}" + marks + f"  {n_in}/5")
    core = set.intersection(*sets.values())
    print(f"\n  全5仕様で good に残った地点(頑健core, {len(core)}): " + ", ".join(sorted(name_by[s] for s in core)))

    # --- 4. PARETO frontier (lived-volatility axes; no scalar) ---
    pf = pareto(valid, [("winter_mean_h", "up"), ("spell_cvar80_t2", "down"), ("interannual_cv_pct", "down")])
    print(f"\n=== Pareto最適 (冬明るさ↑ × 連続暗↓ × 当たり外れ↓), {sum(pf)}地点 ===")
    for r, p in sorted(zip(valid, pf), key=lambda x: -x[0]["winter_mean_h"]):
        if p:
            star = "★" if r["name"] in BASINS else " "
            v2 = dj.get(r["stid"], {})
            print(f" {star}{r['name']:<8} 冬{r['winter_mean_h']:.1f}h 連続暗{r['spell_cvar80_t2']:>4.0f}d "
                  f"当外れ{r['interannual_cv_pct']:.0f}% [{r['band']}]  (v2#{v2.get('rank','-')})")

    # --- 5. candidate placement vs the 'most stable' v2 winners ---
    print("\n=== 注目地点: 実日別 vs v2(平年値) ===")
    print(f"  {'地点':<8}{'冬h':>5}{'連続暗d':>7}{'当外れ%':>7}{'band':>7}{'v2#':>5}")
    order = sorted([r for r in valid if r["name"] in WATCH], key=lambda r: r["spell_cvar80_t2"])
    for r in order:
        v2 = dj.get(r["stid"], {})
        print(f"  {r['name']:<8}{r['winter_mean_h']:>5.1f}{r['spell_cvar80_t2']:>7.0f}"
              f"{r['interannual_cv_pct']:>7.0f}{r['band']:>7}{v2.get('rank','-'):>5}")


if __name__ == "__main__":
    main()
