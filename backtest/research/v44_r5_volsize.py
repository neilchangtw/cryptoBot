"""V44 第 5 輪：依波動縮小部位（低波動時只用部分保證金）。

預登記：doc/v44_maxhold_reframe_plan.md 第 8 節。只改進場名目，所有進出場規則不變；最大名目不超過基準。
資料與口徑同 V41／V44 診斷：data/v39/ETH_1h_long.csv、固定 200U、realistic、每筆 +$5。
開發 2021-03～2024-10（entry < 2024-11-01），保留 2024-11～2026-10。

用法（VPS，cryptobot，~/cryptoBot）：
    python backtest/research/v44_r5_volsize.py
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "doc" / "research_results" / "v44"
SPLIT = pd.Timestamp("2024-11-01")
REF_WIN, REF_MIN = 24 * 365, 24 * 90       # 參考波動 = 過去一年 ATR14% 中位數（不含當根）
CANDIDATES = {                              # 名稱: 部位下限（部位 = clip(ATR/ATR_ref, 下限, 1)）
    "S0.5": 0.5, "S0.4": 0.4, "S0.67": 0.67,
}
CENTERS = ("S0.5",)
NEIGHBORS = {"S0.5": ("S0.4", "S0.67")}


def atr_ratio(candles: pd.DataFrame) -> np.ndarray:
    h, l, c = (candles[k].to_numpy(float) for k in ("high", "low", "close"))
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atrp = pd.Series(tr).rolling(14, min_periods=14).mean() / c
    ref = atrp.shift(1).rolling(REF_WIN, min_periods=REF_MIN).median()
    return (ref / atrp).to_numpy()          # >1 = 比過去一年平常更安靜


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        ("lp_ntl = NOTIONAL * cbs   # 名目/費用在進場 bar 定死（qty 同實盤不變）\n            lp_fee = FEE * cbs",
         "lp_ntl = NOTIONAL * cbs * float(ind['size_L'][i])\n            lp_fee = FEE * cbs * float(ind['size_L'][i])"),
        ("sp_ntl = NOTIONAL * cbs\n            sp_fee = FEE * cbs",
         "sp_ntl = NOTIONAL * cbs * float(ind['size_S'][i])\n            sp_fee = FEE * cbs * float(ind['size_S'][i])"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def run(sim, ind, dt, size, slip, size_s=None):
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    f = pd.DataFrame(sim({**ind, "size_L": size, "size_S": size if size_s is None else size_s}, dt.to_numpy(), start_bar=sb,
                         realistic=True, slip_bps=slip, extra_cost=v40.EXTRA_COST))
    f["entry_dt"] = pd.to_datetime(f["entry_dt"])
    return f


def summ(f: pd.DataFrame) -> dict:
    out = {}
    for name, g in (("dev", f[f.entry_dt < SPLIT]), ("hold", f[f.entry_dt >= SPLIT]), ("all", f)):
        p = g.pnl_usd.to_numpy()
        eq = np.cumsum(p)
        mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(p) else 0.0
        out[name] = dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(),
                         mh=g[g.exit_reason == "MH"].pnl_usd.sum(), mhn=int((g.exit_reason == "MH").sum()), mdd=mdd)
    out["years"] = f.groupby(f.entry_dt.dt.year).pnl_usd.sum().round(0).to_dict()
    return out


def line(name, s):
    d, h = s["dev"], s["hold"]
    return (f"  {name:6s} 開發 {d['n']:3d}筆 WR {d['wr']:5.1f}% ${d['pnl']:7,.0f} MH {d['mhn']:3d}筆 ${d['mh']:7,.0f} | "
            f"保留 {h['n']:3d}筆 WR {h['wr']:5.1f}% ${h['pnl']:7,.0f} MH {h['mhn']:3d}筆 ${h['mh']:7,.0f} | MDD ${s['all']['mdd']:,.0f}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    ratio = atr_ratio(candles)
    ratio = np.where(np.isnan(ratio), 1.0, ratio)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}，ATR/一年中位 比值：中位 {np.median(1 / ratio):.2f}")

    res = {}
    for slip in (0, 2, 5):
        base = summ(run(sim, ind, dt, np.ones(len(dt)), slip))
        res[(slip, "base")] = base
        print(f"\n[滑價 {slip}bp]")
        print(line("基準", base))
        for name, lo in CANDIDATES.items():
            f = run(sim, ind, dt, np.clip(1 / ratio, lo, 1.0), slip)
            res[(slip, name)] = summ(f)
            print(line(name, res[(slip, name)]))
            if slip == 0 and name in CENTERS:
                f.to_csv(OUT_DIR / f"r5_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(res[(0, "base")]["years"])
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ("base",) + tuple(CANDIDATES):
        print("  " + " ".join(f"{res[(0, name)]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    # 使用者提案：依 GK 分組勝率決定保證金（最高 500U、中 300U、低 200U）。分組切點與勝率排名只用開發期基準交易。
    print("\n[使用者提案] 依 GK 分組勝率配保證金 500/300/200U（切點與排名只用開發期）")
    b = run(sim, ind, dt, np.ones(len(dt)), 0)
    dev = b[b.entry_dt < SPLIT]
    sizes = {}
    for side, key in (("L", "pctile_L"), ("S", "pctile_S")):
        d = dev[dev.side == side]
        q = np.percentile(d.gk_pctile, [100 / 3, 200 / 3])
        grp = np.digitize(d.gk_pctile, q)
        wr = [((d.pnl_usd > 0)[grp == k]).mean() for k in range(3)]
        rank = np.argsort(wr)[::-1]                     # 勝率高 → 低
        mult = np.empty(3); mult[rank] = [2.5, 1.5, 1.0]
        print(f"  {side} GK 切點 {q.round(1)}，開發期勝率 深/中/淺 " + "/".join(f"{w * 100:.0f}%" for w in wr)
              + f" → 保證金 深/中/淺 " + "/".join(f"{int(m * 200)}U" for m in mult))
        sizes[side] = mult[np.digitize(np.nan_to_num(ind[key], nan=50.0), q)]
    for slip in (0, 2, 5):
        g = run(sim, ind, dt, sizes["L"], slip, sizes["S"])
        bb = run(sim, ind, dt, np.ones(len(dt)), slip)
        sg, sb = summ(g), summ(bb)
        avg_m = g.margin.mean()
        print(f"  {slip}bp 基準 " + line("", sb)[8:])
        print(f"  {slip}bp GK配 " + line("", sg)[8:] + f" | 平均保證金 {avg_m:.0f}U")
        print(f"        每 200U 換算：開發 ${sg['dev']['pnl'] * 200 / avg_m:,.0f} vs 基準 ${sb['dev']['pnl']:,.0f}；"
              f"保留 ${sg['hold']['pnl'] * 200 / avg_m:,.0f} vs ${sb['hold']['pnl']:,.0f}；"
              f"MH 桶/保證金 {sg['all']['mh'] / avg_m:.1f} vs {sb['all']['mh'] / 200:.1f}")
        if slip == 0:
            print("        逐年 GK配 " + "  ".join(f"{y}:{v:,.0f}" for y, v in sg["years"].items()))

    print("\n[預登記門檻]")
    for c in CENTERS:
        b0, c0 = res[(0, "base")], res[(0, c)]
        worst = lambda r: min(r["years"].values())
        g1 = worst(c0) > worst(b0) and c0["dev"]["mh"] > b0["dev"]["mh"]
        g2 = all(c0[p]["pnl"] >= b0[p]["pnl"] * 0.90 for p in ("dev", "hold"))
        g3 = c0["all"]["mdd"] <= b0["all"]["mdd"] * 0.90
        g4 = all(worst(res[(0, nb)]) > worst(b0) and res[(0, nb)]["all"]["mdd"] < b0["all"]["mdd"] for nb in NEIGHBORS[c])
        g5 = all(worst(res[(s, c)]) > worst(res[(s, "base")]) and res[(s, c)]["all"]["mdd"] < res[(s, "base")]["all"]["mdd"]
                 for s in (2, 5))
        g6 = all(c0[p]["pnl"] / max(c0["all"]["mdd"], 1) >= b0[p]["pnl"] / max(b0["all"]["mdd"], 1) for p in ("dev", "hold"))
        flags = dict(G1最差年改善且MH少虧=g1, G2各期PnL不少於90pct=g2, G3回撤降10pct=g3, G4鄰域同向=g4,
                     G5滑價穩健=g5, G6報酬回撤比不降=g6)
        verdict = "PASS → 列 SHADOW 候選（不改實盤）" if all(flags.values()) else "REJECTED"
        print(f"  {c}: " + "  ".join(f"{k} {'✓' if v else '✗'}" for k, v in flags.items()) + f"  → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
