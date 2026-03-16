# Phase 2: Light Stability Metrics - Implementation Report

## Overview

Computed stability and seasonal transition metrics for 156 sunshine stations and 49 solar radiation stations from JMA daily normal values. Produced a composite ranking score to screen for locations with stable, gentle light environments suitable for bipolar disorder management.

**Date**: 2026-03-16
**Input**: `data/jma_solar.db` (Phase 1-2 output, 193 stations)
**Script**: `analysis/compute_metrics.py`

---

## Methodology

### Data Preparation

- Daily normal values loaded from SQLite (`sunshine_normal` for 157 stations, `solar_normal` for 49 stations)
- Stations with != 365 days of data excluded (only Mt. Fuji / 富士山 dropped — 38 days)
- 15-day circular moving average applied for smoothing
- Missing days filled with NaN (not zero) to avoid bias

### Metrics Computed

All metrics are computed from the **smoothed** (15-day circular MA) series, except `mean_val` which uses raw values.

**Primary metrics (used in scoring):**

| Metric | Meaning | Good direction |
|--------|---------|---------------|
| `mean_val` | Annual average sunshine (hours/day) | High = sunny |
| `amplitude` | (P95-P05)/mean of smoothed | Low = stable |
| `ramp` | mean(abs(diff(smoothed))) | Low = stable |
| `winter_floor` | Mean smoothed Dec15-Feb15 | High = bright winter |
| `spring_30d_gain` | Max 30-day increase (trough->peak) | Low = gentle |
| `autumn_30d_drop` | Max 30-day decrease (peak->trough) | Low = gentle |
| `spring_rise_days` | Days from 25%->75% level | High = slow/gentle |
| `autumn_fall_days` | Days from 75%->25% level | High = slow/gentle |

**Auxiliary metrics (output but not scored):** `summer_ceiling`, `spring_ramp`, `autumn_ramp`, `spring_30d_gain_rel`, `autumn_30d_drop_rel`, `trough_day`, `peak_day`

### Composite Score

```
stability_score  = z(amplitude) + z(ramp) - z(winter_floor)
transition_score = z(spring_30d_gain) + z(autumn_30d_drop) - z(spring_rise_days) - z(autumn_fall_days)
overall_score    = stability_score + transition_score
```

**Low overall_score = better** (more stable, gentler transitions, brighter winter).

---

## Results

### Station Counts

| Dataset | Total in DB | With 365 days | Excluded |
|---------|-------------|---------------|----------|
| `sunshine_normal` | 157 | 156 | 1 (富士山) |
| `solar_normal` | 49 | 49 | 0 |

### Top 20 Candidates (Sunshine, by overall_score)

| Rank | Station | Lat | Overall | Stability | Transition | Mean (h/d) | Amplitude | Winter Floor |
|------|---------|-----|---------|-----------|------------|------------|-----------|-------------|
| 1 | 宮古 | 39.6 | -8.19 | -3.48 | -4.71 | 5.2 | 0.361 | 5.1 |
| 2 | 根室 | 43.3 | -7.25 | -3.30 | -3.95 | 5.1 | 0.449 | 5.1 |
| 3 | 帯広 | 42.9 | -6.89 | -3.30 | -3.58 | 5.5 | 0.556 | 6.1 |
| 4 | 大船渡 | 39.1 | -6.41 | -2.75 | -3.67 | 4.8 | 0.399 | 4.5 |
| 5 | 網走 | 44.0 | -6.32 | -2.57 | -3.75 | 5.1 | 0.495 | 3.9 |
| 6 | 石巻 | 38.4 | -6.30 | -3.03 | -3.27 | 5.3 | 0.390 | 5.3 |
| 7 | 広尾 | 42.3 | -6.17 | -2.97 | -3.20 | 5.0 | 0.535 | 5.3 |
| 8 | 八戸 | 40.5 | -6.17 | -2.10 | -4.07 | 5.1 | 0.500 | 4.2 |
| 9 | 仙台 | 38.3 | -5.27 | -2.25 | -3.03 | 5.0 | 0.475 | 4.9 |
| 10 | 松本 | 36.2 | -5.13 | -2.74 | -2.39 | 5.9 | 0.386 | 5.6 |
| 11 | 河口湖 | 35.5 | -5.06 | -2.12 | -2.94 | 5.5 | 0.565 | 6.8 |
| 12 | 紋別 | 44.3 | -4.85 | -1.74 | -3.11 | 4.6 | 0.591 | 3.3 |
| 13 | 盛岡 | 39.7 | -4.79 | -1.27 | -3.51 | 4.6 | 0.588 | 3.7 |
| 14 | 白河 | 37.1 | -4.77 | -2.65 | -2.12 | 4.9 | 0.489 | 5.0 |
| 15 | 軽井沢 | 36.3 | -4.76 | -2.74 | -2.02 | 5.5 | 0.486 | 6.1 |
| 16 | 延岡 | 32.6 | -4.76 | -2.64 | -2.12 | 5.8 | 0.383 | 6.1 |
| 17 | 宇都宮 | 36.5 | -4.28 | -2.67 | -1.61 | 5.4 | 0.646 | 6.8 |
| 18 | 釧路 | 43.0 | -4.21 | -3.37 | -0.84 | 5.4 | 0.528 | 6.0 |
| 19 | 高知 | 33.6 | -4.20 | -2.96 | -1.23 | 5.9 | 0.387 | 6.2 |
| 20 | 南鳥島 | 24.3 | -4.16 | -1.58 | -2.59 | 7.7 | 0.611 | 5.7 |

