"""V56 資料：USDⓈ-M ETHUSDT 標記價 1h K 線（data.binance.vision markPriceKlines 月檔）。輸出 data/ETHUSDT_mark_1h.csv。
用法：python backtest/research/v56_fetch_mark.py"""
import os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v54_fetch_basis as F
if __name__ == '__main__':
    out = []; miss = []
    for yy, mm in F.months((2020, 9), (2026, 10)):
        d = F.get(f'https://data.binance.vision/data/futures/um/monthly/markPriceKlines/ETHUSDT/1h/ETHUSDT-1h-{yy}-{mm:02d}.zip')
        if d is None: miss.append(f'{yy}-{mm:02d}'); continue
        d['open_time_utc'] = pd.to_datetime(d.ot.where(d.ot < 1e14, d.ot / 1000), unit='ms'); out.append(d)
    s = pd.concat(out); s[['open_time_utc', 'o', 'h', 'l', 'c']].to_csv(os.path.join(F.ROOT, 'data', 'ETHUSDT_mark_1h.csv'), index=False)
    print('列數', len(s), '缺檔', miss)
