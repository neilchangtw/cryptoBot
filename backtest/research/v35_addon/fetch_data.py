"""V35 外掛研究資料下載：5.6 年 ETH/BTC 1h、ETH 30m、ETH funding（Binance Futures 公開端點）。

用法（在專案根目錄）：
    python backtest/research/v35_addon/fetch_data.py data/v35
之後各腳本以同一個資料夾為第一個參數，例如：
    python backtest/research/v35_addon/base.py data/v35
"""
import os, sys, time
import pandas as pd
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
import fetch_backtest_data as fbd

out = sys.argv[1] if len(sys.argv) > 1 else 'data/v35'
os.makedirs(out, exist_ok=True)
fbd.fetch_history('ETHUSDT', '1h', 2060).to_csv(os.path.join(out, 'ETH_1h_long.csv'), index=False)
fbd.fetch_history('BTCUSDT', '1h', 2060).to_csv(os.path.join(out, 'BTC_1h_long.csv'), index=False)
fbd.fetch_history('ETHUSDT', '30m', 2060).to_csv(os.path.join(out, 'ETH_30m_long.csv'), index=False)

rows, st = [], int(pd.Timestamp('2020-12-01').timestamp() * 1000)
while True:
    j = requests.get('https://fapi.binance.com/fapi/v1/fundingRate',
                     params={'symbol': 'ETHUSDT', 'startTime': st, 'limit': 1000}, timeout=20).json()
    if not j:
        break
    rows += j
    st = j[-1]['fundingTime'] + 1
    time.sleep(0.3)
    if len(j) < 1000:
        break
f = pd.DataFrame(rows)
f['t'] = pd.to_datetime(f.fundingTime, unit='ms')
f['rate'] = f.fundingRate.astype(float)
f[['t', 'rate']].to_csv(os.path.join(out, 'funding_long.csv'), index=False)
print('done ->', out)
