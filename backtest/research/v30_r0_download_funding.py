"""
V30 R0 — Download ETHUSDT funding rate full history (Binance public endpoint, no key)

Funding rate 每 8h 一筆（00/08/16 UTC），/fapi/v1/fundingRate 有完整歷史（不像 OI/LSR 只有 30 天）。
輸出 data/ETHUSDT_funding.csv（funding_time_utc, rate）覆蓋 1h 快取整個窗口。
"""
import os
import csv
import json
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')
OUT = os.path.join(DATA_DIR, 'ETHUSDT_funding.csv')

BASE = 'https://fapi.binance.com/fapi/v1/fundingRate'
SYMBOL = 'ETHUSDT'

# 從 1h 快取第一根之前 60 天開始抓（留 rolling percentile 暖機）
import pandas as pd
kl = pd.read_csv(os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv'))
first_dt = pd.to_datetime(kl['datetime'].iloc[0])
last_dt = pd.to_datetime(kl['datetime'].iloc[-1])
start_ms = int((first_dt - pd.Timedelta(days=60)).timestamp() * 1000)
end_ms = int((last_dt + pd.Timedelta(hours=2)).timestamp() * 1000)
print(f"K線窗口: {first_dt} ~ {last_dt}")
print(f"抓取 funding: {pd.to_datetime(start_ms, unit='ms')} 起")

rows = []
cur = start_ms
while cur < end_ms:
    url = f"{BASE}?symbol={SYMBOL}&startTime={cur}&endTime={end_ms}&limit=1000"
    with urllib.request.urlopen(url, timeout=30) as r:
        batch = json.loads(r.read().decode())
    if not batch:
        break
    for it in batch:
        rows.append((int(it['fundingTime']), float(it['fundingRate'])))
    print(f"  +{len(batch)} 筆, 至 {pd.to_datetime(batch[-1]['fundingTime'], unit='ms')}")
    nxt = int(batch[-1]['fundingTime']) + 1
    if nxt <= cur:
        break
    cur = nxt
    time.sleep(0.3)

# 去重排序
rows = sorted(set(rows))
with open(OUT, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['funding_time_ms', 'funding_time_utc', 'rate'])
    for ts, rate in rows:
        w.writerow([ts, pd.to_datetime(ts, unit='ms').strftime('%Y-%m-%d %H:%M:%S'), f"{rate:.8f}"])
print(f"\n共 {len(rows)} 筆 → {OUT}")
print(f"範圍: {pd.to_datetime(rows[0][0], unit='ms')} ~ {pd.to_datetime(rows[-1][0], unit='ms')}")
