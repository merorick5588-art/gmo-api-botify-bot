"""既存cronから呼ぶ単一エントリーポイント。スケジュール機能は持たない。"""
from __future__ import annotations

import argparse
import sys

import fetch_gmo_ohlcv
import ohlcv_calc
import prepare_features
import notify_discord_all
from llm_config import DEFAULT_MODEL


def main(symbols_file: str = "symbols.csv", model: str = DEFAULT_MODEL) -> int:
    try:
        fetch_gmo_ohlcv.main(symbols_file)
        ohlcv_calc.main(symbols_file)
        prepare_features.prepare_ai_input(symbols_file)
        notify_discord_all.run(symbols_file, model)
    except SystemExit as exc:
        # 市場CLOSE等は異常再試行させるより正常終了扱いにする。
        code = exc.code if isinstance(exc.code, int) else 0
        print(f"run stopped: {exc}")
        return code
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols_file", default="symbols.csv")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    raise SystemExit(main(args.symbols_file, args.model))
