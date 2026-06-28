#!/usr/bin/env python3
"""Phase 2: Compute stability and transition metrics for all stations.

Reads daily normal values from SQLite, computes metrics per station,
outputs CSVs, scatter plots, validation correlations, and ranked candidates.
"""

import csv
import json
import math
import sqlite3
import sys
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from scipy import stats

# Configure Japanese font for matplotlib
_jp_font = None
for _candidate in ["Hiragino Sans", "YuGothic", "Noto Sans CJK JP"]:
    if any(f.name == _candidate for f in fm.fontManager.ttflist):
        _jp_font = _candidate
        break
if _jp_font:
    plt.rcParams["font.family"] = _jp_font

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jma_solar.db"
OUT_DIR = Path(__file__).resolve().parent.parent / "data"
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Primary metric fields used in scoring
PRIMARY_FIELDS: list[str] = [
    "mean_val", "amplitude", "ramp", "winter_floor",
    "spring_30d_gain", "autumn_30d_drop",
    "spring_rise_days", "autumn_fall_days",
]

# Validation metric fields (sunshine vs solar comparison)
VALIDATION_FIELDS: list[str] = [
    "amplitude", "winter_floor", "spring_30d_gain", "autumn_30d_drop",
]


@dataclass
class StationMeta:
    stid: str
    name: str
    latitude: float | None
    longitude: float | None
    prid: str


@dataclass
class Metrics:
    # Primary: Annual
    mean_val: float
    amplitude: float          # (P95-P05)/mean of smoothed
    ramp: float               # mean(abs(diff(smoothed)))
    total_variation: float    # sum(abs(diff(smoothed))) over the year (= ramp*365); "整流済み" total swing
    excess_tv: float          # total_variation - 2*(max-min): non-seasonal reversals (e.g. 梅雨 plateau)

    # Primary: Seasonal
    winter_floor: float       # mean smoothed Dec15-Feb15

    # Auxiliary: Seasonal
    summer_ceiling: float     # mean smoothed Jun15-Aug15

    # Primary: Spring transition
    spring_30d_gain: float    # max 30-day increase (trough->peak)
    spring_rise_days: float   # days from 25% to 75% level

    # Primary: Autumn transition
    autumn_30d_drop: float    # max 30-day decrease (peak->trough), abs
    autumn_fall_days: float   # days from 75% to 25% level

    # Auxiliary: Spring
    spring_ramp: float        # max daily increase (noisy, supplementary)
    spring_30d_gain_rel: float  # gain / (max-min)

    # Auxiliary: Autumn
    autumn_ramp: float        # max daily decrease (abs, noisy)
    autumn_30d_drop_rel: float  # drop / (max-min)

    # Auxiliary: Trough/peak info
    trough_day: int           # DOY of smoothed minimum
    peak_day: int             # DOY of smoothed maximum

    # v3: effective-light (geometry-corrected) metrics. See docs/adr/008.
    winter_floor_eff: float   # mean E(d) over Dec15-Feb15 (beam-equiv MJ or measured GHI)
    autumn_rate: float        # max 30d drop of E in fixed autumn window, / seasonal range (relative)
    spring_rate: float        # max 30d gain of E in fixed spring window, / seasonal range (relative)
    autumn_rate_abs: float    # same drop, absolute (sensitivity variant)
    spring_rate_abs: float    # same gain, absolute (sensitivity variant)
    eff_trough_day: int       # DOY of smoothed E minimum (should land near the solstice)


def _circular_ma(values: np.ndarray, window: int = 15) -> np.ndarray:
    """Compute moving average with circular wrap-around."""
    n = len(values)
    padded = np.concatenate([values[-window:], values, values[:window]])
    kernel = np.ones(window) / window
    smoothed = np.convolve(padded, kernel, mode="same")
    return smoothed[window:window + n]


def _date_to_doy(mmdd: str) -> int:
    """Convert 'MM-DD' to day-of-year index (0-based)."""
    month, day = int(mmdd[:2]), int(mmdd[3:5])
    days_before = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    return days_before[month - 1] + day - 1


def _mmdd_to_doy(mmdd: str) -> int:
    """Convert 'MM-DD' to approximate day-of-year (0-based)."""
    return _date_to_doy(mmdd)


def _doy_range(start_mmdd: str, end_mmdd: str) -> list[int]:
    """Return list of DOY indices for a date range (inclusive, circular)."""
    s = _mmdd_to_doy(start_mmdd)
    e = _mmdd_to_doy(end_mmdd)
    if s <= e:
        return list(range(s, e + 1))
    else:
        return list(range(s, 365)) + list(range(0, e + 1))


def _circular_range(start: int, end: int, n: int = 365) -> list[int]:
    """Return indices from start to end, wrapping around at n."""
    if start <= end:
        return list(range(start, end + 1))
    else:
        return list(range(start, n)) + list(range(0, end + 1))


