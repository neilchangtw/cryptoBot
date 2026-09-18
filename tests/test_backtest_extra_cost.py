"""回測額外執行成本應納入淨損益與熔斷帳本。"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest" / "research"))
import v14_export_trades as engine  # noqa: E402


class BacktestExtraCostTests(unittest.TestCase):
    def setUp(self):
        n = 350
        self.ind = {key: np.full(n, 100.0) for key in ("o", "h", "l", "c")}
        self.ind.update({
            key: np.zeros(n, dtype=bool)
            for key in ("brk_up", "brk_dn", "regime_block_l", "regime_block_s")
        })
        self.ind.update({
            key: np.full(n, value)
            for key, value in (
                ("pctile_L", 10.0),
                ("pctile_S", 10.0),
                ("hours", 10),
                ("dows", 1),
                ("months", 202501),
                ("days", 20250101),
                ("slope", 0.0),
            )
        })
        self.times = pd.date_range("2025-01-01", periods=n, freq="h").to_numpy()

    def test_extra_cost_is_recorded_and_reduces_net_pnl(self):
        self.ind["brk_up"][310] = True
        self.ind["h"][311] = 104.0
        self.ind["c"][311] = 100.5

        base = engine.simulate_v14_detailed(
            self.ind, self.times, realistic=True, extra_cost=0
        )
        stress = engine.simulate_v14_detailed(
            self.ind, self.times, realistic=True, extra_cost=5
        )

        self.assertEqual(len(base), 1)
        self.assertEqual(len(stress), 1)
        self.assertAlmostEqual(base[0]["pnl_usd"] - stress[0]["pnl_usd"], 5.0)
        self.assertEqual(stress[0]["extra_cost_usd"], 5.0)

    def test_extra_cost_can_trigger_monthly_loss_cap(self):
        self.ind["brk_up"][310] = True
        self.ind["h"][311] = 104.0
        self.ind["brk_up"][320] = True
        self.ind["h"][321] = 104.0

        original_cap = engine.CB_L_MONTH
        try:
            engine.CB_L_MONTH = -5.0
            base = engine.simulate_v14_detailed(
                self.ind, self.times, realistic=True, extra_cost=0
            )
            stress = engine.simulate_v14_detailed(
                self.ind, self.times, realistic=True, extra_cost=5
            )
        finally:
            engine.CB_L_MONTH = original_cap

        self.assertEqual(len(base), 2)
        self.assertEqual(len(stress), 1)


if __name__ == "__main__":
    unittest.main()
