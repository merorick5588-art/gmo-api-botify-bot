from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ohlcv_calc import add_features, compute_rsi
from prepare_features import summarize


class ForecastFeatureTests(unittest.TestCase):
    def frame(self):
        close = np.full(40, 100.0)
        df = pd.DataFrame({"Open": close, "High": close + 1,
                           "Low": close - 1, "Close": close})
        return add_features(df)

    def test_flat_rsi_is_neutral_after_warmup(self):
        rsi = compute_rsi(pd.Series([100.0] * 40))
        self.assertTrue(rsi.iloc[:14].isna().all())
        self.assertTrue((rsi.iloc[14:] == 50).all())

    def test_rsi_still_distinguishes_up_and_down(self):
        self.assertEqual(compute_rsi(pd.Series(np.arange(40.0))).iloc[-1], 100)
        self.assertEqual(compute_rsi(pd.Series(np.arange(40.0)[::-1])).iloc[-1], 0)

    def test_close_breakout_excludes_current_bar_extreme(self):
        df = self.frame()
        df.loc[39, ["High", "Close"]] = [103, 102]
        df.loc[39, "ATR_14"] = 2
        f = summarize(df, "1h")
        self.assertEqual(f["close"], 102)
        self.assertEqual(f["h20"], 0.5)  # current high is above close
        self.assertEqual(f["break_high20"], 0.5)  # close broke prior high
        self.assertEqual(f["break_low20"], -1.5)

    def test_wick_alone_is_not_close_breakout(self):
        df = self.frame()
        df.loc[39, "High"] = 110
        f = summarize(df, "1h")
        self.assertLess(f["break_high20"], 0)

    def test_move_is_net_change_and_acceleration_uses_three_bars(self):
        df = self.frame()
        df.loc[39, "Close"] = 104
        df.loc[35, "Close"] = 102
        df.loc[27, "Close"] = 98
        df.loc[39, "ATR_14"] = 2
        df.loc[39, ["RSI_14", "MACD", "MACD_signal"]] = [60, 1.2, 0.8]
        df.loc[36, ["RSI_14", "MACD", "MACD_signal"]] = [65, 1.4, 0.6]
        f = summarize(df, "1h")
        self.assertEqual(f["move4"], 1)
        self.assertEqual(f["move12"], 3)
        self.assertEqual(f["rsi_d3"], -5)
        self.assertEqual(f["mh_d3"], -0.2)

    def test_missing_change_is_omitted_not_zero(self):
        df = self.frame()
        df.loc[36, "RSI_14"] = float("nan")
        self.assertNotIn("rsi_d3", summarize(df, "1h"))


if __name__ == "__main__":
    unittest.main()
