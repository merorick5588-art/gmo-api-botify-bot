from __future__ import annotations

import argparse
import json
import math
import os

import pandas as pd

from symbol_config import load_symbols

TIMEFRAMES = {"15m": "15min", "1h": "1hour", "4h": "4hour"}


def _safe(value, default=0.0):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def derive_regime(df: pd.DataFrame) -> str:
    row = df.iloc[-1]
    close = _safe(row["Close"])
    sma20 = _safe(row["SMA_20"], close)
    sma50 = _safe(row["SMA_50"], close)
    adx = _safe(row.get("ADX_14"), 0)
    plus_di = _safe(row.get("PLUS_DI_14"), 0)
    minus_di = _safe(row.get("MINUS_DI_14"), 0)
    ret = df["Close"].pct_change()
    recent_vol = _safe(ret.tail(20).std(), 0)
    base_vol = _safe(ret.tail(100).std(), recent_vol or 1e-9)
    vol_ratio = recent_vol / max(base_vol, 1e-9)

    if vol_ratio >= 1.8:
        return "HIGH_VOL"
    if adx < 18:
        return "RANGE"
    if adx >= 20 and sma20 > sma50 and plus_di >= minus_di:
        return "TREND_UP"
    if adx >= 20 and sma20 < sma50 and minus_di >= plus_di:
        return "TREND_DOWN"
    return "TRANSITION"


def summarize(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    close = _safe(last["Close"])
    atr = max(_safe(last["ATR_14"]), 1e-9)
    sma20 = _safe(last["SMA_20"], close)
    sma50 = _safe(last["SMA_50"], close)
    macd = _safe(last["MACD"])
    macd_signal = _safe(last["MACD_signal"])
    returns = df["Close"].pct_change().dropna().tail(20)
    recent = df.tail(20)
    high = _safe(recent["High"].max(), close)
    low = _safe(recent["Low"].min(), close)
    sma20_prev = _safe(df["SMA_20"].iloc[-6], sma20) if len(df) >= 6 else sma20
    sma50_prev = _safe(df["SMA_50"].iloc[-6], sma50) if len(df) >= 6 else sma50
    total_ret = df["Close"].pct_change().dropna()
    recent_std = _safe(total_ret.tail(20).std())
    base_std = _safe(total_ret.tail(100).std(), recent_std or 1e-9)

    # 絶対値より価格桁に依存しにくい正規化特徴を優先する。
    return {
        "reg": derive_regime(df),
        "rsi": round(_safe(last["RSI_14"], 50), 2),
        "adx": round(_safe(last.get("ADX_14")), 2),
        "pdi": round(_safe(last.get("PLUS_DI_14")), 2),
        "mdi": round(_safe(last.get("MINUS_DI_14")), 2),
        "s20": round((close - sma20) / atr, 3),
        "s50": round((close - sma50) / atr, 3),
        "sl20": round((sma20 - sma20_prev) / atr, 3),
        "sl50": round((sma50 - sma50_prev) / atr, 3),
        "macd": round(macd / atr, 3),
        "mh": round((macd - macd_signal) / atr, 3),
        "atrp": round(atr / close * 100, 4) if close else 0,
        "vr": round(recent_std / max(base_std, 1e-9), 3),
        "h20": round((high - close) / atr, 3),
        "l20": round((close - low) / atr, 3),
        "ret20": round(_safe(returns.mean()) * 100, 4),
        "up20": round(_safe((returns > 0).mean(), 0.5), 3),
        "last": round(_safe(returns.iloc[-1]) * 100, 4) if not returns.empty else 0,
        "atr": round(atr, 7),
    }


def recent_ohlc(df: pd.DataFrame) -> list[list[float]]:
    # [O,H,L,C]、古い→新しい。モデルには凡例を明示する。
    rows = df.tail(4)
    return [
        [round(_safe(r.Open), 7), round(_safe(r.High), 7), round(_safe(r.Low), 7), round(_safe(r.Close), 7)]
        for _, r in rows.iterrows()
    ]


def prepare_ai_input(symbols_csv: str):
    for symbol in load_symbols(symbols_csv):
        result = {"symbol": symbol, "tf": {}}
        complete = True
        for label, suffix in TIMEFRAMES.items():
            path = f"{symbol}_{suffix}_forex_features.csv"
            if not os.path.exists(path):
                complete = False
                break
            df = pd.read_csv(path)
            if len(df) < 60:
                complete = False
                break
            result["tf"][label] = {"f": summarize(df), "c": recent_ohlc(df)}
        if not complete:
            print(f"Skip AI input {symbol}: timeframe不足")
            continue
        out = f"{symbol}_ai_input.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        print(f"Saved {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols_csv")
    args = parser.parse_args()
    prepare_ai_input(args.symbols_csv)
