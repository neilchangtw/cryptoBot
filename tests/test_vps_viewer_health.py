import csv
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import vps_viewer


FIELDS = [
    "trade_id", "trade_number", "entry_time_utc8", "exit_time_utc8", "sub_strategy",
    "entry_price", "exit_price", "exit_type", "hold_bars", "hold_hours",
    "gross_pnl_usd", "net_pnl_usd", "net_pnl_pct",
    "max_adverse_excursion_pct", "max_favorable_excursion_pct", "gk_pctile_at_entry", "entry_regime",
]


def _live_row(number, side, entry, exit_time, entry_price, exit_price, notional, exit_type="MaxHold", mfe="1.0"):
    direction = 1 if side == "L" else -1
    gross = (exit_price - entry_price) / entry_price * direction * notional
    fee = notional / 1000
    return {
        "trade_id": f"T{number}", "trade_number": number,
        "entry_time_utc8": entry, "exit_time_utc8": exit_time, "sub_strategy": side,
        "entry_price": entry_price, "exit_price": exit_price, "exit_type": exit_type,
        "hold_bars": 6, "hold_hours": 6,
        "gross_pnl_usd": round(gross, 4), "net_pnl_usd": round(gross - fee, 4),
        "net_pnl_pct": "", "max_adverse_excursion_pct": "-0.5", "max_favorable_excursion_pct": mfe,
        "gk_pctile_at_entry": "12", "entry_regime": "SIDE",
    }


class VpsViewerHealthTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "data_live").mkdir()
        self.live_path = self.root / "data_live" / "trades.csv"
        rows = [
            # 200U（$4,000 名目）：L 漲 1% → 毛利 $40
            _live_row(1, "L", "2026-06-02 10:00:00", "2026-06-02 16:00:00", 2000.0, 2020.0, 4000, "TP", "3.6"),
            # 500U（$10,000 名目）：S 價格上漲 1% → 毛損 -$100
            _live_row(2, "S", "2026-08-03 10:00:00", "2026-08-03 18:00:00", 2000.0, 2020.0, 10000),
        ]
        with self.live_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        self.state_path = self.root / "eth_state_live.json"
        self.state_path.write_text(json.dumps({"edge_health": {"cusum": 200.0, "level": "green"}}), encoding="utf-8")
        patches = [
            patch.object(vps_viewer, "LIVE_DIR", self.root / "data_live"),
            patch.object(vps_viewer, "STATE_PATH", self.state_path),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.store = vps_viewer.DataStore(allow_network=False)

    def test_live_margin_is_derived_from_fill_and_normalized_to_200u(self):
        first, second = self.store.trades("live")
        self.assertEqual(200, first["margin"])
        self.assertEqual("成交推算", first["size_source"])
        self.assertAlmostEqual(first["pnl"], first["pnl_200u"], places=4)
        self.assertEqual(500, second["margin"])
        self.assertAlmostEqual(second["pnl"] * 0.4, second["pnl_200u"], places=4)

    def test_basis_switch_changes_pnl_only_in_200u_mode(self):
        rows = self.store.trades("live")
        actual = vps_viewer._with_basis(rows, "actual")
        normalized = vps_viewer._with_basis(rows, "200u")
        self.assertIs(actual, rows)
        self.assertAlmostEqual(rows[1]["pnl_200u"], normalized[1]["pnl"])
        self.assertAlmostEqual(rows[1]["pnl_actual"], normalized[1]["pnl_actual"])
        with self.assertRaises(ValueError):
            vps_viewer._basis("usd")

    def test_exit_codes_match_edge_falsify_short_codes(self):
        self.assertEqual("MHx", vps_viewer._exit_code("延長超時 (MH-ext)"))
        self.assertEqual("TP", vps_viewer._exit_code("止盈 (TP)"))
        self.assertEqual("MH", vps_viewer._exit_code("MaxHold"))
        self.assertEqual("SN", vps_viewer._exit_code("SafeNet"))

    def test_margin_schedule_is_read_without_importing_run_backtest(self):
        schedule = vps_viewer._margin_schedule()
        self.assertEqual(200.0, schedule[0][1])
        self.assertEqual(500.0, vps_viewer._scheduled_margin("2026-08-05 10:00", schedule))

    def test_edge_uses_state_file_and_reports_monthly_lights(self):
        payload = self.store.edge("live")
        self.assertEqual("機器人狀態檔", payload["v29"]["cusum_source"])
        self.assertAlmostEqual(75.0, payload["v29"]["pct"])
        self.assertEqual(2, payload["current"]["trades"])
        self.assertEqual(6, len(payload["monthly"]))
        self.assertIn(payload["current"]["overall_light"], {"green", "yellow", "red"})
        # 整段回測對照只在回測模式提供
        self.assertIsNone(payload["full_window"])

    def test_long_gk_series_is_bucketed_without_dropping_compression(self):
        path = self.root / "data_live" / "bar_snapshots.csv"
        lines = ["bar_time_utc8,gk_pctile,gk_pctile_s"]
        for hour in range(3000):
            stamp = datetime(2026, 6, 2) + timedelta(hours=hour)
            # 每 50 根只有 1 根壓縮：跳點抽樣容易漏掉，分桶必須保留 below 計數
            gk = 5.0 if hour % 50 == 7 else 80.0
            lines.append(f"{stamp:%Y-%m-%d %H:%M:%S},{gk},{gk}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        payload = self.store.analysis("live", start_date="2026-06-02", end_date="2026-10-07")
        snap = payload["evidence"]["bar_snapshots"]
        self.assertLessEqual(len(snap["gk_series"]), 1500)
        self.assertEqual(60, sum(item["long_below"] for item in snap["gk_series"]))
        self.assertAlmostEqual(2.0, snap["gk_stats"]["long_rate"])

    def test_rolling_metrics_cover_full_history(self):
        rolling = self.store._rolling_metrics("live")
        self.assertEqual({"T1", "T2"}, set(rolling))
        # 少於 5 筆時不輸出滾動平均，避免小樣本畫成趨勢
        self.assertIsNone(rolling["T2"]["rolling_mfe"])
        self.assertEqual(2, rolling["T2"]["rolling_count"])

    def test_backtest_metrics_are_joined_by_side_and_entry_time(self):
        backtest = self.root / "backtest.txt"
        backtest.write_text(
            "回測交易明細\n"
            "1 L 2026-01-01 10:00 2000 2026-01-01 12:00 1990 最長持倉 (MaxHold) 2 500 -60.00 -0.50 -12.00 盤整 (SIDE)\n",
            encoding="utf-8",
        )
        metrics = self.root / "metrics.csv"
        metrics.write_text(
            "side,entry_exec_utc8,exit_exec_utc8,exit_reason,mae_pct,mfe_pct,gk_pctile,margin,pnl_usd\n"
            "L,2026-01-01 10:00,2026-01-01 12:00,MH,-0.8,0.4,11.5,500,-60\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"VIEWER_BACKTEST_PATH": str(backtest), "VIEWER_BACKTEST_METRICS_PATH": str(metrics)}):
            row = self.store.trades("backtest")[0]
        self.assertEqual(500, row["margin"])
        self.assertAlmostEqual(-24.0, row["pnl_200u"])
        self.assertAlmostEqual(0.4, row["mfe_pct"])
        self.assertAlmostEqual(11.5, row["gk_pctile"])
        self.assertTrue(self.store._backtest_metrics_status["available"])


if __name__ == "__main__":
    unittest.main()
