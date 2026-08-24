import json
import os
import sys

import pandas as pd

TIMEFRAMES = {"15m": "15min", "1h": "1hour", "4h": "4hour"}


def calculate_features(df):
    df20 = df.tail(20).reset_index(drop=True)
    returns = df20["Close"].pct_change().dropna()

    features_summary = {
        "sma20": float(df20["Close"].mean()),
        "sma50": float(df.tail(50)["Close"].mean()) if len(df) >= 50 else float("nan"),
        "rsi14": float(df20["RSI_14"].iloc[-1]),
        "macd": float(df20["MACD"].iloc[-1]),
        "macd_signal": float(df20["MACD_signal"].iloc[-1]),
        "avg_ret20": float(returns.mean()) if not returns.empty else 0.0,
        "std_ret20": float(returns.std()) if not returns.empty else 0.0,
        "trend_up_ratio": float((returns > 0).mean()) if not returns.empty else 0.5,
        "last_ret": float(returns.iloc[-1]) if not returns.empty else 0.0,
        "atr14": float(df20["ATR_14"].iloc[-1]),
        "atr_pct": float(df20["ATR_14"].iloc[-1] / df20["Close"].iloc[-1] * 100),
    }

    # 新しい足から3本だけ送る。FXのVolumeはLLM入力に含めない。
    recent_rows = df.tail(3).iloc[::-1]
    recent_ohlc = [
        {"o": float(r.Open), "h": float(r.High), "l": float(r.Low), "c": float(r.Close)}
        for _, r in recent_rows.iterrows()
    ]
    return recent_ohlc, features_summary


def derive_market_phase(df):
    sma20 = df["SMA_20"].iloc[-1]
    sma50 = df["SMA_50"].iloc[-1]
    close = df["Close"].iloc[-1]

    if close > sma20 > sma50:
        return "strong_uptrend"
    if sma20 > close > sma50:
        return "pullback_uptrend"
    if close < sma20 < sma50:
        return "strong_downtrend"
    if sma20 < close < sma50:
        return "pullback_downtrend"
    return "range"


def derive_phase_tags(df):
    rsi = df["RSI_14"].iloc[-1]
    ret = df["Close"].pct_change().tail(5)
    tags = []

    if rsi < 30:
        tags.append("oversold")
    elif rsi > 70:
        tags.append("overbought")

    total_std = df["Close"].pct_change().std()
    if ret.std() < total_std * 0.7:
        tags.append("volatility_contraction")
    if not ret.empty and abs(ret.iloc[-1]) > ret.std() * 1.5:
        tags.append("impulse_bar")
    return tags


def derive_price_context(df):
    recent = df.tail(20)
    high = recent["High"].max()
    low = recent["Low"].min()
    close = df["Close"].iloc[-1]
    return {
        "position_in_20bar_range": round((close - low) / (high - low + 1e-9), 3),
        "distance_from_high_pct": round((close - high) / high * 100, 2),
        "distance_from_low_pct": round((close - low) / low * 100, 2),
    }


def derive_volatility_state(df):
    ret = df["Close"].pct_change()
    recent_std = ret.tail(20).std()
    past_std = ret.tail(100).std()
    ratio = recent_std / (past_std + 1e-9)
    return {
        "volatility_level": "high" if ratio > 1.3 else "low" if ratio < 0.8 else "normal",
        "volatility_ratio": round(ratio, 2),
    }


def prepare_ai_input(symbols_csv):
    df_symbols = pd.read_csv(symbols_csv)

    for symbol in df_symbols["symbol"].dropna().astype(str):
        result = {"symbol": symbol}
        phases = {}

        for tf_label, tf_suffix in TIMEFRAMES.items():
            fname = f"{symbol}_{tf_suffix}_forex_features.csv"
            if not os.path.exists(fname):
                continue

            df = pd.read_csv(fname)
            recent_ohlc, features = calculate_features(df)
            phase_label = derive_market_phase(df)
            phase_tags = derive_phase_tags(df)
            phases[tf_label] = phase_label

            result.setdefault("timeframes", {})[tf_label] = {
                "market_phase": {"label": phase_label, "tags": phase_tags},
                "price_context": derive_price_context(df),
                "volatility_state": derive_volatility_state(df),
                "recent_ohlc": recent_ohlc,
                "features_summary": features,
            }

        if "4h" in phases and "1h" in phases:
            dominant = "4h" if "trend" in phases["4h"] else "1h"
        else:
            dominant = "1h"

        result["timeframe_relationship"] = {"dominant_tf": dominant, "alignment": phases}

        out_name = f"{symbol}_ai_input.json"
        with open(out_name, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        print(f"Saved {out_name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python prepare_features.py symbols.csv")
        sys.exit(1)
    prepare_ai_input(sys.argv[1])
