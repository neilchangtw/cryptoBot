"""V44 第 3 輪：波動時鐘 MaxHold（低波動時給更長持倉時間）。

預登記：doc/v44_maxhold_reframe_plan.md 第 6 節。只改 MH 根數，TP／SafeNet／MFE／延長／進場全部不變。
資料與口徑同 V41／V44 診斷：data/v39/ETH_1h_long.csv、固定 200U、realistic、每筆 +$5。
開發 2021-03～2024-10（entry < 2024-11-01），保留 2024-11～2026-10。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v44_r3_voltime.py
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
CANDIDATES = {                              # 名稱: (下限, 上限)
    "A1.25": (1.0, 1.25), "A1.5": (1.0, 1.5), "A2.0": (1.0, 2.0),          # 只在低波動拉長
    "B1.25": (0.8, 1.25), "B1.5": (1 / 1.5, 1.5), "B2.0": (0.5, 2.0),      # 低波動拉長、高波動縮短
}
CENTERS = ("A1.5", "B1.5")
NEIGHBORS = {"A1.5": ("A1.25", "A2.0"), "B1.5": ("B1.25", "B2.0")}


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
        ("l_mh_eff = _L_MH_BR.get(lp_regime, L_MH)",
         "l_mh_eff = int(round(_L_MH_BR.get(lp_regime, L_MH) * lp_mult))"),
        ("mh = L_CMH_MH if lp_reduced else l_mh_eff",
         "mh = int(round(L_CMH_MH * lp_mult)) if lp_reduced else l_mh_eff"),
        ("s_mh_eff = _S_MH_BR.get(sp_regime, S_MH)",
         "s_mh_eff = int(round(_S_MH_BR.get(sp_regime, S_MH) * sp_mult))"),
        ("lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            lp_mult = float(ind['mh_mult'][i]) if 'mh_mult' in ind else 1.0"),
        ("sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            sp_mult = float(ind['mh_mult'][i]) if 'mh_mult' in ind else 1.0"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def run(sim, ind, dt, mult, slip):
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    f = pd.DataFrame(sim({**ind, "mh_mult": mult}, dt.to_numpy(), start_bar=sb,
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
        for name, (lo, hi) in CANDIDATES.items():
            f = run(sim, ind, dt, np.clip(ratio, lo, hi), slip)
            res[(slip, name)] = summ(f)
            print(line(name, res[(slip, name)]))
            if slip == 0 and name in CENTERS:
                f.to_csv(OUT_DIR / f"r3_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(res[(0, "base")]["years"])
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ("base",) + tuple(CANDIDATES):
        print("  " + " ".join(f"{res[(0, name)]['years'].get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻]")
    for c in CENTERS:
        b0, c0 = res[(0, "base")], res[(0, c)]
        g1 = c0["dev"]["pnl"] >= b0["dev"]["pnl"] * 1.05 and c0["dev"]["mh"] > b0["dev"]["mh"]
        g2 = c0["hold"]["pnl"] >= b0["hold"]["pnl"]
        g3 = all(res[(0, nb)]["dev"]["pnl"] > b0["dev"]["pnl"] and res[(0, nb)]["hold"]["pnl"] >= b0["hold"]["pnl"]
                 for nb in NEIGHBORS[c])
        g4 = all(res[(s, c)]["all"]["pnl"] > res[(s, "base")]["all"]["pnl"] for s in (2, 5))
        g5 = c0["all"]["mdd"] <= b0["all"]["mdd"] * 1.2
        g6 = c0["all"]["wr"] >= b0["all"]["wr"] - 1.0
        flags = dict(G1開發改善且MH少虧=g1, G2保留不輸=g2, G3鄰域同向=g3, G4滑價穩健=g4, G5回撤=g5, G6勝率不掉逾1pp=g6)
        verdict = "PASS → 列 SHADOW 候選（不改實盤）" if all(flags.values()) else "REJECTED"
        print(f"  {c}: " + "  ".join(f"{k} {'✓' if v else '✗'}" for k, v in flags.items()) + f"  → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
