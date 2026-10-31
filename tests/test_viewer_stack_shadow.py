import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import viewer_lowvol_shadow as lowvol
import viewer_stack_shadow as stack
import vps_viewer

ROOT = Path(__file__).resolve().parents[1]
LONG_CSV = ROOT / "data" / "v39" / "ETH_1h_long.csv"
HOUR_MS = 3_600_000
START_MS = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)


def synthetic(n=2000, seed=3):
    rng = np.random.default_rng(seed)
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return [dict(time_ms=START_MS + i * HOUR_MS, high=c * 1.002, low=c * 0.998, close=float(c), time_display=f"b{i}", closed=True)
            for i, c in enumerate(close)]


class ReturnParityTest(unittest.TestCase):
    def check(self, close):
        sys.path.insert(0, str(ROOT / "backtest" / "research"))
        try:
            from v44_r18_ltrendskip import past_return
        except ImportError as exc:
            raise unittest.SkipTest(f"回測腳本無法載入：{exc}")
        ref = past_return(np.asarray(close, float), 30)
        bars = [dict(time_ms=i, close=float(c)) for i, c in enumerate(close)]
        got = stack.past_returns(bars)
        for i in range(len(close)):
            if np.isnan(ref[i]):
                self.assertNotIn(i, got)
            else:
                self.assertAlmostEqual(got[i], ref[i], places=12)

    def test_synthetic(self):
        self.check([bar["close"] for bar in synthetic()])

    @unittest.skipUnless(LONG_CSV.exists(), "沒有 ETH_1h_long.csv")
    def test_real_candles(self):
        self.check(pd.read_csv(LONG_CSV)["close"].tolist()[:4000])

    def test_next_bar_uses_last_close(self):
        bars = synthetic(800)
        got = stack.past_returns(bars)
        self.assertAlmostEqual(got[None], bars[-1]["close"] / bars[-1 - 720]["close"] - 1)


class MultiplierTest(unittest.TestCase):
    def test_ct(self):
        self.assertEqual(stack.ct_mult("L", -0.05), 0.5)
        self.assertEqual(stack.ct_mult("L", -0.2), 1.0)
        self.assertEqual(stack.ct_mult("L", 0.05), 1.0)
        self.assertEqual(stack.ct_mult("S", 0.05), 0.5)
        self.assertEqual(stack.ct_mult("S", 0.2), 1.0)

    def test_n1_locked_table(self):
        self.assertEqual(stack.n1_mult("L", 5, 2026), 0.6)
        self.assertEqual(stack.n1_mult("L", 7.766667, 2026), 0.9)
        self.assertEqual(stack.n1_mult("L", 17.2, 2026), 1.5)
        self.assertEqual(stack.n1_mult("S", 5, 2026), 1.5)
        self.assertEqual(stack.n1_mult("S", 24.2, 2026), 0.6)

    def test_n1_matches_backtest_digitize(self):
        for gk in (0.0, 7.766667, 7.8, 17.2, 17.3, 99.0):
            cuts, mults = stack.N1_TABLE[2026]["L"]
            self.assertEqual(stack.n1_mult("L", gk, 2026), mults[int(np.digitize(gk, cuts))])


class LedgerTest(unittest.TestCase):
    def row(self, number, side, opened, pnl, gk):
        key = "gk_pctile" if side == "L" else "gk_pctile_s"
        return {"number": number, "side": side, "entry_signal_time_utc": opened.astimezone(timezone.utc).isoformat(),
                "entry_time_display": "x", "pnl_200u": pnl, key: gk}

    def test_mean_m_uses_active_trades_and_pause_is_zero(self):
        lock = lowvol.LOCK_TIME
        hour = timedelta(hours=1)
        a, b, c, old = lock + hour, lock + 2 * hour, lock + 3 * hour, lock - hour
        ms = lambda t: int(t.timestamp() * 1000)
        paused = {ms(a): False, ms(b): False, ms(c): True, ms(old): False}
        r = {ms(a): 0.05, ms(b): -0.05, ms(c): 0.05, ms(old): 0.05}
        rows = [self.row(0, "L", old, 999.0, 20), self.row(1, "L", a, 30.0, 20),      # 1.5 × 1
                self.row(2, "L", b, -10.0, 20), self.row(3, "S", c, -50.0, 5)]        # 1.5 × 0.5；暫停
        out = stack.shadow_ledger(rows, paused, r)
        self.assertEqual(out["trades"], 3)
        self.assertAlmostEqual(out["mean_m"], 1.125)
        self.assertEqual(out["actual_pnl"], -30.0)
        self.assertAlmostEqual(out["shadow_pnl"], (30 * 1.5 - 10 * 0.75) / 1.125, places=2)
        self.assertEqual(out["actual_mdd"], 60.0)
        self.assertEqual(out["recent"][0]["m"], 0.0)

    def test_all_paused(self):
        lock = lowvol.LOCK_TIME + timedelta(hours=1)
        ms = int(lock.timestamp() * 1000)
        out = stack.shadow_ledger([self.row(1, "L", lock, -20.0, 20)], {ms: True}, {ms: 0.05})
        self.assertIsNone(out["mean_m"])
        self.assertEqual(out["shadow_pnl"], 0)


class StoreTest(unittest.TestCase):
    def test_fetch_failure_reports_insufficient(self):
        store = vps_viewer.DataStore(allow_network=True)
        with patch.object(store, "_fetch_public_klines", side_effect=OSError("down")):
            out = store.stack_shadow()
        self.assertEqual(out["status"], "insufficient")

    def test_state_uses_shared_bars(self):
        store = vps_viewer.DataStore(allow_network=True)
        rng = np.random.default_rng(5)
        close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.005, 6500)))
        bars = [dict(time_ms=START_MS + i * HOUR_MS, high=c * 1.003, low=c * 0.997, close=float(c), time_display=f"b{i}", closed=True)
                for i, c in enumerate(close)]
        now = datetime.fromtimestamp((bars[-1]["time_ms"] + 2 * HOUR_MS) / 1000, timezone.utc)
        with patch.object(store, "_fetch_public_klines", side_effect=lambda a, b: [x for x in bars if a <= x["time_ms"] <= b]), \
                patch.object(store, "trades", return_value=[]), patch.object(vps_viewer, "_utcnow", return_value=now):
            out = store.stack_shadow()
            low = store.lowvol_shadow()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["ledger"]["trades"], 0)
        self.assertEqual(out["state"]["paused"], low["paused"])


if __name__ == "__main__":
    unittest.main()
