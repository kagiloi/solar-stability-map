#!/usr/bin/env python3
"""Phase 2: Compute stability and transition metrics for all stations.

Reads daily normal values from SQLite, computes metrics per station,
outputs CSVs, scatter plots, validation correlations, and ranked candidates.
"""

import csv
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


def compute_metrics(values_365: np.ndarray) -> Metrics:
    """Compute all metrics from a 365-element array of daily normals."""
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

    return Metrics(
        mean_val=round(mean_val, 3),
        amplitude=round(amplitude, 4),
        ramp=round(ramp, 4),
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
        m = compute_metrics(arr)
        sun_results.append((meta, m))

    sun_csv = OUT_DIR / "station_metrics_sunshine.csv"
    write_csv(sun_csv, sun_results, "sunshine_normal")
    print(f"  Wrote {sun_csv} ({len(sun_results)} stations)")

    # --- Compute for solar_normal (sub: ~49 stations) ---
    print("\nLoading solar_normal data...")
    solar_data = load_station_data(conn, "solar_normal")
    print(f"  {len(solar_data)} stations with solar_normal (365 days)")

    solar_results: list[tuple[StationMeta, Metrics]] = []
    for stid, (meta, arr) in sorted(solar_data.items()):
        m = compute_metrics(arr)
        solar_results.append((meta, m))

    solar_csv = OUT_DIR / "station_metrics_solar.csv"
    write_csv(solar_csv, solar_results, "solar_normal")
    print(f"  Wrote {solar_csv} ({len(solar_results)} stations)")

    conn.close()

    # --- Overall scores (sunshine) ---
    print("\nComputing overall scores (sunshine)...")
    z = compute_z_scores(sun_results)
    stability, transition, overall = compute_overall_scores(z)

    # Write top candidates CSV
    scored = list(zip(sun_results, overall, stability, transition))
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

    print("\nDone.")


if __name__ == "__main__":
    main()
