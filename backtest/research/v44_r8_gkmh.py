"""V44 第 8 輪：進場當下依 GK 分組決定 MaxHold 長度（勝率低的組縮短 MH）。

預登記：doc/v44_maxhold_reframe_plan.md 第 11 節。第 7 輪顯示「到期才決定多抱」全族失敗；唯一通過的
第 6 輪是在進場時依 GK 分組判斷，所以本輪把判斷點移到進場：只改 MH 根數，TP／SafeNet／MFE／延長／進場不動。
GK 分組（每方向三等分）與勝率排名每個日曆年只用「該年之前」的基準交易重算（2021 年無過去資料 → 不調整）。

候選（勝率最低／中／最高 三組的 MH 根數增減，L 與 S 分開）：
  M1  中心：最低組 L -2、S -3，其餘不變
  M1a 鄰域：最低組 L -1、S -2
  M1b 鄰域：最低組 L -3、S -4
  M2  探索：M1 再加最高組 L +1、S +2（只判斷是否值得下一輪，不能單獨列 SHADOW）
MH 根數下限 2。期間 P1 = 進場 < 2024-07-01（MH 研究未用過），P2 = 之後。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r8_gkmh.py
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

P_END = pd.Timestamp("2024-07-01")
SCHEMES = {                      # 名稱: {方向: (勝率最低, 中, 最高) 的 MH 根數增減}
    "M1": {"L": (-2, 0, 0), "S": (-3, 0, 0)},
    "M1a": {"L": (-1, 0, 0), "S": (-2, 0, 0)},
    "M1b": {"L": (-3, 0, 0), "S": (-4, 0, 0)},
    "M2": {"L": (-2, 0, 1), "S": (-3, 0, 2)},
}
NEIGHBORS = ("M1a", "M1b")
KEYS = {"L": "pctile_L", "S": "pctile_S"}


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        ("l_mh_eff = _L_MH_BR.get(lp_regime, L_MH)",
         "l_mh_eff = max(2, _L_MH_BR.get(lp_regime, L_MH) + lp_off)"),
        ("mh = L_CMH_MH if lp_reduced else l_mh_eff",
         "mh = max(2, L_CMH_MH + lp_off) if lp_reduced else l_mh_eff"),
        ("s_mh_eff = _S_MH_BR.get(sp_regime, S_MH)",
         "s_mh_eff = max(2, _S_MH_BR.get(sp_regime, S_MH) + sp_off)"),
        ("lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "lp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            lp_off = int(ind['off_L'][i]) if 'off_L' in ind else 0"),
        ("sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"",
         "sp_regime = _classify_regime(slope[i]) if slope is not None else \"NA\"\n            sp_off = int(ind['off_S'][i]) if 'off_S' in ind else 0"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old
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


def rank_cuts(trades: pd.DataFrame, side: str):
    d = trades[trades.side == side]
    q = np.percentile(d.gk_pctile, [100 / 3, 200 / 3])
    grp = np.digitize(d.gk_pctile, q)
    wr = np.array([(d.pnl_usd[grp == k] > 0).mean() for k in range(3)])
    return q, np.argsort(wr)                      # 勝率低 → 高 的組別


def offsets(ind, dt, base, scheme):
    """每根 K 線的 MH 增減（進場時套用），每年只用該年之前的基準交易排名。另回傳每根的組別排名（0=最低）。"""
    years = dt.dt.year.to_numpy()
    out = {f"off_{s}": np.zeros(len(dt), dtype=int) for s in KEYS}
    rank = {s: np.full(len(dt), -1) for s in KEYS}
    for y in sorted(set(years)):
        prior = base[base.entry_dt < pd.Timestamp(f"{y}-01-01")]
        if (prior.side == "L").sum() < 30 or (prior.side == "S").sum() < 30:
            continue
        m = years == y
        for side, key in KEYS.items():
            q, order = rank_cuts(prior, side)
            r = np.empty(3, dtype=int); r[order] = np.arange(3)       # 組別 → 勝率排名
            g = r[np.digitize(np.nan_to_num(ind[key][m], nan=50.0), q)]
            rank[side][m] = g
            out[f"off_{side}"][m] = np.asarray(scheme[side])[g]
    return out, rank


def part(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    eq = np.cumsum(p)
    mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(p) else 0.0
    mh = g.exit_reason == "MH"
    return dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(),
                mhn=int(mh.sum()), mh=g.pnl_usd[mh].sum(), mdd=mdd)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    base0 = run(sim, ind, dt, {}, 0)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    offs = {name: offsets(ind, dt, base0, sc) for name, sc in SCHEMES.items()}
    _, rank = offs["M1"]
    print("\n[基準交易依滾動 GK 勝率排名分組（0bp）]")
    for side in ("L", "S"):
        d = base0[base0.side == side]
        r = rank[side][d.entry_bar.astype(int).to_numpy()]
        for k, lab in ((-1, "未分組(首年)"), (0, "勝率最低"), (1, "中"), (2, "勝率最高")):
            g = d[r == k]
            if len(g):
                s = part(g)
                print(f"  {side} {lab:10s} {s['n']:3d}筆 WR {s['wr']:5.1f}% ${s['pnl']:7,.0f} MH {s['mhn']:3d}筆 ${s['mh']:7,.0f}")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(SCHEMES):
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, {} if name == "基準" else offs[name][0], slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:4s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r8_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(SCHEMES):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：P1 與 P2 都要成立]")
    for name in ("M1", "M2"):
        ok = {}
        for pi, pn in ((0, "P1"), (1, "P2")):
            for slip in (0, 2, 5):
                ok[f"{pn}{slip}bp PnL勝"] = res[(slip, name)][pi]["pnl"] > res[(slip, "基準")][pi]["pnl"]
            b, c = res[(0, "基準")][pi], res[(0, name)][pi]
            ok[f"{pn}MH少虧"] = c["mh"] > b["mh"]
            ok[f"{pn}WR不掉逾1pp"] = c["wr"] >= b["wr"] - 1.0
            ok[f"{pn}MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
        if name == "M1":
            ok["鄰域同向"] = all(res[(0, n)][pi]["pnl"] > res[(0, "基準")][pi]["pnl"] for n in NEIGHBORS for pi in (0, 1))
            verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
        else:
            verdict = "全過 → 值得下一輪預登記" if all(ok.values()) else "REJECTED"
        print(f"  {name}: " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
