"""V43 資金費率套利：資金費正負號與區間、基差、成本、再平衡、擇時、平台選擇、保留期守門。全部合成資料。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest" / "research"))
import v43_carry as v43  # noqa: E402

S_UTC = pd.Timestamp("2021-01-01") - v43.TPE
E_UTC = pd.Timestamp("2021-02-01") - v43.TPE


def panel(perp, spot=None, start=S_UTC, hours=24 * 31) -> pd.DataFrame:
    idx = pd.date_range(start + pd.Timedelta(hours=1), periods=hours, freq="h")
    perp = np.broadcast_to(np.asarray(perp, float), (hours,)).copy()
    spot = perp.copy() if spot is None else np.broadcast_to(np.asarray(spot, float), (hours,)).copy()
    return pd.DataFrame({"perp": perp, "spot": spot}, index=idx)


def funding(rate, start="2020-12-01", end="2021-02-02") -> pd.Series:
    idx = pd.date_range(start, end, freq="8h")
    r = rate(idx) if callable(rate) else np.full(len(idx), rate, float)
    return pd.Series(r, index=idx)


def one_way_cost(notional, slip_bps):
    return notional * (v43.SPOT_FEE + slip_bps / 1e4) + notional * (v43.PERP_FEE + slip_bps / 1e4)


class CarryMathTests(unittest.TestCase):
    def test_c0_flat_price_collects_positive_funding_minus_cost(self):
        tr, _ = v43.simulate(panel(100.0), funding(0.0001), S_UTC, E_UTC, None, 2.0)
        self.assertEqual(len(tr), 1)
        t = tr.iloc[0]
        # 建倉 2021-01-01 01:00 UTC，期末 2021-01-31 16:00 UTC：(建倉, 平倉] 內 08/16/00 結算
        n = int(((funding(0.0).index > pd.Timestamp("2021-01-01 01:00")) &
                 (funding(0.0).index <= pd.Timestamp("2021-01-31 16:00"))).sum())
        self.assertEqual(t.n_settle, n)
        self.assertAlmostEqual(t.funding_usd, n * 2000 * 0.0001, places=6)
        self.assertAlmostEqual(t.basis_usd, 0.0, places=9)
        self.assertAlmostEqual(t.cost_usd, 2 * one_way_cost(2000, 2.0), places=6)
        self.assertEqual(t.reason, "period_end")

    def test_negative_funding_is_paid_by_short(self):
        tr, _ = v43.simulate(panel(100.0), funding(-0.0002), S_UTC, E_UTC, None, 0.0)
        self.assertLess(tr.iloc[0].funding_usd, 0)

    def test_price_move_hedged_basis_counts(self):
        p = np.linspace(100, 130, 24 * 31)             # 漲 30%（未達再平衡 35%）
        s = p + np.where(np.arange(len(p)) >= 48, 1.0, 0.0)   # 建倉（第 8 棒）後現貨溢價擴大 1 → 基差 +qty×1
        tr, _ = v43.simulate(panel(p, s), funding(0.0), S_UTC, E_UTC, None, 0.0)
        self.assertEqual(len(tr), 1)
        qty = 2000 / p[8]
        self.assertAlmostEqual(tr.iloc[0].basis_usd, qty * 1.0, places=6)

    def test_rebalance_when_perp_loss_hits_70pct_margin(self):
        p = np.linspace(100, 200, 24 * 31)             # 翻倍 → 至少一次再平衡
        tr, _ = v43.simulate(panel(p), funding(0.0), S_UTC, E_UTC, None, 0.0)
        reb = tr[tr.reason == "rebalance"]
        self.assertGreaterEqual(len(reb), 1)
        first = reb.iloc[0]
        self.assertGreaterEqual(first.qty * (first.perp_out - first.perp_in), 0.7 * v43.MARGIN_SYM - 1e-9)
        self.assertAlmostEqual(tr.basis_usd.sum(), 0.0, places=6)          # 兩腿同價：完全對沖
        self.assertAlmostEqual(tr.net_pnl.sum(), -tr.cost_usd.sum(), places=6)

    def test_daily_mtm_sums_to_realized(self):
        tr, eq = v43.simulate(panel(np.linspace(100, 120, 24 * 31)), funding(0.0003), S_UTC, E_UTC, None, 3.0)
        d = v43.daily_from_equity(eq)
        self.assertAlmostEqual(d.sum(), tr.net_pnl.sum(), places=6)


class TimingTests(unittest.TestCase):
    def test_c1_enters_when_high_and_exits_when_negative(self):
        # 1/1～1/10 高費率，之後轉負
        fr = funding(lambda idx: np.where(idx < pd.Timestamp("2021-01-10"), 0.0005, -0.0005))
        tr, _ = v43.simulate(panel(100.0), fr, S_UTC, E_UTC, 3, 0.0)
        self.assertEqual(len(tr), 1)
        t = tr.iloc[0]
        self.assertEqual(t.reason, "signal")
        self.assertEqual(t.entry_dt, pd.Timestamp("2021-01-01 01:00") + v43.TPE)
        self.assertEqual(t.exit_dt, pd.Timestamp("2021-01-11 01:00") + v43.TPE)   # 1/11 00:00 最近 3 次平均才 < 0

    def test_c1_stays_flat_in_middle_band(self):
        tr, _ = v43.simulate(panel(100.0), funding(0.00005), S_UTC, E_UTC, 9, 0.0)   # 0 < f̄ < 0.01%
        self.assertTrue(tr.empty)

    def test_decision_ignores_future_settlements(self):
        fr = funding(0.0)
        times = panel(100.0).index
        base = v43.desired_by_bar(times, fr, S_UTC, E_UTC, 3)
        fr2 = fr.copy()
        fr2[fr2.index > pd.Timestamp("2021-01-15")] = 0.01                  # 只改未來
        alt = v43.desired_by_bar(times, fr2, S_UTC, E_UTC, 3)
        cut = int(times.searchsorted(pd.Timestamp("2021-01-15 01:00")))
        self.assertEqual({k: v for k, v in base.items() if k <= cut}, {k: v for k, v in alt.items() if k <= cut})

    def test_plateau_choice_prefers_longer_on_tie(self):
        n, _ = v43.plateau_choice({9: 100.0, 21: 100.0, 42: 100.0})
        self.assertEqual(n, 42)
        n, _ = v43.plateau_choice({9: -500.0, 21: 300.0, 42: 200.0})
        self.assertEqual(n, 42)                                              # 21：中位 200；42：中位 250


class HoldoutGuardTests(unittest.TestCase):
    def test_guard_requires_flag_and_pre_gates_and_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(v43, "OUT_DIR", out), mock.patch.object(v43, "HOLDOUT_LOG", out / "holdout_log.txt"):
                with self.assertRaises(SystemExit):
                    v43.holdout_guard("c0", False)
                with self.assertRaises(SystemExit):                          # 沒有 dev／val
                    v43.holdout_guard("c0", True)
                good = {"total": {"net": 500.0, "apr": 0.08}}
                (out / "c0_dev_summary.json").write_text(json.dumps(good))
                (out / "c0_val_summary.json").write_text(json.dumps(good))
                v43.holdout_guard("c0", True)
                v43.holdout_record("c0", "always-on")
                with self.assertRaises(SystemExit):
                    v43.holdout_guard("c0", True)

    def test_guard_rejects_low_apr(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with mock.patch.object(v43, "OUT_DIR", out), mock.patch.object(v43, "HOLDOUT_LOG", out / "holdout_log.txt"):
                s = {"total": {"net": 100.0, "apr": 0.01}}
                (out / "c0_dev_summary.json").write_text(json.dumps(s))
                (out / "c0_val_summary.json").write_text(json.dumps(s))
                with self.assertRaises(SystemExit):
                    v43.holdout_guard("c0", True)


if __name__ == "__main__":
    unittest.main()
