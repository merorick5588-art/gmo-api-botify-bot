from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from bot_config import VIRTUAL_SIGNAL_PENDING_HOURS
from state_db import StateDB

PENDING_EXPIRY_HOURS = VIRTUAL_SIGNAL_PENDING_HOURS


def _as_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def update_virtual_trades(db: StateDB) -> list[dict]:
    """完成済み15m BID足で仮想シグナルを追跡する。

    ASK側はシグナル発生時spreadを固定近似して補正する。PENDING注文が約定した
    同一15m足でTP/SLへ到達した場合は時系列を復元できないためAMBIGUOUSとし、
    勝率/期待値へ混ぜない。
    """
    closed: list[dict] = []
    now = datetime.now(timezone.utc)

    for trade in db.active_virtual_trades():
        symbol = trade["symbol"]
        path = f"{symbol}_15min_forex.csv"
        try:
            df = pd.read_csv(path, parse_dates=["OpenTime"])
        except (FileNotFoundError, ValueError):
            continue
        if df.empty:
            continue

        opened_at = _as_utc(trade["opened_at"])
        last_dt = _as_utc(trade["last_checked_at"] or trade["opened_at"])

        # CSVはJST naive。UTC awareへ変換して比較する。
        times = pd.to_datetime(df["OpenTime"], errors="coerce")
        times = (
            times.dt.tz_localize("Asia/Tokyo", nonexistent="shift_forward", ambiguous="NaT")
            .dt.tz_convert("UTC")
        )
        df = df.assign(_utc=times).dropna(subset=["_utc"])
        rows = df[df["_utc"] > last_dt]

        entry = float(trade["entry"])
        sl = float(trade["stop_loss"])
        tp = float(trade["take_profit"])
        risk = float(trade["risk_distance"])
        spread = max(0.0, float(trade["spread"] or 0.0))
        side = str(trade["side"]).lower()
        status = str(trade["status"])
        mfe = float(trade["mfe_r"])
        mae = float(trade["mae_r"])
        activated_at = trade["activated_at"]
        last_seen = last_dt

        for _, row in rows.iterrows():
            bid_high = float(row["High"])
            bid_low = float(row["Low"])
            ask_high = bid_high + spread
            ask_low = bid_low + spread
            bar_time = row["_utc"].to_pydatetime()
            last_seen = bar_time

            if status == "PENDING":
                if side == "buy":
                    touched = ask_low <= entry <= ask_high
                    tp_hit_same_bar = bid_high >= tp
                    sl_hit_same_bar = bid_low <= sl
                else:
                    touched = bid_low <= entry <= bid_high
                    tp_hit_same_bar = ask_low <= tp
                    sl_hit_same_bar = ask_high >= sl
                if not touched:
                    continue

                status = "OPEN"
                activated_at = bar_time.isoformat()
                # 約定とExitの前後関係が15m OHLCだけでは分からない。
                if tp_hit_same_bar or sl_hit_same_bar:
                    db.update_virtual_trade(
                        trade["id"],
                        mfe_r=mfe,
                        mae_r=mae,
                        last_checked_at=bar_time.isoformat(),
                        status="CLOSED",
                        activated_at=activated_at,
                        closed_at=bar_time.isoformat(),
                        result="AMBIGUOUS",
                        exit_price=None,
                        realized_r=None,
                    )
                    closed.append({"symbol": symbol, "result": "AMBIGUOUS", "r": None})
                    status = "CLOSED"
                    break
                # 約定バー内のMFE/MAEも前後関係が不明なので次の完成足から計測する。
                continue

            if status != "OPEN":
                continue

            if side == "buy":
                mfe = max(mfe, (bid_high - entry) / risk)
                mae = min(mae, (bid_low - entry) / risk)
                tp_hit = bid_high >= tp
                sl_hit = bid_low <= sl
            else:
                # SELLの決済はBUY(ASK)側になるためspread近似を反映。
                mfe = max(mfe, (entry - ask_low) / risk)
                mae = min(mae, (entry - ask_high) / risk)
                tp_hit = ask_low <= tp
                sl_hit = ask_high >= sl

            if tp_hit and sl_hit:
                result, realized, exit_price = "AMBIGUOUS", None, None
            elif tp_hit:
                result, realized, exit_price = "WIN", abs(tp - entry) / risk, tp
            elif sl_hit:
                result, realized, exit_price = "LOSS", -1.0, sl
            else:
                continue

            db.update_virtual_trade(
                trade["id"],
                mfe_r=mfe,
                mae_r=mae,
                last_checked_at=bar_time.isoformat(),
                status="CLOSED",
                activated_at=activated_at,
                closed_at=bar_time.isoformat(),
                result=result,
                exit_price=exit_price,
                realized_r=realized,
            )
            closed.append({"symbol": symbol, "result": result, "r": realized})
            status = "CLOSED"
            break

        if status == "CLOSED":
            continue

        # 新しい足が毎回存在していても、未約定シグナルは期限で必ず失効させる。
        if status == "PENDING" and now - opened_at > timedelta(hours=PENDING_EXPIRY_HOURS):
            db.update_virtual_trade(
                trade["id"],
                mfe_r=mfe,
                mae_r=mae,
                last_checked_at=now.isoformat(),
                status="CLOSED",
                closed_at=now.isoformat(),
                result="EXPIRED",
                realized_r=0.0,
            )
            closed.append({"symbol": symbol, "result": "EXPIRED", "r": 0.0})
            continue

        db.update_virtual_trade(
            trade["id"],
            mfe_r=mfe,
            mae_r=mae,
            last_checked_at=last_seen.isoformat(),
            status=status,
            activated_at=activated_at,
        )

    return closed
