"""V44 第 20 輪：連虧縮倉與權益曲線暫停。

預登記：doc/v44_maxhold_reframe_plan.md 第 23 節。用基準交易清單事後計算，不改交易序列；
決策只用 exit_dt < 當筆 entry_dt 的已平倉基準交易。K 類換算成每 200U 平均保證金；E 類用原值。
期間 P1 = 進場 < 2024-07-01，P2 = 之後；2026-07 起只列參考。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r20_streak.py
"""

from __future__ import annotations

from math import erf, sqrt

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r15_lowvolpause import build_sim, run

P_END = pd.Timestamp("2024-07-01")
RECENT = pd.Timestamp("2026-07-01")
K_CANDS = {"K3H": (3, 0.5), "K2H": (2, 0.5), "K4H": (4, 0.5), "K3Q": (3, 0.25)}
E_CANDS = {"E10": 10, "E5": 5, "E20": 20}
GROUPS = (("K3H", ("K2H", "K4H", "K3Q")), ("E10", ("E5", "E20")))
EXTRA = v40.EXTRA_COST


def prepare(f: pd.DataFrame) -> pd.DataFrame:
    """回傳依進場排序的交易，附 streak（進場時已平倉的連虧筆數）、closed（已平倉 PnL 序列長度）。"""
    f = f.sort_values("entry_dt").reset_index(drop=True).copy()
    f["exit_ts"] = pd.to_datetime(f["exit_dt"])
    order = f.sort_values("exit_ts")
    ex_t, ex_p, ex_r = order.exit_ts.to_numpy(), order.pnl_usd.to_numpy(), (order.exit_reason == "MH").to_numpy()
    streak = np.zeros(len(ex_p) + 1, int)
    for k in range(len(ex_p)):
        streak[k + 1] = streak[k] + 1 if ex_p[k] <= 0 else 0
    cum = np.concatenate([[0.0], np.cumsum(ex_p)])
    closed = np.searchsorted(ex_t, f.entry_dt.to_numpy(), side="left")
    f["closed"], f["streak"] = closed, streak[closed]
    f["prev_mh"] = [bool(ex_r[c - 1]) if c else False for c in closed]
    f.attrs["cum"] = cum
    return f


def e_ok(f: pd.DataFrame, n: int) -> np.ndarray:
    cum = f.attrs["cum"]
    c = f.closed.to_numpy()
    start = np.clip(c - n, 0, None)
    return ~(((cum[c] - cum[start]) < 0) & (c >= n))


def seq(pnl: np.ndarray) -> dict:
    eq = np.cumsum(pnl)
    mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(pnl) else 0.0
    run_n = run_amt = best_n = best_amt = 0.0
    for p in pnl:
        if p < 0:
            run_n += 1; run_amt += p
            best_n, best_amt = max(best_n, run_n), min(best_amt, run_amt)
        elif p > 0:
            run_n = run_amt = 0.0
    return dict(n=len(pnl), wr=(pnl > 0).mean() * 100 if len(pnl) else np.nan, pnl=pnl.sum(), mdd=mdd, ln=int(best_n), la=best_amt)


def parts(f: pd.DataFrame, pnl: np.ndarray, keep=None) -> dict:
    keep = np.ones(len(f), bool) if keep is None else keep
    t = f.entry_dt
    return {pn: seq(pnl[m & keep]) for pn, m in
            (("P1", (t < P_END).to_numpy()), ("P2", (t >= P_END).to_numpy()), ("近期", (t >= RECENT).to_numpy()))}


def show(name, s, extra=""):
    print(f"  {name:5s}{extra} " + " | ".join(
        f"{k} {x['n']:3d}筆 WR {x['wr']:5.1f}% ${x['pnl']:7,.0f} MDD ${x['mdd']:5,.0f} 連虧 {x['ln']}筆 ${x['la']:6,.0f}" for k, x in s.items()))


def runs_test(w: np.ndarray):
    a, b = w.sum(), (~w).sum()
    r = 1 + (w[1:] != w[:-1]).sum()
    mu = 2 * a * b / (a + b) + 1
    var = (mu - 1) * (mu - 2) / (a + b - 1)
    z = (r - mu) / sqrt(var)
    return r, mu, 2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2))))


