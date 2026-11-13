"""V59-B 資料：Coin Metrics 社群 API，ETH 交易所流入／流出／交易所存量（日頻）。輸出 data/eth_exchange_flows_daily.csv
注意：此資料會重算（回傳含 status-time），不是時點資料。用法：python backtest/research/v59_fetch_flows.py"""
import os, json, urllib.request
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == '__main__':
    url = 'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=eth&metrics=FlowInExNtv,FlowOutExNtv,SplyExNtv&frequency=1d&start_time=2020-01-01&end_time=2026-11-12&page_size=10000'
    rows = []
    while url:
        d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research-client'}), timeout=120)); rows += d['data']; url = d.get('next_page_url')
    f = pd.DataFrame(rows); f['date_utc'] = pd.to_datetime(f.time).dt.tz_localize(None)
    print('status-time 範圍', f['FlowInExNtv-status-time'].min(), f['FlowInExNtv-status-time'].max(), f['FlowInExNtv-status'].value_counts().to_dict())
    o = f[['date_utc', 'FlowInExNtv', 'FlowOutExNtv', 'SplyExNtv']].astype({'FlowInExNtv': float, 'FlowOutExNtv': float, 'SplyExNtv': float}); o.columns = ['date_utc', 'flow_in', 'flow_out', 'supply_ex']
    o.to_csv(os.path.join(ROOT, 'data', 'eth_exchange_flows_daily.csv'), index=False); print(len(o), o.date_utc.min(), o.date_utc.max())
