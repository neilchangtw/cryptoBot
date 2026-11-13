"""V53 資料下載（data.binance.vision，不需 API key）：ETHUSDT 永續 1h K 線、funding、metrics。
用法：python backtest/research/v53_fetch_data.py   → 寫入 data/ETHUSDT_1h_2020_2026.csv、ETHUSDT_funding_2020_2026.csv、ETHUSDT_metrics_1h_2021_2026.csv
注意：公司電腦不可下載市場資料（專案規定），請在個人電腦或雲端執行。月檔未出的當月以日檔補；只保留已收盤 K 線。"""
import io, os, sys, zipfile, urllib.request
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); B = 'https://data.binance.vision/data/futures/um'
def get(u):
    try: return zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read()))
    except Exception: return None
def kl(z):
    d = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(d.iloc[0, 0]).isdigit(): d = d.iloc[1:]
    d = d.iloc[:, :6].astype(float); d.columns = ['ot', 'open', 'high', 'low', 'close', 'volume']; return d
if __name__ == '__main__':
    now = pd.Timestamp.utcnow().tz_localize(None); months = pd.period_range('2020-09', now.to_period('M') - 1, freq='M'); out = []; fr = []
    for p in months:
        z = get(f'{B}/monthly/klines/ETHUSDT/1h/ETHUSDT-1h-{p}.zip'); out.append(kl(z)) if z else print('缺月檔', p)
        z = get(f'{B}/monthly/fundingRate/ETHUSDT/ETHUSDT-fundingRate-{p}.zip'); fr.append(pd.read_csv(z.open(z.namelist()[0]))) if z else print('缺 funding', p)
    for d in pd.date_range(now.normalize().replace(day=1), now.normalize() - pd.Timedelta(days=1)):
        z = get(f'{B}/daily/klines/ETHUSDT/1h/ETHUSDT-1h-{d.date()}.zip'); out.append(kl(z)) if z else None
    k = pd.concat(out).drop_duplicates('ot').sort_values('ot'); k['datetime'] = pd.to_datetime(k.ot, unit='ms') + pd.Timedelta(hours=8)
    k[['datetime', 'open', 'high', 'low', 'close', 'volume']].to_csv(os.path.join(ROOT, 'data', 'ETHUSDT_1h_2020_2026.csv'), index=False)
    pd.concat(fr).to_csv(os.path.join(ROOT, 'data', 'ETHUSDT_funding_2020_2026.csv'), index=False)
    ms = []
    for d in pd.date_range('2021-12-01', now.normalize() - pd.Timedelta(days=2)):
        z = get(f'{B}/daily/metrics/ETHUSDT/ETHUSDT-metrics-{d.date()}.zip'); ms.append(pd.read_csv(z.open(z.namelist()[0]))) if z else None
    m = pd.concat(ms); m['t'] = pd.to_datetime(m.create_time); m = m.drop(columns=['create_time', 'symbol']).set_index('t').sort_index().resample('1h', label='right', closed='right').last()
    m.index = m.index + pd.Timedelta(hours=8); m.to_csv(os.path.join(ROOT, 'data', 'ETHUSDT_metrics_1h_2021_2026.csv')); print('完成', len(k), len(m))
