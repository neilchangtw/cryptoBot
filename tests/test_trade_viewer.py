import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import trade_viewer


class TradeViewerTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.live_path = root / "live.txt"
        self.backtest_path = root / "backtest.txt"
        self.kline_path = root / "klines.csv"
        # 固定的合成資料，測試不依賴個人 Downloads 或未納入 Git 的行情快取。
        self.live_path.write_text(
            "正式盤交易明細\n"
            "1 S 2026-01-01 10:00 2000 2026-01-01 12:00 1980 止盈 (TP) 2 +36.00 空頭 (DOWN)\n"
            "2 L 2026-01-01 13:00 2000 2026-01-01 15:00 1990 最長持倉 (MaxHold) 2 -24.00 盤整 (SIDE)\n"
            "3 S 2026-01-02 00:00 2000 2026-01-02 02:00 1980 止盈 (TP) 2 +36.00 空頭 (DOWN)\n"
            "4 S 2025-12-31 10:00 2000 2025-12-31 12:00 1980 止盈 (TP) 2 +36.00 空頭 (DOWN)\n",
            encoding="utf-8-sig",
        )
        self.backtest_path.write_text(
            "回測交易明細\n"
            "1 L 2026-01-01 10:00 2000 2026-01-01 12:00 1990 最長持倉 (MaxHold) 2 200 -24.00 -0.50 -12.00 盤整 (SIDE)\n",
            encoding="utf-8",
        )
        self.kline_path.write_text(
            "datetime,close\n"
            "2025-12-31 23:00:00,2000\n"
            "2026-01-01 00:00:00,2000\n"
            "2026-01-01 12:00:00,1980\n"
            "2026-01-02 00:00:00,2000\n",
            encoding="utf-8",
        )

    def test_parse_live_and_backtest_reports(self):
        live = trade_viewer.load_text_trades(self.live_path)
        backtest = trade_viewer.load_text_trades(self.backtest_path)

        self.assertEqual(4, len(live))
        self.assertEqual(1, len(backtest))
        self.assertEqual("S", live[0]["side"])
        self.assertEqual("2026-01-01 10:00", live[0]["entry_time"])
        self.assertAlmostEqual(36.00, live[0]["pnl"])
        self.assertEqual("L", backtest[0]["side"])
        self.assertAlmostEqual(-24.00, backtest[0]["pnl"])
        self.assertEqual("最長持倉 (MaxHold)", backtest[0]["exit_reason"])
        self.assertEqual("盤整 (SIDE)", backtest[0]["regime"])

    def test_recorder_csv_times_are_shifted_to_execution_time(self):
        path = Path(__file__).parent / "fixtures" / "trades_sample.csv"
        rows = trade_viewer.load_csv_trades(path)

        self.assertEqual("2026-01-01 11:00", rows[0]["entry_time"])
        self.assertEqual("2026-01-01 13:00", rows[0]["exit_time"])

    def test_store_filters_direction_and_date(self):
        store = trade_viewer.DataStore({"live": self.live_path})
        with patch.object(trade_viewer, "KLINE_PATH", self.kline_path):
            payload = store.data("live", "2026-01-01", "2026-01-01", "S")

        self.assertEqual([1], [row["number"] for row in payload["trades"]])
        self.assertEqual(
            ["2026-01-01 00:00", "2026-01-01 12:00"],
            [row["time"] for row in payload["candles"]],
        )
        self.assertAlmostEqual(36.00, payload["stats"]["pnl"])


if __name__ == "__main__":
    unittest.main()
