"""V82：下載 Coinbase ETH-USD 日 K（公開 API，每次 300 根）。輸出 data/coinbase_ethusd_1d.csv 與來源紀錄。公司電腦不可執行。"""
import os, json, time, hashlib, subprocess, datetime as dt
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); OUT = os.path.join(ROOT, 'doc', 'research_results', 'v82')
rows = []; t = pd.Timestamp('2020-06-01'); end = pd.Timestamp('2026-11-01')
while t < end:
    e = min(t + pd.Timedelta(days=299), end); url = f'https://api.exchange.coinbase.com/products/ETH-USD/candles?granularity=86400&start={t:%Y-%m-%dT%H:%M:%SZ}&end={e:%Y-%m-%dT%H:%M:%SZ}'
    for k in range(4):
        r = subprocess.run(['curl', '-sS', '-f', '-H', 'User-Agent: research-client', url], capture_output=True)
        if r.returncode == 0: break
        time.sleep(2 ** (k + 1))
    rows += json.loads(r.stdout); t = e + pd.Timedelta(days=1); time.sleep(0.4)
d = pd.DataFrame(rows, columns=['ts', 'low', 'high', 'open', 'close', 'volume']).drop_duplicates('ts').sort_values('ts'); d['date'] = pd.to_datetime(d.ts, unit='s'); d = d[d.date < end]
p = os.path.join(ROOT, 'data', 'coinbase_ethusd_1d.csv'); d[['date', 'open', 'high', 'low', 'close', 'volume']].to_csv(p, index=False)
full = pd.date_range(d.date.min(), d.date.max(), freq='D'); miss = full.difference(d.date)
info = dict(來源='https://api.exchange.coinbase.com/products/ETH-USD/candles granularity=86400', 取得時間UTC=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M'), 行數=len(d), 起訖=[str(d.date.min())[:10], str(d.date.max())[:10]], 缺日=[str(x)[:10] for x in miss], 零成交量日=int((d.volume == 0).sum()), sha256=hashlib.sha256(open(p, 'rb').read()).hexdigest(), 限制='API 回傳目前版本，是否曾回補未確認')
json.dump(info, open(os.path.join(OUT, 'data_coinbase.json'), 'w'), ensure_ascii=False, indent=1); print(info)
