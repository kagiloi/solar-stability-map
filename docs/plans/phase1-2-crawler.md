# Phase 1-2: JMA Solar Radiation Crawler Plan

## Goal
Crawl daily normal values (平年値) of **global solar radiation** (全天日射量) and **sunshine hours** (日照時間) for all observation stations across Japan from the JMA website, and store them in SQLite + raw CSV.

---

## Verified Facts (from browser investigation)

| Item | Value |
|------|-------|
| Prefectures (regions) | 61 |
| Total unique stations | 1,738 (s=official, a=AMeDAS, h=regional group) |
| Stations with solar radiation (`kansoku[3]='1'`) | **193 unique** (includes some with ended observation) |
| CSV encoding | Shift_JIS |
| CSV header rows | 6 (metadata, blank, station name, item name, normal label, sub-header) |
| Data rows per station | 365 (2025/1/1 - 2025/12/31) |
| API session | **Required** — GET `index.php` first for cookies (`ci_session`, `AWSALB`, `AWSALBCORS`). Without session → HTML error page. |
| CSV encoding detail | Shift_JIS (use `cp932` in Python for `㎡` etc.) |

### CSV Column Mapping (13 columns per row)

| Index | Content | Type |
|-------|---------|------|
| 0 | 年月日 (date) | YYYY/M/D |
| 1 | 日照時間 (actual) | float hours |
| 2 | 日照時間 現象なし情報 | flag |
| 3 | 日照時間 品質情報 | flag |
| 4 | 日照時間 均質番号 | flag |
| 5 | **日照時間 平年値** | float hours |
| 6 | 日照時間 平年値 現象なし情報 | flag |
| 7 | 日照時間 平年値 品質情報 | flag |
| 8 | 合計全天日射量 (actual) | float MJ/m² |
| 9 | 合計全天日射量 品質情報 | flag |
| 10 | 合計全天日射量 均質番号 | flag |
| 11 | **合計全天日射量 平年値** | float MJ/m² |
| 12 | 合計全天日射量 平年値 品質情報 | flag |

Target data: **columns 5 (sunshine normal) and 11 (solar radiation normal)**, plus actual values (1, 8) for reference.

---

## Architecture

```
crawler/
├── crawl.py          # Main crawler script (entry point)
├── stations.py       # Station discovery logic
├── downloader.py     # CSV download logic
├── parser.py         # CSV parsing logic
├── db.py             # SQLite schema & insert logic
├── config.py         # Constants (URLs, delays, etc.)
data/
├── raw_html/         # Raw HTML responses from station discovery (per prefecture)
│   ├── prefectures.html   # Prefecture list response
│   ├── pref_11.html       # Stations for prid=11, etc.
│   └── ...
├── raw_csv/          # Raw CSV files per station (e.g., s47936.csv)
├── stations.json     # Parsed station list
└── jma_solar.db      # SQLite database
```

---

## Steps

### Step 1: Station Discovery (`stations.py`)

1. **GET** `https://www.data.jma.go.jp/risk/obsdl/index.php` to establish session cookies.
2. **POST** `https://www.data.jma.go.jp/risk/obsdl/top/station` (no body) → save raw HTML to `data/raw_html/prefectures.html` → parse to extract all `prid` values (61 regions).
3. For each `prid`, **POST** with `pd={prid}` → save raw HTML to `data/raw_html/pref_{prid}.html` → parse to extract station info:
   - `stid` (e.g., `s47936`)
   - `stname` (e.g., `那覇`)
   - `prid` (e.g., `91`)
   - `kansoku` (e.g., `111111`)
   - Raw `title` attribute (contains: 地点名, カナ, 北緯, 東経, 標高, 観測終了日 etc.)
   - Parsed: latitude, longitude, elevation from title
   - Note: some stations have ended observation (e.g., "2003年10月16日に観測終了")
4. Filter: keep only stations where `kansoku[3] == '1'` (4th digit = sunshine/solar radiation).
   - `kansoku` digit mapping (verified from `top.2.1.js`):
     `[0]=降水量, [1]=風, [2]=気温, [3]=日照時間/日射, [4]=積雪・降雪, [5]=その他`
   - `'1'` = full data (sunshine + solar radiation + normals), `'2'` = sunshine only (no solar radiation), `'0'` = no data
   - In practice, all `s`-prefixed stations have `kansoku=111111`; `a`-prefixed AMeDAS stations vary.
5. Deduplicate by `stid` (stations appear in multiple regions, e.g., 富士山).
6. Output: list of station dicts → save as `data/stations.json` for inspection/resumability.

**Expected result**: ~193 unique stations (some may have ended observation — kept for completeness, flagged in DB).

### Step 2: CSV Download (`downloader.py`)

For each station, POST to `https://www.data.jma.go.jp/risk/obsdl/show/table`:

