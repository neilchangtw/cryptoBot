"""V60 資料：Deribit 歷史 API，ETH 選擇權逐筆成交，每天只取 UTC 22:00~24:00 兩小時視窗（降低下載量）。
輸出 data/deribit_eth_option_trades_22_24.csv（ts_ms, instrument, iv, index_price, price, amount, direction）。可續傳。
用法：python backtest/research/v60_fetch_deribit.py [起日 YYYY-MM-DD] [迄日]"""
import os, sys, json, time, urllib.request
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); OUT = os.path.join(ROOT, 'data', 'deribit_eth_option_trades_22_24.csv')
U = 'https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time?currency=ETH&kind=option&start_timestamp={a}&end_timestamp={b}&count=1000&sorting=asc'
def call(a, b):
    for k in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(U.format(a=a, b=b), headers={'User-Agent': 'research-client'}), timeout=60))['result']
        except Exception as e:
            time.sleep(2 + 3 * k)
    return None
if __name__ == '__main__':
    d0 = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else '2020-10-01'); d1 = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else '2026-10-31')
    done = set()
    if os.path.exists(OUT):
        done = set(pd.to_datetime(pd.read_csv(OUT, usecols=['ts_ms']).ts_ms, unit='ms').dt.floor('D').unique())
    else:
        open(OUT, 'w').write('ts_ms,instrument,iv,index_price,price,amount,direction\n')
    fail = []
    for d in pd.date_range(d0, d1, freq='D'):
        if d in done: continue
        a = int((d + pd.Timedelta(hours=22)).timestamp() * 1000); b = int((d + pd.Timedelta(hours=24)).timestamp() * 1000) - 1; rows = []
        while True:
            r = call(a, b)
            if r is None: fail.append(str(d.date())); break
            rows += r['trades']
            if not r.get('has_more') or not r['trades']: break
            a = r['trades'][-1]['timestamp'] + 1
        with open(OUT, 'a') as f:
            for t in rows: f.write(f"{t['timestamp']},{t['instrument_name']},{t.get('iv','')},{t.get('index_price','')},{t['price']},{t['amount']},{t['direction']}\n")
        time.sleep(0.12)
        if d.day == 1: print(d.date(), len(rows), flush=True)
    print('完成；失敗日', fail)
