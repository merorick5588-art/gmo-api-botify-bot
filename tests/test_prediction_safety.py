from __future__ import annotations

import unittest
from unittest.mock import patch

from test_llm_contract import analyze_ohlcv
from test_token_budget import _Client, _Response


class PredictionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.item = {"symbol": "USD_JPY", "bid": 149.99, "ask": 150.0,
                     "ai_input": {"tf": {"15m": {"f": {"atr": 0.2}}}}}
        self.row = {"symbol": "USD_JPY", "trend_score": 0.8, "entry_quality": 0.8,
                    "entry_plan": "ENTER_NOW", "entry": 150.0,
                    "trend_invalidation": 149.5, "take_profit": 150.8, "reason": "test"}

    def request(self, row):
        with patch.object(analyze_ohlcv, "_response_json_with_retry", return_value={"results": [row]}) as request:
            result = analyze_ohlcv.analyze_entry_batch([self.item])
        self.assertEqual(request.call_count, 1)
        return result["USD_JPY"]

    def test_explicit_abstention_is_preserved_without_retry(self):
        row = dict(self.row, entry_plan="NO_TRADE", trend_score=0, entry_quality=0,
                   entry=None, trend_invalidation=None, take_profit=None, reason="方向不明")
        result = self.request(row)
        self.assertEqual(result["entry_plan"], "NO_TRADE")
        self.assertIsNone(result["direction"])
        self.assertIsNone(result["rr"])
        self.assertEqual(result["reason"], "方向不明")

    def test_low_rr_and_neutral_do_not_get_resampled(self):
        for change in ({"take_profit": 150.1}, {"trend_score": 0}):
            with self.subTest(change=change):
                self.assertEqual(self.request(dict(self.row, **change))["entry_plan"], "NO_TRADE")

    def test_abstention_cannot_contain_order_prices(self):
        ok, _ = analyze_ohlcv._validate_entry(dict(self.row, entry_plan="NO_TRADE", entry_quality=0), self.item)
        self.assertFalse(ok)

    def test_nonfinite_and_nonpositive_prices_rejected(self):
        for key in ("entry", "trend_invalidation", "take_profit"):
            for value in (float("inf"), float("-inf"), float("nan"), 0, -1):
                with self.subTest(key=key, value=value):
                    self.assertFalse(analyze_ohlcv._validate_entry(dict(self.row, **{key: value}), self.item)[0])

    def test_bad_quotes_and_missing_atr_rejected(self):
        items = [dict(self.item, bid=151), dict(self.item, ask=float("inf")),
                 dict(self.item, ai_input={"tf": {}})]
        for item in items:
            self.assertFalse(analyze_ohlcv._validate_entry(self.row, item)[0])

    def test_completed_malformed_json_is_not_retried(self):
        client = _Client([_Response("not json", status="completed")])
        with patch.object(analyze_ohlcv, "_client", return_value=client):
            with self.assertRaises(ValueError):
                analyze_ohlcv._response_json_with_retry(label="test", create_kwargs={}, initial_max_tokens=1600)
        self.assertEqual(len(client.responses.calls), 1)

    def test_invalid_order_is_not_resampled(self):
        row = dict(self.row, entry_plan="BREAKOUT_STOP", entry=149.9)
        with patch.object(analyze_ohlcv, "_response_json_with_retry", return_value={"results": [row]}) as request:
            result = analyze_ohlcv.analyze_entry_batch([self.item])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result, {})

    def test_filtered_response_not_accepted_even_if_json_valid(self):
        client = _Client([_Response('{"results": []}', status="incomplete", reason="content_filter")])
        with patch.object(analyze_ohlcv, "_client", return_value=client):
            with self.assertRaises(ValueError):
                analyze_ohlcv._response_json_with_retry(label="test", create_kwargs={}, initial_max_tokens=1600)
        self.assertEqual(len(client.responses.calls), 1)


if __name__ == "__main__":
    unittest.main()
