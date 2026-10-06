from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

import pandas as pd

from forecast_audit import record_forecasts, evaluate_forecasts
from state_db import StateDB
from analyze_technical import stage1_filter


class ForecastAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = StateDB(Path(self.tmp.name) / "audit.db")
        self.quote = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
        self.item = {"symbol": "USD_JPY", "bid": 150.0, "ask": 150.01,
                     "quote_time": self.quote.isoformat(), "ai_input": {"tf": {
                         "4h": {"f": {"reg": "TREND_UP"}},
                         "1h": {"f": {"reg": "TREND_UP"}, "close_time": self.quote.isoformat()},
                         "15m": {"f": {"atr": 0.2}, "close_time": self.quote.isoformat()}}}}

    def record(self, result):
        record_forecasts(self.db, [self.item], result, "test-model", "test-prompt")

    def rows(self):
        with self.db.connect() as conn:
            return list(conn.execute("SELECT * FROM forecast_audit ORDER BY id"))

    def candle(self, close_time, close):
        opened = (close_time - timedelta(minutes=15)).astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)
        pd.DataFrame([{"OpenTime": opened, "Close": close}]).to_csv(
            Path(self.tmp.name) / "USD_JPY_15min_forex.csv", index=False)

    def test_no_trade_and_error_are_saved(self):
        self.record({"USD_JPY": {"entry_plan": "NO_TRADE", "trend_score": -0.4}})
        self.record({})
        rows = self.rows()
        self.assertEqual([r["plan"] for r in rows], ["NO_TRADE", "ERROR"])
        self.assertEqual(json.loads(rows[0]["input_json"]), self.item)
        self.assertEqual(len(rows[0]["prompt_hash"]), 64)

    def test_no_trade_direction_and_same_sample_baseline(self):
        self.record({"USD_JPY": {"entry_plan": "NO_TRADE", "trend_score": -0.4}})
        target = self.quote + timedelta(hours=8)
        self.candle(target, 149)
        evaluate_forecasts(self.db, self.tmp.name, now=target)
        row = self.rows()[0]
        self.assertEqual((row["status"], row["hit"], row["baseline_hit"]), ("EVALUATED", 1, 0))

    def test_future_candle_is_not_used_early(self):
        self.record({"USD_JPY": {"entry_plan": "ENTER_NOW", "trend_score": 0.7}})
        target = self.quote + timedelta(hours=8)
        self.candle(target + timedelta(minutes=5), 151)
        evaluate_forecasts(self.db, self.tmp.name, now=target)
        self.assertEqual(self.rows()[0]["status"], "PENDING")
        evaluate_forecasts(self.db, self.tmp.name, now=target + timedelta(minutes=5))
        self.assertEqual(self.rows()[0]["hit"], 1)

    def test_missing_target_not_replaced_by_next_day(self):
        self.record({"USD_JPY": {"entry_plan": "ENTER_NOW", "trend_score": 0.7}})
        target = self.quote + timedelta(hours=8)
        self.candle(target + timedelta(days=1), 151)
        evaluate_forecasts(self.db, self.tmp.name, now=target + timedelta(hours=25))
        self.assertEqual(self.rows()[0]["status"], "MISSING")

    def test_neutral_not_counted_as_directional_hit(self):
        self.record({"USD_JPY": {"entry_plan": "NO_TRADE", "trend_score": 0}})
        target = self.quote + timedelta(hours=8)
        self.candle(target, 151)
        evaluate_forecasts(self.db, self.tmp.name, now=target)
        self.assertIsNone(self.rows()[0]["hit"])

    def test_corrupt_close_expires_as_missing(self):
        self.record({"USD_JPY": {"entry_plan": "NO_TRADE", "trend_score": 0.4}})
        target = self.quote + timedelta(hours=8)
        self.candle(target, "broken")
        evaluate_forecasts(self.db, self.tmp.name, now=target + timedelta(hours=25))
        self.assertEqual(self.rows()[0]["status"], "MISSING")

    def test_stale_and_future_features_rejected(self):
        ai = self.item["ai_input"]
        self.assertTrue(stage1_filter(ai, 150, 150.01, self.quote.isoformat())["llm_call_allowed"])
        for offset in (-60, 1):
            ai["tf"]["15m"]["close_time"] = (self.quote + timedelta(minutes=offset)).isoformat()
            self.assertFalse(stage1_filter(ai, 150, 150.01, self.quote.isoformat())["llm_call_allowed"])

    def test_run_passes_fresh_quote_and_event_then_records_abstention(self):
        import notify_discord_all as bot
        from economic_calendar import EconomicEvent
        now = datetime.now(timezone.utc)
        ai = self.item["ai_input"]
        for tf in ("1h", "15m"):
            ai["tf"][tf]["close_time"] = (now - timedelta(minutes=1)).isoformat()
        client = MagicMock()
        client.ticker.return_value = {"USD_JPY": {
            "bid": 151, "ask": 151.01, "timestamp": now.isoformat(), "status": "OPEN"}}
        client.symbols.return_value = {}
        event = EconomicEvent("CPI", "USD", "High", now + timedelta(hours=2))
        output = {"USD_JPY": {"entry_plan": "NO_TRADE", "trend_score": -0.4, "reason": "test"}}
        with ExitStack() as stack:
            for name, value in {
                "StateDB": self.db, "GMOClient": client,
                "load_symbols": ["USD_JPY"], "update_virtual_trades": [],
                "_load_market_input": (ai, {"bid": 150, "ask": 150.01}),
                "fetch_calendar": ([event], {"usable": True}),
                "fetch_market_news": {"retrieved_at": now.isoformat(), "sources": [], "headlines": []},
                "relevant_high_impact_events": [], "newly_released_events": [],
                "_account_snapshot": (None, [], [], [], None),
                "_sync_executions": None, "margin_ok": (True, None),
                "_estimate_existing_risk": (0, {}, []), "send_discord": True,
            }.items():
                stack.enter_context(patch.object(bot, name, return_value=value))
            analysis = stack.enter_context(patch.object(bot, "analyze_entry_batch", return_value=output))
            bot.run(model="test-model")
            # GPT待機中に1円動いた場合、古いENTER_NOW案をサイズ計算へ渡さない。
            fresh = client.ticker.return_value
            client.ticker.side_effect = [fresh, {"USD_JPY": {
                **fresh["USD_JPY"], "bid": 152, "ask": 152.01}}]
            analysis.return_value = {"USD_JPY": {
                "entry_plan": "ENTER_NOW", "entry": 151.01,
                "trend_invalidation": 150.5, "take_profit": 152,
                "trend_score": 0.8, "entry_quality": 0.8, "reason": "test"}}
            with patch.object(bot, "calculate_size") as sizing:
                bot.run(model="test-model")
                sizing.assert_not_called()
            self.assertEqual(client.ticker.call_count, 3)
        sent = analysis.call_args.args[0][0]
        self.assertEqual(sent["bid"], 151)
        self.assertEqual(sent["events"][0]["title"], "CPI")
        self.assertIn("market_context", sent)
        self.assertEqual(json.loads(self.rows()[0]["input_json"])["market_context"]["retrieved_at"], now.isoformat())
        self.assertEqual(self.rows()[0]["plan"], "NO_TRADE")
