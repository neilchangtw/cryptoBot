"""V54 資料：Binance 幣本位（COIN-M）ETHUSD 季度交割合約 1h K 線＋ETHUSD 指數價 1h，來源 data.binance.vision 月檔。
輸出 data/ETHUSD_quarterly_1h.csv（symbol, open_time_utc, close, volume, expiry_utc）與 data/ETHUSD_index_1h.csv。
用法：python backtest/research/v54_fetch_basis.py"""
import io, os, zipfile, urllib.request, datetime as dt
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
B = 'https://data.binance.vision/data/futures/cm/monthly'
def last_friday(y, m):
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() != 4: d -= dt.timedelta(days=1)
    return d
def get(url):
    try:
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url, timeout=60).read()))
    except Exception as e:
        return None
    df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit(): df = df.iloc[1:]
    df = df.iloc[:, :6].astype(float); df.columns = ['ot', 'o', 'h', 'l', 'c', 'v']
    return df
def months(a, b):
    y, m = a
    while (y, m) <= b:
        yield y, m
        y, m = (y + (m == 12), m % 12 + 1)
if __name__ == '__main__':
    q = []; miss = []
    for y in range(2020, 2027):
        for m in (3, 6, 9, 12):
            ex = last_friday(y, m)
            if ex < dt.date(2020, 12, 1): continue
            sym = 'ETHUSD_' + ex.strftime('%y%m%d')
            s = (y, m - 6) if m > 6 else (y - 1, m + 6)
            for yy, mm in months(s, (y, m)):
                if (yy, mm) < (2020, 9) or (yy, mm) > (2026, 10): continue
                d = get(f'{B}/klines/{sym}/1h/{sym}-1h-{yy}-{mm:02d}.zip')
                if d is None: miss.append(f'{sym} {yy}-{mm:02d}'); continue
                d['symbol'] = sym; d['expiry_utc'] = pd.Timestamp(ex) + pd.Timedelta(hours=8); q.append(d)
    q = pd.concat(q); q['open_time_utc'] = pd.to_datetime(q.ot, unit='ms')
    q[['symbol', 'open_time_utc', 'c', 'v', 'expiry_utc']].rename(columns={'c': 'close', 'v': 'volume'}).to_csv(os.path.join(ROOT, 'data', 'ETHUSD_quarterly_1h.csv'), index=False)
    ix = []
    for yy, mm in months((2020, 9), (2026, 10)):
        d = get(f'{B}/indexPriceKlines/ETHUSD/1h/ETHUSD-1h-{yy}-{mm:02d}.zip')
        if d is None: miss.append(f'index {yy}-{mm:02d}'); continue
        ix.append(d)
    ix = pd.concat(ix); ix['open_time_utc'] = pd.to_datetime(ix.ot, unit='ms')
    ix[['open_time_utc', 'c']].rename(columns={'c': 'close'}).to_csv(os.path.join(ROOT, 'data', 'ETHUSD_index_1h.csv'), index=False)
    print('季度合約', q.symbol.nunique(), '檔列數', len(q), '指數列數', len(ix)); print('缺檔', miss)
