"""注文損益とは独立した、保存時点から8時間後の方向評価。"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


def init_audit(db):
    with db.connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS forecast_audit (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, symbol TEXT NOT NULL,
            model TEXT NOT NULL, prompt_hash TEXT NOT NULL, quote_time TEXT NOT NULL,
            target_time TEXT NOT NULL, bid REAL NOT NULL, score REAL,
            baseline_score REAL NOT NULL, plan TEXT NOT NULL,
            input_json TEXT NOT NULL, result_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING', outcome_close REAL,
            outcome_time TEXT, hit INTEGER, baseline_hit INTEGER
        )""")


def utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return stamp.tz_convert("UTC").to_pydatetime()


def record_forecasts(db, items, results, model, instructions):
    init_audit(db)
    prompt_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    created = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        for item in items:
            symbol = item["symbol"]
            quote = utc(item["quote_time"])
            result = results.get(symbol) or {}
            reg = item["ai_input"].get("tf", {}).get("4h", {}).get("f", {}).get("reg")
            baseline = 1 if reg == "TREND_UP" else -1 if reg == "TREND_DOWN" else 0
            conn.execute("""INSERT INTO forecast_audit
                (created_at,symbol,model,prompt_hash,quote_time,target_time,bid,score,
                 baseline_score,plan,input_json,result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (created, symbol, model, prompt_hash, quote.isoformat(),
                 (quote + timedelta(hours=8)).isoformat(), float(item["bid"]),
                 result.get("trend_score"), baseline, result.get("entry_plan", "ERROR"),
                 json.dumps(item, ensure_ascii=False, allow_nan=False),
                 json.dumps(result, ensure_ascii=False, allow_nan=False)))


def evaluate_forecasts(db, data_dir=".", now=None):
    init_audit(db)
    now = now or datetime.now(timezone.utc)
    with db.connect() as conn:
        pending = list(conn.execute("SELECT * FROM forecast_audit WHERE status='PENDING'"))
        frames = {}
        for row in pending:
            target = utc(row["target_time"])
            if now < target:
                continue
            symbol = row["symbol"]
            if symbol not in frames:
                try:
                    df = pd.read_csv(Path(data_dir) / f"{symbol}_15min_forex.csv")
                    times = pd.to_datetime(df["OpenTime"], errors="coerce")
                    if times.dt.tz is None:
                        times = times.dt.tz_localize("Asia/Tokyo")
                    df = df.assign(end=times.dt.tz_convert("UTC") + pd.Timedelta(minutes=15))
                    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
                    df = df[df["Close"].map(lambda v: math.isfinite(v) and v > 0)].dropna(subset=["end"])
                    frames[symbol] = df.sort_values("end").drop_duplicates("end")
                except (OSError, ValueError, KeyError):
                    frames[symbol] = pd.DataFrame()
            df = frames[symbol]
            # 8h後以降の最初の完成足終値。最大15分の評価粒度を明示する。
            candidates = df[(df["end"] >= target) & (df["end"] <= target + timedelta(minutes=15)) &
                            (df["end"] <= now)] if not df.empty else df
            if candidates.empty:
                # 遅延取得を24h待つ。休場や取得欠落を何日後かの価格で代用しない。
                if now > target + timedelta(hours=24):
                    conn.execute("UPDATE forecast_audit SET status='MISSING' WHERE id=?", (row["id"],))
                continue
            candle = candidates.iloc[0]
            close = float(candle["Close"])
            if not math.isfinite(close) or close <= 0:
                continue
            change = close - row["bid"]
            # 同値は上昇/下降のどちらにも的中扱いにしない。
            hit = int(change * row["score"] > 0) if row["score"] else None
            baseline_hit = int(change * row["baseline_score"] > 0) if row["baseline_score"] else None
            conn.execute("""UPDATE forecast_audit SET status='EVALUATED',outcome_close=?,
                outcome_time=?,hit=?,baseline_hit=? WHERE id=?""",
                (close, candle["end"].isoformat(), hit, baseline_hit, row["id"]))


def report_forecasts(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='forecast_audit'").fetchone():
        return
    print("\n=== 8時間方向予測（注文損益とは別） ===")
    rows = conn.execute("""SELECT model,prompt_hash,COUNT(*) n,
        SUM(plan='NO_TRADE') skipped,SUM(plan='ERROR') errors,
        SUM(status='MISSING') missing,SUM(status='PENDING') pending,
        COUNT(hit) scored,AVG(hit) accuracy,
        AVG(CASE WHEN hit IS NOT NULL THEN baseline_hit END) baseline
        FROM forecast_audit GROUP BY model,prompt_hash""")
    for row in rows:
        accuracy = f"{row['accuracy']:.1%}" if row['accuracy'] is not None else "-"
        baseline = f"{row['baseline']:.1%}" if row['baseline'] is not None else "-"
        print(f"{row['model']} / {row['prompt_hash'][:12]} N={row['n']} "
              f"見送り={row['skipped']} エラー={row['errors']} 欠測={row['missing']} 未評価={row['pending']} "
              f"方向評価N={row['scored']} 的中率={accuracy} 同一対象4h順張り={baseline}")
    print("※ 事前選別を通った候補のみ。見送りでも非ゼロの方向スコアは評価。中立は方向評価から除外。")
    print("※ 8h後から最大15分後のBID終値を使用。重複期間を含むため標本は独立ではありません。")
