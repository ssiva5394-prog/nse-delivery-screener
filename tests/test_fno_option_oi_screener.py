import unittest
import pandas as pd
from datetime import date

from nse_fno_option_screener import build_signals


class OptionOISignalTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame([
            {"Date": date(2026, 9, 1), "Symbol": "ABC", "Expiry": date(2026, 10, 29),
             "Strike": 100, "Option_Type": "CE", "Open": 10, "High": 12, "Low": 9,
             "Close": 11, "LTP": 11, "Prev_Close": 10, "Underlying_Value": 100,
             "Volume_Contracts": 100, "Change_in_OI": 150, "Total_OI": 1000},
            {"Date": date(2026, 9, 30), "Symbol": "ABC", "Expiry": date(2026, 10, 29),
             "Strike": 100, "Option_Type": "CE", "Open": 11, "High": 13, "Low": 10,
             "Close": 12, "LTP": 12, "Prev_Close": 11, "Underlying_Value": 101,
             "Volume_Contracts": 110, "Change_in_OI": 100, "Total_OI": 900},
            {"Date": date(2026, 10, 1), "Symbol": "ABC", "Expiry": date(2026, 10, 29),
             "Strike": 100, "Option_Type": "CE", "Open": 12, "High": 14, "Low": 11,
             "Close": 13, "LTP": 13, "Prev_Close": 12, "Underlying_Value": 102,
             "Volume_Contracts": 120, "Change_in_OI": 250, "Total_OI": 2100},
        ])

    def test_no_signal_without_prior_month_observations(self):
        result = build_signals(self.rows.iloc[[2]].copy())
        self.assertEqual(len(result), 0)

    def test_all_four_oi_conditions_must_pass(self):
        result = build_signals(self.rows)
        self.assertEqual(len(result), 1)
        self.assertTrue(bool(result.iloc[0]["Combined_Signal"]))

    def test_fails_when_total_oi_previous_day_rule_fails(self):
        rows = self.rows.copy()
        rows.loc[2, "Total_OI"] = 1500  # not greater than 2 x previous day (900)
        result = build_signals(rows)
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
