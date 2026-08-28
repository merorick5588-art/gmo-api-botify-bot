from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from symbol_config import load_symbols


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (alpha=1/period)."""
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # 損失が0なら100、利益が0なら0に寄せる。
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain != 0, 0.0)
    return rsi


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder_rma(true_range(df), period)


def compute_macd(series: pd.Series, short: int = 12, long: int = 26, signal: int = 9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd = ema_short - ema_long
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line


def compute_adx(df: pd.DataFrame, period: int = 14):
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    atr = compute_atr(df, period)
    plus_di = 100 * wilder_rma(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * wilder_rma(minus_dm, period) / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = wilder_rma(dx, period)
    return adx, plus_di, minus_di


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["SMA_20"] = out["Close"].rolling(window=20).mean()
    out["SMA_50"] = out["Close"].rolling(window=50).mean()
    out["RSI_14"] = compute_rsi(out["Close"], 14)
    out["MACD"], out["MACD_signal"] = compute_macd(out["Close"])
    out["ATR_14"] = compute_atr(out, 14)
    out["ADX_14"], out["PLUS_DI_14"], out["MINUS_DI_14"] = compute_adx(out, 14)
    return out


def process_csv(file_path: str):
    try:
        df = pd.read_csv(file_path, parse_dates=["OpenTime"])
    except FileNotFoundError:
        print(f"CSV not found: {file_path}")
        return None

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = add_features(df)
    out_name = file_path.replace(".csv", "_features.csv")
    df.to_csv(out_name, index=False)
    print(f"Saved {out_name}")
    return df


def main(symbols_csv: str):
    for symbol in load_symbols(symbols_csv):
        print(f"\n=== Processing {symbol} ===")
        for interval in ("15min", "1hour", "4hour"):
            process_csv(f"{symbol}_{interval}_forex.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols_csv")
    args = parser.parse_args()
    main(args.symbols_csv)
