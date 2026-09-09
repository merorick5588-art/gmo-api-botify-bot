from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from fetch_gmo_ohlcv import fetch_ohlcv
from ohlcv_calc import add_features
from prepare_features import prepare_ai_input

JST = ZoneInfo("Asia/Tokyo")


def _kline_rows(start: datetime, count: int, step_days: int = 1) -> list[dict]:
    rows = []
    for i in range(count):
        dt = start + timedelta(days=i * step_days)
        px = 0.84 + i * 0.0001
        rows.append({
            "openTime": int(dt.timestamp() * 1000),
            "open": str(px),
            "high": str(px + 0.0010),
            "low": str(px - 0.0010),
            "close": str(px + 0.0002),
        })
    return rows


class PartialDailyHistoryTests(unittest.TestCase):
    def test_new_symbol_daily_uses_available_history_and_stops_at_prelaunch_404(self):
        now = datetime(2026, 9, 9, 12, 0, tzinfo=JST)

        class Client:
            def __init__(self):
                self.years = []

            def klines(self, symbol, interval, date, price_type):
                self.years.append(date)
                if date == "2026":
                    return _kline_rows(datetime(2026, 1, 1, tzinfo=JST), 179)
                raise RuntimeError("404 Client Error: Not Found")

        client = Client()
        with patch("fetch_gmo_ohlcv.time.sleep", return_value=None):
            df = fetch_ohlcv(client, "EUR_GBP", "1day", now)

        self.assertEqual(len(df), 179)
        self.assertEqual(client.years, ["2026", "2025"])

    def test_year_boundary_fetches_older_year_until_target_is_reached(self):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=JST)

        class Client:
            def __init__(self):
                self.years = []

            def klines(self, symbol, interval, date, price_type):
                self.years.append(date)
                if date == "2026":
                    return _kline_rows(datetime(2026, 1, 1, tzinfo=JST), 10)
                if date == "2025":
                    return _kline_rows(datetime(2025, 1, 1, tzinfo=JST), 260)
                if date == "2024":
                    return _kline_rows(datetime(2024, 1, 1, tzinfo=JST), 100)
                return []

        client = Client()
        with patch("fetch_gmo_ohlcv.time.sleep", return_value=None):
            df = fetch_ohlcv(client, "USD_JPY", "1day", now)

        self.assertEqual(len(df), 320)
        self.assertEqual(client.years, ["2026", "2025", "2024"])

    def test_ai_input_accepts_partial_daily_and_omits_unavailable_long_features(self):
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            try:
                os.chdir(td)
                Path("symbols.csv").write_text("symbol\nEUR_GBP\n", encoding="utf-8")

                for suffix, count in (("15min", 320), ("1hour", 320), ("4hour", 320), ("1day", 179)):
                    x = np.arange(count, dtype=float)
                    close = 0.84 + x * 0.00005 + np.sin(x / 8.0) * 0.0002
                    raw = pd.DataFrame({
                        "OpenTime": pd.date_range("2025-01-01", periods=count, freq="D"),
                        "Open": close - 0.00005,
                        "High": close + 0.00020,
                        "Low": close - 0.00020,
                        "Close": close,
                        "Volume": 0.0,
                    })
                    feat = add_features(raw)
                    feat.to_csv(f"EUR_GBP_{suffix}_forex_features.csv", index=False)

                prepare_ai_input("symbols.csv")
                payload = json.loads(Path("EUR_GBP_ai_input.json").read_text(encoding="utf-8"))
                daily = payload["tf"]["1d"]

                self.assertEqual(daily["n"], 179)
                self.assertIn("h100", daily["f"])
                self.assertIn("s100", daily["f"])
                self.assertNotIn("s200", daily["f"])
                self.assertNotIn("h250", daily["f"])
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
