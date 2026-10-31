"""V44 第 10 輪：用 BTC 同方向當 ETH 突破的進場確認（降低 MH 單）。

預登記：doc/v44_maxhold_reframe_plan.md 第 13 節。訊號、出場全部不變，只在訊號棒多加一個 BTC 條件：
  L：BTC 近 n 根報酬 > 0；S：BTC 近 n 根報酬 < 0（用訊號棒「同根」BTC 收盤，與 ETH 同時收盤）。
候選：中心 B3（n=3）；鄰域 B1、B6；探索 BX（BTC 自己也在近 3 根內收盤突破前 15 根高／低）。
BTC 缺資料的棒不過濾（照原訊號），另列筆數。
期間 P1 = 進場 < 2024-07-01（BTC 條件從沒用過），P2 = 之後（9/23 MH 延長研究看過 BTC 特徵）。

資料：data/v44/BTC_1h_long.csv（與 ETH 長檔同來源、同欄位，用 datetime 對齊）。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r10_btcconfirm.py
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

ROOT = Path(__file__).resolve().parents[2]
BTC_CSV = ROOT / "data" / "v44" / "BTC_1h_long.csv"
P_END = pd.Timestamp("2024-07-01")
CANDS = ("B3", "B1", "B6", "BX")
CENTER, NEIGHBORS = "B3", ("B1", "B6")


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


def btc_close(dt: pd.Series) -> np.ndarray:
    b = pd.read_csv(BTC_CSV)
    b["datetime"] = pd.to_datetime(b["datetime"])
    s = b.drop_duplicates("datetime").set_index("datetime")["close"].astype(float)
    return pd.to_datetime(dt).map(s).to_numpy(float)


def filters(bc: np.ndarray) -> dict:
    out = {}
    s = pd.Series(bc)
    for n in (1, 3, 6):
        r = (s / s.shift(n) - 1).to_numpy()
        out[f"B{n}"] = (np.where(np.isnan(r), True, r > 0), np.where(np.isnan(r), True, r < 0))
    hi = s.shift(1).rolling(15, min_periods=15).max()
    lo = s.shift(1).rolling(15, min_periods=15).min()
    up = (s > hi).rolling(3, min_periods=1).max().astype(bool).to_numpy()
    dn = (s < lo).rolling(3, min_periods=1).max().astype(bool).to_numpy()
    miss = np.isnan(bc) | hi.isna().to_numpy()
    out["BX"] = (up | miss, dn | miss)
    return out


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
    bc = btc_close(dt)
    flt = filters(bc)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；BTC 缺值 {int(np.isnan(bc[sb:]).sum())} 根（交易期內）；"
          f"P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易依 BTC 條件分組（0bp）：條件成立 vs 不成立]")
    for name in CANDS:
        for side, k in (("L", 0), ("S", 1)):
            d = base0[base0.side == side]
            ok = flt[name][k][d.entry_bar.astype(int).to_numpy()]
            cells = []
            for lab, m in (("成立", ok), ("不成立", ~ok)):
                for pn, pm in (("P1", d.entry_dt < P_END), ("P2", d.entry_dt >= P_END)):
                    s = part(d[m & pm.to_numpy()])
                    cells.append(f"{lab}{pn} {s['n']:3d}筆 WR {s['wr']:4.0f}% MH {s['mhn'] / max(s['n'], 1) * 100:3.0f}% ${s['pnl']:6,.0f}")
            print(f"  {name} {side} " + " | ".join(cells))

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ("基準",) + CANDS:
            extra = {} if name == "基準" else {"okL": flt[name][0], "okS": flt[name][1]}
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, extra, slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:4s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} TP {a['tpn']:3d}筆 MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} TP {b['tpn']:3d}筆 MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r10_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ("基準",) + CANDS:
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 B3]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    b, c = res[(0, "基準")][0], res[(0, CENTER)][0]
    ok["P1 MH少虧"] = c["mh"] > b["mh"]
    ok["P1 WR不降"] = c["wr"] >= b["wr"]
    ok["P1 MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    ok["P2 0bp PnL勝"] = res[(0, CENTER)][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    ok["P2 2bp PnL勝"] = res[(2, CENTER)][1]["pnl"] > res[(2, "基準")][1]["pnl"]
    ok["鄰域P1同向"] = all(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    bx = all(res[(s, "BX")][0]["pnl"] > res[(s, "基準")][0]["pnl"] for s in (0, 2, 5)) and res[(0, "BX")][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    print(f"  探索 BX：{'P1 三種滑價與 P2 0bp 都勝 → 值得另行預登記' if bx else '未全勝 → 不追'}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
