"""V42 篩選腳本：B 的排名／換倉成本／funding 方向，A 與現行引擎一致，保留期只准一次。全部合成資料。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backtest" / "research"))
import v14_export_trades as engine  # noqa: E402
import v42_screen as v42  # noqa: E402

SYMS = [f"C{i}USDT" for i in range(10)]


def synth_candles(n=3000, seed=7) -> pd.DataFrame:
    """波動輪流壓縮／放大的隨機漫步，讓 GK 壓縮突破訊號出現。"""
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 60) % 2 == 0, 0.002, 0.012)
    ret = rng.normal(0, vol)
    c = 2000 * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]]
    spread = np.abs(rng.normal(0, vol)) * c
    h = np.maximum(o, c) + spread
    lo = np.minimum(o, c) - spread
    dt = pd.date_range("2021-01-01", periods=n, freq="h")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1.0,
                         "taker_buy_volume": 0.5, "datetime": dt})


def flat_panel(prices: dict, start="2021-01-04", hours=24 * 7 * 4) -> pd.DataFrame:
    idx = pd.date_range(start, periods=hours, freq="h")
    return pd.DataFrame({s: np.full(len(idx), p, float) for s, p in prices.items()}, index=idx)


class BRankingTests(unittest.TestCase):
    def test_top_and_bottom_three(self):
        idx = pd.date_range("2021-01-01", periods=24 * 30, freq="h")
        rets = np.linspace(-0.2, 0.25, 10)                  # C0 最差 … C9 最好
        raw = pd.DataFrame({s: np.linspace(100, 100 * (1 + r), len(idx)) for s, r in zip(SYMS, rets)}, index=idx)
        t = idx[-1]
        longs, shorts = v42.momentum_pick(raw, t, 14)
        self.assertEqual(longs, ["C9USDT", "C8USDT", "C7USDT"])
        self.assertEqual(shorts, ["C0USDT", "C1USDT", "C2USDT"])

    def test_too_few_valid_symbols_goes_flat(self):
        idx = pd.date_range("2021-01-01", periods=24 * 20, freq="h")
        raw = pd.DataFrame({s: 100.0 for s in SYMS}, index=idx)
        raw.iloc[:, 5:] = np.nan
        self.assertEqual(v42.momentum_pick(raw, idx[-1], 14), ([], []))

    def test_missing_lookback_bar_goes_flat(self):
        idx = pd.date_range("2021-01-01", periods=24 * 10, freq="h")
        raw = pd.DataFrame({s: 100.0 for s in SYMS}, index=idx)
        self.assertEqual(v42.momentum_pick(raw, idx[-1], 14), ([], []))   # 回看超出資料

    def test_plateau_choice_uses_neighbor_median(self):
        L, score = v42.plateau_choice({14: 500.0, 28: 100.0, 56: 90.0})
        self.assertEqual(score, {14: 300.0, 28: 100.0, 56: 95.0})
        self.assertEqual(L, 14)
        L, _ = v42.plateau_choice({14: 900.0, 28: -50.0, 56: 80.0})     # 孤峰 14 → median(900,-50)=425
        self.assertEqual(L, 14)
        L, _ = v42.plateau_choice({14: -900.0, 28: 50.0, 56: 80.0})
        self.assertEqual(L, 56)
        self.assertEqual(v42.neighbors(28), [14, 56])
        self.assertEqual(v42.neighbors(14), [28])


class BCostTests(unittest.TestCase):
    def setUp(self):
        self.raw = flat_panel({s: 100.0 for s in SYMS})
        self.fr = pd.DataFrame(columns=SYMS, index=pd.DatetimeIndex([]), dtype=float)
        self.times = [pd.Timestamp("2021-01-04"), pd.Timestamp("2021-01-11"), pd.Timestamp("2021-01-18")]
        self.slip = {s: 4 / 1e4 for s in SYMS}
        self.leg = 4000 / 2 / 3

    def picker(self, plan):
        return lambda t: plan[t]

    def test_only_changed_positions_pay_cost(self):
        a, b = self.times[0], self.times[1]
        plan = {a: (["C0USDT", "C1USDT", "C2USDT"], ["C7USDT", "C8USDT", "C9USDT"]),
                b: (["C0USDT", "C1USDT", "C3USDT"], ["C7USDT", "C8USDT", "C9USDT"]),   # 換一支多單
                self.times[2]: (["C0USDT", "C1USDT", "C3USDT"], ["C7USDT", "C8USDT", "C9USDT"])}
        wk, pos = v42.simulate_b(self.raw, self.fr, self.times, self.picker(plan), slip_map=self.slip)
        rate = v42.TAKER + 4 / 1e4
        self.assertAlmostEqual(wk.cost_usd.iloc[0], 6 * self.leg * rate, places=6)       # 建倉
        self.assertAlmostEqual(wk.cost_usd.iloc[1], 2 * self.leg * rate, places=6)       # 平 C2、開 C3
        self.assertAlmostEqual(wk.cost_usd.iloc[2], 6 * self.leg * rate, places=6)       # 不變＋期末全平
        self.assertTrue((wk.gross_usd == 0).all())
        self.assertAlmostEqual(wk.net_pnl.sum(), -14 * self.leg * rate, places=6)
        self.assertEqual(len(pos), 18)
        self.assertAlmostEqual(pos.notional[pos.side == "L"].sum(), 9 * self.leg, places=1)

    def test_drift_is_rebalanced_and_long_short_pnl_signs(self):
        raw = self.raw.copy()
        raw.loc[raw.index > self.times[0], "C0USDT"] = 110.0      # 多單 +10%
        raw.loc[raw.index > self.times[0], "C9USDT"] = 110.0      # 空單 −10%
        t = self.times[:2]
        plan = {x: (["C0USDT", "C1USDT", "C2USDT"], ["C7USDT", "C8USDT", "C9USDT"]) for x in t}
        wk, _ = v42.simulate_b(raw, self.fr, t, self.picker(plan), slip_map=self.slip)
        self.assertAlmostEqual(wk.gross_usd.iloc[0], 0.0, places=6)       # +10% 多 與 −10% 空 抵銷
        rate = v42.TAKER + 4 / 1e4
        # 第二週：C0 漂移到 1.1×leg 需減碼 0.1×leg；C9 空單漂移到 −1.1×leg 需回補 0.1×leg
        self.assertAlmostEqual(wk.cost_usd.iloc[1] - 6.2 * self.leg * rate, 0.0, places=6)

    def test_funding_long_pays_short_receives(self):
        fr = pd.DataFrame({s: [0.001] for s in SYMS}, index=[pd.Timestamp("2021-01-04 08:00")])
        t = self.times[:1]
        plan = {t[0]: (["C0USDT", "C1USDT", "C2USDT"], ["C7USDT", "C8USDT", "C9USDT"])}
        _, pos = v42.simulate_b(self.raw, fr, t, self.picker(plan), slip_map=self.slip)
        self.assertTrue(np.allclose(pos.funding_usd[pos.side == "L"], -self.leg * 0.001, atol=1e-3))
        self.assertTrue(np.allclose(pos.funding_usd[pos.side == "S"], self.leg * 0.001, atol=1e-3))

    def test_btc_slippage_is_two_bp(self):
        self.assertEqual(v42.slip_bps("BTCUSDT"), 2.0)
        self.assertEqual(v42.slip_bps("SOLUSDT"), 4.0)


class ATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = synth_candles()
        cls.start, cls.end = "2021-01-20", "2021-04-20"
        cls.empty_f = pd.DataFrame({"funding_time_ms": pd.Series(dtype="int64"), "rate": pd.Series(dtype=float)})

    def direct_engine(self, slip):
        ind = engine.compute_indicators(self.c)
        dts = self.c.datetime.to_numpy()
        sb = int(np.searchsorted(dts, np.datetime64(pd.Timestamp(self.start))))
        tr = pd.DataFrame(engine.simulate_v14_detailed(ind, dts, start_bar=sb, realistic=True,
                                                       slip_bps=slip, extra_cost=5.0))
        tr["entry_dt"] = pd.to_datetime(tr["entry_dt"])
        return tr[(tr.entry_dt >= self.start) & (tr.entry_dt < self.end)].reset_index(drop=True)

    def test_matches_current_engine_trade_by_trade(self):
        ref = self.direct_engine(0.0)
        got = v42.a_trades(self.c, self.empty_f, self.start, self.end, 0.0)
        self.assertGreater(len(ref), 3, "合成資料應產生交易")
        cols = ["side", "entry_bar", "exit_bar", "entry_price", "exit_price", "pnl_usd", "exit_reason"]
        pd.testing.assert_frame_equal(got[cols], ref[cols])
        self.assertTrue((got.funding_usd == 0).all())
        self.assertTrue(np.allclose(got.net_pnl, ref.pnl_usd))

    def test_slippage_passed_through(self):
        ref = self.direct_engine(4.0)
        got = v42.a_trades(self.c, self.empty_f, self.start, self.end, 4.0)
        self.assertTrue(np.allclose(got.pnl_usd.to_numpy(), ref.pnl_usd.to_numpy()))
        self.assertLess(got.pnl_usd.sum(), v42.a_trades(self.c, self.empty_f, self.start, self.end, 0.0).pnl_usd.sum())

    def test_funding_sign_and_window(self):
        close_ms = ((self.c.datetime - pd.Timedelta(hours=7) - pd.Timestamp("1970-01-01"))
                    // pd.Timedelta(milliseconds=1)).to_numpy("int64")
        f = pd.DataFrame({"funding_time_ms": close_ms[::8], "rate": 0.0001})      # 每 8 根結算一次
        got = v42.a_trades(self.c, f, self.start, self.end, 0.0)
        closes = self.c.close.to_numpy()
        for t in got.itertuples():
            js = [j for j in range(t.entry_bar + 1, t.exit_bar + 1) if j % 8 == 0]
            qty = t.margin * 20 / t.entry_price
            exp = (-1 if t.side == "L" else 1) * qty * sum(closes[j] * 0.0001 for j in js)
            self.assertAlmostEqual(t.funding_usd, exp, places=3)
        self.assertTrue((got.funding_usd[got.side == "L"] <= 0).all())
        self.assertTrue((got.funding_usd[got.side == "S"] >= 0).all())


class DataParseTests(unittest.TestCase):
    """v42_data 解析 data.binance.vision 檔（離線、記憶體內 zip）。"""

    @staticmethod
    def zip_of(text: str) -> bytes:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("x.csv", text)
        return buf.getvalue()

    def test_kline_with_and_without_header_and_microseconds(self):
        import v42_data
        row_ms = "1609459200000,1,2,0.5,1.5,10,1609462799999,15,3,4,6,0"
        row_us = "1609459200000000,1,2,0.5,1.5,10,1609462799999999,15,3,4,6,0"
        header = ",".join(v42_data.KLINE_COLS)
        for text in (row_ms + "\n", header + "\n" + row_ms + "\n", header + "\n" + row_us + "\n"):
            df = v42_data.parse_klines(v42_data.read_zip_csv(self.zip_of(text), v42_data.KLINE_COLS))
            self.assertEqual(len(df), 1)
            self.assertEqual(int(df.open_time.iloc[0]), 1609459200000)
            self.assertEqual(float(df.taker_buy_volume.iloc[0]), 4.0)

    def test_months_cover_warmup_to_holdout_end(self):
        import v42_data
        ms = v42_data.months()
        self.assertEqual((ms[0], ms[-1]), ("2020-11", "2026-10"))
        self.assertIn("ETHUSDT", v42_data.SYMBOLS)
        self.assertEqual(len(set(v42_data.B_SYMBOLS)), 10)


class HoldoutGuardTests(unittest.TestCase):
    def test_second_holdout_run_refused(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "holdout_log.txt"
            with mock.patch.object(v42, "HOLDOUT_LOG", log), mock.patch.object(v42, "OUT_DIR", Path(d)):
                with self.assertRaises(SystemExit):
                    v42.holdout_guard("a", False)               # 沒加旗標
                v42.holdout_guard("a", True)
                v42.holdout_record("a", "symbols=BTCUSDT")
                with self.assertRaises(SystemExit):
                    v42.holdout_guard("a", True)
                v42.holdout_guard("b", True)                    # 各候選分開計次


if __name__ == "__main__":
    unittest.main()
