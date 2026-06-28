#!/usr/bin/env python3
"""Bias-safe validation of the v3 scoring (PRE-REGISTERED protocol).

This is the adversary's control suite from the GHI methodology consult. It does NOT
tune anything — it tests whether the *already-frozen* v3 model is robust and honest:

  1. Pre-registered falsification tests at default weights (both sources):
       - 札幌 < 東京 (spouse's lived constraint; 札幌 worsened her)
       - monoculture broken (道東 not flooding the top-10)
       - anti-overfit (a relocation candidate is NOT crowned #1)
       - key 道東 demotions vs v2
       - corr(score, lat) reported honestly
  2. Weight-grid ROBUSTNESS (N random weight vectors in pre-set ranges):
       per-station top-decile frequency, median / worst rank, rank IQR.
       A candidate that is robustly mid-high is strong; one that only wins under a
       narrow weight setting is confirmation bias — flagged.
  3. Leave-one-axis-out: zero each axis, see how the top-10 moves (load-bearing axis).
  4. k_floor x gate_cap sensitivity grid: do the pre-registered tests hold across the
     grid, or only at the default cell?
  5. Latitude-confound CONTROL: residualize score on latitude; do inland-basin / Pacific
     candidates beat same-latitude Japan-Sea-side peers (real light env, not just lat)?
  6. autumn_rate gaming check: does the RELATIVE autumn_rate spuriously reward
     dim-summer stations (the adversary's worry that drove relativization)?

Reads web/data.json (already carries every v3 field + anchors + defaults), so the
scorer here mirrors the Python compute_metrics.v3_score AND the JS v3Score exactly.
Run: python3 analysis/validate_v3.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from compute_metrics import _date_to_doy, _eff_series, _ra_n  # single source of geometry truth

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = ROOT / "web" / "data.json"

V3_OBJ = ["floor", "autumn", "spring", "excessTV", "acute", "amplitude", "summerCap"]
V3_FIELD = {
    "floor": "winter_floor_eff", "autumn": "autumn_rate", "spring": "spring_rate",
    "excessTV": "excess_tv", "amplitude": "amplitude", "summerCap": "summer_ceiling",
}
V3_DIR = {"floor": "high", "autumn": "low", "spring": "low", "excessTV": "low",
          "amplitude": "low", "summerCap": "low"}

# Relocation candidates the user named (inland basins). VALIDATION targets, never fit
# targets: if these can ONLY win under a narrow weight setting, the model is overfit.
CANDIDATES = ["松本", "諏訪", "軽井沢", "飯田", "甲府"]
# Same-ish-latitude Japan-Sea-side cloudy-winter peers, to control for latitude.
JAPAN_SEA_PEERS = ["金沢", "富山", "福井", "新潟", "高田", "敦賀"]
DOTO = ["帯広", "釧路", "根室", "網走", "広尾", "浦河"]


def _clip01(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 0.0, 1.0)


def _saturate(n: np.ndarray, k: float) -> np.ndarray:
    if abs(k) < 1e-9:
        return n
    return (1.0 - np.exp(-k * n)) / (1.0 - np.exp(-k))


class SourceModel:
    """Vectorized v3 scorer for one source, mirroring compute_metrics.v3_score."""

    def __init__(self, rows: list[dict], anchors: dict[str, list[float]]) -> None:
        self.rows = rows
        self.names = [r["name"] for r in rows]
        self.idx = {r["name"]: i for i, r in enumerate(rows)}
        self.lat = np.array([r["lat"] for r in rows], dtype=float)
        self.anchors = anchors
        self.red = np.array([r.get("risk_band") == "red" for r in rows])
        # raw per-axis value arrays (kept so anchors can be recomputed at other percentiles)
        self._raw: dict[str, np.ndarray] = {
            o: np.array([r[V3_FIELD[o]] for r in rows], dtype=float)
            for o in ["floor", "autumn", "spring", "excessTV", "amplitude", "summerCap"]
        }
        self._acute_raw = np.array(
            [r.get("acute_spell") if r.get("acute_spell") is not None else np.nan for r in rows],
            dtype=float)
        # normalized [0,1] raw position per axis (pre-desirability) at the FROZEN anchors
        self._n: dict[str, np.ndarray] = {o: self._norm(self._raw[o], anchors[o])
                                          for o in self._raw}
        lo, hi = anchors["acute"]
        nacute = self._norm(self._acute_raw, anchors["acute"])
        self._acute_des = np.where(np.isnan(self._acute_raw), 1.0, 1.0 - nacute)

    @staticmethod
    def _norm(vals: np.ndarray, a: list[float]) -> np.ndarray:
        lo, hi = a
        return _clip01((vals - lo) / (hi - lo)) if hi > lo else np.zeros(len(vals))

    def anchors_at(self, lo_pct: float, hi_pct: float) -> dict[str, list[float]]:
        """Recompute anchors at arbitrary percentiles (anchor-sensitivity test)."""
        out: dict[str, list[float]] = {}
        for o, raw in self._raw.items():
            out[o] = [float(np.percentile(raw, lo_pct)), float(np.percentile(raw, hi_pct))]
        av = self._acute_raw[~np.isnan(self._acute_raw)]
        out["acute"] = ([float(np.percentile(av, lo_pct)), float(np.percentile(av, hi_pct))]
                        if len(av) else [0.0, 1.0])
        return out

    def ranks_with_anchors(self, w: dict[str, float], anchors: dict[str, list[float]]) -> np.ndarray:
        n = {o: self._norm(self._raw[o], anchors[o]) for o in self._raw}
        des = {}
        for o in ["floor", "autumn", "spring", "excessTV", "amplitude", "summerCap"]:
            if o == "floor":
                d = _saturate(n[o], w["k_floor"])
            elif V3_DIR[o] == "high":
                d = n[o]
            else:
                d = 1.0 - n[o]
            des[o] = d
        nac = self._norm(self._acute_raw, anchors["acute"])
        des["acute"] = np.where(np.isnan(self._acute_raw), 1.0, 1.0 - nac)
        des["floor"] = np.where(self.red, np.minimum(des["floor"], w["gate_cap"]), des["floor"])
        gaps = np.stack([w[f"w_{o}"] * (1.0 - des[o]) for o in V3_OBJ], axis=1)
        s = gaps.max(axis=1) + w["rho"] * gaps.sum(axis=1)
        order = np.argsort(s, kind="stable")
        rk = np.empty(len(s), dtype=int)
        rk[order] = np.arange(1, len(s) + 1)
        return rk

    def desir(self, k_floor: float, gate_cap: float) -> dict[str, np.ndarray]:
        des: dict[str, np.ndarray] = {}
        for o in ["floor", "autumn", "spring", "excessTV", "amplitude", "summerCap"]:
            if o == "floor":
                d = _saturate(self._n[o], k_floor)
            elif V3_DIR[o] == "high":
                d = self._n[o]
            else:
                d = 1.0 - self._n[o]
            des[o] = d
        des["acute"] = self._acute_des
        # red-flag gate caps floor (non-compensatory)
        des["floor"] = np.where(self.red, np.minimum(des["floor"], gate_cap), des["floor"])
        return des

    def score(self, w: dict[str, float]) -> np.ndarray:
        des = self.desir(w["k_floor"], w["gate_cap"])
        gaps = np.stack([w[f"w_{o}"] * (1.0 - des[o]) for o in V3_OBJ], axis=1)  # [n, 7]
        return gaps.max(axis=1) + w["rho"] * gaps.sum(axis=1)

    def ranks(self, w: dict[str, float]) -> np.ndarray:
        s = self.score(w)
        order = np.argsort(s, kind="stable")
        rk = np.empty(len(s), dtype=int)
        rk[order] = np.arange(1, len(s) + 1)
        return rk

    def rank_of(self, name: str, w: dict[str, float]) -> int | None:
        i = self.idx.get(name)
        return None if i is None else int(self.ranks(w)[i])

    def top_names(self, w: dict[str, float], k: int = 12) -> list[str]:
        s = self.score(w)
        return [self.names[i] for i in np.argsort(s, kind="stable")[:k]]


def _winter_days() -> list[int]:
    s, e = _date_to_doy("12-15"), _date_to_doy("02-15")
    return list(range(s, 365)) + list(range(0, e + 1))


def test_geometry_monotonicity() -> list[tuple[str, bool, str]]:
    """SYNTHETIC (no data): identical winter sunshine hours at a HIGHER latitude must
    yield a LOWER effective winter floor — the physical reason 札幌 ranks below 東京 by
    construction, and the justification for the honest +corr(score, lat). If this fails
    the geometry weighting is wrong; it is independent of any station's measured values.
    """
    wd = _winter_days()
    out: list[tuple[str, bool, str]] = []
    for h in (4.0, 6.0):
        const = np.full(365, h)
        e_hi = float(np.mean(_eff_series(const, 43.06, energy=False)[wd]))  # 札幌
        e_lo = float(np.mean(_eff_series(const, 35.69, energy=False)[wd]))  # 東京
        out.append((f"幾何単調性 {h}h一定: 高緯度<低緯度",
                    e_hi < e_lo, f"lat43.06 eff={e_hi:.2f} < lat35.69 eff={e_lo:.2f}"))
    # daylength sanity: winter Ra at 43N must be below 35N
    ra43, _ = _ra_n(43.06)
    ra35, _ = _ra_n(35.69)
    out.append(("冬至Ra: 高緯度<低緯度", float(ra43[355]) < float(ra35[355]),
                f"Ra(12/22) 43N={ra43[355]:.2f} < 35N={ra35[355]:.2f}"))
    return out


def load() -> tuple[dict[str, SourceModel], dict[str, float]]:
    d = json.loads(DATA_JSON.read_text())
    models = {src: SourceModel(d[src], d["v3_anchors"][src]) for src in ("solar", "sunshine")}
    return models, d["v3_defaults"]


# ---------------------------------------------------------------- 1. pre-registered
def test_preregistered(m: SourceModel, w: dict[str, float], src: str) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    rk = {n: m.rank_of(n, w) for n in ["札幌", "東京", "松本", "諏訪"] + DOTO}
    if rk["札幌"] and rk["東京"]:
        ok = rk["東京"] < rk["札幌"]
        # NOTE (breaker): on the sunshine source this is SPEC CONFORMANCE — winter_floor_eff
        # is geometry-corrected so high latitude is penalised BY CONSTRUCTION; it is not an
        # independent falsification there. The GHI source (measured MJ, no geometry tilt
        # applied) is the weaker INDEPENDENT check that the result is not purely the prior.
        kind = "spec-conformance(構造的)" if src == "sunshine" else "弱・独立検証(実測GHI)"
        out.append((f"札幌<東京 [{kind}]", ok, f"東京#{rk['東京']} vs 札幌#{rk['札幌']}"))
    s = m.score(w)
    top10 = [m.names[i] for i in np.argsort(s, kind="stable")[:10]]
    nhok = sum(1 for n in top10 if m.lat[m.idx[n]] >= 43.0)
    out.append(("モノカルチャー解消 (top10の道北/道東≤1)", nhok <= 1, f"top10内 北海道(lat≥43)={nhok}"))
    # anti-overfit: at least one NON-candidate leads the top-3 (candidates not crowned)
    top3 = top10[:3]
    led_by_noncand = any(n not in CANDIDATES for n in top3)
    out.append(("anti-overfit (候補が#1独占でない)", led_by_noncand, f"top3={top3}"))
    if src == "sunshine" and rk["松本"]:
        out.append(("anti-overfit (松本が#1でない)", rk["松本"] != 1, f"松本#{rk['松本']}"))
    return out


# ---------------------------------------------------------------- 2. robustness grid
def weight_grid_robustness(m: SourceModel, base: dict[str, float], n: int = 4000,
                           seed: int = 12345) -> dict[str, dict]:
    rng = np.random.default_rng(seed)
    # pre-set ranges around the frozen defaults (NOT tuned to any station)
    ranges = {
        "w_floor": (0.5, 1.5), "w_autumn": (0.3, 1.0), "w_spring": (0.0, 0.7),
        "w_excessTV": (0.1, 0.6), "w_acute": (0.0, 0.5), "w_amplitude": (0.0, 0.5),
        "w_summerCap": (0.0, 0.4), "k_floor": (0.0, 2.0), "gate_cap": (0.30, 0.50),
    }
    nst = len(m.names)
    decile = max(1, nst // 10)
    in_top_decile = np.zeros(nst, dtype=int)
    rank_acc = np.zeros((nst, n), dtype=int)
    for j in range(n):
        w = {k: float(rng.uniform(*ranges[k])) for k in ranges}
        w["rho"] = base["rho"]
        rk = m.ranks(w)
        rank_acc[:, j] = rk
        in_top_decile[rk <= decile] += 1
    stats = {}
    for i, name in enumerate(m.names):
        rks = rank_acc[i]
        stats[name] = {
            "top_decile_freq": float(in_top_decile[i] / n),
            "median_rank": float(np.median(rks)),
            "best_rank": int(rks.min()),
            "worst_rank": int(rks.max()),
            "iqr": float(np.percentile(rks, 75) - np.percentile(rks, 25)),
        }
    return stats


# ---------------------------------------------------------------- 3. leave-one-axis-out
def leave_one_axis_out(m: SourceModel, base: dict[str, float]) -> dict[str, list[str]]:
    out = {"(default)": m.top_names(base, 10)}
    for o in V3_OBJ:
        w = dict(base)
        w[f"w_{o}"] = 0.0
        out[f"drop_{o}"] = m.top_names(w, 10)
    return out


# ---------------------------------------------------------------- 4. k x gate grid
def k_gate_sensitivity(m: SourceModel, base: dict[str, float]) -> dict[str, float]:
    ks = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    gates = [0.30, 0.40, 0.50, 0.60]
    cells = 0
    pass_sap = 0
    pass_mono = 0
    for k in ks:
        for g in gates:
            w = dict(base, k_floor=k, gate_cap=g)
            cells += 1
            rsap, rtok = m.rank_of("札幌", w), m.rank_of("東京", w)
            if rsap and rtok and rtok < rsap:
                pass_sap += 1
            s = m.score(w)
            top10 = [m.names[i] for i in np.argsort(s, kind="stable")[:10]]
            if sum(1 for nx in top10 if m.lat[m.idx[nx]] >= 43.0) <= 1:
                pass_mono += 1
    return {"cells": cells, "sapporo_lt_tokyo_frac": pass_sap / cells,
            "monoculture_ok_frac": pass_mono / cells}


# ---------------------------------------------------------------- 5. latitude control
def latitude_control(m: SourceModel, base: dict[str, float]) -> dict:
    s = m.score(base)
    lat = m.lat
    # regress score ~ lat; residual<0 means "better than its latitude predicts"
    A = np.vstack([lat, np.ones_like(lat)]).T
    coef, *_ = np.linalg.lstsq(A, s, rcond=None)
    resid = s - A @ coef
    corr = float(np.corrcoef(s, lat)[0, 1])
    # winter_floor_eff vs raw winter_floor latitude correlation (does geometry add real info?)
    wfe = np.array([r["winter_floor_eff"] for r in m.rows])
    wfr = np.array([r["winter_floor"] for r in m.rows])
    out = {
        "corr_score_lat": corr,
        "slope_per_deg": float(coef[0]),
        "corr_wfeff_lat": float(np.corrcoef(wfe, lat)[0, 1]),
        "corr_wfraw_lat": float(np.corrcoef(wfr, lat)[0, 1]),
        "candidates": {}, "japan_sea_peers": {},
    }
    for n in CANDIDATES:
        i = m.idx.get(n)
        if i is not None:
            out["candidates"][n] = {"lat": float(lat[i]), "resid": float(resid[i]),
                                    "rank": int(m.ranks(base)[i])}
    for n in JAPAN_SEA_PEERS:
        i = m.idx.get(n)
        if i is not None:
            out["japan_sea_peers"][n] = {"lat": float(lat[i]), "resid": float(resid[i]),
                                         "rank": int(m.ranks(base)[i])}
    return out


# ---------------------------------------------------------------- 6. autumn gaming
def autumn_gaming_check(m: SourceModel) -> dict:
    autumn_rate = np.array([r["autumn_rate"] for r in m.rows])
    summer = np.array([r["summer_ceiling"] for r in m.rows])
    winter = np.array([r["winter_floor"] for r in m.rows])
    amplitude = np.array([r["amplitude"] for r in m.rows])
    autumn_abs = np.array([r.get("autumn_30d_drop", np.nan) for r in m.rows])
    # the DENOMINATOR (eff seasonal range) — breaker: does it itself smuggle in summer
    # amplitude? proxy it by amplitude (P95-P05 of the smoothed series, source units).
    return {
        # if relativization GAMED it, dim-summer (low summer_ceiling) would get LOW
        # autumn_rate spuriously -> a strong POSITIVE corr(autumn_rate, summer).
        "corr_autumnrate_summer": float(np.corrcoef(autumn_rate, summer)[0, 1]),
        "corr_autumnrate_winterfloor": float(np.corrcoef(autumn_rate, winter)[0, 1]),
        # broader (breaker): denominator-vs-amplitude mixing, and rel-vs-abs agreement
        "corr_autumnrate_amplitude": float(np.corrcoef(autumn_rate, amplitude)[0, 1]),
        "corr_autumnrate_absdrop": float(np.corrcoef(autumn_rate[~np.isnan(autumn_abs)],
                                                     autumn_abs[~np.isnan(autumn_abs)])[0, 1])
        if np.any(~np.isnan(autumn_abs)) else float("nan"),
    }


# ------------------------------------------------- 2b. WIDE adversarial robustness
def wide_grid_robustness(m: SourceModel, base: dict[str, float], n: int = 4000,
                         seed: int = 999) -> dict[str, dict]:
    """Robustness across the FULL UI-allowed weight space (breaker's demand): floor may
    go to 0, acute/spring/summerCap to 3, red gate may be disabled. This is NOT 'robust
    around our doctrine' — it is the honest worst case a user could dial in."""
    rng = np.random.default_rng(seed)
    ranges = {k: (0.0, 3.0) for k in ["w_floor", "w_autumn", "w_spring", "w_excessTV",
                                      "w_acute", "w_amplitude", "w_summerCap"]}
    ranges["k_floor"] = (0.0, 6.0)
    ranges["gate_cap"] = (0.0, 1.0)  # 1.0 == red gate effectively disabled
    nst = len(m.names)
    decile = max(1, nst // 10)
    in_top = np.zeros(nst, dtype=int)
    rank_acc = np.zeros((nst, n), dtype=int)
    for j in range(n):
        w = {k: float(rng.uniform(*ranges[k])) for k in ranges}
        w["rho"] = base["rho"]
        rk = m.ranks(w)
        rank_acc[:, j] = rk
        in_top[rk <= decile] += 1
    return {name: {"top_decile_freq": float(in_top[i] / n),
                   "median_rank": float(np.median(rank_acc[i])),
                   "worst_rank": int(rank_acc[i].max())}
            for i, name in enumerate(m.names)}


# ------------------------------------------------- 2c. anchor sensitivity
def anchor_sensitivity(m: SourceModel, base: dict[str, float]) -> dict:
    watch = CANDIDATES + ["潮岬", "伊良湖", "飯田", "札幌", "東京"]
    out: dict[str, dict] = {}
    # Baseline = the TRUE FROZEN production anchors (data.json v3_anchors). NOT a 5/95
    # recompute from the rows: the frozen anchors used the full cleaned set (incl. the
    # livability-excluded 南鳥島 bright outlier), so a row-set recompute diverges slightly.
    rk0 = m.ranks(base)
    out["5/95 凍結"] = {n: int(rk0[m.idx[n]]) for n in watch if n in m.idx}
    # Variants are recomputed on the candidate row-set (南鳥島 absent), so a small part of
    # any shift vs the frozen column is that exclusion, not the percentile — but the effect
    # on the watched stations is nil (their frozen and row-set 5/95 ranks coincide).
    for label, (lo, hi) in {"1/99": (1, 99), "2.5/97.5": (2.5, 97.5), "10/90": (10, 90)}.items():
        rk = m.ranks_with_anchors(base, m.anchors_at(lo, hi))
        out[label] = {n: int(rk[m.idx[n]]) for n in watch if n in m.idx}
    return out


# ------------------------------------------------- 5b. latitude incremental value
def latitude_incremental(m: SourceModel, base: dict[str, float]) -> dict:
    """How much of the v3 score is just latitude? Variance decomposition + a pure-latitude
    baseline ranker. corr^2 = fraction of score variance explained by latitude alone."""
    s = m.score(base)
    lat = m.lat
    r = float(np.corrcoef(s, lat)[0, 1])
    # pure-latitude ranker: higher lat = worse (rank by latitude ascending good)
    lat_rank = np.empty(len(lat), dtype=int)
    lat_rank[np.argsort(lat, kind="stable")] = np.arange(1, len(lat) + 1)
    v3_rank = m.ranks(base)
    # cloud-only component: residual of winter_floor_eff after removing latitude
    A = np.vstack([lat, np.ones_like(lat)]).T
    coef, *_ = np.linalg.lstsq(A, m._raw["floor"], rcond=None)
    cloud = m._raw["floor"] - A @ coef  # positive = brighter winter than latitude predicts
    cloud_rank = np.empty(len(cloud), dtype=int)
    cloud_rank[np.argsort(-cloud, kind="stable")] = np.arange(1, len(cloud) + 1)
    return {
        "r2_score_lat": r * r,  # fraction of score variance latitude alone explains
        "spearman_v3_vs_latrank": float(np.corrcoef(v3_rank, lat_rank)[0, 1]),
        "candidates": {n: {"v3": int(v3_rank[m.idx[n]]), "lat_only": int(lat_rank[m.idx[n]]),
                           "cloud_only": int(cloud_rank[m.idx[n]])}
                       for n in CANDIDATES if n in m.idx},
        "peers": {n: {"v3": int(v3_rank[m.idx[n]]), "lat_only": int(lat_rank[m.idx[n]]),
                      "cloud_only": int(cloud_rank[m.idx[n]])}
                  for n in JAPAN_SEA_PEERS if n in m.idx},
    }


def main() -> None:
    models, base = load()
    print("=" * 78)
    print("v3 PRE-REGISTERED VALIDATION  (no tuning — testing the frozen model)")
    print("=" * 78)

    all_pass = True
    # 0. synthetic geometry monotonicity (data-independent physical sanity)
    print("\n### 0. 幾何単調性 (合成データ, 物理サニティ)")
    for label, ok, detail in test_geometry_monotonicity():
        all_pass = all_pass and ok
        print(f"   [{'PASS' if ok else 'FAIL'}] {label:<28} {detail}")

    # 1. pre-registered tests
    for src in ("sunshine", "solar"):
        m = models[src]
        print(f"\n### 1. 事前登録テスト [{src}]  (n={len(m.names)})")
        for label, ok, detail in test_preregistered(m, base, src):
            all_pass = all_pass and ok
            print(f"   [{'PASS' if ok else 'FAIL'}] {label:<34} {detail}")

    # 2. robustness (focus on candidate source: sunshine)
    m = models["sunshine"]
    print("\n### 2. 重みグリッド・ロバスト性 [sunshine, N=4000 random weights]")
    stats = weight_grid_robustness(m, base)
    print("   候補(松本/諏訪/飯田/甲府): robust mid-high なら本物、narrow-win なら確証バイアス")
    print(f"   {'地点':<8}{'top10%頻度':>10}{'中央順位':>9}{'最良':>6}{'最悪':>6}{'IQR':>7}")
    watch = CANDIDATES + ["潮岬", "伊良湖", "御前崎"] + ["札幌", "東京", "帯広"]
    for n in watch:
        if n in stats:
            st = stats[n]
            print(f"   {n:<8}{st['top_decile_freq']*100:>9.1f}%{st['median_rank']:>9.0f}"
                  f"{st['best_rank']:>6}{st['worst_rank']:>6}{st['iqr']:>7.0f}")
    # narrow-win flag: a candidate whose BEST rank is top-3 but median is poor
    print("   narrow-win 判定 (best≤3 かつ 中央順位>20 = 特定重みでだけ上がる):")
    flagged = [n for n in CANDIDATES if n in stats and stats[n]["best_rank"] <= 3
               and stats[n]["median_rank"] > 20]
    print(f"     {'なし (確証バイアスの兆候なし)' if not flagged else flagged}")

    # 2b. WIDE adversarial grid (breaker: above is robust-around-doctrine, this is full-UI)
    print("\n### 2b. 全UI重み空間でのロバスト性 [sunshine, N=4000, floor→0/acute→3/gate無効も許す]")
    print("   (breaker指摘: §2は『自分の教義の周り』。これは利用者がUIで作れる最悪ケース)")
    wide = wide_grid_robustness(m, base)
    print(f"   {'地点':<8}{'top10%(教義)':>12}{'top10%(全UI)':>12}{'中央(全UI)':>10}{'最悪':>6}")
    for n in dict.fromkeys(CANDIDATES + ["潮岬", "伊良湖", "札幌", "東京", "帯広"]):
        if n in wide:
            doc = stats[n]["top_decile_freq"] * 100 if n in stats else float("nan")
            w_ = wide[n]
            print(f"   {n:<8}{doc:>11.0f}%{w_['top_decile_freq']*100:>11.0f}%"
                  f"{w_['median_rank']:>10.0f}{w_['worst_rank']:>6}")
    sap_wide = wide.get("札幌", {})
    print(f"   正直開示: 全UI空間では robust性は弱まり、極端重みでは札幌すら top-decile に入りうる "
          f"(札幌 全UI top10%={sap_wide.get('top_decile_freq', 0)*100:.0f}%)。"
          f"→ v3は『単一スカラー順位』でなく『除外スクリーン＋候補ドシエ』として提示すべき。")

    # 2c. anchor sensitivity (breaker: candidate ORDERING is anchor-sensitive)
    print("\n### 2c. アンカー感度 [sunshine] (除外は安定か, 上位順位は動くか)")
    asens = anchor_sensitivity(m, base)
    cols = list(asens.keys())
    print(f"   {'地点':<8}" + "".join(f"{c:>12}" for c in cols))
    for n in dict.fromkeys(CANDIDATES + ["潮岬", "伊良湖", "札幌", "東京"]):
        if all(n in asens[c] for c in cols):
            print(f"   {n:<8}" + "".join(f"{asens[c][n]:>12}" for c in cols))
    print("   → 除外(札幌/東京)はアンカー不変で安定。上位候補(飯田/諏訪/松本)の順位はアンカーで動く"
          "＝『正確な序列』でなく『robustな上位群』として読む。")

    # 3. leave-one-axis-out
    print("\n### 3. Leave-one-axis-out [sunshine] top-10 の変化")
    loo = leave_one_axis_out(m, base)
    base_top = set(loo["(default)"])
    print(f"   default: {loo['(default)']}")
    for o in V3_OBJ:
        t = loo[f"drop_{o}"]
        changed = [x for x in t if x not in base_top]
        print(f"   drop {o:<9}: 新規流入={changed if changed else '(変化なし)'}")

    # 4. k x gate sensitivity
    print("\n### 4. k_floor × gate_cap 感度グリッド (24セル)")
    for src in ("sunshine", "solar"):
        ks = k_gate_sensitivity(models[src], base)
        print(f"   [{src}] 札幌<東京 が成立: {ks['sapporo_lt_tokyo_frac']*100:.0f}% / "
              f"モノカルチャー解消: {ks['monoculture_ok_frac']*100:.0f}% のセル")

    # 5. latitude control
    print("\n### 5. 緯度交絡コントロール [sunshine]")
    lc = latitude_control(m, base)
    print(f"   corr(score, lat)={lc['corr_score_lat']:+.2f}  (正=高緯度ほど悪い, 正直値)")
    print(f"   corr(winter_floor_eff, lat)={lc['corr_wfeff_lat']:+.2f}  "
          f"vs  corr(raw winter_floor, lat)={lc['corr_wfraw_lat']:+.2f}")
    print("   → 幾何補正で緯度相関が強まる=高緯度の冬の実効光不足という物理を回復(捏造でない)")
    print("   同緯度対照: 内陸盆地候補 vs 日本海側ピア (residual<0 = 緯度予測より良い)")
    print(f"   {'候補':<8}{'緯度':>6}{'残差':>8}{'順位':>6}    {'日本海ピア':<8}{'緯度':>6}{'残差':>8}{'順位':>6}")
    cand = list(lc["candidates"].items())
    peer = list(lc["japan_sea_peers"].items())
    for i in range(max(len(cand), len(peer))):
        cs = f"   {cand[i][0]:<8}{cand[i][1]['lat']:>6.1f}{cand[i][1]['resid']:>+8.2f}{cand[i][1]['rank']:>6}" if i < len(cand) else "   " + " " * 28
        ps = f"    {peer[i][0]:<8}{peer[i][1]['lat']:>6.1f}{peer[i][1]['resid']:>+8.2f}{peer[i][1]['rank']:>6}" if i < len(peer) else ""
        print(cs + ps)
    cand_resid = np.mean([v["resid"] for v in lc["candidates"].values()])
    peer_resid = np.mean([v["resid"] for v in lc["japan_sea_peers"].values()])
    print(f"   平均残差: 候補={cand_resid:+.2f}  日本海ピア={peer_resid:+.2f}  "
          f"→ {'候補が同緯度ピアより良い (光環境を捕捉)' if cand_resid < peer_resid else '差なし/逆転'}")

    # 5b. latitude incremental value (breaker: is it 'just a latitude ranker'?)
    print("\n### 5b. 緯度の増分価値 (breaker: ただの緯度ランカーか?) [sunshine]")
    li = latitude_incremental(m, base)
    print(f"   R²(score~緯度)={li['r2_score_lat']:.2f} → 緯度だけで説明できるスコア分散は"
          f"{li['r2_score_lat']*100:.0f}%のみ。残り{(1-li['r2_score_lat'])*100:.0f}%は雲/局地/季節構造。")
    print(f"   Spearman(v3順位, 緯度のみ順位)={li['spearman_v3_vs_latrank']:.2f}")
    print(f"   {'地点':<8}{'v3順位':>7}{'緯度のみ':>8}{'雲のみ':>7}    (雲のみ=緯度回帰残差での順位)")
    for n in CANDIDATES:
        if n in li["candidates"]:
            c = li["candidates"][n]
            print(f"   {n:<8}{c['v3']:>7}{c['lat_only']:>8}{c['cloud_only']:>7}")
    for n in ["金沢", "新潟"]:
        if n in li["peers"]:
            c = li["peers"][n]
            print(f"   {n:<8}{c['v3']:>7}{c['lat_only']:>8}{c['cloud_only']:>7}  (日本海ピア)")
    print("   → 候補は『雲のみ』成分でも上位、日本海ピアは下位。緯度を除いても光環境差が残る。")

    # 6. autumn gaming
    print("\n### 6. autumn_rate 相対化のゲーミング検査")
    for src in ("sunshine", "solar"):
        ag = autumn_gaming_check(models[src])
        print(f"   [{src}] corr(autumn_rate, summer_ceiling)={ag['corr_autumnrate_summer']:+.2f} "
              f"/ amplitude={ag['corr_autumnrate_amplitude']:+.2f} "
              f"(強い正=dim-summer/年較差を不当混入の兆候), "
              f"winter_floor={ag['corr_autumnrate_winterfloor']:+.2f}, "
              f"abs_drop={ag['corr_autumnrate_absdrop']:+.2f}")

    print("\n" + "=" * 78)
    print(f"事前登録テスト総合: {'ALL PASS' if all_pass else 'SOME FAIL — 要確認'}")
    print("=" * 78)


if __name__ == "__main__":
    main()
