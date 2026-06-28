#!/usr/bin/env python3
"""Sensitivity analysis for the v2 light-environment score.

WHAT IT IS: the v2 score has tunable knobs (the 5 objective weights, the floor
saturation k, the Chebyshev tie-breaker rho, and the anchor percentiles). Those
values are judgement calls. Sensitivity analysis asks: *if we wiggle every knob
within a reasonable range, do the top recommendations stay the same, or do they
swing around?* If the same stations stay on top across hundreds of random knob
settings, the recommendation is robust (driven by the data, not by our parameter
choices). If they swing wildly, the result is fragile and any single ranking is
not trustworthy.

It does this by:
  1. one-at-a-time (OAT) perturbations of each knob, and
  2. a Monte-Carlo sweep: N random knob settings, each weight ~U(0.5x, 1.5x) of
     its default, k~U(1.5,4), rho~U(0,0.2), anchor pct in {5/95, 2.5/97.5, 10/90}.
Then it reports, per station, the fraction of runs it lands in the top-10, the
Kendall-tau rank agreement vs the baseline ranking, and a rank-distribution plot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sqlite3
from compute_metrics import (
    DB_PATH, V2_DEFAULTS, V2_OBJECTIVES, LIVABILITY_EXCLUDE,
    load_station_data, compute_metrics, compute_v2_anchors,
    v2_desirabilities, v2_score,
)

for _c in ["Hiragino Sans", "YuGothic", "Noto Sans CJK JP"]:
    if any(f.name == _c for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _c
        break

N_MONTE = 500
SEED = 12345
OUT_PLOT = Path(__file__).resolve().parent.parent / "data" / "plots" / "sensitivity_v2.png"


def load_stations(column: str = "sunshine_normal"):
    """Return (full_clean, candidates).

    Anchors must be computed on `full_clean` (the invariant in compute_v2_anchors /
    ADR 004); only ranking/display uses `candidates` (livability-filtered).
    """
    conn = sqlite3.connect(str(DB_PATH))
    data = load_station_data(conn, column)
    conn.close()
    full_clean = [(meta, compute_metrics(arr)) for stid, (meta, arr) in sorted(data.items())]
    candidates = [(meta, m) for meta, m in full_clean if meta.stid not in LIVABILITY_EXCLUDE]
    return full_clean, candidates


def rank_names(candidates, anchors, params) -> list[str]:
    """Return station names ordered best->worst under the given params."""
    scored = []
    for meta, m in candidates:
        d = v2_desirabilities(m, anchors, params["k_floor"])
        scored.append((v2_score(d, params), meta.name))
    scored.sort(key=lambda x: x[0])
    return [n for _, n in scored]


def crosscheck_against_data_json(candidates, anchors) -> None:
    """Assert the default v2 scores here match the shipped web/data.json (same model)."""
    dj = Path(__file__).resolve().parent.parent / "web" / "data.json"
    if not dj.exists():
        print("  (skip cross-check: web/data.json not found)\n")
        return
    import json
    shipped = {r["name"]: r["score_v2"] for r in json.loads(dj.read_text())["sunshine"]}
    max_err = 0.0
    for meta, m in candidates:
        if meta.name in shipped:
            here = v2_score(v2_desirabilities(m, anchors, V2_DEFAULTS["k_floor"]), V2_DEFAULTS)
            max_err = max(max_err, abs(here - shipped[meta.name]))
    assert max_err < 1e-3, f"sensitivity model diverges from data.json (max err {max_err:.4g})"
    print(f"  cross-check vs web/data.json: max |score_v2 diff| = {max_err:.2e} (OK)\n")


def main() -> None:
    full_clean, candidates = load_stations()
    names = [meta.name for meta, _ in candidates]
    n = len(candidates)
    print(f"Sensitivity analysis: {n} livable sunshine stations "
          f"(anchored on {len(full_clean)} full-clean), "
          f"{N_MONTE} Monte-Carlo runs + OAT perturbations.\n")

    # Anchor sets for the three percentile choices — computed on the FULL cleaned set
    # (NOT the livability-filtered candidates), matching the production exporter.
    anchor_cache = {
        (5.0, 95.0): compute_v2_anchors(full_clean, 5.0, 95.0),
        (2.5, 97.5): compute_v2_anchors(full_clean, 2.5, 97.5),
        (10.0, 90.0): compute_v2_anchors(full_clean, 10.0, 90.0),
    }
    pct_choices = list(anchor_cache.keys())

    # Regression guard: our default model must equal the deployed one.
    crosscheck_against_data_json(candidates, anchor_cache[(5.0, 95.0)])

    baseline = rank_names(candidates, anchor_cache[(5.0, 95.0)], V2_DEFAULTS)
    base_rank = {nm: i for i, nm in enumerate(baseline)}

    # ---- Build the perturbation set ----
    runs: list[list[str]] = []  # each run = ordered names

    # 1) OAT: each weight x0.5 and x1.5; k in {1.5,4}; rho in {0,0.2}; anchors 2.5/97.5 & 10/90
    def with_override(**ov):
        p = dict(V2_DEFAULTS)
        p.update(ov)
        return p

    oat_labels = []
    for obj in V2_OBJECTIVES:
        for factor in (0.5, 1.5):
            p = with_override(**{f"w_{obj}": V2_DEFAULTS[f"w_{obj}"] * factor})
            runs.append(rank_names(candidates, anchor_cache[(5.0, 95.0)], p))
            oat_labels.append(f"w_{obj}x{factor}")
    for k in (1.5, 4.0):
        runs.append(rank_names(candidates, anchor_cache[(5.0, 95.0)], with_override(k_floor=k)))
        oat_labels.append(f"k={k}")
    for rho in (0.0, 0.2):
        runs.append(rank_names(candidates, anchor_cache[(5.0, 95.0)], with_override(rho=rho)))
        oat_labels.append(f"rho={rho}")
    for pct in [(2.5, 97.5), (10.0, 90.0)]:
        runs.append(rank_names(candidates, anchor_cache[pct], V2_DEFAULTS))
        oat_labels.append(f"anchors={pct}")

    n_oat = len(runs)

    # 2) Monte-Carlo: random knobs
    rng = np.random.default_rng(SEED)
    for _ in range(N_MONTE):
        p = dict(V2_DEFAULTS)
        for obj in V2_OBJECTIVES:
            base = V2_DEFAULTS[f"w_{obj}"]
            p[f"w_{obj}"] = base * rng.uniform(0.5, 1.5)
        p["k_floor"] = rng.uniform(1.5, 4.0)
        p["rho"] = rng.uniform(0.0, 0.2)
        pct = pct_choices[rng.integers(len(pct_choices))]
        runs.append(rank_names(candidates, anchor_cache[pct], p))

    # ---- Aggregate ----
    top10_count = {nm: 0 for nm in names}
    rank_lists = {nm: [] for nm in names}
    taus = []
    for r in runs:
        rmap = {nm: i for i, nm in enumerate(r)}
        for nm in r[:10]:
            top10_count[nm] += 1
        for nm in names:
            rank_lists[nm].append(rmap[nm] + 1)
        # Kendall tau vs baseline (rank vectors over common names)
        a = [base_rank[nm] for nm in names]
        b = [rmap[nm] for nm in names]
        tau, _ = stats.kendalltau(a, b)
        taus.append(tau)

    total = len(runs)
    taus = np.array(taus)
    print(f"Total runs: {total} ({n_oat} OAT + {N_MONTE} Monte-Carlo)")
    print(f"Kendall-tau vs baseline ranking:  median={np.median(taus):.3f}  "
          f"min={taus.min():.3f}  (1.0 = identical order)\n")

    # Robust core: in top-10 in >= 80% of runs
    freq = {nm: top10_count[nm] / total for nm in names}
    ordered = sorted(names, key=lambda nm: -freq[nm])
    print(f"{'station':<10} {'top10%':>7} {'base#':>6} {'rank min/med/max':>18}")
    print("-" * 46)
    for nm in ordered[:18]:
        rl = rank_lists[nm]
        print(f"{nm:<10} {freq[nm]*100:>6.0f}% {base_rank[nm]+1:>6} "
              f"{min(rl):>6}/{int(np.median(rl)):>3}/{max(rl):<3}")
    robust = [nm for nm in ordered if freq[nm] >= 0.80]
    print(f"\nRobust core (top-10 in >=80% of {total} runs): {', '.join(robust)}")

    # ---- Plot: rank distribution (box) for the top-15 baseline stations ----
    top15 = baseline[:15]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 8), gridspec_kw={"width_ratios": [1, 1.1]})

    # left: top-10 frequency bars
    bars_nm = ordered[:15][::-1]
    ax1.barh(range(len(bars_nm)), [freq[nm] * 100 for nm in bars_nm], color="#4caf82")
    ax1.set_yticks(range(len(bars_nm)))
    ax1.set_yticklabels([f"{nm} (#{base_rank[nm]+1})" for nm in bars_nm], fontsize=9)
    ax1.set_xlabel("top-10 入りした試行の割合 (%)", fontsize=11)
    ax1.set_title(f"頑健性: {total}試行でtop-10に入る頻度\n(括弧=既定重みでの順位)", fontsize=12)
    ax1.axvline(80, color="#e0a030", ls="--", lw=1, alpha=0.7)
    ax1.grid(axis="x", alpha=0.3)

    # right: rank distribution boxplot for the baseline top-15
    box_data = [rank_lists[nm] for nm in top15]
    ax2.boxplot(box_data, vert=False, labels=top15, showfliers=False,
                medianprops=dict(color="#e05555"))
    ax2.set_xlabel("順位 (低い=良い)", fontsize=11)
    ax2.set_title(f"既定top-15の順位ブレ\n(重み±50%・k・ρ・アンカーを振った{total}試行)", fontsize=12)
    ax2.invert_yaxis()
    ax2.grid(axis="x", alpha=0.3)

    fig.suptitle(f"v2スコア 感度分析 (日照, 居住可能 {n}地点)  "
                 f"Kendall-τ中央値={np.median(taus):.2f}", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    OUT_PLOT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(OUT_PLOT), dpi=150)
    print(f"\nSaved plot: {OUT_PLOT}")


if __name__ == "__main__":
    main()
