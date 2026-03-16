# nissha (日射量研究)

Research project analyzing JMA (Japan Meteorological Agency) solar radiation and sunshine hour data to find locations with stable, gentle light environments.

## Prerequisites

- Python 3.13+
- (Optional) Node.js — not required; the web app has no build step

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Project Structure

```
crawler/          Phase 1 — JMA data crawler
analysis/         Phase 2 — Metrics computation
web/              Phase 2 — Interactive visualization (static site)
data/             SQLite DB, CSVs, plots (gitignored)
docs/adr/         Architecture decision records
```

## Running the Analysis

```bash
source .venv/bin/activate
python analysis/compute_metrics.py
```

Outputs CSVs and plots to `data/`.

## Local Development Server (Web)

The web app is a static single-page app with no build step. Serve the `web/` directory with any HTTP server:

```bash
# Python (built-in)
python -m http.server 8000 -d web

# Then open http://localhost:8000
```

`web/data.json` must exist before the app works. Generate it by running the analysis script first.
