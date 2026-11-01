"""V45 第 6 輪：L-M 趨勢腿縮小部位後與現行策略的組合試算（固定規則，只改名目）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v45_engine as E
from v45_r4_portfolio import SPLIT, START, W, daily, hourly, legs, mdd, sharpe


def main():
    d = pd.read_pickle("data/v45/feat.pkl")
    b = pd.read_pickle("data/v45/base.pkl")
    t = legs(W.load("data/v39"))["L-M"]
    t = t[t.entry_dt >= START]
    h, _ = hourly(t, d, E.load_funding())
    idx = pd.date_range(START, d.dt.iloc[-1].floor("D"))
    leg = daily(h).reindex(idx).fillna(0)
    live = b.groupby(pd.to_datetime(b.exit_dt).dt.floor("D")).pnl_usd.sum().reindex(idx).fillna(0)
    px = pd.Series(d.close.values, index=d.dt).resample("ME").last().pct_change()
    bull = px[px > .15].index.to_period("M")
    mon = lambda x: x.groupby(x.index.to_period("M")).sum()
    for w, nm in ((0, "現行"), (.25, "1/4 $1,000"), (.5, "1/2 $2,000"), (1, "全 $4,000")):
        c = live + w * leg
        p1, p2 = c[idx < SPLIT], c[idx >= SPLIT]
        m = mon(c)
        print(f"{nm}: 全期 {c.sum():.0f} Sh {sharpe(c):.2f} MDD {mdd(c):.0f}｜P1 {p1.sum():.0f} Sh {sharpe(p1):.2f} MDD {mdd(p1):.0f}｜P2 {p2.sum():.0f} Sh {sharpe(p2):.2f} MDD {mdd(p2):.0f}｜最差月 {m.min():.0f}｜大漲月 {len(bull)} 個合計 {m[m.index.isin(bull)].sum():.0f}")
        print("   逐年", c.groupby(c.index.year).sum().round().astype(int).to_dict())


if __name__ == "__main__":
    main()
