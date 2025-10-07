import pandas as pd
import sys
import os
import json

TIMEFRAMES = {
    "15m": "15min",
    "1h": "1hour",
    "4h": "4hour"
}

def calculate_features(df):
    df20 = df.tail(20).reset_index(drop=True)
    returns = df20["Close"].pct_change().dropna()

    features_summary = {
        "sma20": float(df20["Close"].mean()),
        "sma50": float(df.tail(50)["Close"].mean()) if len(df) >= 50 else float("nan"),
        "rsi14": float(df20["RSI_14"].iloc[-1]) if "RSI_14" in df20.columns else float("nan"),
        "macd": float(df20["MACD"].iloc[-1]) if "MACD" in df20.columns else float("nan"),
        "macd_signal": float(df20["MACD_signal"].iloc[-1]) if "MACD_signal" in df20.columns else float("nan"),
        "avg_ret20": float(returns.mean()) if not returns.empty else float("nan"),
        "std_ret20": float(returns.std()) if not returns.empty else float("nan"),
        "trend_up_ratio": float((returns > 0).mean()) if not returns.empty else float("nan"),
        "last_ret": float(returns.iloc[-1]) if not returns.empty else float("nan")
    }

    recent_rows = df.tail(3).reset_index(drop=True)
    recent_ohlc = []
    for _, r in recent_rows.iterrows():
        recent_ohlc.append({
            "o": float(r["Open"]),
            "h": float(r["High"]),
            "l": float(r["Low"]),
            "c": float(r["Close"]),
            "v": float(r["Volume"])
        })

    return recent_ohlc, features_summary

def prepare_ai_input(symbols_csv):
    df_symbols = pd.read_csv(symbols_csv)
    
    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]  # crypto or forex

        print(f"Processing {symbol} ({market})...")
        result = {"symbol": symbol, "timeframes": {}}

        for tf_label, tf_suffix in TIMEFRAMES.items():
            if market == "forex":
                fname = f"{symbol}_{tf_suffix}_forex_features.csv"
            else:
                fname = f"{symbol}_{tf_suffix}_crypto_features.csv"

            if not os.path.exists(fname):
                print(f"  File not found: {fname}")
                continue

            df = pd.read_csv(fname, parse_dates=["OpenTime"])
            if df.empty:
                print(f"  No data in {fname}")
                continue

            recent_ohlc, features_summary = calculate_features(df)
            result["timeframes"][tf_label] = {
                "recent_ohlc": recent_ohlc,
                "features_summary": features_summary
            }

        out_name = f"{symbol}_ai_input.json"
        with open(out_name, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print(f"  Saved AI input JSON: {out_name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python prepare_ai_input.py symbols.csv")
        sys.exit(1)
    
    symbols_csv = sys.argv[1]
    prepare_ai_input(symbols_csv)
