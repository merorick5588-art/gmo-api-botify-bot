from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bot_config import STATE_DB
from state_db import StateDB


def _fmt(v, digits=2):
    return "-" if v is None else f"{float(v):.{digits}f}"


def _max_drawdown_r(realized: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in realized:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def report(db_path: str) -> None:
    # 初回でも空DBを作って安全にレポートできるようにする。
    StateDB(Path(db_path))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _report_with_conn(conn)
    finally:
        conn.close()


def _report_with_conn(conn: sqlite3.Connection) -> None:
    trades = list(
        conn.execute(
            """
            SELECT vt.symbol,vt.result,vt.realized_r,vt.mfe_r,vt.mae_r,vt.closed_at,vt.opened_at,
                   COALESCE(d.entry_plan, CASE vt.entry_mode WHEN 'MARKET_LIKE' THEN 'ENTER_NOW' WHEN 'PENDING_LIMIT' THEN 'PULLBACK_LIMIT' WHEN 'PENDING_STOP' THEN 'BREAKOUT_STOP' ELSE vt.entry_mode END) entry_plan
            FROM virtual_trades vt
            LEFT JOIN decisions d ON d.id=vt.decision_id
            WHERE vt.status='CLOSED'
            ORDER BY COALESCE(vt.closed_at,vt.opened_at), vt.id
            """
        )
    )
    scored = [r for r in trades if r["result"] in {"WIN", "LOSS"} and r["realized_r"] is not None]
    realized = [float(r["realized_r"]) for r in scored]
    wins = sum(1 for r in scored if r["result"] == "WIN")
    gross_win = sum(max(0.0, x) for x in realized)
    gross_loss = abs(sum(min(0.0, x) for x in realized))
    pf = gross_win / gross_loss if gross_loss > 0 else None
    expectancy = sum(realized) / len(realized) if realized else None
    max_dd = _max_drawdown_r(realized)
    avg_mfe = sum(float(r["mfe_r"] or 0) for r in scored) / len(scored) if scored else None
    avg_mae = sum(float(r["mae_r"] or 0) for r in scored) / len(scored) if scored else None
    ambiguous = sum(1 for r in trades if r["result"] == "AMBIGUOUS")
    expired = sum(1 for r in trades if r["result"] == "EXPIRED")

    print("=== Bot仮想シグナル成績 ===")
    print(f"評価可能: {len(scored)} trades / Ambiguous: {ambiguous} / Expired: {expired}")
    print(f"勝率: {(wins/len(scored)*100 if scored else 0):.1f}%")
    print(f"総損益: {_fmt(sum(realized))} R")
    print(f"Expectancy: {_fmt(expectancy)} R/trade")
    print(f"Profit Factor: {_fmt(pf)}")
    print(f"最大DD: {_fmt(max_dd)} R")
    print(f"平均MFE: {_fmt(avg_mfe)} R / 平均MAE: {_fmt(avg_mae)} R")

    by_symbol: dict[str, list[float]] = defaultdict(list)
    for row in scored:
        by_symbol[str(row["symbol"])].append(float(row["realized_r"]))
    if by_symbol:
        print("\n=== 銘柄別 ===")
        for symbol, values in sorted(by_symbol.items(), key=lambda kv: sum(kv[1]), reverse=True):
            sw = sum(1 for x in values if x > 0)
            sgw = sum(max(0.0, x) for x in values)
            sgl = abs(sum(min(0.0, x) for x in values))
            spf = sgw / sgl if sgl > 0 else None
            print(
                f"{symbol:8s} N={len(values):3d} Win={sw/len(values)*100:5.1f}% "
                f"Total={sum(values):+6.2f}R Exp={sum(values)/len(values):+5.2f}R PF={_fmt(spf)}"
            )

    by_plan: dict[str, list[float]] = defaultdict(list)
    for row in scored:
        by_plan[str(row["entry_plan"] or "UNKNOWN")].append(float(row["realized_r"]))
    if by_plan:
        print("\n=== Entry方式別 ===")
        for plan, values in sorted(by_plan.items(), key=lambda kv: sum(kv[1]), reverse=True):
            pw = sum(1 for x in values if x > 0)
            pgw = sum(max(0.0, x) for x in values)
            pgl = abs(sum(min(0.0, x) for x in values))
            ppf = pgw / pgl if pgl > 0 else None
            print(
                f"{plan:16s} N={len(values):3d} Win={pw/len(values)*100:5.1f}% "
                f"Total={sum(values):+6.2f}R Exp={sum(values)/len(values):+5.2f}R PF={_fmt(ppf)}"
            )

    year = datetime.now(timezone.utc).year
    pnl = conn.execute(
        """
        SELECT SUM(COALESCE(loss_gain,0)+COALESCE(settled_swap,0)-COALESCE(fee,0)) pnl
        FROM executions
        WHERE settle_type='CLOSE' AND timestamp LIKE ?
        """,
        (f"{year}%",),
    ).fetchone()["pnl"]
    print(f"\n{year}年 GMO同期済み決済損益: ¥{float(pnl or 0):,.0f}")
    print("※ latestExecutionsからBot導入後に同期できた範囲のみ。GMO年間損益の完全な代替ではありません。")

    snapshots = list(
        conn.execute(
            """
            SELECT created_at,equity,balance,margin_ratio
            FROM account_snapshots
            WHERE created_at LIKE ? AND equity IS NOT NULL
            ORDER BY created_at,id
            """,
            (f"{year}%",),
        )
    )
    if snapshots:
        equities = [float(r["equity"]) for r in snapshots]
        peak = equities[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        for value in equities:
            peak = max(peak, value)
            dd = peak - value
            max_dd = max(max_dd, dd)
            if peak > 0:
                max_dd_pct = max(max_dd_pct, dd / peak * 100)
        start, last = equities[0], equities[-1]
        change = last - start
        print("\n=== GMO口座Equity推移 ===")
        print(f"観測回数: {len(equities)}")
        print(f"年内初回: ¥{start:,.0f} / 最新: ¥{last:,.0f} / 差分: ¥{change:+,.0f}")
        print(f"観測期間内最大DD: ¥{max_dd:,.0f} ({max_dd_pct:.2f}%)")
        print("※ 入出金を補正しない口座Equityベースの参考値です。")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(STATE_DB))
    a = p.parse_args()
    report(a.db)
