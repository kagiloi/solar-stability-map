# Phase 1-2: JMA Solar Radiation Crawler - Implementation Report

## Overview

Implemented and executed a Python crawler that downloads daily normal values (平年値) of global solar radiation (全天日射量) and sunshine hours (日照時間) for all observation stations across Japan from the JMA (Japan Meteorological Agency) website.

**Date**: 2026-03-16
**Duration**: ~15 minutes total execution (station discovery ~3min, CSV download ~10min, parse <1s)
**Result**: All 193 stations successfully crawled with 100% data completeness.

---

## Pipeline

The crawler runs in 4 sequential steps via `python -m crawler.crawl`:

| Step | Command | What it does | Output |
|------|---------|-------------|--------|
| 1. Discover | `--discover` | Crawl 61 prefectures from JMA, extract station metadata, filter for solar radiation capability | `data/stations.json` (193 stations) |
| 2. Download | `--download` | Download CSV for each station via POST API, decode cp932, validate | `data/raw_csv/*.csv` (193 files) |
| 3. Parse | `--parse` | Parse CSVs, insert into SQLite | `data/jma_solar.db` |
| 4. Verify | `--verify` | Row counts, value range checks, top stations | Console output |

---

## Results

### Station Discovery

- **61** prefecture/region IDs crawled
- **1,677** unique stations found across Japan
- **193** stations with solar radiation data (`kansoku[3] == '1'`)
- Raw HTML responses preserved in `data/raw_html/` (62 files)

### CSV Download

- **193/193** stations downloaded successfully (0 failures)
- Rate limiting: 3-second delay between requests
- Session management: cookies obtained via GET to `index.php` before POST requests
- CSV encoding: cp932 (Shift_JIS superset that handles `㎡`) decoded to UTF-8
- Two CSV formats handled:
  - 13 columns (official stations): includes 現象なし情報 columns
  - 11 columns (some AMeDAS stations): without 現象なし情報 columns

### Data Summary

| Metric | Value |
|--------|-------|
| Total stations | 193 |
| Rows per station | 365 |
| Total daily_data rows | 70,445 |
| Stations with incomplete data | 0 |
| Solar normal range | 0.00 - 30.60 MJ/m² |
| Solar normal average | 13.42 MJ/m² |
| Sunshine normal range | 0.00 - 14.00 hours |
| Sunshine normal average | 5.08 hours |

### Top 5 Stations by Average Solar Radiation Normal

| Station | Name | Avg Solar Normal (MJ/m²) |
|---------|------|--------------------------|
| s47991 | 南鳥島 | 19.19 |
| s47945 | 南大東島 | 16.37 |
| s47971 | 父島 | 16.30 |
| s47918 | 石垣島 | 15.39 |
| s47927 | 宮古島 | 14.90 |

---

## File Structure

```
crawler/
├── __init__.py
├── __main__.py       # Allows `python -m crawler`
├── config.py         # URLs, delays, paths, constants
├── db.py             # SQLite schema (stations, daily_data) & CRUD
├── stations.py       # Station discovery (session, prefecture crawl, parsing)
├── downloader.py     # CSV download (POST API, retry, resume, validation)
├── parser.py         # CSV parsing (13-col & 11-col formats) & verification
└── crawl.py          # CLI entry point (--discover/--download/--parse/--verify)

data/
├── raw_html/         # 62 raw HTML files from station discovery
│   ├── prefectures.html
│   └── pref_{prid}.html (x61)
├── raw_csv/          # 193 raw CSV files (UTF-8, converted from cp932)
├── stations.json     # Station metadata (193 entries with lat/lon/elevation/kansoku)
└── jma_solar.db      # SQLite database
```

### SQLite Schema

```sql
stations (stid, name, prid, latitude, longitude, elevation, kansoku, title_raw, observation_ended)
daily_data (stid, date[MM-DD], sunshine_hours, sunshine_hours_quality, sunshine_normal,
            solar_radiation, solar_radiation_quality, solar_normal, solar_normal_quality)
```

---

## Technical Notes

- **Session requirement**: JMA requires cookies (`ci_session`, `AWSALB`, `AWSALBCORS`) obtained by visiting `index.php` first. Without them, the API returns an HTML error page.
- **kansoku field**: 6-digit string where `[3]` indicates sunshine/solar data availability. `'1'` = full data (sunshine + solar radiation + normals), `'2'` = sunshine only, `'0'` = none.
- **Resumability**: The download step skips stations whose CSV already exists and passes validation (>= 365 data rows). Safe to re-run.
- **Dependencies**: `requests`, `beautifulsoup4` (+ stdlib: `sqlite3`, `json`, `csv`, `logging`, `argparse`, `pathlib`, `re`, `time`)

---

## Next Steps

Phase 2: Smooth the daily normal values and compute rate of change (delta) of solar radiation across stations — to investigate the hypothesis that variability in light, not just quantity, impacts bipolar disorder symptoms.
