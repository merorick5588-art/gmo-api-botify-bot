import requests
import pandas as pd
from datetime import datetime, timedelta, date
import time

CRYPTO_KLINES_URL = "https://api.coin.z.com/public/v1/klines"
FOREX_KLINES_URL = "https://forex-api.coin.z.com/public/v1/klines"

def fetch_ohlcv(symbol: str, interval: str, market: str, price_type: str = "BID", days: int = 30):
    dfs = []

    # 4時間足など年単位の長時間足は前年＋今年のデータを取得
    if interval in ["4hour", "8hour", "12hour", "1day", "1week", "1month"]:
        for yr in [date.today().year - 1, date.today().year]:
            params = {"symbol": symbol, "interval": interval, "date": str(yr)}
            if market == "forex":
                params["priceType"] = price_type
                url = FOREX_KLINES_URL
            else:
                url = CRYPTO_KLINES_URL

            try:
                resp = requests.get(url, params=params)
                jd = resp.json()
                if jd.get("status") != 0 or "data" not in jd or not jd["data"]:
                    continue
                df = pd.DataFrame(jd["data"])
                df["OpenTime"] = pd.to_datetime(df["openTime"].astype(int), unit="ms", utc=True)\
                                  .dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
                df["Volume"] = df.get("volume", 0) if market=="forex" else df["volume"]
                df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
                dfs.append(df[["OpenTime","Open","High","Low","Close","Volume"]])
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
            if market == "forex":
                params["priceType"] = price_type
                url = FOREX_KLINES_URL
            else:
                url = CRYPTO_KLINES_URL

            try:
                resp = requests.get(url, params=params)
                jd = resp.json()
                if jd.get("status") != 0 or "data" not in jd or not jd["data"]:
                    continue
                df = pd.DataFrame(jd["data"])
                df["OpenTime"] = pd.to_datetime(df["openTime"].astype(int), unit="ms", utc=True)\
                                  .dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
                df["Volume"] = df.get("volume", 0) if market=="forex" else df["volume"]
                df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
                dfs.append(df[["OpenTime","Open","High","Low","Close","Volume"]])
            except Exception as e:
                print(f"{market} {symbol} fetch error on {date_str}: {e}")
            time.sleep(0.5)

    if dfs:
        # 全データを結合して時系列順にソート
        return pd.concat(dfs).sort_values("OpenTime").reset_index(drop=True)
    else:
        return pd.DataFrame(columns=["OpenTime","Open","High","Low","Close","Volume"])


if __name__ == "__main__":
    csv_file = "symbols.csv"  # CSVパス
    intervals = ["15min", "1hour", "4hour"]
    days = 30

    df_symbols = pd.read_csv(csv_file)

    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]  # crypto or forex
        print(f"\n=== Fetching {symbol} ({market}) ===")
        for interval in intervals:
            print(f"Fetching {interval} data for {symbol}...")
            df = fetch_ohlcv(symbol, interval, market, days=days)
            if df.empty:
                print(f"No data for {symbol} {interval}")
                continue

            # 出力ファイル名
            if market=="forex":
                out_name = f"{symbol}_{interval}_forex.csv"
            else:
                out_name = f"{symbol}_{interval}.csv"

            df.to_csv(out_name, index=False)
            print(f"Saved {out_name}")
