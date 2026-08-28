"""本番cron投入前の読み取り専用セットアップ診断。

注文POST、Discord送信、OpenAI API呼び出しは行わない。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from bot_config import DISCORD_FOREX_MAIN, DISCORD_FOREX_OTHER
from economic_calendar import fetch_calendar
from gmo_client import GMOClient
from symbol_config import load_symbols


def _ok(label: str, detail: str = "") -> None:
    print(f"[OK]   {label}" + (f": {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"[WARN] {label}" + (f": {detail}" if detail else ""))


def _ng(label: str, detail: str = "") -> None:
    print(f"[NG]   {label}" + (f": {detail}" if detail else ""))


def main(symbols_file: str = "symbols.csv") -> int:
    fatal = False
    symbols = load_symbols(symbols_file)

    if os.getenv("OPENAI_API_KEY"):
        _ok("OPENAI_API_KEY", "設定あり（API呼び出しは未実施）")
    else:
        _ng("OPENAI_API_KEY", "未設定")
        fatal = True

    if DISCORD_FOREX_MAIN:
        _ok("DISCORD_FOREX_MAIN", "設定あり（送信テストは未実施）")
    else:
        _ng("DISCORD_FOREX_MAIN", "未設定")
        fatal = True
    if DISCORD_FOREX_OTHER:
        _ok("DISCORD_FOREX_OTHER", "設定あり（送信テストは未実施）")
    else:
        _ng("DISCORD_FOREX_OTHER", "未設定")
        fatal = True

    client = GMOClient()
    try:
        status = client.service_status()
        if status == "OPEN":
            _ok("GMO Public API", f"service status={status}")
        else:
            _warn("GMO Public API", f"service status={status}")
        rules = client.symbols()
        unsupported = [s for s in symbols if s not in rules]
        if unsupported:
            _ng("symbols.csv", f"GMO未対応: {unsupported}")
            fatal = True
        else:
            _ok("symbols.csv", f"{len(symbols)}銘柄すべてGMO対応")
    except Exception as exc:
        _ng("GMO Public API", str(exc))
        fatal = True

    if not client.private_available:
        _ng("GMO Private API", "GMO_FX_API_KEY / GMO_FX_API_SECRET 未設定")
        fatal = True
    else:
        try:
            assets = client.assets()
            orders = client.active_orders()
            positions = client.open_positions()
            summaries = client.position_summary()
            _ok(
                "GMO Private API",
                f"equity={assets.get('equity', '-')} activeOrders={len(orders)} "
                f"openPositions={len(positions)} summaries={len(summaries)}",
            )
        except Exception as exc:
            _ng("GMO Private API", str(exc))
            fatal = True

    try:
        events, status = fetch_calendar(datetime.now(timezone.utc))
        if status.get("usable"):
            _ok("無料経済指標カレンダー", f"source={status.get('source')} events={len(events)}")
        else:
            _ng("無料経済指標カレンダー", str(status))
            fatal = True
    except Exception as exc:
        _ng("無料経済指標カレンダー", str(exc))
        fatal = True

    if os.getenv("DISCORD_FOREX_EVENT"):
        _ok("DISCORD_FOREX_EVENT", "設定あり")
    else:
        _warn("DISCORD_FOREX_EVENT", "未設定のためイベント通知はMAINを使用")

    print("\n診断結果:", "NGあり" if fatal else "問題なし")
    return 1 if fatal else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols_file", default="symbols.csv")
    args = parser.parse_args()
    raise SystemExit(main(args.symbols_file))
