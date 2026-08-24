import pandas as pd


def add_features(df: pd.DataFrame):
    df["SMA_20"] = df["Close"].rolling(window=20).mean()
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    df["RSI_14"] = compute_rsi(df["Close"], 14)
    df["MACD"], df["MACD_signal"] = compute_macd(df["Close"])
    df["ATR_14"] = compute_atr(df, 14)
    return df


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(df, period=14):
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period).mean()


def compute_macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line


def process_csv(file_path: str):
    try:
        df = pd.read_csv(file_path, parse_dates=["OpenTime"])
    except FileNotFoundError:
        print(f"CSV not found: {file_path}")
        return None

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Close"])
    df = add_features(df)

    out_name = file_path.replace(".csv", "_features.csv")
    df.to_csv(out_name, index=False)
    print(f"Saved {out_name}")
    return df


def main(symbols_csv: str):
    intervals = ["15min", "1hour", "4hour"]
    df_symbols = pd.read_csv(symbols_csv)

    for symbol in df_symbols["symbol"].dropna().astype(str):
        print(f"\n=== Processing {symbol} (forex) ===")
        for interval in intervals:
            process_csv(f"{symbol}_{interval}_forex.csv")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("symbols_csv", type=str)
    args = parser.parse_args()
    main(args.symbols_csv)
