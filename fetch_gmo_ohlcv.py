import requests
import pandas as pd
from datetime import datetime, timedelta, date
import time

CRYPTO_KLINES_URL = "https://api.coin.z.com/public/v1/klines"
FOREX_KLINES_URL = "https://forex-api.coin.z.com/public/v1/klines"
CRYPTO_TICKER_URL = "https://api.coin.z.com/public/v1/ticker"
FOREX_TICKER_URL = "https://forex-api.coin.z.com/public/v1/ticker"


def fetch_ohlcv(symbol: str, interval: str, market: str, price_type: str = "BID", days: int = 30):
    """
    GMO APIからOHLCVデータを取得する
    """
    dfs = []

    # 4時間足以上は年単位で取得
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
            time.sleep(0.5)  # API制限対策

    else:
        # 分足・1時間足は日単位で過去days日ループ
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
            time.sleep(0.5)

    if dfs:
        return pd.concat(dfs).sort_values("OpenTime").reset_index(drop=True)
    else:
        return pd.DataFrame(columns=["OpenTime", "Open", "High", "Low", "Close", "Volume"])


def fetch_latest_price(symbol: str, market: str):
    """
    最新レート (bid/ask) を取得
    """
    url = FOREX_TICKER_URL if market == "forex" else CRYPTO_TICKER_URL
    try:
        resp = requests.get(url, params={"symbol": symbol})
        jd = resp.json()
        if jd.get("status") != 0 or "data" not in jd or not jd["data"]:
            return None
        data = jd["data"][0]
        bid = float(data["bid"])
        ask = float(data["ask"])
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"symbol": symbol, "type": market, "bid": bid, "ask": ask, "timestamp": ts}
    except Exception as e:
        print(f"Error fetching latest price for {symbol}: {e}")
        return None


if __name__ == "__main__":
    csv_file = "symbols.csv"  # 銘柄リスト
    intervals = ["15min", "1hour", "4hour"]
    days = 30

    df_symbols = pd.read_csv(csv_file)
    latest_rows = []

    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]  # crypto or forex

        # === 最新レートを取得 ===
        latest = fetch_latest_price(symbol, market)
        if latest:
            latest_rows.append(latest)

        # === OHLCVを取得 ===
        print(f"\n=== Fetching {symbol} ({market}) ===")
        for interval in intervals:
            print(f"Fetching {interval} data for {symbol}...")
            df = fetch_ohlcv(symbol, interval, market, days=days)
            if df.empty:
                print(f"No data for {symbol} {interval}")
                continue

            # 保存ファイル名
            if market == "forex":
                out_name = f"{symbol}_{interval}_forex.csv"
            else:
                out_name = f"{symbol}_{interval}.csv"

            df.to_csv(out_name, index=False)
            print(f"Saved {out_name}")

    # === 最新レートを個別CSVで保存 ===
    for latest in latest_rows:
        symbol = latest["symbol"]
        out_name = f"{symbol}_latest_rates.csv"
        pd.DataFrame([latest]).to_csv(out_name, index=False)
        print(f"Saved {out_name}")

