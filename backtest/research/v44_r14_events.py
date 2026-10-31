"""V44 第 14 輪：總經事件（FOMC 決議、CPI 公布）前後不做新突破。

預登記：doc/v44_maxhold_reframe_plan.md 第 17 節。出場全部不變，只在訊號棒多一個條件：
  訊號棒收盤時間（UTC）落在任一事件 [事件前 b 小時, 事件後 a 小時] 內 → 不做。
事件時間：FOMC 決議 14:00 美東、CPI 08:30 美東（依美國夏令時間換成 UTC）。
候選：中心 E24（b=24、a=2，FOMC+CPI）；鄰域 E12、E48（b=12、48）；探索 F24（只有 FOMC）。
期間 P1 = 進場 < 2024-07-01（主要），P2 = 之後。
資料：data/v44/macro_events.csv（欄位 date,type；type = FOMC 或 CPI）。K 線 datetime 是 UTC+8 開盤標記。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r14_events.py
"""

from __future__ import annotations

import inspect
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

ROOT = Path(__file__).resolve().parents[2]
EV_CSV = ROOT / "data" / "v44" / "macro_events.csv"
P_END = pd.Timestamp("2024-07-01")
ET_TIME = {"FOMC": (14, 0), "CPI": (8, 30)}
CANDS = {"E24": (24, 2, ("FOMC", "CPI")), "E12": (12, 2, ("FOMC", "CPI")), "E48": (48, 2, ("FOMC", "CPI")),
         "F24": (24, 2, ("FOMC",))}
CENTER, NEIGHBORS = "E24", ("E12", "E48")


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


def event_times(types) -> np.ndarray:
    ev = pd.read_csv(EV_CSV)
    ny, out = ZoneInfo("America/New_York"), []
    for d, t in zip(ev["date"], ev["type"]):
        if t not in types:
            continue
        hh, mm = ET_TIME[t]
        ts = pd.Timestamp(f"{d} {hh:02d}:{mm:02d}").tz_localize(ny).tz_convert("UTC").tz_localize(None)
        out.append(ts)
    return np.array(sorted(out), dtype="datetime64[ns]")


def ok_mask(dt: pd.Series, before: int, after: int, types) -> np.ndarray:
    close_utc = (pd.to_datetime(dt) - pd.Timedelta(hours=7)).to_numpy()      # UTC+8 開盤標記 → UTC 收盤
    ev = event_times(types)
    j = np.searchsorted(ev, close_utc)                                         # 下一個事件
    nxt = np.where(j < len(ev), ev[np.clip(j, 0, len(ev) - 1)], np.datetime64("NaT", "ns"))
    prv = np.where(j > 0, ev[np.clip(j - 1, 0, len(ev) - 1)], np.datetime64("NaT", "ns"))
    near_next = (nxt - close_utc) <= np.timedelta64(before, "h")
    near_prev = (close_utc - prv) <= np.timedelta64(after, "h")
    return ~(np.nan_to_num(near_next, nan=False).astype(bool) | np.nan_to_num(near_prev, nan=False).astype(bool))


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
    ev = pd.read_csv(EV_CSV)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；事件 " +
          "、".join(f"{t} {n} 個（{ev[ev.type == t].date.min()}～{ev[ev.type == t].date.max()}）"
                   for t, n in ev.type.value_counts().items()) + f"；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")
    masks = {name: ok_mask(dt, b, a, ty) for name, (b, a, ty) in CANDS.items()}

    base0 = run(sim, ind, dt, {}, 0)
    print("\n[基準交易：事件窗內 vs 窗外（0bp）]")
    for name in CANDS:
        ok = masks[name][base0.entry_bar.astype(int).to_numpy()]
        cells = []
        for lab, m in (("窗外", ok), ("窗內", ~ok)):
            for pn, pm in (("P1", base0.entry_dt < P_END), ("P2", base0.entry_dt >= P_END)):
                s = part(base0[m & pm.to_numpy()])
                cells.append(f"{lab}{pn} {s['n']:3d}筆 WR {s['wr']:4.0f}% MH {s['mhn'] / max(s['n'], 1) * 100:3.0f}% ${s['pnl']:6,.0f}")
        print(f"  {name} " + " | ".join(cells))

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(CANDS):
            extra = {} if name == "基準" else {"okL": masks[name], "okS": masks[name]}
            f = base0 if (slip == 0 and name == "基準") else run(sim, ind, dt, extra, slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:4s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} TP {a['tpn']:3d}筆 MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} TP {b['tpn']:3d}筆 MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r14_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base0.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 E24]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    b, c = res[(0, "基準")][0], res[(0, CENTER)][0]
    ok["P1 MH少虧"] = c["mh"] > b["mh"]
    ok["P1 WR不掉逾1pp"] = c["wr"] >= b["wr"] - 1.0
    ok["P1 MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    for slip in (0, 2):
        ok[f"P2 {slip}bp PnL勝"] = res[(slip, CENTER)][1]["pnl"] > res[(slip, "基準")][1]["pnl"]
    ok["鄰域P1同向"] = all(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    fx = all(res[(s, "F24")][0]["pnl"] > res[(s, "基準")][0]["pnl"] for s in (0, 2, 5)) and res[(0, "F24")][1]["pnl"] > res[(0, "基準")][1]["pnl"]
    print(f"  探索 F24（只有 FOMC）：{'P1 三種滑價與 P2 0bp 都勝 → 值得另行預登記' if fx else '未全勝 → 不追'}")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
