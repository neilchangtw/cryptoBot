"""V45 第 0b 輪：大行情觸發的條件切分（開發期，純診斷）。

觸發固定：|z_6| ≥ 2.5（同向 24 根去重）。切分：OI、ETH/BTC、成交量、funding、波動狀態、時段。
OI 只用 create_time（UTC）≤ 訊號根收盤的資料，訊號根 UTC+8 開盤 t → 收盤 t+1h，取 t+1h−15min。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from v45_r0_diag import D0, D1, events, fwd, load

HS = (6, 12, 24, 48, 72)


def build():
    d = load()
    d["lc"] = np.log(d.close)
    d["lr"] = d.lc.diff()
    d["sig"] = d.lr.rolling(168).std().shift(1)
    d["z6"] = d.lc.diff(6) / (d.sig * np.sqrt(6))
    b = pd.read_csv("data/v44/BTC_1h_long.csv", parse_dates=["datetime"]).set_index("datetime").close
    d["btc"] = d.dt.map(b)
    d["lbtc"] = np.log(d.btc)
    d["rs6"] = d.lc.diff(6) - d.lbtc.diff(6)
    d["volz"] = d.volume.rolling(6).sum() / d.volume.rolling(168).sum().shift(6) * 28
    d["sigp"] = d.sig.rolling(24 * 180, min_periods=24 * 60).rank(pct=True).shift(1)
    m = pd.read_csv("data/v45/ETHUSDT_metrics.csv", usecols=["create_time", "sum_open_interest"])
    m["t"] = pd.to_datetime(m.create_time) + pd.Timedelta(hours=8)
    oi = m.set_index("t").sum_open_interest.astype(float).sort_index()
    q = d.dt + pd.Timedelta(minutes=45)
    v = oi.reindex(q, method="ffill", tolerance=pd.Timedelta(minutes=15)).values
    v0 = oi.reindex(q - pd.Timedelta(hours=6), method="ffill", tolerance=pd.Timedelta(minutes=15)).values
    d["oichg"] = v / v0 - 1
    f = pd.read_csv("data/v44/ETH_funding.csv")
    f["t"] = pd.to_datetime(f.fundingTime, unit="ms") + pd.Timedelta(hours=8)
    fr = f.set_index("t").fundingRate.sort_index()
    d["fund"] = fr.reindex(d.dt + pd.Timedelta(hours=1), method="ffill").rolling(1).mean().values
    d["fund3d"] = pd.Series(d.fund.values).rolling(72).mean()
    return d


def show(d, s, name, cond_sets):
    o = d.open.values
    dev = ((d.dt >= D0) & (d.dt < D1)).values
    base = dev & (s * d.z6.values >= 2.5)
    for label, mask in cond_sets:
        idx = events(base & mask.values)
        row = [f"{name} {label:22s} N={len(idx):3d}"]
        for h in HS:
            f = fwd(o, idx, s, h)
            row.append(f"h{h}={f.mean():6.0f}(t{f.mean() / (f.std(ddof=1) / np.sqrt(len(f))):4.1f})" if len(f) > 2 else f"h{h}=   nan")
        print(" ".join(row))


def main():
    d = build()
    d.to_pickle("data/v45/feat.pkl")
    for s, nm in ((1, "UP"), (-1, "DN")):
        oi = d.oichg
        q = d.volz
        sets = [("all", d.z6.notna()),
                ("OI>+1%", oi > .01), ("OI±1%", oi.abs() <= .01), ("OI<-1%", oi < -.01), ("OI<-3%", oi < -.03), ("OI>+3%", oi > .03),
                ("ETH領先 rs>0", s * d.rs6 > 0), ("BTC領先 rs<0", s * d.rs6 <= 0),
                ("vol低", d.volz < 2), ("vol中", (d.volz >= 2) & (d.volz < 3)), ("vol高", d.volz >= 3),
                ("波動狀態低<.33", d.sigp < .33), ("波動中", (d.sigp >= .33) & (d.sigp < .67)), ("波動高>.67", d.sigp >= .67),
                ("funding同向擁擠", s * d.fund > 0.0003), ("funding中性", (s * d.fund <= 0.0003) & (s * d.fund >= 0)), ("funding反向", s * d.fund < 0),
                ("亞洲8-16", d.dt.dt.hour.between(8, 15)), ("歐16-22", d.dt.dt.hour.between(16, 21)), ("美22-8", ~d.dt.dt.hour.between(8, 21))]
        show(d, s, nm, sets)


if __name__ == "__main__":
    main()
