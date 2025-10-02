import pandas as pd
import sys
import os

def prepare_ai_input(symbols_csv):
    df_symbols = pd.read_csv(symbols_csv)
    
    for _, row in df_symbols.iterrows():
        symbol = row["symbol"]
        market = row["type"]  # crypto or forex

        print(f"Processing {symbol} ({market})...")

        # 各時間足の _features.csv を読み込む
        dfs = {}
        for interval in ["15min", "1hour", "4hour"]:
            if market == "forex":
                fname = f"{symbol}_{interval}_forex_features.csv"
            else:
                fname = f"{symbol}_{interval}_features.csv"
            
            if not os.path.exists(fname):
                print(f"  File not found: {fname}")
                continue
            
            df = pd.read_csv(fname, parse_dates=["OpenTime"])
            dfs[interval] = df

        if "1hour" not in dfs:
            print(f"  1hour data not found for {symbol}, skipping.")
            continue

        # 最新行の取得
        df_1h_latest = dfs["1hour"].iloc[[-1]].copy()
        df_1h_latest = df_1h_latest.add_prefix("1H_")

        # 15分足の最新行（直近1本）
        if "15min" in dfs:
            df_15m_latest = dfs["15min"].iloc[[-1]].copy()
            df_15m_latest = df_15m_latest.add_prefix("15M_")
        else:
            df_15m_latest = pd.DataFrame()

        # 4時間足の最新行（直近1本）
        if "4hour" in dfs:
            df_4h_latest = dfs["4hour"].iloc[[-1]].copy()
            df_4h_latest = df_4h_latest.add_prefix("4H_")
        else:
            df_4h_latest = pd.DataFrame()

        # 結合
        df_ai_input = df_1h_latest
        if not df_15m_latest.empty:
            df_ai_input = pd.concat([df_ai_input.reset_index(drop=True), df_15m_latest.reset_index(drop=True)], axis=1)
        if not df_4h_latest.empty:
            df_ai_input = pd.concat([df_ai_input, df_4h_latest.reset_index(drop=True)], axis=1)

        out_name = f"{symbol}_ai_input.csv"
        df_ai_input.to_csv(out_name, index=False)
        print(f"  Saved AI input CSV: {out_name}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python prepare_ai_input.py symbols.csv")
        sys.exit(1)
    
    symbols_csv = sys.argv[1]
    prepare_ai_input(symbols_csv)
