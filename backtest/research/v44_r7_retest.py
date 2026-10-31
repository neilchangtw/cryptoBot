"""V44 第 7 輪：過去「差一點」的 MaxHold 規則，用從沒被 MH 研究用過的 2021-03～2024-06 重測。

預登記：doc/v44_maxhold_reframe_plan.md 第 10 節。過去 ~80 輪 MH 研究都只用 2024-07 之後約 2 年資料
（約 80 筆 MH），本輪把當時的近似候選原樣搬到更早的 3.3 年（約 126 筆 MH）做真正的樣本外檢查。

候選（定義照原文件／程式）：
  C1 V31 S 淺虧寬限：S 到原 MH 時若 -1.5% ≤ 收盤報酬 ≤ 0，每根重新判斷，最多多抱 4 根
  C2 V33 S/MILD_UP MH 10→8
  C3 9/8 S+3h：S 一般與 UP 的 MH 各 +3
  C4 9/8 S+4h：各 +4
  C5 9/24 風控餘額回本寬限（L、S）：MH 到期虧損時，若「月虧額度」與「日虧額度」都還大於本筆虧損，
     最多再抱 4 根，期間淨損益轉正即平倉，否則 MH+4 收盤出場

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r7_retest.py
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

OOS_END = pd.Timestamp("2024-07-01")      # 早於所有 MH 研究的資料起點（V31 從 2024-07-23 起）
CANDS = {
    "C1 S淺虧寬限": {"S_GRACE": (0.015, 4)},
    "C2 S/MILD_UP MH8": {"S_MH_MAP": {"UP": 8, "MILD_UP": 8}},
    "C3 S+3h": {"S_MH_ADD": 3},
    "C4 S+4h": {"S_MH_ADD": 4},
    "C5 風控餘額回本寬限": {"HEADROOM": 4},
}


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        # S MH 根數可由 ind 覆寫
        ("s_mh_eff = _S_MH_BR.get(sp_regime, S_MH)",
         "s_mh_eff = ind.get('S_MH_MAP', _S_MH_BR).get(sp_regime, S_MH) + ind.get('S_MH_ADD', 0)"),
        # S 到期判斷：C1 淺虧寬限、C5 風控餘額寬限
        ("""                if not sp_ext:
                    if bh >= s_mh_eff:
                        if cpnl > 0:
                            sp_ext = True
                            sp_ext_bars = 0
                        else:
                            ex_price = s_mkt
                            ex_reason = 'MH'
""", """                if not sp_ext:
                    if sp_g and bh > s_mh_eff:
                        if cpnl * sp_ntl - sp_fee - extra_cost > 0:
                            ex_price = s_mkt
                            ex_reason = 'MHg'
                        elif bh >= s_mh_eff + ind['HEADROOM']:
                            ex_price = s_mkt
                            ex_reason = 'MH'
                    elif bh >= s_mh_eff:
                        if cpnl > 0:
                            sp_ext = True
                            sp_ext_bars = 0
                        elif 'S_GRACE' in ind and -ind['S_GRACE'][0] <= cpnl and bh < s_mh_eff + ind['S_GRACE'][1]:
                            pass
                        elif 'HEADROOM' in ind and not sp_g:
                            sc = sp_ntl / NOTIONAL
                            now = cpnl * sp_ntl - sp_fee - extra_cost
                            unit = abs(now)
                            if (s_m_pnl + now - CB_S_MONTH * sc > unit) and (d_pnl + now - CB_DAILY * sc > unit):
                                sp_g = True
                            else:
                                ex_price = s_mkt
                                ex_reason = 'MH'
                        else:
                            ex_price = s_mkt
                            ex_reason = 'MH'
"""),
        # L 到期判斷：C5
        ("""                    if not lp_ext:
                        if bh >= mh:
                            if cpnl > 0:
                                lp_ext = True
                                lp_ext_bars = 0
                            else:
                                ex_price = l_mkt
                                ex_reason = 'MH'
""", """                    if not lp_ext:
                        if lp_g and bh > mh:
                            if cpnl * lp_ntl - lp_fee - extra_cost > 0:
                                ex_price = l_mkt
                                ex_reason = 'MHg'
                            elif bh >= mh + ind['HEADROOM']:
                                ex_price = l_mkt
                                ex_reason = 'MH'
                        elif bh >= mh:
                            if cpnl > 0:
                                lp_ext = True
                                lp_ext_bars = 0
                            elif 'HEADROOM' in ind and not lp_g:
                                sc = lp_ntl / NOTIONAL
                                now = cpnl * lp_ntl - lp_fee - extra_cost
                                unit = abs(now)
                                if (l_m_pnl + now - CB_L_MONTH * sc > unit) and (d_pnl + now - CB_DAILY * sc > unit):
                                    lp_g = True
                                else:
                                    ex_price = l_mkt
                                    ex_reason = 'MH'
                            else:
                                ex_price = l_mkt
                                ex_reason = 'MH'
"""),
        ("lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            lp_g = False"),
        ("sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            sp_g = False"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old[:60]
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def run(sim, ind, dt, extra, slip):
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    f = pd.DataFrame(sim({**ind, **extra}, dt.to_numpy(), start_bar=sb,
                         realistic=True, slip_bps=slip, extra_cost=v40.EXTRA_COST))
    f["entry_dt"] = pd.to_datetime(f["entry_dt"])
    return f


def part(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    eq = np.cumsum(p)
    mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(p) else 0.0
    return dict(n=len(g), wr=(p > 0).mean() * 100, pnl=p.sum(), mhn=int(g.exit_reason.isin(["MH", "MHg"]).sum()),
                mh=g.pnl_usd[g.exit_reason.isin(["MH", "MHg"])].sum(), mdd=mdd)   # MHg = 寬限期內回本出場


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；樣本外 = 進場 < {OOS_END:%Y-%m-%d}")
    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  樣本外 2021-03～2024-06 | 參考：2024-07 之後（原研究用過）")
        for name, extra in [("基準", {})] + list(CANDS.items()):
            f = run(sim, ind, dt, extra, slip)
            o, r = part(f[f.entry_dt < OOS_END]), part(f[f.entry_dt >= OOS_END])
            res[(slip, name)] = (o, r, f)
            print(f"  {name:12s} {o['n']:3d}筆 WR {o['wr']:5.1f}% ${o['pnl']:7,.0f} MH {o['mhn']:3d}筆 ${o['mh']:7,.0f} MDD ${o['mdd']:5,.0f}"
                  f" | {r['n']:3d}筆 WR {r['wr']:5.1f}% ${r['pnl']:7,.0f} MH ${r['mh']:7,.0f}")

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(res[(0, "基準")][2].entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：樣本外 2021-03～2024-06]")
    for name in CANDS:
        ok = {}
        for slip in (0, 2, 5):
            b, c = res[(slip, "基準")][0], res[(slip, name)][0]
            ok[f"{slip}bp PnL勝"] = c["pnl"] > b["pnl"]
            ok[f"{slip}bp MH少虧"] = c["mh"] > b["mh"]
        b0, c0 = res[(0, "基準")][0], res[(0, name)][0]
        ok["WR不降"] = c0["wr"] >= b0["wr"]
        ok["MDD不增逾10%"] = c0["mdd"] <= b0["mdd"] * 1.1
        verdict = "PASS → SHADOW 候選" if all(ok.values()) else "REJECTED"
        print(f"  {name:12s} " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
