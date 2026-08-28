from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from analyze_technical import stage1_filter
from economic_calendar import _parse_event, event_guard_for_symbol, fetch_calendar
from ohlcv_calc import add_features, compute_atr, compute_rsi
from prepare_features import TIMEFRAMES, derive_regime, recent_ohlc, summarize
from risk_engine import calculate_size, quote_to_jpy_rate, total_risk_ok, margin_ok
from state_db import StateDB
from notify_discord_all import _send_dedup, _entry_plan_label, _management_requires_main
from virtual_tracker import update_virtual_trades
from symbol_config import load_symbols


class CoreTests(unittest.TestCase):
    def test_symbols_dedup_preserves_order(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "symbols.csv"
            p.write_text("symbol\n usd_jpy \nEUR_USD\nUSD_JPY\n\n", encoding="utf-8")
            self.assertEqual(load_symbols(str(p)), ["USD_JPY", "EUR_USD"])

    def test_rsi_uptrend_high(self):
        s = pd.Series(np.arange(1.0, 50.0))
        rsi = compute_rsi(s).iloc[-1]
        self.assertGreater(rsi, 99)

    def test_atr_positive(self):
        df = pd.DataFrame({"High": np.arange(2.0, 42.0), "Low": np.arange(1.0, 41.0), "Close": np.arange(1.5, 41.5)})
        self.assertGreater(float(compute_atr(df).iloc[-1]), 0)

    def test_regime_trend_up(self):
        n = 100
        close = np.linspace(100, 120, n)
        df = pd.DataFrame({
            "Open": close - .05, "High": close + .2, "Low": close - .2, "Close": close, "Volume": 0
        })
        feat = add_features(df)
        self.assertIn(derive_regime(feat), {"TREND_UP", "TRANSITION"})

    def test_recent_ohlc_order_old_to_new(self):
        df = pd.DataFrame({"Open":[1,2,3,4,5],"High":[2,3,4,5,6],"Low":[0,1,2,3,4],"Close":[1.5,2.5,3.5,4.5,5.5]})
        rows = recent_ohlc(df)
        self.assertEqual(rows[0][0], 2)
        self.assertEqual(rows[-1][0], 5)


    def test_long_history_features_are_meaningful_and_daily_enabled(self):
        n = 320
        # 単調すぎない上昇系列でSMA200/長期構造を計算できることを確認。
        x = np.arange(n, dtype=float)
        close = 100 + x * 0.03 + np.sin(x / 7.0) * 0.25
        df = pd.DataFrame({
            "Open": close - 0.03, "High": close + 0.12,
            "Low": close - 0.12, "Close": close, "Volume": 0,
        })
        feat = add_features(df)
        self.assertTrue(np.isfinite(float(feat["SMA_200"].iloc[-1])))
        f4 = summarize(feat, "4h")
        for key in ("s100", "s200", "sl100", "sl200", "h250", "l250", "atrq", "er50", "p50"):
            self.assertIn(key, f4)
        self.assertGreaterEqual(f4["atrq"], 0.0)
        self.assertLessEqual(f4["atrq"], 1.0)
        self.assertGreaterEqual(f4["er50"], 0.0)
        self.assertLessEqual(f4["er50"], 1.0)
        # 15mにも長期ボラ/トレンド効率は使うが、SMA200文脈は上位足だけ。
        f15 = summarize(feat, "15m")
        self.assertIn("atrq", f15)
        self.assertIn("er50", f15)
        self.assertIn("h100", f15)
        self.assertNotIn("s200", f15)
        self.assertEqual(TIMEFRAMES["1d"], "1day")

    def test_stage1_allows_15m_pullback(self):
        ai = {"tf": {
            "4h":{"f":{"reg":"TREND_UP","atr":1}},
            "1h":{"f":{"reg":"TREND_UP","atr":1}},
            "15m":{"f":{"reg":"TREND_DOWN","atr":0.20}},
        }}
        r = stage1_filter(ai, 100.00, 100.01)
        self.assertTrue(r["llm_call_allowed"])

    def test_stage1_blocks_4h_1h_opposite(self):
        ai = {"tf": {
            "4h":{"f":{"reg":"TREND_UP","atr":1}},
            "1h":{"f":{"reg":"TREND_DOWN","atr":1}},
            "15m":{"f":{"reg":"TRANSITION","atr":0.20}},
        }}
        self.assertFalse(stage1_filter(ai, 100.00, 100.01)["llm_call_allowed"])

    def test_calendar_parse_and_guard(self):
        now = datetime.now(timezone.utc)
        e = _parse_event({
            "title":"CPI", "country":"USD", "date":(now+timedelta(minutes=20)).isoformat(),
            "impact":"High", "forecast":"3.0%", "previous":"2.9%", "actual":""
        })
        self.assertIsNotNone(e)
        blockers = event_guard_for_symbol("USD_JPY", [e], now)
        self.assertEqual(len(blockers), 1)

    def test_quote_conversion(self):
        tick = {"USD_JPY":{"bid":150.0,"ask":150.2}}
        self.assertAlmostEqual(quote_to_jpy_rate("EUR_USD", tick), 150.1)
        self.assertEqual(quote_to_jpy_rate("USD_JPY", tick), 1.0)

    def test_size_plan(self):
        rule = {"minOpenOrderSize":1000,"maxOrderSize":1000000,"sizeStep":1000}
        plan = calculate_size("USD_JPY", 150.0, 149.5, 400000, rule, {"USD_JPY":{"bid":150,"ask":150.1}})
        self.assertTrue(plan.allowed)
        self.assertGreaterEqual(plan.size, 1000)
        self.assertLessEqual(plan.estimated_loss_jpy, 400000 * 0.0075 + 1e-6)

    def test_state_db_custom_parent_and_migration(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nested" / "test.sqlite3"
            db = StateDB(p)
            self.assertTrue(p.exists())
            with db.connect() as c:
                cols = {r[1] for r in c.execute("PRAGMA table_info(virtual_trades)")}
                decision_cols = {r[1] for r in c.execute("PRAGMA table_info(decisions)")}
            self.assertIn("activated_at", cols)
            self.assertIn("trend_invalidation", decision_cols)
            self.assertIn("entry_plan", decision_cols)

    def test_notification_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            db = StateDB(Path(td)/"x.sqlite3")
            self.assertTrue(db.should_notify("same", 60))
            self.assertFalse(db.should_notify("same", 60))

    def test_failed_discord_send_releases_dedup_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            db = StateDB(Path(td)/"x.sqlite3")
            with patch("notify_discord_all.send_discord", return_value=False):
                self.assertFalse(_send_dedup(db, "retry", 60, {"title":"x"}, "https://example.invalid"))
            self.assertTrue(db.should_notify("retry", 60))

    def test_account_snapshot_saved(self):
        with tempfile.TemporaryDirectory() as td:
            db = StateDB(Path(td)/"x.sqlite3")
            snapshot_id = db.save_account_snapshot({
                "equity": "412345", "balance": "410000",
                "availableAmount": "380000", "margin": "30000",
                "marginRatio": "1374.48", "positionLossGain": "2345",
                "totalSwap": "0"
            }, "2026-08-27T12:00:00+00:00")
            self.assertGreater(snapshot_id, 0)
            with db.connect() as c:
                row = c.execute("SELECT equity,margin_ratio FROM account_snapshots").fetchone()
            self.assertAlmostEqual(row["equity"], 412345.0)
            self.assertAlmostEqual(row["margin_ratio"], 1374.48)


    def test_global_calendar_event_blocks_all_symbols(self):
        now = datetime.now(timezone.utc)
        e = _parse_event({
            "title":"Global central-bank event", "country":"All",
            "date":(now+timedelta(minutes=20)).isoformat(), "impact":"High"
        })
        self.assertEqual(len(event_guard_for_symbol("EUR_USD", [e], now)), 1)

    def test_calendar_live_but_stale_is_unusable(self):
        now = datetime.now(timezone.utc)
        payload = [{
            "title":"Old CPI", "country":"USD",
            "date":(now-timedelta(days=3)).isoformat(), "impact":"High"
        }]
        class Resp:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return payload
        with tempfile.TemporaryDirectory() as td, patch("economic_calendar.requests.get", return_value=Resp()):
            _, status = fetch_calendar(now=now, cache_path=Path(td)/"cal.json")
        self.assertFalse(status["usable"])

    def test_stale_live_calendar_does_not_overwrite_good_cache(self):
        now = datetime.now(timezone.utc)
        good_cache = [{
            "title":"Future CPI", "country":"USD",
            "date":(now+timedelta(hours=2)).isoformat(), "impact":"High"
        }]
        stale_live = [{
            "title":"Old CPI", "country":"USD",
            "date":(now-timedelta(days=4)).isoformat(), "impact":"High"
        }]
        class Resp:
            def raise_for_status(self): return None
            def json(self): return stale_live
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)/"cal.json"
            cache.write_text(json.dumps({
                "fetched_at": now.isoformat(), "events": good_cache
            }), encoding="utf-8")
            with patch("economic_calendar.requests.get", return_value=Resp()):
                events, status = fetch_calendar(now=now, cache_path=cache)
            self.assertTrue(status["usable"])
            self.assertEqual(status["source"], "cache")
            self.assertEqual(events[0].title, "Future CPI")
            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(saved["events"][0]["title"], "Future CPI")

    def test_state_db_new_virtual_columns(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.sqlite3"
            db = StateDB(p)
            with db.connect() as c:
                cols = {r[1] for r in c.execute("PRAGMA table_info(virtual_trades)")}
            self.assertIn("spread", cols)
            self.assertIn("entry_mode", cols)

    def test_pending_virtual_trade_expires_even_with_new_bars(self):
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                db = StateDB(Path(td)/"v.sqlite3")
                opened = datetime.now(timezone.utc) - timedelta(hours=13)
                decision = {
                    "symbol":"USD_JPY", "created_at":opened.isoformat(), "decision_type":"ENTRY",
                    "action":"ENTER", "direction":"buy", "entry":150.0, "stop_loss":149.0,
                    "take_profit":151.5, "spread":0.01, "entry_mode":"PENDING_LIMIT"
                }
                did = db.save_decision(decision)
                db.create_virtual_trade(did, decision)
                jst_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)
                # entry=150へ届かない新しい完成足が存在していても期限切れすること。
                pd.DataFrame([{
                    "OpenTime": jst_now - timedelta(hours=1), "Open":151.0,
                    "High":151.2,"Low":150.8,"Close":151.1,"Volume":0
                }]).to_csv("USD_JPY_15min_forex.csv", index=False)
                update_virtual_trades(db)
                with db.connect() as c:
                    row = c.execute("SELECT status,result FROM virtual_trades").fetchone()
                self.assertEqual(row["status"], "CLOSED")
                self.assertEqual(row["result"], "EXPIRED")
            finally:
                os.chdir(old_cwd)

    def test_sell_virtual_trade_uses_spread_for_exit(self):
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                db = StateDB(Path(td)/"v.sqlite3")
                opened = datetime.now(timezone.utc) - timedelta(hours=2)
                decision = {
                    "symbol":"EUR_USD", "created_at":opened.isoformat(), "decision_type":"ENTRY",
                    "action":"ENTER", "direction":"sell", "entry":100.0, "stop_loss":101.0,
                    "take_profit":98.0, "spread":0.2, "entry_mode":"MARKET_LIKE"
                }
                did = db.save_decision(decision)
                db.create_virtual_trade(did, decision)
                # BID高値100.85だけならSL未到達だが、ASK近似101.05ならSL到達。
                open_time = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None) - timedelta(hours=1)
                pd.DataFrame([{
                    "OpenTime":open_time,"Open":100.0,"High":100.85,"Low":99.5,"Close":100.4,"Volume":0
                }]).to_csv("EUR_USD_15min_forex.csv", index=False)
                update_virtual_trades(db)
                with db.connect() as c:
                    row = c.execute("SELECT status,result,realized_r FROM virtual_trades").fetchone()
                self.assertEqual(row["status"], "CLOSED")
                self.assertEqual(row["result"], "LOSS")
                self.assertAlmostEqual(row["realized_r"], -1.0)
            finally:
                os.chdir(old_cwd)

    def test_pullback_notification_label(self):
        self.assertEqual(_entry_plan_label("PULLBACK_LIMIT", "buy"), "押し目買いLIMIT")
        self.assertEqual(_entry_plan_label("PULLBACK_LIMIT", "sell"), "戻り売りLIMIT")

    def test_entry_plan_is_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            db = StateDB(Path(td)/"plan.sqlite3")
            decision = {
                "symbol":"USD_JPY", "decision_type":"ENTRY", "action":"ENTER",
                "trend_score":0.8, "entry_quality":0.85, "entry_plan":"PULLBACK_LIMIT",
                "entry":149.8, "trend_invalidation":149.4, "stop_loss":149.4,
                "take_profit":150.6, "rr":2.0, "suggested_size":1000,
            }
            db.save_decision(decision)
            with db.connect() as c:
                row = c.execute("SELECT entry_plan FROM decisions").fetchone()
            self.assertEqual(row["entry_plan"], "PULLBACK_LIMIT")

    def test_total_risk_budget_is_cumulative(self):
        allocated = 1.0
        accepted = 0
        for new_risk in (0.75, 0.75, 0.75):
            ok, _ = total_risk_ok(allocated, new_risk)
            if ok:
                allocated += new_risk
                accepted += 1
        self.assertEqual(accepted, 2)
        self.assertAlmostEqual(allocated, 2.5)


    def test_margin_ratio_150_is_allowed(self):
        ok, reason = margin_ok({"marginRatio": "150.0"})
        self.assertTrue(ok, reason)
        ok, reason = margin_ok({"marginRatio": "149.9"})
        self.assertFalse(ok)
        self.assertIn("150%", reason)

    def test_actionable_position_management_goes_main(self):
        state = {"kind": "position"}
        self.assertFalse(_management_requires_main(state, {"action": "HOLD"}))
        for action in ("CLOSE", "TAKE_PARTIAL", "TIGHTEN_SL", "REVIEW_MANUALLY"):
            self.assertTrue(_management_requires_main(state, {"action": action}), action)

    def test_actionable_pending_order_management_goes_main(self):
        state = {"kind": "order"}
        self.assertFalse(_management_requires_main(state, {"action": "KEEP_ORDER"}))
        for action in ("CANCEL_ORDER", "REPRICE_ORDER", "REVIEW_MANUALLY"):
            self.assertTrue(_management_requires_main(state, {"action": action}), action)


if __name__ == "__main__":
    unittest.main()
