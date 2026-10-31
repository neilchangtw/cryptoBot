"""V44 第 12 輪：進場時資金費率「過度擁擠」就不做（2021～2024 長歷史重測 V30 的結論）。

預登記：doc/v44_maxhold_reframe_plan.md 第 15 節。出場全部不變，只在訊號棒多一個條件：
  L：最近一次已結算 funding ≥ 過去 90 天（270 次結算）的第 q 百分位 → 不做（多方太擠）
  S：最近一次已結算 funding ≤ 第 (100-q) 百分位 → 不做（空方太擠）
候選：中心 F80（q=80）；鄰域 F70、F90。百分位只用當時之前的資料。
時間對齊（保守）：K 線 datetime 不論是 UTC 或 UTC+8 標記，都只用 fundingTime ≤ datetime − 7h（視為 UTC）的費率，
  最多比實際可得資訊晚一次結算，不會看到未來。
期間 P1 = 進場 < 2024-07-01（V30 只看過 2024-05 之後，主要判定），P2 = 之後（V30 看過，次要）。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r12_funding.py fetch     # 下載 ETHUSDT funding → data/v44/ETH_funding.csv
    python backtest/research/v44_r12_funding.py run
"""

from __future__ import annotations

import argparse
import inspect
import time
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

ROOT = Path(__file__).resolve().parents[2]
FUND_CSV = ROOT / "data" / "v44" / "ETH_funding.csv"
P_END = pd.Timestamp("2024-07-01")
CANDS = {"F80": 80, "F70": 70, "F90": 90}
CENTER, NEIGHBORS = "F80", ("F70", "F90")
WIN = 270


def fetch() -> None:
    import requests
    FUND_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows, start = [], int(pd.Timestamp("2020-12-01", tz="UTC").timestamp() * 1000)
    while True:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": "ETHUSDT", "startTime": start, "limit": 1000}, timeout=30)
        r.raise_for_status()
        d = r.json()
        if not d:
            break
        rows += d
        start = int(d[-1]["fundingTime"]) + 1
        if len(d) < 1000:
            break
        time.sleep(0.3)
    f = pd.DataFrame(rows)[["fundingTime", "fundingRate"]].drop_duplicates("fundingTime")
    f.to_csv(FUND_CSV, index=False)
    t = pd.to_datetime(f.fundingTime, unit="ms")
    print(f"funding {len(f)} 筆 {t.min()}～{t.max()} (UTC) → {FUND_CSV}")


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


def funding_state(dt: pd.Series):
    f = pd.read_csv(FUND_CSV).sort_values("fundingTime")
    ft = pd.to_datetime(f.fundingTime, unit="ms").to_numpy()
    fr = f.fundingRate.astype(float).to_numpy()
    cut = (pd.to_datetime(dt) - pd.Timedelta(hours=7)).to_numpy()
    idx = np.searchsorted(ft, cut, side="right") - 1          # 最近一次已結算（保守）
    last = np.where(idx >= 0, fr[np.clip(idx, 0, None)], np.nan)
    pct = {}
    for q in sorted(set(CANDS.values())):
        hi = pd.Series(fr).rolling(WIN, min_periods=90).quantile(q / 100).to_numpy()
        lo = pd.Series(fr).rolling(WIN, min_periods=90).quantile(1 - q / 100).to_numpy()
        h = np.where(idx >= 0, hi[np.clip(idx, 0, None)], np.nan)
        l_ = np.where(idx >= 0, lo[np.clip(idx, 0, None)], np.nan)
        # 百分位含最近一次本身（都是已結算資料）；缺值時不過濾
        pct[q] = (~(last >= h), ~(last <= l_))
    return last, pct


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


def main_run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    last, pct = funding_state(dt)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；交易期內 funding 缺值 {int(np.isnan(last[sb:]).sum())} 根；"
          f"P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易依 funding 是否「擁擠」分組（0bp）]")
    for name, q in CANDS.items():
        for side, k in (("L", 0), ("S", 1)):
            d = base0[base0.side == side]
            ok = pct[q][k][d.entry_bar.astype(int).to_numpy()]
            cells = []
            for lab, m in (("照做", ok), ("會被擋", ~ok)):
                for pn, pm in (("P1", d.entry_dt < P_END), ("P2", d.entry_dt >= P_END)):
                    s = part(d[m & pm.to_numpy()])
                    cells.append(f"{lab}{pn} {s['n']:3d}筆 WR {s['wr']:4.0f}% MH {s['mhn'] / max(s['n'], 1) * 100:3.0f}% ${s['pnl']:6,.0f}")
            print(f"  {name} {side} " + " | ".join(cells))

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(CANDS):
            extra = {} if name == "基準" else {"okL": pct[CANDS[name]][0], "okS": pct[CANDS[name]][1]}
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, extra, slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:4s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} TP {a['tpn']:3d}筆 MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} TP {b['tpn']:3d}筆 MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r12_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 F80]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    b, c = res[(0, "基準")][0], res[(0, CENTER)][0]
    ok["P1 MH少虧"] = c["mh"] > b["mh"]
    ok["P1 WR不掉逾1pp"] = c["wr"] >= b["wr"] - 1.0
    ok["P1 MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    ok["P2 0bp PnL勝"] = res[(0, CENTER)][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    ok["鄰域P1同向"] = all(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "run"])
    a = ap.parse_args()
    fetch() if a.cmd == "fetch" else main_run()
