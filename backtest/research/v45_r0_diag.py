"""V45 第 0 輪：大行情發生的當下，往後還會不會延續（只看開發期 < 2024-07-01，純診斷）。

觸發：過去 n 根對數報酬 |r_n| ≥ k × (過去 168 根 1h 報酬標準差 × √n)，不含當根的標準差。
同方向 24 根內只算第一次。次根開盤進場，往後 h 根的順向報酬（bp，未扣成本）。
成本參考 V35 嚴格規則：每筆 $9/$4,000 = 22.5bp，再加 2bp×2。

用法：python backtest/research/v45_r0_diag.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

D0, D1 = pd.Timestamp("2021-03-01"), pd.Timestamp("2024-07-01")
NS = (1, 3, 6, 12, 24)
KS = (2, 3, 4, 5)
HS = (6, 12, 24, 48, 72)


def load(path="data/v39/ETH_1h_long.csv"):
    d = pd.read_csv(path)
    d["dt"] = pd.to_datetime(d.datetime)
    return d.reset_index(drop=True)


def events(mask, gap=24):
    out, last = [], -10**9
    for i in np.flatnonzero(mask):
        if i - last >= gap:
            out.append(int(i))
            last = i
    return np.array(out, int)


def fwd(o, idx, s, h):
    idx = idx[idx + 1 + h < len(o)]
    return s * np.log(o[idx + 1 + h] / o[idx + 1]) * 1e4


def main():
    d = load()
    o, c = d.open.values, d.close.values
    lr = pd.Series(np.log(c)).diff()
    sig = lr.rolling(168).std().shift(1).values
    dev = ((d.dt >= D0) & (d.dt < D1)).values
    print("開發期根數", dev.sum())
    print("無條件做多 mean bp:", {h: round(float(np.nanmean(np.log(o[h:] / o[:-h])[dev[:-h]]) * 1e4), 1) for h in HS})
    rows = []
    for n in NS:
        r = pd.Series(np.log(c)).diff(n).values
        z = r / (sig * np.sqrt(n))
        for k in KS:
            for s, name in ((1, "up"), (-1, "dn")):
                idx = events(dev & (s * z >= k))
                row = dict(n=n, k=k, side=name, N=len(idx))
                for h in HS:
                    f = fwd(o, idx, s, h)
                    row[f"h{h}"] = round(float(f.mean()), 0) if len(f) else np.nan
                    row[f"t{h}"] = round(float(f.mean() / (f.std(ddof=1) / np.sqrt(len(f)))), 1) if len(f) > 2 else np.nan
                rows.append(row)
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 200, "display.max_rows", 200, "display.max_columns", 30)
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
