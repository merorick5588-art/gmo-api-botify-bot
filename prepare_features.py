import pandas as pd
import sys
import os
import json
import numpy as np

TIMEFRAMES = {
    "15m": "15min",
    "1h": "1hour",
    "4h": "4hour"
}

# =========================
# 既存：特徴量要約
# =========================
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
        "last_ret": float(returns.iloc[-1]) if not returns.empty else 0.0
    }

    recent_rows = df.tail(3)
    recent_ohlc = [
        {
            "o": float(r.Open),
            "h": float(r.High),
            "l": float(r.Low),
            "c": float(r.Close),
            "v": float(r.Volume)
        }
        for _, r in recent_rows.iterrows()
    ]

    return recent_ohlc, features_summary

# =========================
# ① マーケットフェーズ
# =========================
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

# =========================
# ② フェーズ補助タグ
# =========================
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

# =========================
# ③ 価格ポジション
# =========================
def derive_price_context(df):
    recent = df.tail(20)
    high = recent["High"].max()
    low = recent["Low"].min()
    close = df["Close"].iloc[-1]

    return {
        "position_in_20bar_range": round((close - low) / (high - low + 1e-9), 3),
        "distance_from_high_pct": round((close - high) / high * 100, 2),
        "distance_from_low_pct": round((close - low) / low * 100, 2)
    }

# =========================
# ④ ボラティリティ状態
# =========================
def derive_volatility_state(df):
    ret = df["Close"].pct_change()
    recent_std = ret.tail(20).std()
    past_std = ret.tail(100).std()

    ratio = recent_std / (past_std + 1e-9)

    return {
        "volatility_level": "high" if ratio > 1.3 else "low" if ratio < 0.8 else "normal",
        "volatility_ratio": round(ratio, 2)
    }

# =========================
# ⑤ 出来高（Crypto専用）
# =========================
def derive_volume_context(df):
    recent_vol = df["Volume"].tail(5).mean()
    past_vol = df["Volume"].tail(30).mean()

    spike = recent_vol > past_vol * 1.5
    price_up = df["Close"].iloc[-1] > df["Close"].iloc[-2]

    return {
        "volume_spike": bool(spike),
        "price_move_with_volume": (
            "up_with_volume" if spike and price_up else
            "down_with_volume" if spike else
            "no_signal"
        )
    }

# =========================
# メイン：AI入力生成
# =========================
def prepare_ai_input(symbols_csv):
    df_symbols = pd.read_csv(symbols_csv)

    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]
        result = {"symbol": symbol}

        phases = {}

        for tf_label, tf_suffix in TIMEFRAMES.items():
            fname = f"{symbol}_{tf_suffix}_{market}_features.csv"
            if not os.path.exists(fname):
                continue

            df = pd.read_csv(fname)

            recent_ohlc, features = calculate_features(df)

            phase_label = derive_market_phase(df)
            phase_tags = derive_phase_tags(df)
            phases[tf_label] = phase_label

            tf_block = {
                "market_phase": {
                    "label": phase_label,
                    "tags": phase_tags
                },
                "price_context": derive_price_context(df),
                "volatility_state": derive_volatility_state(df),
                "recent_ohlc": recent_ohlc,
                "features_summary": features
            }

            # FXには volume_context を出さない
            if market == "crypto":
                tf_block["volume_context"] = derive_volume_context(df)

            result.setdefault("timeframes", {})[tf_label] = tf_block

        # 上位足支配構造
        if "4h" in phases and "1h" in phases:
            dominant = "4h" if "trend" in phases["4h"] else "1h"
        else:
            dominant = "1h"

        result["timeframe_relationship"] = {
            "dominant_tf": dominant,
            "alignment": phases
        }

        out_name = f"{symbol}_ai_input.json"
        with open(out_name, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"Saved {out_name}")

# =========================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python prepare_ai_input.py symbols.csv")
        sys.exit(1)

    prepare_ai_input(sys.argv[1])
