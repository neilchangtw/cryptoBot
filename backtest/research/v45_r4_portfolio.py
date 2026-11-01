"""V45 第 4 輪：趨勢套組 × 現行策略的組合檢驗（預登記 doc/v45_big_move_plan.md）。"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "backtest/research/v35_addon")
import swing as W  # noqa: E402

import v44_r19_late_diag as r19  # noqa: E402
import v45_engine as E  # noqa: E402

SPLIT = pd.Timestamp("2024-07-01")
START = pd.Timestamp("2021-07-01")
RUNS = 1000


def legs(F):
    out = {}
    for nm, fam, side, p in (("S-T", "T", "S", (55, 2)), ("L-M", "M", "L", (20, 100))):
        e, x = W.signals(F, fam, side, p)
        out[nm] = W.run(F, side, e, x)
    return out


def hourly(t, d, fund):
    """盯市：逐小時 $ 損益（含成本與實際 funding）。"""
    c = d.close.values
    op = d.open.values
    pos = np.zeros(len(d))
    pnl = np.zeros(len(d))
    for x in t.itertuples():
        eb, xb, s = int(x.eb), int(x.xb), int(x.s)
        ep = op[eb]
        px = ep * (1 + x.ret * s) if s == 1 else ep / (1 + x.ret)
        path = np.r_[ep, c[eb:xb], px]
        pnl[eb:xb + 1] += s * W.NOT * np.diff(path) / ep
        pnl[xb] -= W.COST
        pos[eb:xb + 1] = s
    ft = fund[(fund.index >= d.dt.iloc[0])]
    b = np.searchsorted(d.dt.values, ft.index.values, "right") - 1
    ok = b >= 0
    np.add.at(pnl, b[ok], -pos[b[ok]] * W.NOT * ft.values[ok])
    return pd.Series(pnl, index=d.dt), pos


def daily(h):
    return h.groupby(h.index.floor("D")).sum()


def sharpe(x):
    return float(x.mean() / x.std() * np.sqrt(365)) if x.std() else 0


def mdd(x):
    e = x.cumsum().values
    return float((np.maximum.accumulate(np.r_[0, e]) - np.r_[0, e]).max())


def main():
    d = pd.read_pickle("data/v45/feat.pkl")
    b = pd.read_pickle("data/v45/base.pkl")
    F = W.load("data/v39")
    fund = E.load_funding()
    live = b.groupby(pd.to_datetime(b.exit_dt).dt.floor("D")).pnl_usd.sum()
    idx = pd.date_range(START, d.dt.iloc[-1].floor("D"))
    live = live.reindex(idx).fillna(0)
    per = (("P1", idx < SPLIT), ("P2", idx >= SPLIT))
    ev = r19.find_events(d.close.values, 0, 0.08)
    print("現行", {n: dict(pnl=round(live[m].sum()), sh=round(sharpe(live[m]), 2), mdd=round(mdd(live[m]))) for n, m in per})
    for nm, t in legs(F).items():
        t = t[t.entry_dt >= START]
        h, pos = hourly(t, d, fund)
        leg = daily(h).reindex(idx).fillna(0)
        print(f"\n== {nm} {len(t)} 筆 PnL {leg.sum():.0f}（引擎 {t.pnl.sum():.0f}）持倉 {np.mean(pos[d.dt.values >= np.datetime64(START)] != 0) * 100:.0f}%")
        print(" 逐年", leg.groupby(leg.index.year).sum().round().to_dict())
        for n, m in per:
            cb = live[m] + leg[m]
            print(f" {n} 腿 {leg[m].sum():.0f} MDD {mdd(leg[m]):.0f}｜現行 Sh {sharpe(live[m]):.2f} MDD {mdd(live[m]):.0f}｜組合 PnL {cb.sum():.0f} Sh {sharpe(cb):.2f} MDD {mdd(cb):.0f}（×{mdd(cb) / mdd(live[m]):.2f}）")
        s = int(t.s.iloc[0])
        evs = [e for e in ev if e[1] == s and d.dt.iloc[e[0]] >= START]
        for n, a, z in (("P1", START, SPLIT), ("P2", SPLIT, d.dt.iloc[-1])):
            g = [e for e in evs if a <= d.dt.iloc[e[0]] < z]
            print(f" T5 {n} 同向 ≥8% 事件 {len(g)}，起點已持有 {sum(pos[e[0]] == s for e in g)}")
        hd = t.xb - t.eb + 1
        if nm == "S-T":
            rng = np.random.default_rng(45)
            c, o = d.close.values, d.open.values
            cand = np.flatnonzero((d.dt >= START).values)
            r = {}
            for pn, (a, z) in dict(P1=(START, SPLIT), P2=(SPLIT, d.dt.iloc[-1])).items():
                tt = t[(t.entry_dt >= a) & (t.entry_dt < z)]
                cc = cand[(d.dt.values[cand] >= np.datetime64(a)) & (d.dt.values[cand] < np.datetime64(z) - np.timedelta64(30, "D"))]
                res = []
                for _ in range(RUNS):
                    st = rng.choice(cc, len(tt))
                    en = np.minimum(st + (tt.xb - tt.eb).values, len(c) - 1)
                    res.append((-(c[en] / o[st] - 1) * W.NOT - W.COST).sum())
                r[pn] = (round(np.percentile(res, 95)), round((np.array(res) < tt.pnl.sum()).mean() * 100, 1), round(tt.pnl.sum()))
            print(" T4 隨機做空（95%線、百分位、實際）", r)
        else:
            ret = pd.Series(d.close.values, index=d.dt).resample("D").last().pct_change().reindex(idx).fillna(0)
            for n, m in per:
                ex = abs(pos[(d.dt.values >= np.datetime64(idx[m][0])) & (d.dt.values <= np.datetime64(idx[m][-1]))]).mean()
                st = ret[m] * W.NOT * ex
                print(f" T4 {n} 平均曝險 {ex:.2f}：L-M Sh {sharpe(leg[m]):.2f} vs 固定持有 Sh {sharpe(st):.2f}，PnL {leg[m].sum():.0f} vs {st.sum():.0f}")


if __name__ == "__main__":
    main()
