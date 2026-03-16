# Phase 2 Web: Interactive Visualization - Implementation Report

## Overview

Built and deployed an interactive single-page web app to visualize the light stability metrics computed in Phase 2. Deployed to GitHub Pages as a static site with no build step.

**Date**: 2026-03-16
**URL**: https://kagiloi.github.io/solar-stability-map/
**Repo**: https://github.com/kagiloi/solar-stability-map
**Stack**: Vanilla HTML/JS, Plotly.js, Leaflet.js (no framework, no build)

---

## Features

### Data Source Switcher

Two data sources selectable via toggle buttons:

| Source | Stations | Unit | Description |
|--------|----------|------|-------------|
| **全天日射量** (default) | 48 | MJ/m2/day | Total solar energy including diffuse radiation through clouds |
| 日照時間 | 155 | h/day | Binary threshold (direct sunshine > 120 W/m2), 3x more coverage |

All views (map, scatter, table, scores) update on source switch. Unit labels change dynamically.

### Map View (Leaflet)

- Circle markers colored by selected metric (dropdown: Overall Score, Amplitude, Winter Floor, etc.)
- Green = good, red = bad (inverted for metrics where high = good)
- Hover tooltip shows station name, click popup shows key metrics
- Dark basemap (CARTO dark)

### Scatter Plot (Plotly.js)

- Configurable X, Y, and Color axes via dropdowns
- Interactive: hover, zoom, pan, box/lasso select
- Color scale adapts to metric direction (lower_is_good vs higher_is_good)

### Ranking Table

- All primary metrics displayed
- Click column headers to sort (ascending/descending toggle)
- Overall score color-coded: green < -3 (good), red > 3 (bad)

### Score Weight Sliders

Interactive weight customization panel (collapsible, positioned above tabs so it's accessible from all views):

- 8 sliders, one per metric: Amplitude, Ramp, Winter Floor, Mean Value, Spring 30d Gain, Autumn 30d Drop, Spring Rise Days, Autumn Fall Days
- Range: -10.0 to +10.0, step 0.1
- Default weights: `amplitude=+1, ramp=+1, winter_floor=-1, mean_val=0, spring_30d_gain=+1, autumn_30d_drop=+1, spring_rise_days=-1, autumn_fall_days=-1`
- Color-coded values: red (+) = penalizes high values, green (-) = rewards high values, gray (0) = disabled
- **Reset to default** / **All zero** buttons
- Real-time formula preview: `overall = +1.0*z(amplitude) +1.0*z(ramp) -1.0*z(winter_floor) ...`

Z-scores are computed client-side from raw metric values. When any slider changes, overall_score and ranks are recomputed and all views (map, scatter, table, summary cards) update instantly.

### About Page

Explains purpose (bipolar disorder light environment screening), metrics definitions, composite score formula, limitations, and data source.

---

## Architecture

```
web/
  index.html   -- single-page app (~550 lines), all logic inline
  data.json    -- { solar: [...48 stations], sunshine: [...155 stations] }
```

### Data Pipeline

1. `analysis/compute_metrics.py` computes metrics from SQLite
2. Inline Python script generates `web/data.json` with both solar and sunshine datasets
3. 昭和基地 (Antarctica, stid=s89532) excluded from both datasets
4. JSON contains raw metric values; z-scores and composite scores computed client-side

### Deployment

- GitHub repo: `kagiloi/solar-stability-map` (public)
- GitHub Pages from `main` branch root
- No CI/CD needed -- just `git push` to deploy

---

## Design Decisions

1. **No framework / no build step**: The app is simple enough that vanilla JS + CDN libraries suffice. Makes deployment trivial and keeps maintenance low.

2. **Client-side z-score computation**: Originally scores were precomputed in Python. Moved to client-side to enable real-time weight adjustment without a backend.

3. **全天日射量 as default**: Despite having fewer stations (48 vs 155), solar radiation is the more meaningful proxy for the actual light environment. Sunshine hours is a binary threshold measure that loses information about diffuse light.

4. **Weight sliders above tabs**: Initially placed inside the Map panel, but moved to a global position so users can adjust weights while viewing any tab (Map, Scatter, or Ranking).

5. **Excluded 昭和基地**: Antarctic station has extreme seasonality (southern hemisphere reversal makes Dec-Feb "winter floor" actually Antarctic summer). Distorts z-scores for all other stations.

---

## Limitations

- No persistent state -- slider settings reset on page reload
- No direct URL sharing of custom weight configurations
- Scatter plot doesn't show station names (hover only)
- Mobile layout functional but not optimized for small screens
