import requests
import pandas as pd
from datetime import datetime, timedelta, date
import time

CRYPTO_KLINES_URL = "https://api.coin.z.com/public/v1/klines"
FOREX_KLINES_URL = "https://forex-api.coin.z.com/public/v1/klines"
CRYPTO_TICKER_URL = "https://api.coin.z.com/public/v1/ticker"
FOREX_TICKER_URL = "https://forex-api.coin.z.com/public/v1/ticker"

# === OHLCV取得関数（従来通り） ===
def fetch_ohlcv(symbol: str, interval: str, market: str, price_type: str = "BID", days: int = 30):
    dfs = []
    if interval in ["4hour", "8hour", "12hour", "1day", "1week", "1month"]:
        for yr in [date.today().year - 1, date.today().year]:
            params = {"symbol": symbol, "interval": interval, "date": str(yr)}
            url = FOREX_KLINES_URL if market == "forex" else CRYPTO_KLINES_URL
            if market == "forex":
                params["priceType"] = price_type
            try:
                resp = requests.get(url, params=params)
                jd = resp.json()
                if jd.get("status") != 0 or "data" not in jd or not jd["data"]:
                    continue
                df = pd.DataFrame(jd["data"])
                df["OpenTime"] = pd.to_datetime(df["openTime"].astype(int), unit="ms", utc=True)\
                                  .dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
                df["Volume"] = df.get("volume", 0) if market == "forex" else df["volume"]
                df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
                dfs.append(df[["OpenTime", "Open", "High", "Low", "Close", "Volume"]])
            except Exception as e:
                print(f"{market} {symbol} fetch error ({yr}): {e}")
            time.sleep(1)
    else:
        today = datetime.now().date()
        for i in range(days):
            date_iter = today - timedelta(days=i)
            date_str = date_iter.strftime("%Y%m%d")
            params = {"symbol": symbol, "interval": interval, "date": date_str}
            url = FOREX_KLINES_URL if market == "forex" else CRYPTO_KLINES_URL
            if market == "forex":
                params["priceType"] = price_type
            try:
                resp = requests.get(url, params=params)
                jd = resp.json()
                if jd.get("status") != 0 or "data" not in jd or not jd["data"]:
                    continue
                df = pd.DataFrame(jd["data"])
                df["OpenTime"] = pd.to_datetime(df["openTime"].astype(int), unit="ms", utc=True)\
                                  .dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
                df["Volume"] = df.get("volume", 0) if market == "forex" else df["volume"]
                df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
                dfs.append(df[["OpenTime", "Open", "High", "Low", "Close", "Volume"]])
            except Exception as e:
                print(f"{market} {symbol} fetch error on {date_str}: {e}")
            time.sleep(1)
    if dfs:
        return pd.concat(dfs).sort_values("OpenTime").reset_index(drop=True)
    else:
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])

# === 最新レート取得（ForexとCrypto共通化） ===
def fetch_all_latest_prices():
    """
    symbol未指定で全銘柄取得
    """
    all_data = {}

    # Forex
    try:
        resp = requests.get(FOREX_TICKER_URL)
        jd = resp.json()
        if jd.get("status") == 0 and "data" in jd:
            for d in jd["data"]:
                all_data[d["symbol"]] = {"symbol": d["symbol"], "type": "forex",
                                         "bid": float(d["bid"]), "ask": float(d["ask"]),
                                         "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        print(f"Error fetching Forex latest prices: {e}")
    time.sleep(1)

    # Crypto
    try:
        resp = requests.get(CRYPTO_TICKER_URL)
        jd = resp.json()
        if jd.get("status") == 0 and "data" in jd:
            for d in jd["data"]:
                all_data[d["symbol"]] = {"symbol": d["symbol"], "type": "crypto",
                                         "bid": float(d["bid"]), "ask": float(d["ask"]),
                                         "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        print(f"Error fetching Crypto latest prices: {e}")
    time.sleep(1)

    return all_data

if __name__ == "__main__":
    csv_file = "symbols.csv"
    intervals = ["15min", "1hour", "4hour"]
    days = 30

    df_symbols = pd.read_csv(csv_file)
    symbols_list = df_symbols["symbol"].tolist()

    # === 最新レート取得（1回API実行） ===
    all_latest = fetch_all_latest_prices()
    latest_rows = []
    for symbol in symbols_list:
        if symbol in all_latest:
            latest_rows.append(all_latest[symbol])

    # 最新レートCSV保存
    for latest in latest_rows:
        symbol = latest["symbol"]
        out_name = f"{symbol}_latest_rates.csv"
        pd.DataFrame([latest]).to_csv(out_name, index=False)
        print(f"Saved {out_name}")

    # === OHLCV取得（直列、1秒間隔） ===
    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]
        print(f"\n=== Fetching {symbol} ({market}) ===")
        for interval in intervals:
            print(f"Fetching {interval} data for {symbol}...")
            df = fetch_ohlcv(symbol, interval, market, days=days)
            if df.empty:
                print(f"No data for {symbol} {interval}")
                continue
            out_name = f"{symbol}_{interval}_{market}.csv"
            df.to_csv(out_name, index=False)
            print(f"Saved {out_name}")
