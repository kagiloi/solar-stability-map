from pathlib import Path

# Project paths
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_HTML_DIR: Path = DATA_DIR / "raw_html"
RAW_CSV_DIR: Path = DATA_DIR / "raw_csv"
STATIONS_JSON: Path = DATA_DIR / "stations.json"
DB_PATH: Path = DATA_DIR / "jma_solar.db"

# JMA URLs
BASE_URL: str = "https://www.data.jma.go.jp/risk/obsdl"
INDEX_URL: str = f"{BASE_URL}/index.php"
STATION_URL: str = f"{BASE_URL}/top/station"
TABLE_URL: str = f"{BASE_URL}/show/table"

# Request settings
REQUEST_DELAY: float = 3.0  # seconds between requests
MAX_RETRIES: int = 3
RETRY_BACKOFF_BASE: float = 2.0  # exponential backoff base
REQUEST_TIMEOUT: int = 30  # seconds

USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# CSV download POST parameters (template)
CSV_POST_PARAMS: dict[str, str | list[str]] = {
    "aggrgPeriod": "1",
    "interAnnualType": "1",
    "ymdList": '["2025","2025","1","12","1","31"]',
    "optionNumList": '[["op1",0]]',
    "downloadFlag": "true",
    "rmkFlag": "1",
    "disconnectFlag": "1",
    "youbiFlag": "0",
    "fukenFlag": "0",
    "kijiFlag": "0",
    "csvFlag": "1",
    "jikantaiFlag": "0",
    "jikantaiList": "[]",
    "ymdLiteral": "1",
}

# Element numbers for sunshine hours + solar radiation
ELEMENT_NUM_LIST: str = '[["401",""],["610",""]]'

# CSV parsing
CSV_HEADER_ROWS: int = 6
CSV_ENCODING: str = "cp932"
EXPECTED_DATA_ROWS: int = 365