def diag(f: pd.DataFrame) -> None:
    for pn, g in (("P1", f[f.entry_dt < P_END]), ("P2", f[f.entry_dt >= P_END])):
        w = g.pnl_usd.to_numpy() > 0
        r, mu, p = runs_test(w)
        s = seq(g.pnl_usd.to_numpy())
        ac = pd.Series(w.astype(float)).autocorr(1)
        print(f"  {pn} {len(g)} 筆 WR {w.mean() * 100:.1f}%｜lag-1 自相關 {ac:+.3f}（|r|>{1.96 / sqrt(len(g)):.3f} 才顯著）｜runs {r} vs 期望 {mu:.1f}，p={p:.3f}｜最長連虧 {s['ln']} 筆 ${s['la']:,.0f}")
        print(f"     全體均 PnL ${g.pnl_usd.mean():.1f}；MH 率 {(g.exit_reason == 'MH').mean() * 100:.1f}%，前一筆 MH 後 {(g[g.prev_mh].exit_reason == 'MH').mean() * 100:.1f}%（{g.prev_mh.sum()} 筆），前一筆非 MH 後 {(g[~g.prev_mh].exit_reason == 'MH').mean() * 100:.1f}%")
        for k in range(1, 6):
            x = g[g.streak == k] if k < 5 else g[g.streak >= 5]
            print(f"     連虧 {'≥5' if k == 5 else k} 後下一筆：{len(x):3d}筆 WR {(x.pnl_usd > 0).mean() * 100 if len(x) else 0:5.1f}% 均${x.pnl_usd.mean() if len(x) else 0:6.1f}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    base = {slip: prepare(run(sim, ind, dt, {}, slip)) for slip in (0, 2, 5)}
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後；近期 = {RECENT:%Y-%m-%d} 起（只參考）")

    print("\n[診斷（0bp，不判）]")
    diag(base[0])

    res = {}
    for slip, f in base.items():
        p = f.pnl_usd.to_numpy()
        print(f"\n[滑價 {slip}bp]")
        res[(slip, "基準")] = parts(f, p)
        show("基準", res[(slip, "基準")])
        for name, (k, m) in K_CANDS.items():
            size = np.where(f.streak >= k, m, 1.0)
            pnl = ((p + EXTRA) * size - EXTRA) / size.mean()         # 換算成每 200U 平均保證金，額外成本不隨部位縮小
            res[(slip, name)] = parts(f, pnl)
            show(name, res[(slip, name)], f" 均{size.mean() * 200:3.0f}U")
            if slip == 0:
                f.assign(size=size, pnl_adj=pnl).drop(columns=["exit_ts"]).to_csv(OUT_DIR / f"r20_{name}_trades.csv", index=False)
        for name, n in E_CANDS.items():
            ok = e_ok(f, n)
            res[(slip, name)] = parts(f, p, ok)
            show(name, res[(slip, name)], f" 下單{ok.mean() * 100:3.0f}%")
            if slip == 0:
                f[ok].drop(columns=["exit_ts"]).to_csv(OUT_DIR / f"r20_{name}_trades.csv", index=False)

    print("\n[預登記門檻]")
    for center, neighbors in GROUPS:
        ok = {}
        for slip in (0, 2, 5):
            ok[f"P1 {slip}bp PnL≥基準"] = res[(slip, center)]["P1"]["pnl"] >= res[(slip, "基準")]["P1"]["pnl"]
        for slip in (0, 2):
            ok[f"P2 {slip}bp PnL≥90%"] = res[(slip, center)]["P2"]["pnl"] >= res[(slip, "基準")]["P2"]["pnl"] * 0.9
        for pn in ("P1", "P2"):
            c, b = res[(0, center)][pn], res[(0, "基準")][pn]
            ok[f"{pn} MDD低10%"] = c["mdd"] <= b["mdd"] * 0.9
            ok[f"{pn} 最長連虧較低"] = c["la"] > b["la"]
        nb = sum(res[(0, m)]["P1"]["pnl"] > res[(0, "基準")]["P1"]["pnl"] for m in neighbors)
        ok["鄰域P1至少2/3勝"] = nb * 3 >= 2 * len(neighbors)
        verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
        print(f"  {center}: " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}（鄰域 {nb}/{len(neighbors)}）")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
