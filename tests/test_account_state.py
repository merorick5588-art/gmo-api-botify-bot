from __future__ import annotations

import sys
import types
import unittest
import tempfile
from pathlib import Path

try:
    from openai import OpenAI as _OpenAI  # noqa: F401
except Exception:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

from notify_discord_all import _classify_symbols, _sync_executions, _validate_management_result
from state_db import StateDB


class AccountStateTests(unittest.TestCase):
    def test_ifd_oco_children_are_managed_as_one_pending_order(self):
        orders = [
            {"symbol":"USD_JPY","orderId":1,"rootOrderId":100,"settleType":"OPEN","side":"BUY","executionType":"LIMIT"},
            {"symbol":"USD_JPY","orderId":2,"rootOrderId":100,"settleType":"CLOSE","side":"SELL","executionType":"LIMIT"},
            {"symbol":"USD_JPY","orderId":3,"rootOrderId":100,"settleType":"CLOSE","side":"SELL","executionType":"STOP"},
        ]
        flat, management, manual = _classify_symbols(["USD_JPY"], [], orders, [])
        self.assertEqual(flat, [])
        self.assertEqual(manual, [])
        self.assertEqual(len(management), 1)
        self.assertEqual(management[0]["kind"], "order")
        self.assertEqual(len(management[0]["orders"]), 3)

    def test_hedged_summary_requires_manual_review(self):
        summaries = [
            {"symbol":"USD_JPY","side":"BUY","sumPositionSize":"10000"},
            {"symbol":"USD_JPY","side":"SELL","sumPositionSize":"10000"},
        ]
        positions = [
            {"symbol":"USD_JPY","side":"BUY","positionId":1},
            {"symbol":"USD_JPY","side":"SELL","positionId":2},
        ]
        _, management, manual = _classify_symbols(["USD_JPY"], summaries, [], positions)
        self.assertEqual(management, [])
        self.assertEqual(len(manual), 1)

    def test_position_api_mismatch_is_safe_manual(self):
        summaries = [{"symbol":"EUR_USD","side":"BUY","sumPositionSize":"10000"}]
        _, management, manual = _classify_symbols(["EUR_USD"], summaries, [], [])
        self.assertEqual(management, [])
        self.assertEqual(len(manual), 1)

    def test_execution_sync_partial_failure_is_not_marked_complete(self):
        class Client:
            private_available = True
            def latest_executions(self, symbol, count):
                if symbol == "EUR_USD":
                    raise RuntimeError("temporary")
                return [{
                    "executionId": 1, "timestamp": "2026-08-27T00:00:00Z",
                    "symbol": symbol, "side": "BUY", "settleType": "CLOSE",
                    "size": "10000", "price": "150", "lossGain": "100",
                    "fee": "0", "settledSwap": "0"
                }]
        with tempfile.TemporaryDirectory() as td:
            db = StateDB(Path(td) / "state.sqlite3")
            _sync_executions(Client(), db, ["USD_JPY", "EUR_USD"])
            self.assertIsNone(db.get_meta("last_execution_sync"))

    def test_keep_order_requires_recommended_fill_price(self):
        state = {
            "kind": "order",
            "orders": [{
                "settleType": "OPEN", "side": "BUY", "executionType": "LIMIT",
                "price": "149.80", "orderId": 1,
            }],
        }
        result = {
            "action": "KEEP_ORDER", "confidence": 0.8,
            "trend_invalidation": None, "recommended_order_price": None,
            "take_partial_pct": None, "reason": "維持",
        }
        checked = _validate_management_result(state, result, {"bid":149.99,"ask":150.00,"tickSize":0.001})
        self.assertEqual(checked["action"], "REVIEW_MANUALLY")

    def test_pending_limit_recommended_fill_price_is_kept_and_rounded(self):
        state = {
            "kind": "order",
            "tf": {"15m": {"f": {"atr": 0.20}}},
            "orders": [{
                "settleType": "OPEN", "side": "BUY", "executionType": "LIMIT",
                "price": "149.80", "orderId": 1,
            }],
        }
        result = {
            "action": "KEEP_ORDER", "confidence": 0.8,
            "trend_invalidation": None, "recommended_order_price": 149.8014,
            "take_partial_pct": None, "reason": "押し目水準を維持",
        }
        checked = _validate_management_result(state, result, {"bid":149.99,"ask":150.00,"tickSize":0.001})
        self.assertEqual(checked["action"], "KEEP_ORDER")
        self.assertEqual(checked["recommended_order_price"], 149.801)

    def test_keep_order_becomes_reprice_when_recommended_price_differs(self):
        state = {
            "kind": "order",
            "tf": {"15m": {"f": {"atr": 0.30}}},
            "orders": [{
                "settleType": "OPEN", "side": "BUY", "executionType": "LIMIT",
                "price": "149.80", "orderId": 1,
            }],
        }
        result = {
            "action": "KEEP_ORDER", "confidence": 0.8,
            "trend_invalidation": None, "recommended_order_price": 149.60,
            "take_partial_pct": None, "reason": "より深い押し目を待つ",
        }
        checked = _validate_management_result(state, result, {"bid":149.99,"ask":150.00,"tickSize":0.001})
        self.assertEqual(checked["action"], "REPRICE_ORDER")

    def test_position_trend_invalidation_cannot_widen_on_tighten(self):
        state = {
            "kind": "position",
            "position": {"side": "BUY"},
            "orders": [{
                "settleType": "CLOSE", "side": "SELL", "executionType": "STOP",
                "price": "149.50", "size": "10000",
            }],
        }
        result = {
            "action": "TIGHTEN_SL", "confidence": 0.8,
            "trend_invalidation": 149.40, "recommended_order_price": None,
            "take_partial_pct": None, "reason": "管理",
        }
        checked = _validate_management_result(state, result, {"bid":150.00,"ask":150.01,"tickSize":0.001})
        self.assertEqual(checked["action"], "REVIEW_MANUALLY")


if __name__ == "__main__":
    unittest.main()
