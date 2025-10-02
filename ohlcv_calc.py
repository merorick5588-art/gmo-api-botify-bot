import pandas as pd

# ==== 特長量の計算 ====
def add_features(df: pd.DataFrame):
    df["SMA_20"] = df["Close"].rolling(window=20).mean()
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    df["RSI_14"] = compute_rsi(df["Close"], 14)
    df["MACD"], df["MACD_signal"] = compute_macd(df["Close"])
    return df

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

# ==== CSVから特徴量計算 ====
def process_csv(file_path: str):
    try:
        df = pd.read_csv(file_path, parse_dates=["OpenTime"])
    except FileNotFoundError:
        print(f"CSV not found: {file_path}")
        return None

    # float型に変換
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Close"])
    df = add_features(df)

    # 出力ファイル名
    out_name = file_path.replace(".csv", "_features.csv")
    df.to_csv(out_name, index=False)
    print(f"Saved {out_name}")
    return df

# ==== メイン処理（symbols.csv 一括処理）====
def main(symbols_csv: str):
    intervals = ["15min", "1hour", "4hour"]

    # symbols.csv は 1列目: symbol, 2列目: type (crypto/forex)
    df_symbols = pd.read_csv(symbols_csv)

    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"].lower()  # crypto or forex
        print(f"\n=== Processing {symbol} ({market}) ===")

        for interval in intervals:
            if market == "forex":
                file_name = f"{symbol}_{interval}_forex.csv"
            else:
                file_name = f"{symbol}_{interval}.csv"

            process_csv(file_name)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("symbols_csv", type=str, help="銘柄リストCSV (例: symbols.csv)")
    args = parser.parse_args()

    main(args.symbols_csv)
