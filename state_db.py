from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from bot_config import STATE_DB, ensure_state_dir


class StateDB:
    def __init__(self, path: str | Path = STATE_DB):
        ensure_state_dir()
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    trend_score REAL,
                    entry_quality REAL,
                    entry_plan TEXT,
                    entry REAL,
                    trend_invalidation REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    rr REAL,
                    suggested_size REAL,
                    regime TEXT,
                    reason TEXT,
                    payload_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_symbol_time
                    ON decisions(symbol, created_at);

                CREATE TABLE IF NOT EXISTS virtual_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    risk_distance REAL NOT NULL,
                    spread REAL NOT NULL DEFAULT 0,
                    entry_mode TEXT NOT NULL DEFAULT 'PENDING_LIMIT',
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    activated_at TEXT,
                    closed_at TEXT,
                    result TEXT,
                    exit_price REAL,
                    realized_r REAL,
                    mfe_r REAL NOT NULL DEFAULT 0,
                    mae_r REAL NOT NULL DEFAULT 0,
                    last_checked_at TEXT,
                    FOREIGN KEY(decision_id) REFERENCES decisions(id)
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    notification_key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    settle_type TEXT,
                    size REAL,
                    price REAL,
                    loss_gain REAL,
                    fee REAL,
                    settled_swap REAL,
                    raw_json TEXT
                );

                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    equity REAL,
                    balance REAL,
                    available_amount REAL,
                    margin REAL,
                    margin_ratio REAL,
                    position_loss_gain REAL,
                    total_swap REAL,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_account_snapshots_time
                    ON account_snapshots(created_at);

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            # 旧DBからの安全な移行。既存データを消さず不足列のみ追加する。
            cols = {row[1] for row in conn.execute("PRAGMA table_info(virtual_trades)")}
            if "activated_at" not in cols:
                conn.execute("ALTER TABLE virtual_trades ADD COLUMN activated_at TEXT")
            if "spread" not in cols:
                conn.execute("ALTER TABLE virtual_trades ADD COLUMN spread REAL NOT NULL DEFAULT 0")
            if "entry_mode" not in cols:
                conn.execute("ALTER TABLE virtual_trades ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'PENDING_LIMIT'")

            decision_cols = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
            if "trend_invalidation" not in decision_cols:
                conn.execute("ALTER TABLE decisions ADD COLUMN trend_invalidation REAL")
            if "entry_plan" not in decision_cols:
                conn.execute("ALTER TABLE decisions ADD COLUMN entry_plan TEXT")

    def save_decision(self, decision: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO decisions(
                    created_at,symbol,decision_type,action,trend_score,entry_quality,entry_plan,
                    entry,trend_invalidation,stop_loss,take_profit,rr,suggested_size,regime,reason,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    decision["symbol"],
                    decision.get("decision_type", "ENTRY"),
                    decision.get("action", "WAIT"),
                    decision.get("trend_score"),
                    decision.get("entry_quality"),
                    decision.get("entry_plan"),
                    decision.get("entry"),
                    decision.get("trend_invalidation", decision.get("stop_loss")),
                    decision.get("stop_loss", decision.get("trend_invalidation")),
                    decision.get("take_profit"),
                    decision.get("rr"),
                    decision.get("suggested_size"),
                    decision.get("regime"),
                    decision.get("reason"),
                    json.dumps(decision, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid)

    def create_virtual_trade(self, decision_id: int, decision: dict[str, Any]) -> None:
        entry = float(decision["entry"])
        sl = float(decision["stop_loss"])
        tp = float(decision["take_profit"])
        risk = abs(entry - sl)
        if risk <= 0:
            return
        opened_at = decision.get("created_at") or datetime.now(timezone.utc).isoformat()
        entry_mode = str(decision.get("entry_mode") or "PENDING_LIMIT")
        immediate = entry_mode == "MARKET_LIKE"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO virtual_trades(
                    decision_id,symbol,side,opened_at,entry,stop_loss,take_profit,
                    risk_distance,spread,entry_mode,status,activated_at,last_checked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision_id,
                    decision["symbol"],
                    decision["direction"],
                    opened_at,
                    entry,
                    sl,
                    tp,
                    risk,
                    float(decision.get("spread") or 0),
                    entry_mode,
                    "OPEN" if immediate else "PENDING",
                    opened_at if immediate else None,
                    opened_at,
                ),
            )

    def active_virtual_trades(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM virtual_trades WHERE status IN ('PENDING','OPEN')"))

    def has_active_virtual_trade(self, symbol: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM virtual_trades WHERE symbol=? AND status IN ('PENDING','OPEN') LIMIT 1", (symbol,)).fetchone()
            return row is not None

    def update_virtual_trade(
        self,
        trade_id: int,
        *,
        mfe_r: float,
        mae_r: float,
        last_checked_at: str,
        status: str = "OPEN",
        activated_at: str | None = None,
        closed_at: str | None = None,
        result: str | None = None,
        exit_price: float | None = None,
        realized_r: float | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE virtual_trades
                SET mfe_r=?, mae_r=?, last_checked_at=?, status=?, activated_at=COALESCE(?,activated_at), closed_at=?, result=?,
                    exit_price=?, realized_r=?
                WHERE id=?
                """,
                (
                    mfe_r,
                    mae_r,
                    last_checked_at,
                    status,
                    activated_at,
                    closed_at,
                    result,
                    exit_price,
                    realized_r,
                    trade_id,
                ),
            )


    def latest_decision_action(self, symbol: str, decision_type: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT action FROM decisions
                WHERE symbol=? AND decision_type=?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (symbol, decision_type),
            ).fetchone()
            return str(row["action"]) if row and row["action"] is not None else None

    def should_notify(self, key: str, dedup_minutes: int) -> bool:
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT sent_at FROM notifications WHERE notification_key=?", (key,)
            ).fetchone()
            if row:
                try:
                    sent = datetime.fromisoformat(row["sent_at"])
                    if now - sent < timedelta(minutes=dedup_minutes):
                        return False
                except ValueError:
                    pass
            conn.execute(
                """
                INSERT INTO notifications(notification_key,sent_at) VALUES(?,?)
                ON CONFLICT(notification_key) DO UPDATE SET sent_at=excluded.sent_at
                """,
                (key, now.isoformat()),
            )
        return True

    def forget_notification(self, key: str) -> None:
        """Discord送信失敗時などに重複抑制の予約を解除する。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM notifications WHERE notification_key=?", (key,))

    def save_executions(self, rows: list[dict[str, Any]]) -> int:
        inserted = 0
        with self.connect() as conn:
            for row in rows:
                execution_id = row.get("executionId")
                if execution_id is None:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO executions(
                        execution_id,timestamp,symbol,side,settle_type,size,price,loss_gain,
                        fee,settled_swap,raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(execution_id),
                        row.get("timestamp"),
                        row.get("symbol"),
                        row.get("side"),
                        row.get("settleType"),
                        _f(row.get("size")),
                        _f(row.get("price")),
                        _f(row.get("lossGain")),
                        _f(row.get("fee")),
                        _f(row.get("settledSwap")),
                        json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                inserted += int(cur.rowcount > 0)
        return inserted

    def save_account_snapshot(self, assets: dict[str, Any], created_at: str | None = None) -> int:
        if not assets:
            return 0
        created_at = created_at or datetime.now(timezone.utc).isoformat()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO account_snapshots(
                    created_at,equity,balance,available_amount,margin,margin_ratio,
                    position_loss_gain,total_swap,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    created_at,
                    _f(assets.get("equity")),
                    _f(assets.get("balance")),
                    _f(assets.get("availableAmount")),
                    _f(assets.get("margin")),
                    _f(assets.get("marginRatio")),
                    _f(assets.get("positionLossGain")),
                    _f(assets.get("totalSwap")),
                    json.dumps(assets, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid)

    def get_meta(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO meta(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
