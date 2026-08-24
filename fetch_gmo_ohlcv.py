import sys
import time
from datetime import datetime, timedelta, date

import pandas as pd
import requests

FOREX_KLINES_URL = "https://forex-api.coin.z.com/public/v1/klines"
FOREX_TICKER_URL = "https://forex-api.coin.z.com/public/v1/ticker"


def fetch_ohlcv(symbol: str, interval: str, price_type: str = "BID", days: int = 30):
    dfs = []

    if interval in ["4hour", "8hour", "12hour", "1day", "1week", "1month"]:
        periods = [("year", str(yr)) for yr in [date.today().year - 1, date.today().year]]
    else:
        today = datetime.now().date()
        periods = [
            ("day", (today - timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(days)
        ]

    for period_type, date_value in periods:
        params = {
            "symbol": symbol,
            "interval": interval,
            "date": date_value,
            "priceType": price_type,
        }
        try:
            resp = requests.get(FOREX_KLINES_URL, params=params, timeout=15)
            resp.raise_for_status()
            jd = resp.json()
            if jd.get("status") != 0 or not jd.get("data"):
                continue

            df = pd.DataFrame(jd["data"])
            df["OpenTime"] = (
                pd.to_datetime(df["openTime"].astype(int), unit="ms", utc=True)
                .dt.tz_convert("Asia/Tokyo")
                .dt.tz_localize(None)
            )
            df["Volume"] = 0.0
            df = df.rename(
                columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
            )
            dfs.append(df[["OpenTime", "Open", "High", "Low", "Close", "Volume"]])
        except Exception as exc:
            label = f"{period_type}={date_value}"
            print(f"FX {symbol} fetch error ({label}): {exc}")
        time.sleep(1)

    if not dfs:
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])
    return pd.concat(dfs).sort_values("OpenTime").reset_index(drop=True)


def fetch_all_latest_prices():
    """GMO FXの全銘柄tickerを1回で取得する。"""
    all_data = {}
    try:
        resp = requests.get(FOREX_TICKER_URL, timeout=15)
        resp.raise_for_status()
        jd = resp.json()
        if jd.get("status") == 0 and "data" in jd:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for d in jd["data"]:
                all_data[d["symbol"]] = {
                    "symbol": d["symbol"],
                    "bid": float(d["bid"]),
                    "ask": float(d["ask"]),
                    "timestamp": timestamp,
                }
    except Exception as exc:
        print(f"Error fetching Forex latest prices: {exc}")
    return all_data


def main(csv_file: str):
    intervals = ["15min", "1hour", "4hour"]
    days = 30

    df_symbols = pd.read_csv(csv_file)
    symbols = df_symbols["symbol"].dropna().astype(str).tolist()

    # Klines APIはsymbol単位のため内部では順次取得するが、Workflowは全銘柄を1実行で処理する。
    for symbol in symbols:
        print(f"\n=== Fetching {symbol} (forex) ===")
        for interval in intervals:
            print(f"Fetching {interval} data for {symbol}...")
            df = fetch_ohlcv(symbol, interval, days=days)
            if df.empty:
                print(f"No data for {symbol} {interval}")
                continue
            out_name = f"{symbol}_{interval}_forex.csv"
            df.to_csv(out_name, index=False)
            print(f"Saved {out_name}")

    # tickerは全対象銘柄分を1回のAPIリクエストで取得する。
    all_latest = fetch_all_latest_prices()
    for symbol in symbols:
        latest = all_latest.get(symbol)
        if not latest:
            print(f"Latest rate not found: {symbol}")
            continue
        out_name = f"{symbol}_latest_rates.csv"
        pd.DataFrame([latest]).to_csv(out_name, index=False)
        print(f"Saved {out_name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python fetch_gmo_ohlcv.py <symbols_csv_file>")
        sys.exit(1)
    main(sys.argv[1])