# ---------------------------------------------------------------------------
# v3 solar geometry (deterministic; FAO-56). Used to build an "effective light"
# series E(d) that discounts high-latitude winters via shorter days + lower noon
# sun, WITHOUT any fitted coefficient or altitude term (so it sidesteps ADR 005's
# rejection of self-estimated GHI). See docs/adr/008. Verified by analysis/v3_prototype.py.
# ---------------------------------------------------------------------------
def _ra_n(lat_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """FAO-56 extraterrestrial daily irradiation Ra [MJ/m2/d] and daylength N [h], 365 days (DOY 1..365)."""
    J = np.arange(1, 366)
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * np.cos(2 * np.pi * J / 365)
    decl = 0.409 * np.sin(2 * np.pi * J / 365 - 1.39)
    x = np.clip(-np.tan(phi) * np.tan(decl), -1.0, 1.0)
    ws = np.arccos(x)
    N = 24.0 / np.pi * ws
    Ra = (1440.0 / np.pi) * 0.0820 * dr * (
        ws * np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.sin(ws)
    )
    return Ra, N


def _eff_series(smoothed: np.ndarray, lat: float | None, energy: bool) -> np.ndarray:
    """Effective-light daily series E(d).

    energy=True (GHI source): E = measured MJ directly (already embeds geometry).
    energy=False (sunshine):  E = clip(n/N, 0, 1) * Ra  (beam-equivalent MJ; a=0, b=1 —
                              each measured bright hour weighted by clear-sky energy of one
                              daylight hour at that latitude/season). No fitted coefficients.
    """
    if energy or lat is None:
        return smoothed
    Ra, N = _ra_n(lat)
    ratio = np.clip(smoothed / np.where(N > 0.1, N, 0.1), 0.0, 1.0)
    return ratio * Ra


# v3 transition windows: FIXED astronomical windows (0-based DOY), NOT data-driven
# peak->trough (which mislocates autumn to the 梅雨 trough, e.g. Tokyo DOY 172).
_AUTUMN_WIN = (_date_to_doy("09-23"), _date_to_doy("12-22"))  # autumnal equinox -> winter solstice
_SPRING_WIN = (_date_to_doy("12-22"), _date_to_doy("03-20"))  # winter solstice -> vernal equinox (mirror)


def compute_metrics(values_365: np.ndarray, lat: float | None = None, energy: bool = False) -> Metrics:
    """Compute all metrics from a 365-element array of daily normals.

    `lat` + `energy` drive the v3 effective-light metrics: energy=True for the GHI
    (solar) source (E = measured MJ), energy=False for sunshine (E = geometry-weighted
    beam-equivalent). v1/v2 metrics are unaffected by these args.
    """
    raw = values_365
    smoothed = _circular_ma(raw, window=15)

    mean_val = float(np.mean(raw))

    # Amplitude from smoothed values
    p05 = float(np.percentile(smoothed, 5))
    p95 = float(np.percentile(smoothed, 95))
    amplitude = (p95 - p05) / mean_val if mean_val > 0 else 0.0

    # Daily diffs of smoothed (circular)
    diffs = np.diff(np.concatenate([smoothed, smoothed[:1]]))
    ramp = float(np.mean(np.abs(diffs)))
    total_variation = float(np.sum(np.abs(diffs)))  # = ramp * 365

    # Winter floor (Dec15-Feb15)
    winter_days = _doy_range("12-15", "02-15")
    winter_floor = float(np.mean(smoothed[winter_days]))

    # Summer ceiling (Jun15-Aug15)
    summer_days = _doy_range("06-15", "08-15")
    summer_ceiling = float(np.mean(smoothed[summer_days]))

    # Trough and peak (from smoothed)
    trough_day = int(np.argmin(smoothed))
    peak_day = int(np.argmax(smoothed))
    val_min = float(smoothed[trough_day])
    val_max = float(smoothed[peak_day])
    val_range = val_max - val_min

    # Excess total variation: how much the curve "doubles back" beyond the unavoidable
    # single seasonal sweep (a clean one-peak year has TV == 2*range). Captures 梅雨-type
    # plateaus / mid-season reversals that the 4 hand-picked transition metrics miss.
    excess_tv = max(total_variation - 2.0 * val_range, 0.0)

    # Spring: trough -> peak
    spring_indices = _circular_range(trough_day, peak_day)
    spring_smoothed = smoothed[spring_indices] if len(spring_indices) > 1 else smoothed

    spring_diffs = np.diff(spring_smoothed)
    spring_ramp = float(np.max(spring_diffs)) if len(spring_diffs) > 0 else 0.0

    if len(spring_smoothed) >= 31:
        gains_30d = spring_smoothed[30:] - spring_smoothed[:-30]
        spring_30d_gain = float(np.max(gains_30d))
    else:
        spring_30d_gain = float(spring_smoothed[-1] - spring_smoothed[0]) if len(spring_smoothed) > 1 else 0.0

    spring_30d_gain_rel = spring_30d_gain / val_range if val_range > 0 else 0.0

    # Spring rise days (25% -> 75%)
    level_25 = val_min + 0.25 * val_range
    level_75 = val_min + 0.75 * val_range
    day_25 = next((i for i, v in enumerate(spring_smoothed) if v >= level_25), 0)
    day_75 = next((i for i, v in enumerate(spring_smoothed) if v >= level_75), len(spring_smoothed) - 1)
    spring_rise_days = float(max(day_75 - day_25, 1))

    # Autumn: peak -> trough (next cycle)
    autumn_indices = _circular_range(peak_day, trough_day)
    autumn_smoothed = smoothed[autumn_indices] if len(autumn_indices) > 1 else smoothed

    autumn_diffs = np.diff(autumn_smoothed)
    autumn_ramp = float(np.abs(np.min(autumn_diffs))) if len(autumn_diffs) > 0 else 0.0

    if len(autumn_smoothed) >= 31:
        drops_30d = autumn_smoothed[:-30] - autumn_smoothed[30:]
        autumn_30d_drop = float(np.max(drops_30d))
    else:
        autumn_30d_drop = float(autumn_smoothed[0] - autumn_smoothed[-1]) if len(autumn_smoothed) > 1 else 0.0

    autumn_30d_drop_rel = autumn_30d_drop / val_range if val_range > 0 else 0.0

    # Autumn fall days (75% -> 25%)
    day_75a = next((i for i, v in enumerate(autumn_smoothed) if v <= level_75), 0)
    day_25a = next((i for i, v in enumerate(autumn_smoothed) if v <= level_25), len(autumn_smoothed) - 1)
    autumn_fall_days = float(max(day_25a - day_75a, 1))

    # --- v3: effective-light series E(d) and fixed-window seasonal rates ---
    eff = _eff_series(smoothed, lat, energy)
    winter_floor_eff = float(np.mean(eff[winter_days]))
    eff_trough_day = int(np.argmin(eff))
    eff_range = float(np.percentile(eff, 95) - np.percentile(eff, 5))

    # Autumn decline: steepest 30-day drop of E within the FIXED autumn window.
    a0, a1 = _AUTUMN_WIN
    autumn_drop_eff = 0.0
    for i in range(a0, a1 - 30 + 1):
        autumn_drop_eff = max(autumn_drop_eff, float(eff[i] - eff[i + 30]))
    # Spring rise: steepest 30-day gain of E within the FIXED spring window (circular).
    sp_idx = _circular_range(_SPRING_WIN[0], _SPRING_WIN[1])
    eff_sp = eff[sp_idx]
    spring_gain_eff = 0.0
    if len(eff_sp) >= 31:
        gains = eff_sp[30:] - eff_sp[:-30]
        # floored at 0 for symmetry with autumn_drop_eff (a max() seeded at 0.0); a station
        # that only ever loses light over its spring window has spring_rate 0, not negative.
        spring_gain_eff = max(0.0, float(np.max(gains))) if len(gains) else 0.0
    autumn_rate = autumn_drop_eff / eff_range if eff_range > 0 else 0.0
    spring_rate = spring_gain_eff / eff_range if eff_range > 0 else 0.0

    return Metrics(
        mean_val=round(mean_val, 3),
        amplitude=round(amplitude, 4),
        ramp=round(ramp, 4),
        total_variation=round(total_variation, 3),
        excess_tv=round(excess_tv, 3),
        winter_floor=round(winter_floor, 3),
        summer_ceiling=round(summer_ceiling, 3),
        spring_30d_gain=round(spring_30d_gain, 3),
        spring_rise_days=round(spring_rise_days, 1),
        autumn_30d_drop=round(autumn_30d_drop, 3),
        autumn_fall_days=round(autumn_fall_days, 1),
        spring_ramp=round(spring_ramp, 4),
        spring_30d_gain_rel=round(spring_30d_gain_rel, 4),
        autumn_ramp=round(autumn_ramp, 4),
        autumn_30d_drop_rel=round(autumn_30d_drop_rel, 4),
        trough_day=trough_day,
        peak_day=peak_day,
        winter_floor_eff=round(winter_floor_eff, 3),
        autumn_rate=round(autumn_rate, 4),
        spring_rate=round(spring_rate, 4),
        autumn_rate_abs=round(autumn_drop_eff, 3),
        spring_rate_abs=round(spring_gain_eff, 3),
        eff_trough_day=eff_trough_day,
    )


def load_station_data(
    conn: sqlite3.Connection, column: str
) -> dict[str, tuple[StationMeta, np.ndarray]]:
    """Load daily normal values for all stations that have data.

    Returns {stid: (StationMeta, values_365)} where values_365 is ordered by DOY.
    Skips stations that don't have exactly 365 days of data.
    Uses NaN for any missing day within the 365 range.
    """
    rows = conn.execute(
        f"""
        SELECT s.stid, s.name, s.latitude, s.longitude, s.prid,
               d.date, d.{column}
        FROM stations s
        JOIN daily_data d ON s.stid = d.stid
        WHERE d.{column} IS NOT NULL
        ORDER BY s.stid, d.date
        """,
    ).fetchall()

    stations: dict[str, tuple[StationMeta, dict[int, float]]] = {}

    for stid, name, lat, lon, prid, date_mmdd, val in rows:
        if stid not in stations:
            meta = StationMeta(stid=stid, name=name, latitude=lat, longitude=lon, prid=prid)
            stations[stid] = (meta, {})
        doy = _mmdd_to_doy(date_mmdd)
        stations[stid][1][doy] = val

    result: dict[str, tuple[StationMeta, np.ndarray]] = {}
    skipped: list[str] = []
    for stid, (meta, doy_dict) in stations.items():
        # Stage-0 data hygiene (centralized so every artifact — CSVs, rankings,
        # validation, web export — sees the same cleaned station universe):
        # drop stations with no latitude (e.g. 昭和基地 / Antarctica), whose
        # Southern-Hemisphere / polar seasonal cycle breaks every metric.
        if meta.latitude is None:
            skipped.append(f"{stid} ({meta.name}, no latitude)")
            continue
        if len(doy_dict) != 365:
            skipped.append(f"{stid} ({meta.name}, {len(doy_dict)} days)")
            continue
        arr = np.full(365, np.nan)
        for doy, val in doy_dict.items():
            if 0 <= doy < 365:
                arr[doy] = val
        result[stid] = (meta, arr)

    if skipped:
        print(f"  Skipped {len(skipped)} stations with != 365 days: {', '.join(skipped)}")

    return result


def write_csv(
    path: Path,
    data: list[tuple[StationMeta, Metrics]],
    value_label: str,
    extra_fields: list[str] | None = None,
    extra_values: dict[str, list[float]] | None = None,
) -> None:
    """Write station_metrics CSV."""
    metric_fields = [f.name for f in fields(Metrics)]
    header = ["stid", "name", "latitude", "longitude", "prid", "value_type"] + metric_fields
    if extra_fields:
        header += extra_fields

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for idx, (meta, m) in enumerate(data):
            row = [
                meta.stid, meta.name, meta.latitude, meta.longitude, meta.prid,
                value_label,
            ] + [getattr(m, fn) for fn in metric_fields]
            if extra_fields and extra_values:
                for ef in extra_fields:
                    row.append(extra_values[ef][idx])
            writer.writerow(row)


def compute_z_scores(
    results: list[tuple[StationMeta, Metrics]],
) -> dict[str, np.ndarray]:
    """Compute z-scores for all primary metric fields."""
    z: dict[str, np.ndarray] = {}
    for field in PRIMARY_FIELDS:
        vals = np.array([getattr(m, field) for _, m in results])
        mean = np.mean(vals)
        std = np.std(vals)
        z[field] = (vals - mean) / std if std > 0 else np.zeros(len(vals))
    return z


def compute_overall_scores(
    z: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute stability, transition, and overall scores from z-scores.

    Lower = better (more stable, gentler transitions, brighter winter).
    """
    stability = z["amplitude"] + z["ramp"] - z["winter_floor"]
    transition = (
        z["spring_30d_gain"] + z["autumn_30d_drop"]
        - z["spring_rise_days"] - z["autumn_fall_days"]
    )
    overall = stability + transition
    return stability, transition, overall


# ---------------------------------------------------------------------------
# Phase 2 v2 scoring — science-grounded, absolute-anchored, non-compensatory.
# Rationale and citations: docs/adr/004_phase2-scoring-v2.md
#   - winter_floor: saturating (concave) reward, NOT a hard gate (Zeitzer 2000:
#     circadian dose-response saturates; no population "cliff" for winter light).
#   - spring rate (spring_30d_gain): Tier-1 penalty — strongest patient-outcome
#     evidence that *rate of change* matters (Bauer multi-site, PMID 28722128).
#   - autumn rate (autumn_30d_drop): Tier-1b, weighted below spring.
#   - excess_tv / amplitude: Tier-2 (weak direct evidence) — exploratory low weight.
#   - aggregation: augmented weighted Chebyshev (weak axis dominates = non-compensatory),
#     so "bright-enough winter AND low delta" cannot be traded off — but without a cliff.
# (a)/(b)/(c) priorities are the SAME formula with different weights (tune in the web UI).
# ---------------------------------------------------------------------------

# Livability filter: stations that are not realistic permanent-residence candidates.
# Applied to RANKINGS/shortlist only, AFTER anchoring (so it drops rows from the ranking
# but never re-derives the v2 anchors — see compute_v2_anchors / ADR 004). The full
# metric catalogue (station_metrics_*.csv) keeps every station.
#   s47991 南鳥島: no civilian residence (JMSDF/JCG personnel only).
# Inhabited-but-remote islands (父島 s47971, 南大東 s47945, etc.) are kept; add them here
# if medical access becomes a hard criterion.
LIVABILITY_EXCLUDE: set[str] = {"s47991"}

V2_OBJECTIVES: list[str] = ["floor", "spring", "autumn", "excessTV", "amplitude"]

# Option (a) "balanced" defaults.
V2_DEFAULTS: dict[str, float] = {
    "w_floor": 1.0,
    "w_spring": 1.0,      # Tier 1: best-evidenced "delta" (Bauer spring-insolation-rate)
    "w_autumn": 0.7,      # Tier 1b: depression-side, mechanistic; below spring per the science
    "w_excessTV": 0.3,    # Tier 2: 梅雨 plateau / non-seasonal reversals (weak direct evidence)
    "w_amplitude": 0.3,   # Tier 2: seasonal swing
    "k_floor": 2.5,       # winter_floor saturation; k->0 linear, larger = more concave (dark end matters more)
    "rho": 0.1,           # augmented-Chebyshev tie-breaker (avoids weak-Pareto optima)
}

# Winter dark-spell risk bands (from REAL daily obs, analysis/dark_spell_all.py).
# Pre-registered, mechanism-anchored on the worst-winter longest consecutive
# sub-2h-sunshine run (CVaR80, days). This is an OVERLAY / red flag, NOT a score
# weight — it is ~77% redundant with winter_floor (r=-0.88); see ADR 006.
DARK_GREEN_MAX: float = 5.0   # <=5 day worst spells = low risk
DARK_RED_MIN: float = 10.0    # >=10 day worst spells = high risk (Japan Sea etc.)
DARK_SPELL_CSV = OUT_DIR / "dark_spell_metrics.csv"

# Each objective: (Metrics field, direction). direction "high" = more is better, "low" = less is better.
_V2_FIELDS: dict[str, tuple[str, str]] = {
    "floor": ("winter_floor", "high"),
    "spring": ("spring_30d_gain", "low"),
    "autumn": ("autumn_30d_drop", "low"),
    "excessTV": ("excess_tv", "low"),
    "amplitude": ("amplitude", "low"),
}


def compute_v2_anchors(
    results: list[tuple[StationMeta, Metrics]], lo_pct: float = 5.0, hi_pct: float = 95.0
) -> dict[str, list[float]]:
    """[lo, hi] anchors per objective from the 5th/95th percentile of `results`.

    These are *generation-frozen*, not physically absolute: they are derived from the
    station set passed in. Within the app they are fixed (baked into data.json), so
    sliding weights or hiding rows never re-anchors — that is the property z-scores lack.
    But they DO shift if the underlying station universe changes. IMPORTANT: always pass
    the FULL cleaned set here (never a candidate/livability-filtered subset), so adding a
    downstream filter only drops rows from the ranking, not from the anchoring. For a
    stable JMA-normals dataset this is sufficient; a future hardening is to check in
    fixed anchor constants. See docs/adr/004_phase2-scoring-v2.md.
    """
    anchors: dict[str, list[float]] = {}
    for obj, (field, _dir) in _V2_FIELDS.items():
        vals = np.array([getattr(m, field) for _, m in results], dtype=float)
        anchors[obj] = [float(np.percentile(vals, lo_pct)), float(np.percentile(vals, hi_pct))]
    return anchors


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _saturate(n: float, k: float) -> float:
    """Concave saturating map [0,1] -> [0,1]; k>0 emphasizes the low (dark) end."""
    if abs(k) < 1e-9:
        return n
    return (1.0 - math.exp(-k * n)) / (1.0 - math.exp(-k))


def v2_desirabilities(
    m: Metrics, anchors: dict[str, list[float]], k_floor: float
) -> dict[str, float]:
    """Map each objective to a desirability in [0, 1] (1 = ideal) using absolute anchors."""
    desir: dict[str, float] = {}
    for obj, (field, direction) in _V2_FIELDS.items():
        lo, hi = anchors[obj]
        n = _clip01((getattr(m, field) - lo) / (hi - lo)) if hi > lo else 0.0
        if obj == "floor":
            desir[obj] = _saturate(n, k_floor)          # high & saturating
        elif direction == "high":
            desir[obj] = n
        else:
            desir[obj] = 1.0 - n                         # low is better
    return desir


def v2_score(desir: dict[str, float], params: dict[str, float]) -> float:
    """Augmented weighted Chebyshev distance to the ideal point. Lower = better."""
    gaps = [params[f"w_{obj}"] * (1.0 - desir[obj]) for obj in V2_OBJECTIVES]
    return max(gaps) + params["rho"] * sum(gaps)


# ---------------------------------------------------------------------------
# Phase 2 v3 scoring — effective-daylight sufficiency + autumn rate + acute spell.
# Pre-registered design: docs/adr/008. Same non-compensatory augmented-Chebyshev
# aggregation as v2, but:
#   - master axis = winter_floor_eff (geometry-corrected; enforces 札幌<東京 by
#     construction, demotes dim high-latitude winters for a physical reason).
#   - autumn_rate / spring_rate computed on the effective-light series E(d) over
#     FIXED astronomical windows, RELATIVE (/seasonal range) so a dim-summer station
#     with little to fall from does not earn a spurious "gentle autumn" reward.
#   - spring DE-WEIGHTED (0.3, adjustable); amplitude REMOVED (w=0, reversible lever);
#     no unconstrained annual-brightness reward (only an optional one-sided summer cap).
#   - winter dark-spell is a zero-weight red-flag GATE (caps d_floor), not a term, so it
#     cannot double-count winter_floor (~77% redundant). acute (off-winter) spell is the
#     genuinely orthogonal, live-validated 梅雨 axis (capped, season-disjoint from floor).
# All weights are adjustable in the UI; the spouse's lived anchors are VALIDATION
# targets (札幌<東京, December-trough, dim-summer non-reward), never fit targets.
# ---------------------------------------------------------------------------
V3_OBJECTIVES: list[str] = ["floor", "autumn", "spring", "excessTV", "acute", "amplitude", "summerCap"]

V3_DEFAULTS: dict[str, float] = {
    "w_floor": 1.0,
    "w_autumn": 0.7,
    "w_spring": 0.3,
    "w_excessTV": 0.3,
    "w_acute": 0.2,
    "w_amplitude": 0.0,   # REMOVED from v2 (its desirability rose as summers dimmed); reversible
    "w_summerCap": 0.0,   # optional one-sided mania cap on very bright summers
    "k_floor": 0.0,       # 0 = LINEAR floor desirability (no hidden flattering of dark floors)
    "rho": 0.1,
    "gate_cap": 0.4,      # red winter dark-spell band caps d_floor here (non-compensatory red flag)
}

ACUTE_SPELL_CSV = OUT_DIR / "acute_spell_metrics.csv"

# objective -> (Metrics field, direction). "acute" is external (daily obs), handled specially.
_V3_FIELDS: dict[str, tuple[str, str]] = {
    "floor": ("winter_floor_eff", "high"),
    "autumn": ("autumn_rate", "low"),
    "spring": ("spring_rate", "low"),
    "excessTV": ("excess_tv", "low"),
    "amplitude": ("amplitude", "low"),
    "summerCap": ("summer_ceiling", "low"),
}


def load_acute_spell() -> dict[str, float]:
    """Off-winter (Apr-Oct) acute dark-spell CVaR80 per stid, from real daily obs."""
    if not ACUTE_SPELL_CSV.exists():
        return {}
    out: dict[str, float] = {}
    with open(ACUTE_SPELL_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[r["stid"]] = round(float(r["acute_spell_cvar80"]), 2)
            except (ValueError, KeyError):
                continue
    return out


def compute_global_acute_anchor(acute_by_stid: dict[str, float]) -> list[float]:
    """Source-INVARIANT 5/95 anchor for acute_spell over ALL stations that have it.

    acute_spell is the same daily-observation metric regardless of which radiation
    source a station belongs to, so its anchor must NOT be re-derived per source
    (doing so penalised the same acute event differently by mere source coverage —
    sunshine 154-station set vs GHI 47-station set). One global anchor fixes that.
    """
    av = np.array(list(acute_by_stid.values()), dtype=float)
    return [float(np.percentile(av, 5)), float(np.percentile(av, 95))] if len(av) else [0.0, 1.0]


def compute_v3_anchors(
    results: list[tuple[StationMeta, Metrics]], acute_by_stid: dict[str, float],
    acute_anchor: list[float] | None = None,
) -> dict[str, list[float]]:
    """[lo, hi] = 5/95 percentile anchors per v3 objective, from the FULL cleaned set.

    The six effective-light axes are PER-SOURCE (sunshine beam-equiv vs measured GHI
    are different magnitude scales). `acute_spell` is source-invariant, so it takes a
    shared `acute_anchor` when supplied (see compute_global_acute_anchor); otherwise
    it falls back to a per-source percentile for standalone use.
    Same generation-frozen property as the v2 anchors (see compute_v2_anchors).
    """
    anchors: dict[str, list[float]] = {}
    for obj, (field, _dir) in _V3_FIELDS.items():
        vals = np.array([getattr(m, field) for _, m in results], dtype=float)
        anchors[obj] = [float(np.percentile(vals, 5)), float(np.percentile(vals, 95))]
    if acute_anchor is not None:
        anchors["acute"] = list(acute_anchor)
    else:
        av = np.array([acute_by_stid[meta.stid] for meta, _ in results
                       if acute_by_stid.get(meta.stid) is not None], dtype=float)
        anchors["acute"] = [float(np.percentile(av, 5)), float(np.percentile(av, 95))] if len(av) else [0.0, 1.0]
    return anchors


def v3_desirabilities(
    m: Metrics, acute_val: float | None, risk_band: str | None,
    anchors: dict[str, list[float]], params: dict[str, float],
) -> dict[str, float]:
    """v3 desirabilities in [0,1] (1 = ideal). Floor is linear by default (k_floor=0)."""
    desir: dict[str, float] = {}
    for obj, (field, direction) in _V3_FIELDS.items():
        lo, hi = anchors[obj]
        n = _clip01((getattr(m, field) - lo) / (hi - lo)) if hi > lo else 0.0
        if obj == "floor":
            desir[obj] = _saturate(n, params["k_floor"])   # k_floor=0 -> linear
        elif direction == "high":
            desir[obj] = n
        else:
            desir[obj] = 1.0 - n
    if acute_val is None:
        desir["acute"] = 1.0   # no daily obs -> neutral (no penalty)
    else:
        lo, hi = anchors["acute"]
        desir["acute"] = 1.0 - (_clip01((acute_val - lo) / (hi - lo)) if hi > lo else 0.0)
    # Non-compensatory red-flag GATE: a RED winter dark-spell band caps the floor
    # desirability so no gentle-transition axis can buy back a multi-week blackout.
    if risk_band == "red":
        desir["floor"] = min(desir["floor"], params["gate_cap"])
    return desir


def v3_score(desir: dict[str, float], params: dict[str, float]) -> float:
    """Augmented weighted Chebyshev (same family as v2). Lower = better."""
    gaps = [params[f"w_{obj}"] * (1.0 - desir[obj]) for obj in V3_OBJECTIVES]
    return max(gaps) + params["rho"] * sum(gaps)


def pareto_floor_vs_tv(results: list[tuple[StationMeta, Metrics]]) -> list[bool]:
    """Non-dominated flag on the headline 2-axis tradeoff: winter_floor↑ × total_variation↓."""
    pts = [(m.winter_floor, m.total_variation) for _, m in results]
    flags: list[bool] = []
    for i, (fi, ti) in enumerate(pts):
        dominated = any(
            fj >= fi and tj <= ti and (fj > fi or tj < ti)
            for j, (fj, tj) in enumerate(pts)
            if j != i
        )
        flags.append(not dominated)
    return flags


def load_dark_spell() -> dict[str, dict]:
    """Load winter dark-spell metrics (real daily obs) keyed by stid, if available."""
    if not DARK_SPELL_CSV.exists():
        return {}
    out: dict[str, dict] = {}
    with open(DARK_SPELL_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                cvar = float(r["spell_cvar80_t2"])
                winter = float(r["winter_mean_h"])
                cv = float(r["interannual_cv_pct"])
            except (ValueError, KeyError):
                continue
            band = "green" if cvar <= DARK_GREEN_MAX else ("red" if cvar >= DARK_RED_MIN else "yellow")
            out[r["stid"]] = {
                "dark_spell": round(cvar, 1),
                "winter_obs": round(winter, 2),
                "interann_cv": round(cv, 1),
                "risk_band": band,
            }
    return out


def export_web_json(
    sun_results: list[tuple[StationMeta, Metrics]],
    solar_results: list[tuple[StationMeta, Metrics]],
    path: Path,
) -> None:
    """Write web/data.json: v1 (linear z-sum) + v2 (non-compensatory) fields, per source.

    Stations without latitude (e.g. 昭和基地 / Antarctica) are excluded — their
    Southern-Hemisphere / polar seasonal cycle breaks every metric's assumptions.
    """
    v1_keys = [
        "mean_val", "amplitude", "ramp", "total_variation", "excess_tv",
        "winter_floor", "summer_ceiling", "spring_30d_gain", "autumn_30d_drop",
        "spring_rise_days", "autumn_fall_days", "trough_day", "peak_day",
        # v3 effective-light metrics
        "winter_floor_eff", "autumn_rate", "spring_rate", "eff_trough_day",
    ]
    dark = load_dark_spell()    # winter dark-spell overlay (real daily obs), keyed by stid
    acute = load_acute_spell()  # off-winter (Apr-Oct) acute spell (real daily obs), keyed by stid
    # acute_spell is source-invariant -> one global anchor shared by both sources
    # (not re-derived per source, which leaked station-set coverage into the penalty).
    global_acute_anchor = compute_global_acute_anchor(acute)

    def build(results: list[tuple[StationMeta, Metrics]]) -> tuple[list[dict], dict, dict]:
        clean = [(meta, m) for meta, m in results if meta.latitude is not None]
        # Anchors from the FULL cleaned set (before livability filter) so dropping
        # non-residential stations never re-anchors the desirabilities.
        anchors = compute_v2_anchors(clean)
        v3_anchors = compute_v3_anchors(clean, acute, acute_anchor=global_acute_anchor)
        # Candidates = livable subset; everything ranked/displayed is computed on these.
        candidates = [(meta, m) for meta, m in clean if meta.stid not in LIVABILITY_EXCLUDE]
        # v1 scores (linear z-sum) for parity with the existing UI
        z = compute_z_scores(candidates)
        stability, transition, overall = compute_overall_scores(z)
        pareto = pareto_floor_vs_tv(candidates)
        order = np.argsort(overall)  # rank by v1 overall (UI re-ranks on weight change)
        rank_of = {idx: r + 1 for r, idx in enumerate(order)}

        rows: list[dict] = []
        for i, (meta, m) in enumerate(candidates):
            desir = v2_desirabilities(m, anchors, V2_DEFAULTS["k_floor"])
            row: dict = {
                "rank": rank_of[i],
                "stid": meta.stid, "name": meta.name,
                "lat": meta.latitude, "lon": meta.longitude, "prid": meta.prid,
                "overall": round(float(overall[i]), 3),
                "stability": round(float(stability[i]), 3),
                "transition": round(float(transition[i]), 3),
            }
            for k in v1_keys:
                row[k] = getattr(m, k)
            for obj in V2_OBJECTIVES:
                row[f"d_{obj}"] = round(desir[obj], 4)
            row["score_v2"] = round(v2_score(desir, V2_DEFAULTS), 4)
            row["pareto"] = bool(pareto[i])
            # Winter dark-spell OVERLAY (real daily obs; red flag, not in the score)
            d = dark.get(meta.stid)
            row["dark_spell"] = d["dark_spell"] if d else None
            row["winter_obs"] = d["winter_obs"] if d else None
            row["interann_cv"] = d["interann_cv"] if d else None
            row["risk_band"] = d["risk_band"] if d else None
            # v3 scoring (effective-light; uses the dark-spell band as a red-flag gate)
            acute_val = acute.get(meta.stid)
            row["acute_spell"] = acute_val
            desir3 = v3_desirabilities(m, acute_val, row["risk_band"], v3_anchors, V3_DEFAULTS)
            for obj in V3_OBJECTIVES:
                row[f"d3_{obj}"] = round(desir3[obj], 4)
            row["score_v3"] = round(v3_score(desir3, V3_DEFAULTS), 4)
            rows.append(row)
        return rows, anchors, v3_anchors

    sun_rows, sun_anchors, sun_v3a = build(sun_results)
    solar_rows, solar_anchors, solar_v3a = build(solar_results)

    payload = {
        "solar": solar_rows,
        "sunshine": sun_rows,
        "anchors": {"solar": solar_anchors, "sunshine": sun_anchors},
        "v3_anchors": {"solar": solar_v3a, "sunshine": sun_v3a},
        "v2_defaults": V2_DEFAULTS,
        "v2_objectives": V2_OBJECTIVES,
        "v3_defaults": V3_DEFAULTS,
        "v3_objectives": V3_OBJECTIVES,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  Wrote {path} (solar={len(solar_rows)}, sunshine={len(sun_rows)})")


def plot_scatter(
    data: list[tuple[StationMeta, Metrics]],
    x_field: str,
    y_field: str,
    title: str,
    path: Path,
    x_label: str | None = None,
    y_label: str | None = None,
    annotate_top: int = 10,
) -> None:
    """Create a scatter plot with station labels."""
    xs = [getattr(m, x_field) for _, m in data]
    ys = [getattr(m, y_field) for _, m in data]
    names = [meta.name for meta, _ in data]

    fig, ax = plt.subplots(figsize=(12, 8))
    ax.scatter(xs, ys, alpha=0.6, s=40)
    ax.set_xlabel(x_label or x_field, fontsize=12)
    ax.set_ylabel(y_label or y_field, fontsize=12)
    ax.set_title(title, fontsize=14)
    ax.grid(True, alpha=0.3)

    for i, name in enumerate(names):
        if i < annotate_top or len(data) <= 30:
            ax.annotate(name, (xs[i], ys[i]), fontsize=7, alpha=0.8,
                        xytext=(4, 4), textcoords="offset points")

    fig.tight_layout()
    fig.savefig(str(path), dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_validation_scatter(
    sunshine_vals: list[float],
    solar_vals: list[float],
    names: list[str],
    field: str,
    rho: float,
    p_val: float,
    path: Path,
) -> None:
    """Create a validation scatter plot: sunshine metric vs solar metric."""
    fig, ax = plt.subplots(figsize=(10, 8))
    ax.scatter(sunshine_vals, solar_vals, alpha=0.6, s=40)
    ax.set_xlabel(f"{field} (sunshine)", fontsize=12)
    ax.set_ylabel(f"{field} (solar)", fontsize=12)
    ax.set_title(f"Validation: {field} (sunshine vs solar)\nSpearman r={rho:.3f}, p={p_val:.2e}", fontsize=13)
    ax.grid(True, alpha=0.3)

    for i, name in enumerate(names):
        ax.annotate(name, (sunshine_vals[i], solar_vals[i]), fontsize=6, alpha=0.7,
                    xytext=(4, 4), textcoords="offset points")

    fig.tight_layout()
    fig.savefig(str(path), dpi=150)
    plt.close(fig)
    print(f"  Saved: {path}")


def run_validation(
    sun_results: list[tuple[StationMeta, Metrics]],
    solar_results: list[tuple[StationMeta, Metrics]],
    plots_dir: Path,
    out_dir: Path,
) -> None:
    """Validate sunshine vs solar metrics on overlapping stations."""
    sun_by_stid = {meta.stid: (meta, m) for meta, m in sun_results}
    solar_by_stid = {meta.stid: (meta, m) for meta, m in solar_results}

    overlap_stids = sorted(set(sun_by_stid) & set(solar_by_stid))
    if not overlap_stids:
        print("  No overlapping stations for validation.")
        return

    print(f"\n  Validation: {len(overlap_stids)} overlapping stations")

    # Build side-by-side CSV
    metric_fields = [f.name for f in fields(Metrics)]
    header = ["stid", "name", "latitude", "longitude"]
    for mf in metric_fields:
        header += [f"sun_{mf}", f"sol_{mf}"]

    csv_rows: list[list[object]] = []
    for stid in overlap_stids:
        sun_meta, sun_m = sun_by_stid[stid]
        _, sol_m = solar_by_stid[stid]
        row: list[object] = [stid, sun_meta.name, sun_meta.latitude, sun_meta.longitude]
        for mf in metric_fields:
            row += [getattr(sun_m, mf), getattr(sol_m, mf)]
        csv_rows.append(row)

    # Compute Spearman correlations for validation fields
    spearman_results: dict[str, tuple[float, float]] = {}
    print(f"\n  {'field':<20} {'Spearman r':>10} {'p-value':>12}")
    print(f"  {'-'*20} {'-'*10} {'-'*12}")

    for field in VALIDATION_FIELDS:
        sun_vals = [getattr(sun_by_stid[s][1], field) for s in overlap_stids]
        sol_vals = [getattr(solar_by_stid[s][1], field) for s in overlap_stids]
        rho, p_val = stats.spearmanr(sun_vals, sol_vals)
        spearman_results[field] = (rho, p_val)
        print(f"  {field:<20} {rho:>10.3f} {p_val:>12.2e}")

        names = [sun_by_stid[s][0].name for s in overlap_stids]
        plot_validation_scatter(
            sun_vals, sol_vals, names, field, rho, p_val,
            plots_dir / f"validation_{field}.png",
        )

    # Add Spearman r to CSV header
    header.append("spearman_note")
    spearman_summary = "; ".join(f"{k}: r={v[0]:.3f}" for k, v in spearman_results.items())
    for row in csv_rows:
        row.append(spearman_summary)

    val_csv = out_dir / "validation_sunshine_vs_solar.csv"
    with open(val_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in csv_rows:
            writer.writerow(row)
    print(f"  Wrote {val_csv}")

    # Interpret results
    weak = [k for k, (r, _) in spearman_results.items() if abs(r) < 0.7]
    if weak:
        print(f"\n  WARNING: Weak correlation (r < 0.7) on: {', '.join(weak)}")
        print("  Sunshine results may be a coarse proxy for these metrics.")
    else:
        print("\n  All validation metrics have Spearman r >= 0.7. Sunshine is an adequate proxy.")


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plots_dir = OUT_DIR / "plots"
    plots_dir.mkdir(exist_ok=True)

    # --- Compute for sunshine_normal (main: ~157 stations) ---
    print("Loading sunshine_normal data...")
    sun_data = load_station_data(conn, "sunshine_normal")
    print(f"  {len(sun_data)} stations with sunshine_normal (365 days)")

    sun_results: list[tuple[StationMeta, Metrics]] = []
    for stid, (meta, arr) in sorted(sun_data.items()):
        m = compute_metrics(arr, lat=meta.latitude, energy=False)
        sun_results.append((meta, m))

    # Full metric catalogue keeps EVERY station; the `livable` column marks which
    # ones are in the ranked shortlist (data.json / top_candidates). Makes the
    # full-catalogue-vs-shortlist split explicit instead of a silent row-count gap.
    sun_csv = OUT_DIR / "station_metrics_sunshine.csv"
    sun_livable = {"livable": [0 if meta.stid in LIVABILITY_EXCLUDE else 1 for meta, _ in sun_results]}
    write_csv(sun_csv, sun_results, "sunshine_normal", extra_fields=["livable"], extra_values=sun_livable)
    print(f"  Wrote {sun_csv} ({len(sun_results)} stations)")

    # --- Compute for solar_normal (sub: ~49 stations) ---
    print("\nLoading solar_normal data...")
    solar_data = load_station_data(conn, "solar_normal")
    print(f"  {len(solar_data)} stations with solar_normal (365 days)")

    solar_results: list[tuple[StationMeta, Metrics]] = []
    for stid, (meta, arr) in sorted(solar_data.items()):
        m = compute_metrics(arr, lat=meta.latitude, energy=True)
        solar_results.append((meta, m))

    solar_csv = OUT_DIR / "station_metrics_solar.csv"
    solar_livable = {"livable": [0 if meta.stid in LIVABILITY_EXCLUDE else 1 for meta, _ in solar_results]}
    write_csv(solar_csv, solar_results, "solar_normal", extra_fields=["livable"], extra_values=solar_livable)
    print(f"  Wrote {solar_csv} ({len(solar_results)} stations)")

    conn.close()

    # --- Overall scores (sunshine) ---
    # Shortlist excludes non-residential stations (livability filter); the full
    # metric catalogue above (station_metrics_sunshine.csv) still has every station.
    print("\nComputing overall scores (sunshine)...")
    sun_candidates = [(meta, m) for meta, m in sun_results if meta.stid not in LIVABILITY_EXCLUDE]
    z = compute_z_scores(sun_candidates)
    stability, transition, overall = compute_overall_scores(z)

    # Write top candidates CSV
    scored = list(zip(sun_candidates, overall, stability, transition))
    scored.sort(key=lambda x: x[1])  # Low overall = good

    top_csv = OUT_DIR / "top_candidates_sunshine.csv"
    header = [
        "rank", "stid", "name", "latitude", "longitude", "prid",
        "overall_score", "stability_score", "transition_score",
    ] + PRIMARY_FIELDS
    with open(top_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for rank, (result_pair, ov, stab, trans) in enumerate(scored, 1):
            meta, m = result_pair
            row = [
                rank, meta.stid, meta.name, meta.latitude, meta.longitude, meta.prid,
                round(float(ov), 3), round(float(stab), 3), round(float(trans), 3),
            ] + [getattr(m, f) for f in PRIMARY_FIELDS]
            writer.writerow(row)
    print(f"  Wrote {top_csv} ({len(scored)} stations)")

    # --- Print rankings ---
    print("\n" + "=" * 80)
    print(f"SUNSHINE NORMAL RANKINGS ({len(sun_results)} stations)")
    print("=" * 80)

    def print_ranking(
        results: list[tuple[StationMeta, Metrics]],
        field: str,
        label: str,
        reverse: bool = False,
        top_n: int = 15,
    ) -> None:
        sorted_r = sorted(results, key=lambda x: getattr(x[1], field), reverse=reverse)
        print(f"\n--- {label} (top {top_n}) ---")
        print(f"{'#':>3} {'stid':<8} {'name':<14} {'lat':>5} {field:>14}")
        for i, (meta, m) in enumerate(sorted_r[:top_n], 1):
            lat = f"{meta.latitude:.1f}" if meta.latitude else "?"
            print(f"{i:>3} {meta.stid:<8} {meta.name:<14} {lat:>5} {getattr(m, field):>14}")

    print_ranking(sun_results, "mean_val", "Annual Mean Sunshine (hours/day) - HIGH is sunny", reverse=True)
    print_ranking(sun_results, "amplitude", "Amplitude (P95-P05)/mean of smoothed - LOW is stable", reverse=False)
    print_ranking(sun_results, "ramp", "Ramp (avg daily change) - LOW is stable", reverse=False)
    print_ranking(sun_results, "winter_floor", "Winter Floor (Dec15-Feb15) - HIGH is bright winter", reverse=True)
    print_ranking(sun_results, "summer_ceiling", "Summer Ceiling (Jun15-Aug15) - for reference", reverse=True)
    print_ranking(sun_results, "spring_30d_gain", "Spring 30d Gain - LOW is gentle spring", reverse=False)
    print_ranking(sun_results, "autumn_30d_drop", "Autumn 30d Drop - LOW is gentle autumn", reverse=False)
    print_ranking(sun_results, "spring_rise_days", "Spring Rise Days (25%->75%) - HIGH is slow/gentle", reverse=True)
    print_ranking(sun_results, "autumn_fall_days", "Autumn Fall Days (75%->25%) - HIGH is slow/gentle", reverse=True)

    # Overall score ranking
    print(f"\n--- Overall Score (top 20) - LOW is best ---")
    print(f"{'#':>3} {'stid':<8} {'name':<14} {'lat':>5} {'overall':>8} {'stabil':>8} {'transit':>8} {'mean':>6} {'ampl':>6} {'wfloor':>7}")
    for rank, (result_pair, ov, stab, trans) in enumerate(scored[:20], 1):
        meta, m = result_pair
        lat = f"{meta.latitude:.1f}" if meta.latitude else "?"
        print(f"{rank:>3} {meta.stid:<8} {meta.name:<14} {lat:>5} {ov:>8.2f} {stab:>8.2f} {trans:>8.2f} {m.mean_val:>6.1f} {m.amplitude:>6.3f} {m.winter_floor:>7.1f}")

    if solar_results:
        print("\n" + "=" * 80)
        print(f"SOLAR NORMAL RANKINGS ({len(solar_results)} stations)")
        print("=" * 80)
        print_ranking(solar_results, "mean_val", "Annual Mean Solar (MJ/m2/day) - HIGH is sunny", reverse=True)
        print_ranking(solar_results, "amplitude", "Amplitude - LOW is stable", reverse=False)
        print_ranking(solar_results, "spring_30d_gain", "Spring 30d Gain - LOW is gentle", reverse=False)
        print_ranking(solar_results, "autumn_30d_drop", "Autumn 30d Drop - LOW is gentle", reverse=False)
        print_ranking(solar_results, "spring_rise_days", "Spring Rise Days - HIGH is gentle", reverse=True)
        print_ranking(solar_results, "autumn_fall_days", "Autumn Fall Days - HIGH is gentle", reverse=True)

    # --- Scatter plots (sunshine) ---
    print("\nGenerating scatter plots...")

    by_amplitude = sorted(sun_results, key=lambda x: x[1].amplitude)
    plot_scatter(by_amplitude, "amplitude", "winter_floor",
                 "Amplitude vs Winter Floor (sunshine)", plots_dir / "amplitude_vs_winter_floor.png",
                 x_label="Amplitude (P95-P05)/mean [low=stable]",
                 y_label="Winter Floor (hours/day) [high=bright]")

    plot_scatter(by_amplitude, "spring_30d_gain", "autumn_30d_drop",
                 "Spring Gain vs Autumn Drop (sunshine)", plots_dir / "spring_vs_autumn.png",
                 x_label="Spring 30d Gain (hours) [low=gentle]",
                 y_label="Autumn 30d Drop (hours) [low=gentle]")

    by_rise = sorted(sun_results, key=lambda x: x[1].spring_rise_days, reverse=True)
    plot_scatter(by_rise, "spring_rise_days", "autumn_fall_days",
                 "Spring Rise Days vs Autumn Fall Days (sunshine)", plots_dir / "rise_vs_fall_days.png",
                 x_label="Spring Rise Days [high=slow/gentle]",
                 y_label="Autumn Fall Days [high=slow/gentle]")

    plot_scatter(by_amplitude, "ramp", "mean_val",
                 "Ramp (instability) vs Mean Sunshine", plots_dir / "ramp_vs_mean.png",
                 x_label="Ramp (avg daily change) [low=stable]",
                 y_label="Mean Sunshine (hours/day)")

    # --- Validation: sunshine vs solar ---
    if sun_results and solar_results:
        print("\nRunning validation (sunshine vs solar)...")
        run_validation(sun_results, solar_results, plots_dir, OUT_DIR)

    # --- Export web/data.json (v1 + v2 scoring) ---
    print("\nExporting web/data.json...")
    export_web_json(sun_results, solar_results, WEB_DIR / "data.json")

    print("\nDone.")


if __name__ == "__main__":
    main()