```
POST parameters:
  stationNumList  = ["<stid>"]
  aggrgPeriod     = 1                            # daily
  elementNumList  = [["401",""],["610",""]]       # sunshine + solar radiation
  interAnnualType = 1                            # continuous period
  ymdList         = ["2025","2025","1","12","1","31"]  # full year
  optionNumList   = [["op1",0]]                  # include normal values
  downloadFlag    = true
  rmkFlag         = 1
  disconnectFlag  = 1
  youbiFlag       = 0
  fukenFlag       = 0
  kijiFlag        = 0
  csvFlag         = 1
  jikantaiFlag    = 0
  jikantaiList    = []
  ymdLiteral      = 1
```

- Decode response as `cp932` (superset of Shift_JIS, handles `㎡` etc.) → UTF-8.
- Save raw CSV to `data/raw_csv/{stid}.csv` (stid only — avoids Japanese filename issues).
- **Rate limiting**: 3-second delay between requests (be polite to JMA servers).
- **Resumability**: skip stations whose CSV already exists AND passes validation (≥365 data lines starting with `20`, Content-Type was `application/octet-stream`). Download to `.tmp` first, validate, then atomic rename.
- **Error handling**: retry up to 3 times with exponential backoff on failure; log failures and continue.
- **Session refresh**: if response is a redirect or non-CSV (HTML error page), re-GET the main page to refresh cookies and retry.

**Estimated time**: ~193 stations × 3s = ~10 minutes.

### Step 3: CSV Parsing & SQLite Storage (`parser.py`, `db.py`)

#### SQLite Schema

```sql
CREATE TABLE stations (
    stid        TEXT PRIMARY KEY,  -- e.g. 's47936' or 'a0366'
    name        TEXT NOT NULL,     -- e.g. '那覇'
    prid        TEXT NOT NULL,     -- region ID
    latitude    REAL,             -- decimal degrees (parsed from title)
    longitude   REAL,             -- decimal degrees (parsed from title)
    elevation   REAL,             -- meters (parsed from title)
    kansoku     TEXT,             -- observation capability flags (6 digits)
    title_raw   TEXT,             -- raw title attribute (unprocessed metadata)
    observation_ended TEXT        -- e.g. '2003-10-16' if ended, NULL if active
);

CREATE TABLE daily_data (
    stid                    TEXT NOT NULL,
    date                    TEXT NOT NULL,     -- 'MM-DD' (month-day, no year)
    sunshine_hours          REAL,             -- actual daily value (hours)
    sunshine_hours_quality  INTEGER,          -- quality flag
    sunshine_normal         REAL,             -- normal value (hours)
    solar_radiation         REAL,             -- actual daily value (MJ/m²)
    solar_radiation_quality INTEGER,          -- quality flag
    solar_normal            REAL,             -- normal value (MJ/m²)
    solar_normal_quality    INTEGER,          -- quality flag
    PRIMARY KEY (stid, date),
    FOREIGN KEY (stid) REFERENCES stations(stid)
);

CREATE INDEX idx_daily_data_date ON daily_data(date);
PRAGMA foreign_keys = ON;
```

**Parsing logic**:
1. Read CSV (UTF-8, already converted during download).
2. Skip first 6 rows (metadata, blank, station name, item name, normal label, sub-header).
3. For each data row, extract columns 0, 1, 3, 5, 8, 9, 11, 12 (values + quality flags).
4. Convert date `2025/M/D` → `MM-DD` format (strip year since these are normals).
5. Handle empty/missing values as NULL.
6. Insert into `daily_data` within a transaction (batch per station).
7. **Leap day**: 2025 has no Feb 29, so no special handling needed. If normals include it, store as `02-29`.

### Step 4: Verification

- Check row counts per station (expect 365 each); report per-station completeness.
- Basic sanity queries:
  - Any station with fewer than 365 days?
  - Solar normal value range check (should be 0–35 MJ/m² roughly).
  - Compare a few known stations with JMA website values.

---

## Dependencies

```
requests
beautifulsoup4
```

Standard library: `sqlite3`, `csv`, `json`, `time`, `re`, `logging`, `pathlib`, `urllib.parse`

## Code Style

- **Type annotations required** on all functions (as specified in CLAUDE.md)
- Python 3.10+ (use `X | None` syntax)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| JMA rate-limits or blocks | 3s delay, proper User-Agent, session cookies |
| Session expires mid-crawl | Re-establish session if 403/redirect detected |
| Some stations lack data for certain days | Store NULL, log warnings |
| Network interruption | Resume from where we left off (skip existing CSVs) |
| Shift_JIS decoding issues | Use `shift_jis` codec strictly; raw CSV preserved as backup |

---

## Execution Order

```
1. python crawler/crawl.py --discover    # Step 1: discover stations → data/stations.json
2. python crawler/crawl.py --download    # Step 2: download CSVs → data/raw_csv/
3. python crawler/crawl.py --parse       # Step 3: parse CSVs → data/jma_solar.db
4. python crawler/crawl.py --verify      # Step 4: run verification checks
   (or just: python crawler/crawl.py)    # runs all steps sequentially
```
