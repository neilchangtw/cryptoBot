"""V44 第 6 輪：依進場 GK 分組勝率配保證金（使用者提案的正式檢定）。

預登記：doc/v44_maxhold_reframe_plan.md 第 9 節。進出場規則完全不變，只改每筆名目。
GK 分組（每方向三等分）與勝率排名只用「當時之前」的基準交易：
  - 靜態：切點與排名用開發期（進場 < 2024-11-01），套用全期
  - 滾動：每個日曆年用該年之前全部基準交易重算（2021 年用 1 倍）
比較一律換算成「每 200U 平均保證金」，排除加大曝險造成的差異。

用法（個人電腦 D:\\cryptoBot 或 VPS）：
    python backtest/research/v44_r6_gksize.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR, SPLIT, build_sim, run

SCHEMES = {                      # 名稱: (勝率最高, 中, 最低) 的保證金倍數（×200U）
    "N1": (1.5, 0.9, 0.6),       # 中心：平均約 1 倍（300/180/120U）
    "N1a": (1.3, 1.0, 0.7),      # 鄰域（較溫和）
    "N1b": (1.7, 0.8, 0.5),      # 鄰域（較激進）
    "U1": (2.5, 1.5, 1.0),       # 使用者原案 500/300/200U
    "U1a": (2.0, 1.5, 1.0),      # 400/300/200U
    "U1b": (2.5, 1.25, 1.0),     # 500/250/200U
}
NEIGHBORS = ("N1a", "N1b")
KEYS = {"L": "pctile_L", "S": "pctile_S"}


def rank_cuts(trades: pd.DataFrame, side: str):
    d = trades[trades.side == side]
    q = np.percentile(d.gk_pctile, [100 / 3, 200 / 3])
    grp = np.digitize(d.gk_pctile, q)
    wr = np.array([(d.pnl_usd[grp == k] > 0).mean() for k in range(3)])
    return q, wr, np.argsort(wr)[::-1]          # 勝率高 → 低 的組別


def size_static(ind, base, scheme):
    out = {}
    for side, key in KEYS.items():
        q, _, order = rank_cuts(base[base.entry_dt < SPLIT], side)
        mult = np.empty(3); mult[order] = scheme
        out[side] = mult[np.digitize(np.nan_to_num(ind[key], nan=50.0), q)]
    return out


def size_rolling(ind, dt, base, scheme):
    years = dt.dt.year.to_numpy()
    out = {s: np.ones(len(dt)) for s in KEYS}
    for y in sorted(set(years)):
        prior = base[base.entry_dt < pd.Timestamp(f"{y}-01-01")]
        if (prior.side == "L").sum() < 30 or (prior.side == "S").sum() < 30:
            continue
        m = years == y
        for side, key in KEYS.items():
            q, _, order = rank_cuts(prior, side)
            mult = np.empty(3); mult[order] = scheme
            out[side][m] = mult[np.digitize(np.nan_to_num(ind[key][m], nan=50.0), q)]
    return out


def norm(f: pd.DataFrame) -> dict:
    k = 200.0 / f.margin.mean()
    p = f.pnl_usd.to_numpy() * k
    eq = np.cumsum(p)
    mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    dev = f.entry_dt < SPLIT
    yrs = (f.pnl_usd * k).groupby(f.entry_dt.dt.year).sum().to_dict()
    return dict(n=len(f), avg=f.margin.mean(), dev=p[dev.to_numpy()].sum(), hold=p[~dev.to_numpy()].sum(),
                mdd=mdd, mh=(f.pnl_usd[f.exit_reason == "MH"] * k).sum(), wr=(f.pnl_usd > 0).mean() * 100, years=yrs)


def show(name, s):
    return (f"  {name:5s} {s['n']:3d}筆 均保證金 {s['avg']:4.0f}U WR {s['wr']:5.1f}% | 每200U：開發 ${s['dev']:7,.0f} "
            f"保留 ${s['hold']:7,.0f} MH桶 ${s['mh']:7,.0f} MDD ${s['mdd']:6,.0f}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    ones = np.ones(len(dt))
    base0 = run(sim, ind, dt, ones, 0)

    print("[GK 分組勝率穩定性：每年（基準交易，0bp），組別依開發期切點]")
    for side in ("L", "S"):
        q, wr, order = rank_cuts(base0[base0.entry_dt < SPLIT], side)
        d = base0[base0.side == side]
        grp = np.digitize(d.gk_pctile, q)
        print(f"  {side} 切點 {q.round(1)}，開發期勝率 深/中/淺 " + "/".join(f"{w * 100:.0f}%" for w in wr))
        for y, g in d.groupby(d.entry_dt.dt.year):
            gg = grp[d.entry_dt.dt.year.to_numpy() == y]
            cells = []
            for k in range(3):
                x = g.pnl_usd[gg == k]
                cells.append(f"{(x > 0).mean() * 100 if len(x) else float('nan'):3.0f}%({len(x):2d}筆 ${x.mean() if len(x) else 0:5.0f})")
            print(f"    {y} 深 {cells[0]}  中 {cells[1]}  淺 {cells[2]}")

    res = {}
    for slip in (0, 2, 5):
        base = run(sim, ind, dt, ones, slip)
        res[(slip, "base")] = norm(base)
        print(f"\n[滑價 {slip}bp]（靜態排名）")
        print(show("基準", res[(slip, "base")]))
        for name, sc in SCHEMES.items():
            sz = size_static(ind, base, sc)
            f = run(sim, ind, dt, sz["L"], slip, sz["S"])
            res[(slip, name)] = norm(f)
            print(show(name, res[(slip, name)]))
            if slip == 0 and name in ("N1", "U1"):
                f.to_csv(OUT_DIR / f"r6_{name}_static_trades.csv", index=False)

    print("\n[滾動排名，0bp，每年只用過去資料]")
    yrs = sorted(res[(0, "base")]["years"])
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱（每200U）")
    print("  " + " ".join(f"{res[(0, 'base')]['years'].get(y, 0):7,.0f}" for y in yrs) + "  基準")
    roll = {}
    for name in ("N1", "U1"):
        sz = size_rolling(ind, dt, base0, SCHEMES[name])
        f = run(sim, ind, dt, sz["L"], 0, sz["S"])
        roll[name] = norm(f)
        print("  " + " ".join(f"{roll[name]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name} 滾動")
    print("\n[靜態排名逐年，0bp，每200U]")
    for name in ("N1", "U1"):
        print("  " + " ".join(f"{res[(0, name)]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name} 靜態")

    print("\n[預登記門檻：N1（平均約 200U）]")
    b, c = res[(0, "base")], res[(0, "N1")]
    wf_years = [y for y in yrs if y >= 2022]
    wf_better = sum(roll["N1"]["years"].get(y, 0) > b["years"].get(y, 0) for y in wf_years)
    flags = {
        "G1保留期勝基準": c["hold"] > b["hold"],
        "G2開發期勝基準": c["dev"] > b["dev"],
        "G3鄰域同向": all(res[(0, n)]["hold"] > b["hold"] and res[(0, n)]["dev"] > b["dev"] for n in NEIGHBORS),
        "G4滑價穩健": all(res[(s, "N1")]["hold"] > res[(s, "base")]["hold"] and res[(s, "N1")]["dev"] > res[(s, "base")]["dev"]
                      for s in (2, 5)),
        "G5回撤不增": c["mdd"] <= b["mdd"],
        "G6滾動總和勝且過半年份勝": sum(roll["N1"]["years"].get(y, 0) for y in wf_years) > sum(b["years"].get(y, 0) for y in wf_years)
                             and wf_better * 2 > len(wf_years),
        "G7滾動最差年不更差": min(roll["N1"]["years"].get(y, 0) for y in wf_years) >= min(b["years"].get(y, 0) for y in wf_years),
    }
    verdict = "PASS → 列 SHADOW 候選（不改實盤）" if all(flags.values()) else "REJECTED"
    print("  " + "  ".join(f"{k} {'✓' if v else '✗'}" for k, v in flags.items()))
    print(f"  滾動 2022 起勝基準年份 {wf_better}/{len(wf_years)} → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
