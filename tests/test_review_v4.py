import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from risk_engine import calculate_size, margin_ok, total_risk_ok, quote_to_jpy_rate
from gmo_client import GMOClient, GMOAPIError, parse_api_timestamp
from notify_discord_all import _validate_management_result
from fetch_gmo_ohlcv import _rows_to_df
import run_bot


class ReviewV4Tests(unittest.TestCase):
    def test_nonfinite_risk_cannot_pass(self):
        for value in (float("nan"), float("inf"), -1):
            self.assertFalse(margin_ok({"marginRatio": value})[0])
            self.assertFalse(total_risk_ok(value, 0.75)[0])

    def test_missing_rules_and_invalid_capital_cannot_size(self):
        rules = {"minOpenOrderSize": 1000, "maxOrderSize": 1000000, "sizeStep": 1000}
        for rule, equity in (({}, 400000), (rules, float("inf")), (dict(rules, sizeStep=0), 400000)):
            self.assertFalse(calculate_size("USD_JPY", 150, 149, equity, rule, {}).allowed)

    def test_bad_conversion_is_unavailable(self):
        for bid, ask in ((float("nan"), 150), (151, 150), (0, 1)):
            self.assertIsNone(quote_to_jpy_rate("EUR_USD", {"USD_JPY": {"bid": bid, "ask": ask}}))

    def test_partial_pagination_raises(self):
        client = GMOClient()
        page = {"list": [{"orderId": i} for i in range(100)]}
        with patch.object(client, "_private_get", return_value=page):
            with self.assertRaises(GMOAPIError):
                client._paginate_private("/test", "orderId", max_pages=1)
            with self.assertRaises(GMOAPIError):
                client._paginate_private("/test", "orderId", max_pages=3)

    def test_complete_pagination_succeeds(self):
        client = GMOClient()
        with patch.object(client, "_private_get", side_effect=[
            {"list": [{"orderId": i} for i in range(100)]}, {"list": [{"orderId": -1}]}]):
            self.assertEqual(len(client._paginate_private("/test", "orderId")), 101)

    def test_timestamp_requires_timezone(self):
        for value in (123, "2026-09-09T10:00:00", "bad"):
            self.assertIsNone(parse_api_timestamp(value))
        self.assertIsNotNone(parse_api_timestamp("2026-09-09T10:00:00Z"))

    def test_management_invalid_number_is_sanitized(self):
        for value in (float("nan"), float("inf"), -1):
            out = _validate_management_result({"kind": "position"},
                {"action": "TIGHTEN_SL", "trend_invalidation": value}, {"bid": 150, "ask": 150.01})
            self.assertEqual(out["action"], "REVIEW_MANUALLY")
            self.assertIsNone(out["trend_invalidation"])

    def test_partial_percentage_out_of_range(self):
        out = _validate_management_result({"kind": "position"},
            {"action": "TAKE_PARTIAL", "take_partial_pct": 120}, {"bid": 150, "ask": 150.01})
        self.assertEqual(out["action"], "REVIEW_MANUALLY")
        self.assertIsNone(out["take_partial_pct"])

    def test_invalid_ohlc_does_not_become_feature_data(self):
        base = {"openTime": 0, "open": 100, "high": 101, "low": 99, "close": 100}
        rows = [base, dict(base, high=98), dict(base, close="inf"), dict(base, low=-1)]
        self.assertEqual(len(_rows_to_df(rows, "15min", datetime.now(timezone.utc))), 1)

    def test_string_exit_is_error_and_explicit_zero_is_success(self):
        for exit_code, expected in (("invalid config", 1), (0, 0)):
            with patch.object(run_bot.fetch_gmo_ohlcv, "main", side_effect=SystemExit(exit_code)):
                self.assertEqual(run_bot.main(), expected)
