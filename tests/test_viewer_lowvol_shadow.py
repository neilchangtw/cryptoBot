import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import viewer_lowvol_shadow as shadow
import vps_viewer

ROOT = Path(__file__).resolve().parents[1]
LONG_CSV = ROOT / "data" / "v39" / "ETH_1h_long.csv"


def backtest_vol_state():
    sys.path.insert(0, str(ROOT / "backtest" / "research"))
    try:
        from v44_r15_lowvolpause import vol_state
    except ImportError as exc:
        raise unittest.SkipTest(f"回測腳本無法載入：{exc}")
    return vol_state


def synthetic(n=20000, seed=7):
    rng = np.random.default_rng(seed)
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.005, n) * (1 + 0.8 * np.sin(np.arange(n) / 2500))))
    span = np.abs(rng.normal(0, 0.004, n)) * close
    return pd.DataFrame({"high": close + span, "low": close - span, "close": close})


def bars_from(frame):
    start = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return [dict(time_ms=start + i * 3_600_000, high=h, low=l, close=c, time_display=f"bar{i}")
            for i, (h, l, c) in enumerate(zip(frame.high, frame.low, frame.close))]


class VolStateParityTest(unittest.TestCase):
    def assert_same(self, frame):
        vol_state = backtest_vol_state()
        v_ref, r_ref = vol_state(frame, 30)
        v, r = shadow.vol_state(frame.high.tolist(), frame.low.tolist(), frame.close.tolist())
        v = np.array([np.nan if x is None else x for x in v])
        r = np.array([np.nan if x is None else x for x in r])
        np.testing.assert_array_equal(np.isnan(v), np.isnan(v_ref))
        np.testing.assert_array_equal(np.isnan(r), np.isnan(r_ref))
        np.testing.assert_allclose(v[~np.isnan(v)], v_ref[~np.isnan(v_ref)], atol=1e-9)
        np.testing.assert_allclose(r[~np.isnan(r)], r_ref[~np.isnan(r_ref)], atol=0.02)
        np.testing.assert_array_equal(r[~np.isnan(r)] < 20, r_ref[~np.isnan(r_ref)] < 20)

    def test_synthetic(self):
        self.assert_same(synthetic())

    @unittest.skipUnless(LONG_CSV.exists(), "沒有 ETH_1h_long.csv")
    def test_real_candles(self):
        self.assert_same(pd.read_csv(LONG_CSV)[["high", "low", "close"]])


class LedgerTest(unittest.TestCase):
    def trade(self, number, opened, pnl):
        return dict(number=number, side="L", entry_signal_time_utc=opened.astimezone(timezone.utc).isoformat(),
                    entry_time_display="x", exit_reason="MaxHold", pnl=pnl)

    def test_groups_and_lock_date(self):
        lock = shadow.LOCK_TIME
        hour = timedelta(hours=1)
        before, normal, blocked = lock - hour, lock + hour, lock + 2 * hour
        ms = lambda t: int(t.timestamp() * 1000)
        by_time = {ms(before): True, ms(normal): False, ms(blocked): True}
        rows = [self.trade(1, before, -50.0), self.trade(2, normal, 30.0),
                self.trade(3, blocked, -20.0), self.trade(4, lock + 9 * hour, 5.0)]
        out = shadow.shadow_ledger(rows, by_time)
        self.assertEqual((out["normal"]["trades"], out["normal"]["pnl"]), (1, 30.0))
        self.assertEqual((out["blocked"]["trades"], out["blocked"]["pnl"]), (1, -20.0))
        self.assertEqual(out["unknown"], 1)
        self.assertEqual([row["number"] for row in out["recent"]], [4, 3, 2])
        self.assertIs(out["recent"][1]["blocked"], True)


class StoreTest(unittest.TestCase):
    def test_fetch_failure_reports_insufficient(self):
        store = vps_viewer.DataStore(allow_network=True)
        with patch.object(store, "_fetch_public_klines", side_effect=OSError("down")), \
                patch.object(store, "trades", return_value=[]):
            out = store.lowvol_shadow()
        self.assertEqual(out["status"], "insufficient")
        self.assertIn("down", out["reason"])

    def test_incremental_fetch_and_state(self):
        bars = bars_from(synthetic(n=7000))
        for row in bars:
            row["closed"] = True
        store = vps_viewer.DataStore(allow_network=True)
        calls = []

        def fake(start_ms, end_ms):
            calls.append(start_ms)
            return [row for row in bars[:6999 if len(calls) == 1 else 7000] if start_ms <= row["time_ms"] <= end_ms]

        now = datetime.fromtimestamp((bars[-1]["time_ms"] + 7_200_000) / 1000, timezone.utc)
        with patch.object(store, "_fetch_public_klines", side_effect=fake), \
                patch.object(store, "trades", return_value=[]), \
                patch.object(vps_viewer, "_utcnow", return_value=now):
            first = store.lowvol_shadow()
            store._lowvol_checked = 0.0
            second = store.lowvol_shadow()
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["bars"], 7000)
        self.assertEqual(calls[-1], bars[6999]["time_ms"])


if __name__ == "__main__":
    unittest.main()
