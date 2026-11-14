"""V78 資料下載（data.binance.vision，不需 API key）：ETHUSDT 永續 15m K 線 2020-09 起。
用法：python backtest/research/v78_fetch_15m.py   → data/ETHUSDT_15m_2020_2026.csv（datetime＝開盤時間 UTC+8，只留已收盤 K 線）
注意：公司電腦不可下載市場資料（專案規定），請在雲端或家用工作區執行。"""
import io, os, zipfile, urllib.request
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); B = 'https://data.binance.vision/data/futures/um'
def get(u):
    for _ in range(3):
        try: return zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read()))
        except Exception as e: err = e
    return None
def kl(z):
    d = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(d.iloc[0, 0]).isdigit(): d = d.iloc[1:]
    d = d.iloc[:, :6].astype(float); d.columns = ['ot', 'open', 'high', 'low', 'close', 'volume']; return d
if __name__ == '__main__':
    END = pd.Timestamp('2026-11-14'); out = []; miss = []
    for p in pd.period_range('2020-09', '2026-10', freq='M'):
        z = get(f'{B}/monthly/klines/ETHUSDT/15m/ETHUSDT-15m-{p}.zip'); out.append(kl(z)) if z else miss.append(str(p))
    for d in pd.date_range('2026-11-01', END - pd.Timedelta(days=1)):
        z = get(f'{B}/daily/klines/ETHUSDT/15m/ETHUSDT-15m-{d.date()}.zip'); out.append(kl(z)) if z else miss.append(str(d.date()))
    k = pd.concat(out).drop_duplicates('ot').sort_values('ot'); k['datetime'] = pd.to_datetime(k.ot, unit='ms') + pd.Timedelta(hours=8)
    k[['datetime', 'open', 'high', 'low', 'close', 'volume']].to_csv(os.path.join(ROOT, 'data', 'ETHUSDT_15m_2020_2026.csv'), index=False)
    g = k.datetime.diff().dropna(); print('完成', len(k), k.datetime.min(), k.datetime.max(), '缺檔', miss, '非15分間隔', int((g != pd.Timedelta(minutes=15)).sum()))
