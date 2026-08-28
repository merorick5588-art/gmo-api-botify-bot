import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# 運用目標。年間+100%は評価目標であり、無理なロット増加には使わない。
BASE_CAPITAL_JPY = _float("BASE_CAPITAL_JPY", 400_000)
TARGET_ANNUAL_RETURN_PCT = _float("TARGET_ANNUAL_RETURN_PCT", 100.0)

# リスク管理
RISK_PER_TRADE_PCT = _float("RISK_PER_TRADE_PCT", 0.75)
MAX_TOTAL_RISK_PCT = _float("MAX_TOTAL_RISK_PCT", 2.5)
MIN_MARGIN_RATIO = _float("MIN_MARGIN_RATIO", 150.0)
MIN_RR = _float("MIN_RR", 1.5)
MAX_CURRENCY_EXPOSURE_RISK = _float("MAX_CURRENCY_EXPOSURE_RISK", 2.0)
MAX_SPREAD_ATR_RATIO = _float("MAX_SPREAD_ATR_RATIO", 0.12)

# シグナル閾値
ENTRY_SCORE_THRESHOLD = _float("ENTRY_SCORE_THRESHOLD", 0.65)
ENTRY_QUALITY_THRESHOLD = _float("ENTRY_QUALITY_THRESHOLD", 0.68)

# 重要指標ガード（ニュース要約は行わない）
EVENT_PRE_MINUTES = _int("EVENT_PRE_MINUTES", 60)
# 「警告」と「直前」を別通知にする。既存cronが30分刻みでも拾いやすい初期値。
EVENT_IMMINENT_MINUTES = _int("EVENT_IMMINENT_MINUTES", 30)
EVENT_POST_MINUTES = _int("EVENT_POST_MINUTES", 30)
EVENT_RELEASE_LOOKBACK_MINUTES = _int("EVENT_RELEASE_LOOKBACK_MINUTES", 45)
EVENT_FAIL_SAFE = _bool("EVENT_FAIL_SAFE", True)
CALENDAR_CACHE_MAX_AGE_HOURS = _int("CALENDAR_CACHE_MAX_AGE_HOURS", 36)
FOREX_FACTORY_CALENDAR_URL = os.getenv(
    "FOREX_FACTORY_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)

# GMO Private APIが取れない場合、建玉を知らずに新規推奨しない。
PRIVATE_API_FAIL_SAFE = _bool("PRIVATE_API_FAIL_SAFE", True)
MAX_TICKER_AGE_SECONDS = _int("MAX_TICKER_AGE_SECONDS", 180)

# データ量。未確定足を除外した完成足をこの程度確保する。
OHLC_TARGET_BARS = _int("OHLC_TARGET_BARS", 160)
OHLC_MAX_DAYS = _int("OHLC_MAX_DAYS", 14)

# 実約定同期。latestExecutionsは銘柄単位なので頻度を抑える。
SYNC_EXECUTIONS = _bool("SYNC_EXECUTIONS", True)
EXECUTION_SYNC_INTERVAL_HOURS = _int("EXECUTION_SYNC_INTERVAL_HOURS", 6)

# 永続状態
STATE_DIR = Path(os.getenv("BOT_STATE_DIR", "state"))
STATE_DB = Path(os.getenv("BOT_STATE_DB", str(STATE_DIR / "fxbot.sqlite3")))
CALENDAR_CACHE_PATH = Path(
    os.getenv("CALENDAR_CACHE_PATH", str(STATE_DIR / "ff_calendar_cache.json"))
)

# Discord
DISCORD_FOREX_MAIN = os.getenv("DISCORD_FOREX_MAIN")
DISCORD_FOREX_OTHER = os.getenv("DISCORD_FOREX_OTHER")
DISCORD_FOREX_EVENT = os.getenv("DISCORD_FOREX_EVENT") or DISCORD_FOREX_MAIN

# 通知重複抑制
SIGNAL_DEDUP_MINUTES = _int("SIGNAL_DEDUP_MINUTES", 180)
VIRTUAL_SIGNAL_PENDING_HOURS = _int("VIRTUAL_SIGNAL_PENDING_HOURS", 12)


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
