import importlib
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import strategy


def reloaded(**env):
    with patch.dict(os.environ, env):
        return importlib.reload(strategy)


class GkMarginTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(strategy)

    def test_default_off_is_one(self):
        s = reloaded(GK_MARGIN_ENABLED="N")
        self.assertFalse(s.GK_MARGIN_ENABLED)
        for side, gk in (("L", 1.0), ("L", 90.0), ("S", 1.0), ("S", 90.0)):
            self.assertEqual(s.gk_margin_mult(side, gk), 1.0)

    def test_boundaries(self):
        s = reloaded(GK_MARGIN_ENABLED="Y")
        for gk, want in ((0.0, 0.6), (7.76, 0.6), (7.766667, 0.9), (17.19, 0.9), (17.2, 1.5), (24.9, 1.5)):
            self.assertEqual(s.gk_margin_mult("L", gk), want)
        for gk, want in ((0.0, 1.5), (11.09, 1.5), (11.1, 0.9), (24.19, 0.9), (24.2, 0.6), (34.9, 0.6)):
            self.assertEqual(s.gk_margin_mult("S", gk), want)

    def test_matches_shadow_table(self):
        import viewer_stack_shadow as stack
        s = reloaded(GK_MARGIN_ENABLED="Y")
        for side in ("L", "S"):
            cuts, mults = stack.N1_TABLE[2026][side]
            self.assertEqual(s.GK_MARGIN_CUTS[side], cuts)
            self.assertEqual(s.GK_MARGIN_MULT[side], mults)

    def test_missing_gk_falls_back(self):
        s = reloaded(GK_MARGIN_ENABLED="Y")
        self.assertEqual(s.gk_margin_mult("L", None), 1.0)
        self.assertEqual(s.gk_margin_mult("S", float("nan")), 1.0)

    def test_custom_multipliers_and_bad_value(self):
        s = reloaded(GK_MARGIN_ENABLED="Y", GK_MARGIN_MULT_L="0.5,1,2")
        self.assertEqual(s.gk_margin_mult("L", 20.0), 2.0)
        with self.assertRaises(ValueError):
            reloaded(GK_MARGIN_ENABLED="Y", GK_MARGIN_MULT_L="0.5,1")

    def test_position_notional_old_state(self):
        s = reloaded(MARGIN_PER_TRADE="200", LEVERAGE="20")
        self.assertEqual(s.position_notional({}), 4000)
        self.assertEqual(s.position_notional({"margin": 120.0}), 2400)

    def test_risk_lines_unchanged(self):
        off = reloaded(GK_MARGIN_ENABLED="N", MARGIN_PER_TRADE="200")
        caps = (off.DAILY_LOSS_LIMIT, off.L_MONTHLY_LOSS_CAP, off.S_MONTHLY_LOSS_CAP, off.FEE)
        on = reloaded(GK_MARGIN_ENABLED="Y", MARGIN_PER_TRADE="200")
        self.assertEqual(caps, (on.DAILY_LOSS_LIMIT, on.L_MONTHLY_LOSS_CAP, on.S_MONTHLY_LOSS_CAP, on.FEE))


class LotSizeTest(unittest.TestCase):
    def test_smallest_tier_clears_lot(self):
        try:
            import binance_trade
        except ImportError as exc:
            raise unittest.SkipTest(f"這台沒有 binance 套件：{exc}")
        qty = binance_trade.round_to_lot(2400 / 2238.27, 0.001, 0.001)
        self.assertAlmostEqual(qty, 1.072, places=3)


class ExecutorOpenTest(unittest.TestCase):
    def open_with(self, enabled, gk):
        import executor as ex
        s = strategy
        indicators = {"gk_pctile": gk, "gk_ratio": 1.0, "close": 2000.0, "ema20": 1990.0,
                      "breakout_15bar_max": 1990.0, "breakout_15bar_min": 1900.0, "sma_slope": 0.0}
        bar = {"datetime": datetime(2026, 11, 2, 10), "eth_24h_change_pct": 1.0}
        fake = MagicMock()
        fake.get_positions.return_value = []
        fake.place_order.side_effect = lambda sym, side, qty, **kw: {"avgPrice": 2000.0, "executedQty": qty, "orderId": 1}
        fake.get_order_commission.return_value = 1.0
        obj = ex.Executor.__new__(ex.Executor)
        obj.trade_number = 0
        obj.positions = {}
        obj.last_exits = {}
        obj.monthly_entries = {}
        obj.account_balance = 1000.0
        obj.save_state = MagicMock()
        obj._sync_balance = MagicMock()
        with patch.object(s, "GK_MARGIN_ENABLED", enabled), patch.object(s, "MARGIN", 200.0), patch.object(s, "NOTIONAL", 4000.0), \
                patch.dict("sys.modules", {"binance_trade": fake}), patch.object(ex, "recorder"), patch.object(ex, "send_telegram_message"):
            obj._open_position_locked("long", "L", 2000.0, 10, indicators, bar, {"btc_close": 60000.0})
        return next(iter(obj.positions.values())), fake.place_order.call_args.kwargs["qty"]

    def test_qty_scaled_when_enabled(self):
        pos, qty = self.open_with(True, 20.0)
        self.assertAlmostEqual(qty, 3.0)
        self.assertEqual((pos["margin"], pos["margin_mult"]), (300.0, 1.5))

    def test_disabled_keeps_old_qty(self):
        pos, qty = self.open_with(False, 20.0)
        self.assertAlmostEqual(qty, 2.0)
        self.assertEqual(pos["margin_mult"], 1.0)


if __name__ == "__main__":
    unittest.main()
