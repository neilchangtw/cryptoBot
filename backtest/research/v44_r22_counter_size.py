"""V44 第 22 輪：逆勢單減碼。

預登記：doc/v44_maxhold_reframe_plan.md 第 25 節。R = close[i-1] / close[i-1-720] − 1。
逆勢（L 且 R<0、S 且 R>0）且 |R|≤15% → 保證金 × m，其餘 × 1。比較一律換算成每 200U 平均保證金（同第 13 輪）。
候選：中心 CT5（m=0.5）；鄰域 CT3、CT7、CTall（逆勢一律 × 0.5）。探索：疊加第 6 輪 N1 靜態版與第 15 輪 Q20W30。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r22_counter_size.py
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r6_gksize import SCHEMES, size_static
from v44_r13_fundsize import norm, show
from v44_r15_lowvolpause import ok_mask
from v44_r18_ltrendskip import past_return

CANDS = {"CT5": (0.5, True), "CT3": (0.3, True), "CT7": (0.7, True), "CTall": (0.5, False)}
CENTER, NEIGHBORS = "CT5", ("CT3", "CT7", "CTall")
PERIODS = ("P1", "P2")


def build_sim(engine):
    """同時支援每筆倍數（size_L/S，同第 5 輪）與擋單（okL/okS，同第 15 輪）。"""
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        ("lp_ntl = NOTIONAL * cbs   # 名目/費用在進場 bar 定死（qty 同實盤不變）\n            lp_fee = FEE * cbs",
         "lp_ntl = NOTIONAL * cbs * float(ind['size_L'][i])\n            lp_fee = FEE * cbs * float(ind['size_L'][i])"),
        ("sp_ntl = NOTIONAL * cbs\n            sp_fee = FEE * cbs",
         "sp_ntl = NOTIONAL * cbs * float(ind['size_S'][i])\n            sp_fee = FEE * cbs * float(ind['size_S'][i])"),
        ("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):",
         "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):"),
        ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):",
         "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def run(sim, ind, dt, size_l, size_s, slip, extra=None):
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    f = pd.DataFrame(sim({**ind, "size_L": size_l, "size_S": size_s, **(extra or {})}, dt.to_numpy(), start_bar=sb,
                         realistic=True, slip_bps=slip, extra_cost=v40.EXTRA_COST))
    f["entry_dt"] = pd.to_datetime(f["entry_dt"])
    return f


def ct_size(r, m, exclude):
    near = np.abs(r) <= 0.15 if exclude else np.ones(len(r), bool)
    return np.where((r < 0) & near, m, 1.0), np.where((r > 0) & near, m, 1.0)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    c = candles["close"].to_numpy(float)
    r = past_return(c, 30)
    ones = np.ones(len(c))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}")

    sizes = {name: ct_size(r, m, ex) for name, (m, ex) in CANDS.items()}
    base0 = run(sim, ind, dt, ones, ones, 0)
    n1 = size_static(ind, base0, SCHEMES["N1"])
    pause = ok_mask(candles, 20, 30)
    ct = sizes[CENTER]
    explore = {
        "N1": (n1["L"], n1["S"], None),
        "Q20": (ones, ones, {"okL": pause, "okS": pause}),
        "CT5+N1": (ct[0] * n1["L"], ct[1] * n1["S"], None),
        "CT5+Q20": (ct[0], ct[1], {"okL": pause, "okS": pause}),
        "全疊": (ct[0] * n1["L"], ct[1] * n1["S"], {"okL": pause, "okS": pause}),
    }

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]")
        res[(slip, "基準")] = norm(run(sim, ind, dt, ones, ones, slip))
        print(show("基準", res[(slip, "基準")]))
        for name, (sl, ss) in sizes.items():
            f = run(sim, ind, dt, sl, ss, slip)
            res[(slip, name)] = norm(f)
            print(show(name, res[(slip, name)]))
            if slip == 0:
                f.to_csv(OUT_DIR / f"r22_{name}_trades.csv", index=False)
        for name, (sl, ss, extra) in explore.items():
            res[(slip, name)] = norm(run(sim, ind, dt, sl, ss, slip, extra))
            print(show(name, res[(slip, name)]) + "（探索）")

    print("\n[逐年，0bp，每200U]")
    yrs = sorted(res[(0, "基準")]["years"])
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS) + list(explore):
        print("  " + " ".join(f"{res[(0, name)]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 CT5，每200U]")
    ok = {}
    for pn in PERIODS:
        for slip in (0, 2, 5):
            ok[f"{pn} {slip}bp PnL勝"] = res[(slip, CENTER)][pn]["pnl"] > res[(slip, "基準")][pn]["pnl"]
        ok[f"{pn} MDD不增"] = res[(0, CENTER)][pn]["mdd"] <= res[(0, "基準")][pn]["mdd"]
    nb = sum(res[(0, n)][pn]["pnl"] > res[(0, "基準")][pn]["pnl"] for n in NEIGHBORS for pn in PERIODS)
    ok["鄰域6格至少4格勝"] = nb >= 4
    wy = [y for y in yrs if y >= 2022]
    better = sum(res[(0, CENTER)]["years"].get(y, 0) > res[(0, "基準")]["years"].get(y, 0) for y in wy)
    ok["2022起過半年份勝"] = better * 2 > len(wy)
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域勝基準 {nb}/6 格；2022 起勝基準 {better}/{len(wy)} 年")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
