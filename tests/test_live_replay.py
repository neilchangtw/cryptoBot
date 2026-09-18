import unittest
from datetime import datetime

from live_replay import replay_trades, state_at


SCHEDULE_500 = [("2000-01-01", 500)]


def trade(number, side, entry, exit_time, pnl):
    return {
        "id": f"t{number}",
        "number": number,
        "side": side,
        "entry_time": entry,
        "entry_price": 100.0,
        "exit_time": exit_time,
        "exit_price": 100.0,
        "exit_reason": "MH",
        "hold": 1,
        "pnl": pnl,
        "regime": "SIDE",
    }


class LiveReplayTests(unittest.TestCase):
    def test_monthly_loss_blocks_same_side_after_realized_loss(self):
        rows = [
            trade(1, "L", "2026-09-01 10:00", "2026-09-01 11:00", -100.0),
            trade(2, "L", "2026-09-02 10:00", "2026-09-02 11:00", -100.0),
            trade(3, "L", "2026-09-03 10:00", "2026-09-03 11:00", 50.0),
        ]
        result = replay_trades(rows, SCHEDULE_500)
        self.assertTrue(result.entry_audits[0]["risk_allowed"])
        self.assertTrue(result.entry_audits[1]["risk_allowed"])
        self.assertFalse(result.entry_audits[2]["risk_allowed"])
        self.assertTrue(any("L月虧" in x for x in result.entry_audits[2]["risk_reasons"]))

    def test_consecutive_loss_cooldown_expires_after_24_hours(self):
        rows = [
            trade(1, "S", "2026-09-01 00:00", "2026-09-01 01:00", -1.0),
            trade(2, "S", "2026-09-01 03:00", "2026-09-01 04:00", -1.0),
            trade(3, "S", "2026-09-01 06:00", "2026-09-01 07:00", -1.0),
            trade(4, "S", "2026-09-01 09:00", "2026-09-01 10:00", -1.0),
        ]
        state_before_expiry = state_at(rows, datetime(2026, 9, 2, 9, 0), SCHEDULE_500)
        self.assertTrue(any("冷卻中" in x for x in state_before_expiry.block_reasons("S", datetime(2026, 9, 2, 9, 0))))
        state_after_expiry = state_at(rows, datetime(2026, 9, 2, 11, 0), SCHEDULE_500)
        self.assertFalse(any("冷卻中" in x for x in state_after_expiry.block_reasons("S", datetime(2026, 9, 2, 11, 0))))


if __name__ == "__main__":
    unittest.main()
