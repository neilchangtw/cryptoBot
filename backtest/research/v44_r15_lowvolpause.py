"""V44 第 15 輪：低波動時整個策略暫停（L、S 都不開新倉）。

預登記：doc/v44_maxhold_reframe_plan.md 第 18 節。相似期比對顯示 2026-08 之後最像 2023 下半年低波動盤整：
TP 走不到、大量拖成 MH。出場與訊號全部不變，只在訊號棒多一個條件：
  V = 過去 w 天（w×24 根，不含當根）ATR14% 的中位數；
  R = V 在過去 730 天 V 值中的百分位（只用當時之前，至少 180 天）；
  R < q → 暫停（不開新倉，已持倉照原規則出場）。
候選：中心 Q20W30（q=20、w=30）；鄰域 Q10W30、Q30W30、Q20W14。
探索 A075：V < 0.75%（絕對門檻，是看過相似期結果才定的，只決定是否值得另立預登記）。
期間 P1 = 進場 < 2024-07-01，P2 = 之後。另列 2026-08 之後的影響（參考，不列門檻）。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r15_lowvolpause.py
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-08-01")
CANDS = {"Q20W30": (20, 30), "Q10W30": (10, 30), "Q30W30": (30, 30), "Q20W14": (20, 14), "A075": (None, 30)}
CENTER, NEIGHBORS = "Q20W30", ("Q10W30", "Q30W30", "Q20W14")


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
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


def vol_state(candles: pd.DataFrame, w: int):
    h, l, c = (candles[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atrp = pd.Series(tr).rolling(14, min_periods=14).mean() / c * 100
    v = atrp.shift(1).rolling(w * 24, min_periods=w * 24).median()
    rank = v.rolling(730 * 24, min_periods=180 * 24).rank(pct=True) * 100     # 含當根 v（v 本身只用過去資料）
    return v.to_numpy(), rank.to_numpy()


def ok_mask(candles, q, w):
    v, rank = vol_state(candles, w)
    if q is None:
        return ~(v < 0.75)
    return ~(rank < q)                                                          # NaN → 不暫停


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
    mh = g.exit_reason == "MH"
    return dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(), mhn=int(mh.sum()),
                mh=g.pnl_usd[mh].sum(), tpn=int((g.exit_reason == "TP").sum()), mdd=mdd)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    masks = {name: ok_mask(candles, q, w) for name, (q, w) in CANDS.items()}
    t = pd.to_datetime(dt)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")
    print("\n[暫停時間占比]")
    for name, m in masks.items():
        cells = [f"{pn} {(~m[sel]).mean() * 100:4.1f}%" for pn, sel in
                 (("P1", (np.arange(len(t)) >= sb) & (t < P_END).to_numpy()), ("P2", (t >= P_END).to_numpy()),
                  ("2026-08起", (t >= RECENT).to_numpy()))]
        print(f"  {name:7s} " + "  ".join(cells))
    v30, _ = vol_state(candles, 30)
    print(f"  參考：目前 30 天 ATR 中位 {v30[-1]:.2f}%")

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易：暫停期間 vs 平常（0bp）]")
    for name in CANDS:
        ok = masks[name][base0.entry_bar.astype(int).to_numpy()]
        cells = []
        for lab, m in (("平常", ok), ("暫停期", ~ok)):
            for pn, pm in (("P1", base0.entry_dt < P_END), ("P2", base0.entry_dt >= P_END)):
                s = part(base0[m & pm.to_numpy()])
                cells.append(f"{lab}{pn} {s['n']:3d}筆 WR {s['wr']:4.0f}% MH {s['mhn'] / max(s['n'], 1) * 100:3.0f}% ${s['pnl']:6,.0f}")
        print(f"  {name:7s} " + " | ".join(cells))

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後 | 2026-08 之後")
        for name in ["基準"] + list(CANDS):
            extra = {} if name == "基準" else {"okL": masks[name], "okS": masks[name]}
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, extra, slip)
            a, b, r = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END]), part(f[f.entry_dt >= RECENT])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:7s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} MDD ${b['mdd']:5,.0f}"
                  f" | {r['n']:3d}筆 ${r['pnl']:6,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r15_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 Q20W30]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    b, c = res[(0, "基準")][0], res[(0, CENTER)][0]
    ok["P1 MH少虧"] = c["mh"] > b["mh"]
    ok["P1 MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    ok["P2 0bp PnL勝"] = res[(0, CENTER)][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    ok["P2 2bp PnL勝"] = res[(2, CENTER)][1]["pnl"] > res[(2, "基準")][1]["pnl"]
    ok["P2 MDD不增逾10%"] = res[(0, CENTER)][1]["mdd"] <= res[(0, "基準")][1]["mdd"] * 1.1
    nb = sum(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    ok["鄰域P1至少2/3勝"] = nb >= 2
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域 P1 0bp 勝基準 {nb}/3")
    ax = all(res[(s, "A075")][0]["pnl"] > res[(s, "基準")][0]["pnl"] for s in (0, 2, 5)) and res[(0, "A075")][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    print(f"  探索 A075：{'P1 三種滑價與 P2 0bp 都勝 → 值得另立預登記' if ax else '未全勝 → 不追'}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