### Interpretation

The top candidates split into two clusters:

1. **Northern Pacific coast** (宮古, 根室, 帯広, 大船渡, 網走, 八戸, 仙台): score well on **transition** — the seasonal light curve changes slowly and gently. However, winter floor is lower (3-5 h/day) and absolute sunshine is modest.

2. **Inland Kanto-Chubu / Southern Pacific** (松本, 河口湖, 軽井沢, 延岡, 高知, 宇都宮): score well on **stability** — low amplitude, high winter floor (5.6-6.8 h/day). More comfortable winter brightness but slightly sharper seasonal transitions.

For the bipolar disorder use case (hypothesis: gentle transitions matter more than absolute levels), the Northern Pacific coast group may be preferred despite lower winter sunshine. However, this depends on whether the `autumn_30d_drop` metric is reliable — see Validation below.

### Worst Candidates

| Rank | Station | Issue |
|------|---------|-------|
| 152 | 輪島 | Sea of Japan — extreme winter-summer contrast (amplitude=1.27, winter_floor=1.5h) |
| 153 | 長崎 | High amplitude + low winter floor |
| 154 | 佐世保 | Similar to Nagasaki |
| 155 | 平戸 | Same Sea of Japan / Kyushu pattern |
| 156 | 昭和 | Antarctic station — extreme seasonal swing (amplitude=2.55) |

Sea of Japan coast stations dominate the worst rankings, as expected: heavy winter cloud cover creates very low winter floors and sharp seasonal amplitude.

---

## Validation: Sunshine vs Solar Radiation

For the 49 stations with both sunshine hours and solar radiation normals, we computed Spearman rank correlations to verify that sunshine-based metrics are a reasonable proxy for solar radiation metrics.

| Metric | Spearman r | p-value | Adequate? |
|--------|-----------|---------|-----------|
| `amplitude` | 0.799 | 5.70e-12 | Yes |
| `winter_floor` | 0.823 | 3.95e-13 | Yes |
| `spring_30d_gain` | 0.718 | 6.54e-09 | Yes |
| **`autumn_30d_drop`** | **0.127** | **3.84e-01** | **No** |

### Key finding: `autumn_30d_drop` is not a reliable proxy

The sunshine-based `autumn_30d_drop` has essentially **zero correlation** (r=0.127, p=0.38) with the solar radiation equivalent. This means:

- The ranking of stations by autumn transition gentleness cannot be trusted from sunshine data alone
- The `transition_score` component is partially unreliable since it includes `autumn_30d_drop`
- The other three validated metrics (`amplitude`, `winter_floor`, `spring_30d_gain`) are strong proxies (r > 0.7)

**Why?** Sunshine hours measure whether the sun is visible (binary threshold), while solar radiation captures total energy including diffuse radiation through clouds. In autumn, as cloud cover increases, sunshine hours drop sharply (sun falls below threshold) while total radiation decreases more gradually (diffuse component persists). This threshold effect causes sunshine-based autumn drops to behave differently from radiation-based drops.

---

## Output Files

| File | Description | Rows |
|------|------------|------|
| `data/station_metrics_sunshine.csv` | All metrics for sunshine stations | 156 |
| `data/station_metrics_solar.csv` | All metrics for solar radiation stations | 49 |
| `data/top_candidates_sunshine.csv` | Primary metrics + scores, sorted by overall_score | 156 |
| `data/validation_sunshine_vs_solar.csv` | Side-by-side metrics for overlapping stations | 49 |
| `data/plots/amplitude_vs_winter_floor.png` | Scatter: stability vs winter brightness |
| `data/plots/spring_vs_autumn.png` | Scatter: spring gain vs autumn drop |
| `data/plots/rise_vs_fall_days.png` | Scatter: spring rise days vs autumn fall days |
| `data/plots/ramp_vs_mean.png` | Scatter: daily instability vs mean sunshine |
| `data/plots/validation_*.png` | 4 validation scatter plots (sunshine vs solar) |

---

## Limitations and Next Steps

### Limitations

1. **`autumn_30d_drop` unreliable** as sunshine proxy (see Validation). Consider dropping it from the composite score, or weighting it lower.
2. **昭和 (Syowa, Antarctica)** included as an outlier. Its Dec-Feb "winter floor" is actually Antarctic summer, inflating that metric. Should be excluded for Japan-focused analysis.
3. **Only 平年値 (normals)** used — year-to-year variability not captured. A location with stable normals might still have high inter-annual variance.
4. **No population/livability filter** — some top-ranked stations (南鳥島, 広尾) are remote islands or small towns.

### Recommended Next Steps

1. **Revise scoring**: drop `autumn_30d_drop` from composite or replace with a more robust autumn metric
2. **Filter for livable locations**: cross-reference with population, access to medical facilities, cost of living
3. **Phase 2.5**: investigate year-to-year variability using actual observation data (not just normals)
4. **Phase 3**: overlay climate change projections to assess future stability of light environments
