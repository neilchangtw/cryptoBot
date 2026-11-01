"""V45 第 5 輪診斷：更早的觸發（5 分 K，開發期 2021-03～2024-06）。

大行情 1h 訊號太晚。改用 30 分鐘報酬 z ≥ k（標準差 = 前 7 天 5 分報酬 × √6），同向 6 小時去重，
下一根 5 分開盤進場，往後 1/3/6/12/24/48 小時順向報酬 bp（未扣 24bp 成本）。
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

P = Path("data/v45/ETH_5m_dev.csv")
URL = "https://data.binance.vision/data/futures/um/monthly/klines/ETHUSDT/5m/ETHUSDT-5m-{m}.zip"
COLS = "open_time open high low close volume close_time qv n tb tq i".split()


def fetch():
    fr = []
    for m in pd.period_range("2021-02", "2024-06", freq="M"):
        with urllib.request.urlopen(URL.format(m=m), timeout=60) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
        x = pd.read_csv(z.open(z.namelist()[0]), header=None, names=COLS)
        x = x[pd.to_numeric(x.open_time, errors="coerce").notna()]
        fr.append(x[["open_time", "open", "close", "volume"]].astype(float))
    d = pd.concat(fr).drop_duplicates("open_time").sort_values("open_time")
    d.to_csv(P, index=False)


def main():
    if not P.exists():
        fetch()
    d = pd.read_csv(P)
    o, c = d.open.values, d.close.values
    lc = pd.Series(np.log(c))
    sig = lc.diff().rolling(2016).std().shift(1).values
    start = 2016 + 12
    print("根數", len(d))
    for nbar, lab in ((3, "15m"), (6, "30m"), (12, "1h")):
        z = lc.diff(nbar).values / (sig * np.sqrt(nbar))
        for k in (3, 4, 5, 6):
            for s, nm in ((1, "UP"), (-1, "DN")):
                ids, last = [], -10**9
                for i in np.flatnonzero(s * z >= k):
                    if i >= start and i - last >= 72:
                        ids.append(i)
                        last = i
                ids = np.array(ids)
                row = [f"{lab} k{k} {nm} N={len(ids):4d}"]
                for h in (1, 3, 6, 12, 24, 48):
                    j = ids[ids + 1 + h * 12 < len(o)]
                    v = s * np.log(o[j + 1 + h * 12] / o[j + 1]) * 1e4
                    row.append(f"h{h}={v.mean():5.0f}(t{v.mean() / (v.std(ddof=1) / np.sqrt(len(v))):4.1f})" if len(v) > 2 else "")
                print(" ".join(row))


if __name__ == "__main__":
    main()
