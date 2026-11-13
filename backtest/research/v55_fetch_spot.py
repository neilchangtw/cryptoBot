"""V55 資料：Binance 現貨 ETHUSDT 1h K 線（data.binance.vision 月檔）。輸出 data/ETHUSDT_spot_1h.csv（open_time_utc, close, volume）。
注意：現貨檔自 2025-01 起時間戳為微秒，之前為毫秒；此處依位數判斷。用法：python backtest/research/v55_fetch_spot.py"""
import os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v54_fetch_basis as F
if __name__ == '__main__':
    out = []; miss = []
    for yy, mm in F.months((2020, 9), (2026, 10)):
        d = F.get(f'https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/1h/ETHUSDT-1h-{yy}-{mm:02d}.zip')
        if d is None: miss.append(f'{yy}-{mm:02d}'); continue
        d['open_time_utc'] = pd.to_datetime(d.ot.where(d.ot < 1e14, d.ot / 1000), unit='ms'); out.append(d)
    s = pd.concat(out); s[['open_time_utc', 'c', 'v']].rename(columns={'c': 'close', 'v': 'volume'}).to_csv(os.path.join(F.ROOT, 'data', 'ETHUSDT_spot_1h.csv'), index=False)
    print('列數', len(s), '缺檔', miss)
