import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signal_status
import strategy


class ConsecCooldownTest(unittest.TestCase):
    def test_matches_executor_gate(self):
        st = {"bar_counter": 100, "consec_loss_cooldown_until": 113,
              "consec_losses": strategy.CONSEC_LOSS_PAUSE}
        self.assertEqual(signal_status.consec_cooldown_remaining(st), 13)

    def test_stale_until_without_enough_losses_is_not_cooldown(self):
        # 冷卻期間另一邊獲利出場 → 連虧歸零，但 cooldown_until 仍留在未來
        st = {"bar_counter": 100, "consec_loss_cooldown_until": 113,
              "consec_losses": strategy.CONSEC_LOSS_PAUSE - 1}
        self.assertEqual(signal_status.consec_cooldown_remaining(st), 0)

    def test_expired(self):
        st = {"bar_counter": 120, "consec_loss_cooldown_until": 113, "consec_losses": 5}
        self.assertEqual(signal_status.consec_cooldown_remaining(st), 0)


class PositionDisplayTest(unittest.TestCase):
    def test_safenet_prefers_submitted_price(self):
        pos = {"sub_strategy": "S", "entry_price": 2246.50, "safenet_price": 2337.46}
        self.assertEqual(signal_status.position_safenet_price(pos), 2337.46)

    def test_safenet_fallback_from_fill(self):
        pos = {"sub_strategy": "S", "entry_price": 2246.50}
        self.assertAlmostEqual(signal_status.position_safenet_price(pos), 2246.50 * 1.04)
        pos = {"sub_strategy": "L", "entry_price": 2000.0}
        self.assertAlmostEqual(signal_status.position_safenet_price(pos), 2000.0 * (1 - strategy.L_SAFENET_PCT))

    def test_margin_text(self):
        self.assertEqual(signal_status.position_margin_text({"margin": 120.0, "margin_mult": 0.6}), "120U ×0.6")
        self.assertEqual(signal_status.position_margin_text({"margin": 200.0, "margin_mult": 1.0}), "200U")
        self.assertEqual(signal_status.position_margin_text({}), "")


if __name__ == "__main__":
    unittest.main()
