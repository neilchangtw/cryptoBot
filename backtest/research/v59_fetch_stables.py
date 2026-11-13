"""V59 資料：DefiLlama 全市場美元穩定幣流通量（日頻）。輸出 data/stablecoins_total_daily.csv（date_utc, circulating_usd）。
用法：python backtest/research/v59_fetch_stables.py"""
import os, json, urllib.request
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == '__main__':
    req = urllib.request.Request('https://stablecoins.llama.fi/stablecoincharts/all', headers={'User-Agent': 'research-client'})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    s = pd.DataFrame([(pd.to_datetime(int(x['date']), unit='s'), x['totalCirculating'].get('peggedUSD')) for x in d], columns=['date_utc', 'circulating_usd'])
    s.to_csv(os.path.join(ROOT, 'data', 'stablecoins_total_daily.csv'), index=False); print(len(s), s.date_utc.min(), s.date_utc.max())
