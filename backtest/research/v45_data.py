"""V45 資料：data.binance.vision 的 ETHUSDT 永續每日 metrics（5 分鐘 OI 等），2021-12 起。

輸出 data/v45/ETHUSDT_metrics.csv（create_time 為 UTC，原檔欄位）。可續傳。
用法：python backtest/research/v45_data.py
"""

from __future__ import annotations

import concurrent.futures as cf
import io
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "v45" / "_raw"
URL = "https://data.binance.vision/data/futures/um/daily/metrics/ETHUSDT/ETHUSDT-metrics-{d}.zip"


def one(d: str) -> str:
    p = RAW / f"{d}.csv"
    if p.exists():
        return "skip"
    for k in range(4):
        try:
            with urllib.request.urlopen(URL.format(d=d), timeout=30) as r:
                z = zipfile.ZipFile(io.BytesIO(r.read()))
            with z.open(z.namelist()[0]) as f:
                p.write_bytes(f.read())
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "404"
        except Exception:
            pass
    return "fail"


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    days = [x.strftime("%Y-%m-%d") for x in pd.date_range("2021-12-01", "2026-10-30")]
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, days))
    print({k: res.count(k) for k in set(res)})
    print("缺：", [d for d, r in zip(days, res) if r in ("404", "fail")][:30])
    fr = []
    for f in sorted(RAW.glob("*.csv")):
        x = pd.read_csv(f)
        fr.append(x)
    df = pd.concat(fr).drop_duplicates("create_time").sort_values("create_time")
    df.to_csv(ROOT / "data" / "v45" / "ETHUSDT_metrics.csv", index=False)
    print(len(df), df.create_time.min(), df.create_time.max())


if __name__ == "__main__":
    main()
