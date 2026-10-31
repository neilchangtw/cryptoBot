"""V45 獨立大行情策略回測引擎（不碰實盤程式）。

沿用 V35 嚴格規則：收盤訊號 → 次根開盤成交 ±2bp、停損 25% 穿透、每筆 $9、實際 funding、$4,000 名目。
一次一筆；出場後 cooldown 根內不再進場。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NOT = 4000.0
COST = 9.0
SLIP = 0.0002
PEN = 0.25


def load_funding():
    f = pd.read_csv("data/v44/ETH_funding.csv")
    f["t"] = pd.to_datetime(f.fundingTime, unit="ms") + pd.Timedelta(hours=8)
    return f.set_index("t").fundingRate.sort_index()


def sim(d, sig, side, H, stop, trail=None, cooldown=24, fund=None, i0=0, i1=None):
    """sig: 布林陣列，side +1/-1；H 時間出場根數；stop 例如 0.04；trail=(啟動 %, 回吐 %) 以收盤判斷。"""
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    n = len(c) if i1 is None else i1
    dts = d.dt.values
    if fund is None:
        fund = load_funding()
    ft, fr = fund.index.values, fund.values
    out = []
    i = i0
    block_until = -1
    while i < n - 2:
        if not sig[i] or i < block_until:
            i += 1
            continue
        eb = i + 1
        ep = o[eb] * (1 + side * SLIP)
        st = ep * (1 - side * stop)
        peak = 0.0
        xb, xp, why = None, None, None
        for j in range(eb, min(eb + H, len(c))):
            if side == 1 and l[j] <= st:
                base = min(st, o[j])
                xb, xp, why = j, base - PEN * (base - l[j]), "SN"
                break
            if side == -1 and h[j] >= st:
                base = max(st, o[j])
                xb, xp, why = j, base + PEN * (h[j] - base), "SN"
                break
            pnl_c = side * (c[j] / ep - 1)
            peak = max(peak, pnl_c)
            if trail is not None and peak >= trail[0] and peak - pnl_c >= trail[1] and j + 1 < len(c):
                xb, xp, why = j + 1, o[j + 1] * (1 - side * SLIP), "TR"
                break
        if xb is None:
            xb = min(eb + H, len(c) - 1)
            xp, why = o[xb] * (1 - side * SLIP), "T"
        ret = side * (xp / ep - 1)
        a, b = np.searchsorted(ft, dts[eb], "right"), np.searchsorted(ft, dts[xb], "right")
        fpnl = -side * NOT * fr[a:b].sum()
        out.append((i, eb, xb, side, ep, xp, why, ret, ret * NOT - COST + fpnl, fpnl))
        block_until = xb + cooldown
        i = xb + 1
    t = pd.DataFrame(out, columns="sb eb xb side ep xp why ret pnl fund".split())
    if len(t):
        t["entry_dt"] = pd.to_datetime(dts[t.eb.values])
        t["exit_dt"] = pd.to_datetime(dts[t.xb.values])
    return t


def mdd(t):
    if not len(t):
        return 0.0
    e = t.sort_values("exit_dt").pnl.cumsum().values
    return float((np.maximum.accumulate(np.r_[0, e]) - np.r_[0, e]).max())


def stats(t):
    if not len(t):
        return dict(N=0, pnl=0, wr=0, avg=0, pf=0, mdd=0)
    w = t.pnl[t.pnl > 0].sum()
    ls = -t.pnl[t.pnl <= 0].sum()
    return dict(N=len(t), pnl=round(t.pnl.sum()), wr=round((t.pnl > 0).mean() * 100), avg=round(t.pnl.mean(), 1),
                pf=round(w / ls, 2) if ls else 99, mdd=round(mdd(t)))
