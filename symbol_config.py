from pathlib import Path

import pandas as pd


def load_symbols(path: str | Path = "symbols.csv") -> list[str]:
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        raise ValueError("symbols.csv に symbol 列がありません")

    result: list[str] = []
    seen: set[str] = set()
    for raw in df["symbol"].dropna().astype(str):
        symbol = raw.strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def split_symbol(symbol: str) -> tuple[str, str]:
    parts = symbol.upper().split("_")
    if len(parts) != 2:
        raise ValueError(f"不正なsymbol形式: {symbol}")
    return parts[0], parts[1]
