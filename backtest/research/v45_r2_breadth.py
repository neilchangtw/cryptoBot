"""V45 第 2 輪：10 幣廣度（開發期診斷）。

z6 用各幣自己的 168 根標準差；廣度 = 有資料的幣中同向 z6 ≥ 1.5 的比例。
(a) ETH 急動＋廣度高／低 (b) 廣度高但 ETH 還沒動（z6<1）。次根開盤進，順向報酬 bp。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from v45_r0_diag import D0, D1, events, fwd

COINS = "BTC ETH BNB XRP ADA DOGE SOL DOT LTC LINK".split()
HS = (6, 12, 24, 48, 72)


def zmat():
    z = {}
    for s in COINS:
        x = pd.read_csv(f"data/v42/{s}USDT_1h.csv", parse_dates=["datetime"]).drop_duplicates("datetime").set_index("datetime")
        lc = np.log(x.close)
        z[s] = lc.diff(6) / (lc.diff().rolling(168).std().shift(1) * np.sqrt(6))
    return pd.DataFrame(z)


def main():
    z = zmat()
    d = pd.read_pickle("data/v45/feat.pkl")
    Z = z.reindex(d.dt)
    cnt = Z.notna().sum(axis=1).values
    dev = ((d.dt >= D0) & (d.dt < D1)).values
    o = d.open.values
    ez = d.z6.values
    for s, nm in ((1, "UP"), (-1, "DN")):
        br = ((s * Z >= 1.5).sum(axis=1).values / np.maximum(cnt, 1))
        sets = [("ETH急動 全部", s * ez >= 2.5), ("ETH急動 廣度≥.7", (s * ez >= 2.5) & (br >= .7)), ("ETH急動 廣度.4~.7", (s * ez >= 2.5) & (br >= .4) & (br < .7)),
                ("ETH急動 廣度<.4", (s * ez >= 2.5) & (br < .4)), ("ETH未動 廣度≥.5", (s * ez < 1) & (br >= .5)), ("ETH未動 廣度≥.7", (s * ez < 1) & (br >= .7))]
        for lab, m in sets:
            idx = events(dev & (cnt >= 7) & m)
            row = [f"{nm} {lab:18s} N={len(idx):3d}"]
            for h in HS:
                f = fwd(o, idx, s, h)
                row.append(f"h{h}={f.mean():6.0f}(t{f.mean() / (f.std(ddof=1) / np.sqrt(len(f))):4.1f})" if len(f) > 2 else f"h{h}=nan")
            print(" ".join(row))


if __name__ == "__main__":
    main()
