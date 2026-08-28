from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from bot_config import MAX_TICKER_AGE_SECONDS, OHLC_MAX_DAYS, OHLC_TARGET_BARS
from gmo_client import GMOClient, parse_api_timestamp
from symbol_config import load_symbols

JST = ZoneInfo("Asia/Tokyo")
INTERVAL_MINUTES = {"15min": 15, "1hour": 60, "4hour": 240, "1day": 1440}


def _remove_old_outputs(symbol: str) -> None:
    for suffix in (
        "15min_forex.csv",
        "1hour_forex.csv",
        "4hour_forex.csv",
        "1day_forex.csv",
        "15min_forex_features.csv",
        "1hour_forex_features.csv",
        "4hour_forex_features.csv",
        "1day_forex_features.csv",
        "ai_input.json",
        "latest_rates.csv",
    ):
        path = f"{symbol}_{suffix}"
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _rows_to_df(rows: list[dict], interval: str, now: datetime) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(rows)
    required = {"openTime", "open", "high", "low", "close"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])
    df["OpenTime"] = (
        pd.to_datetime(pd.to_numeric(df["openTime"]), unit="ms", utc=True)
        .dt.tz_convert(JST)
        .dt.tz_localize(None)
    )
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Volume"] = 0.0
    df = df.dropna(subset=["OpenTime", "Open", "High", "Low", "Close"])

    # 形成中の足を除外。OpenTime + timeframe <= now の完成足だけを使う。
    now_naive = now.astimezone(JST).replace(tzinfo=None)
    duration = pd.to_timedelta(INTERVAL_MINUTES[interval], unit="m")
    df = df[df["OpenTime"] + duration <= now_naive]
    return df[["OpenTime", "Open", "High", "Low", "Close", "Volume"]]


def fetch_ohlcv(client: GMOClient, symbol: str, interval: str, now: datetime) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    if interval in {"4hour", "1day"}:
        # 4h/日足は年単位。長期特徴量用に必要本数まで前年へ遡る。
        for year in (now.year, now.year - 1):
            try:
                rows = client.klines(symbol, interval, str(year), "BID")
                chunk = _rows_to_df(rows, interval, now)
                if not chunk.empty:
                    chunks.append(chunk)
                if sum(len(c) for c in chunks) >= OHLC_TARGET_BARS:
                    break
            except Exception as exc:
                print(f"FX {symbol} {interval} fetch error year={year}: {exc}")
            time.sleep(0.15)
    else:
        # 日付境界や休日を吸収するため、必要本数に達するまで日単位で遡る。
        jst_now = now.astimezone(JST)
        # GMO FXの日次区切り(06:00 JST)を基準に当該取引日を決める。
        trading_date = (jst_now - timedelta(hours=6)).date()
        for i in range(OHLC_MAX_DAYS):
            day = (trading_date - timedelta(days=i)).strftime("%Y%m%d")
            try:
                rows = client.klines(symbol, interval, day, "BID")
                chunk = _rows_to_df(rows, interval, now)
                if not chunk.empty:
                    chunks.append(chunk)
                if sum(len(c) for c in chunks) >= OHLC_TARGET_BARS:
                    break
            except Exception as exc:
                print(f"FX {symbol} {interval} fetch error date={day}: {exc}")
            time.sleep(0.15)

    if not chunks:
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["OpenTime"]).sort_values("OpenTime")
    return df.tail(OHLC_TARGET_BARS).reset_index(drop=True)


def _ticker_is_fresh(row: dict, now: datetime) -> tuple[bool, str | None]:
    if str(row.get("status", "")).upper() != "OPEN":
        return False, f"ticker status={row.get('status')}"
    ts = parse_api_timestamp(row.get("timestamp"))
    if ts is None:
        return False, "ticker timestamp不明"
    age = (now.astimezone(ts.tzinfo) - ts).total_seconds()
    if age < -30 or age > MAX_TICKER_AGE_SECONDS:
        return False, f"ticker age={age:.0f}s"
    return True, None


def main(csv_file: str):
    now = datetime.now(tz=JST)
    client = GMOClient()
    symbols = load_symbols(csv_file)

    for symbol in symbols:
        _remove_old_outputs(symbol)

    status = client.service_status()
    if status != "OPEN":
        raise SystemExit(f"GMO FX service status={status}; 市場データ生成を停止")

    rules = client.symbols()
    unsupported = [s for s in symbols if s not in rules]
    if unsupported:
        raise SystemExit(f"GMO FX未対応symbol: {unsupported}")

    for symbol in symbols:
        print(f"\n=== Fetching {symbol} ===")
        for interval in ("15min", "1hour", "4hour", "1day"):
            df = fetch_ohlcv(client, symbol, interval, now)
            if len(df) < 260:
                print(f"Insufficient data for {symbol} {interval}: {len(df)} bars")
                continue
            out_name = f"{symbol}_{interval}_forex.csv"
            df.to_csv(out_name, index=False)
            print(f"Saved {out_name}: {len(df)} completed bars")

    # KLine取得後にtickerを取得し直し、Entry基準のBid/Askを可能な限り新鮮にする。
    ticker = client.ticker()
    for symbol in symbols:
        row = ticker.get(symbol)
        if not row:
            print(f"Latest rate not found: {symbol}")
            continue
        ok, reason = _ticker_is_fresh(row, datetime.now(tz=JST))
        if not ok:
            print(f"Stale/closed ticker {symbol}: {reason}")
            continue
        pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "bid": row["bid"],
                    "ask": row["ask"],
                    "timestamp": row.get("timestamp"),
                    "status": row.get("status"),
                    "tickSize": rules[symbol].get("tickSize"),
                    "minOpenOrderSize": rules[symbol].get("minOpenOrderSize"),
                    "maxOrderSize": rules[symbol].get("maxOrderSize"),
                    "sizeStep": rules[symbol].get("sizeStep"),
                }
            ]
        ).to_csv(f"{symbol}_latest_rates.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols_csv")
    args = parser.parse_args()
    main(args.symbols_csv)
