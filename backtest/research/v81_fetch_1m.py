"""V81：下載並校驗 Binance USDⓈ-M ETHUSDT 永續 1 分 K（2021-01～2026-10）。
只在雲端或家用研究工作區執行；公司電腦不可執行。
輸出：data/ETHUSDT_1m_2020_2026.npz、doc/research_results/v81/data_manifest_1m.csv、data_gaps_1m.csv、data_checks_1m.json
"""
import os, io, sys, json, time, zipfile, hashlib, subprocess, datetime as dt
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ZD = os.path.join(ROOT, 'data', 'v81_1m_zip'); OUT = os.path.join(ROOT, 'doc', 'research_results', 'v81')
BASE = 'https://data.binance.vision/data/futures/um/monthly/klines/ETHUSDT/1m/'
DAILY = 'https://data.binance.vision/data/futures/um/daily/klines/ETHUSDT/1m/'
os.makedirs(ZD, exist_ok=True)

def get(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 0: return True
    for k in range(4):
        r = subprocess.run(['curl', '-sS', '-f', '-o', path, url], capture_output=True)
        if r.returncode == 0: return True
        time.sleep(2 ** (k + 1))
    if os.path.exists(path): os.remove(path)
    return False

def read_zip(path):
    z = zipfile.ZipFile(path); raw = z.read(z.namelist()[0])
    first = raw[:20].decode()
    df = pd.read_csv(io.BytesIO(raw), header=0 if first[0].isalpha() else None)
    df = df.iloc[:, :6]; df.columns = ['ts', 'open', 'high', 'low', 'close', 'volume']
    return df, first[0].isalpha()

months = pd.period_range('2020-09', '2026-10', freq='M')   # 2020-09～12 只供暖機期的風控與持倉狀態（登記後補充，評估窗不變）
man = []; parts = []
for m in months:
    fn = f'ETHUSDT-1m-{m}.zip'; p = os.path.join(ZD, fn)
    ok = get(BASE + fn, p) and get(BASE + fn + '.CHECKSUM', p + '.CHECKSUM')
    if not ok:
        man.append(dict(file=fn, status='下載失敗')); continue
    sha = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    off = open(p + '.CHECKSUM').read().split()[0]
    df, hdr = read_zip(p)
    unit = 'ms' if df.ts.iloc[0] < 1e14 else 'us'
    if unit == 'us': df['ts'] = df.ts // 1000
    exp = m.days_in_month * 1440
    man.append(dict(file=fn, 取得日UTC=dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc).strftime('%Y-%m-%d %H:%M'),
                    bytes=os.path.getsize(p), sha256=sha, 官方CHECKSUM相符=(sha == off), 含表頭=hdr, 時間戳單位=unit,
                    行數=len(df), 應有行數=exp, 首根UTC=str(pd.to_datetime(df.ts.iloc[0], unit='ms')),
                    末根UTC=str(pd.to_datetime(df.ts.iloc[-1], unit='ms')), status='OK'))
    parts.append(df)
man = pd.DataFrame(man); man.to_csv(os.path.join(OUT, 'data_manifest_1m.csv'), index=False)
d = pd.concat(parts, ignore_index=True)
chk = {}
chk['總行數'] = int(len(d)); chk['重複時間戳'] = int(d.ts.duplicated().sum())
d = d.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
chk['非整分時間戳'] = int((d.ts % 60000 != 0).sum())
full = np.arange(int(np.datetime64('2020-09-01', 'ms').astype('int64')), int(np.datetime64('2026-11-01', 'ms').astype('int64')), 60000)
miss = np.setdiff1d(full, d.ts.values)
chk['應有分鐘數'] = int(len(full)); chk['缺漏分鐘數'] = int(len(miss))
# 缺漏區段
gaps = []
if len(miss):
    brk = np.where(np.diff(miss) != 60000)[0]
    st = np.r_[0, brk + 1]; en = np.r_[brk, len(miss) - 1]
    for a, b in zip(st, en):
        gaps.append(dict(起UTC=str(pd.to_datetime(miss[a], unit='ms')), 迄UTC=str(pd.to_datetime(miss[b], unit='ms')), 分鐘數=int(b - a + 1)))
# 嘗試官方日檔補齊
filled = []
if len(miss):
    days = sorted(set(pd.to_datetime(miss, unit='ms').strftime('%Y-%m-%d')))
    for day in days:
        fn = f'ETHUSDT-1m-{day}.zip'; p = os.path.join(ZD, fn)
        if get(DAILY + fn, p) and get(DAILY + fn + '.CHECKSUM', p + '.CHECKSUM'):
            sha = hashlib.sha256(open(p, 'rb').read()).hexdigest(); off = open(p + '.CHECKSUM').read().split()[0]
            x, _ = read_zip(p)
            if x.ts.iloc[0] > 1e14: x['ts'] = x.ts // 1000
            add = x[x.ts.isin(miss)]
            filled.append(dict(日檔=fn, sha256=sha, 官方CHECKSUM相符=(sha == off), 日檔行數=len(x), 補回分鐘數=len(add)))
            if len(add) and sha == off: d = pd.concat([d, add], ignore_index=True)
        else:
            filled.append(dict(日檔=fn, sha256='', 官方CHECKSUM相符=False, 日檔行數=0, 補回分鐘數=0))
    d = d.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
miss2 = np.setdiff1d(full, d.ts.values)
chk['日檔補齊'] = filled; chk['補齊後仍缺分鐘數'] = int(len(miss2))
pd.DataFrame(gaps).to_csv(os.path.join(OUT, 'data_gaps_1m.csv'), index=False)
t = pd.to_datetime(d.ts, unit='ms')
per = d.groupby(t.dt.date).size()
chk['天數'] = int(len(per)); chk['非1440根的日數'] = int((per != 1440).sum())
chk['非1440根的日'] = {str(k): int(v) for k, v in per[per != 1440].items()}
chk['零成交量根數'] = int((d.volume == 0).sum()); chk['零成交量_依年'] = {str(k): int(v) for k, v in (d.volume == 0).groupby(t.dt.year).sum().items()}
chk['高低價矛盾根數'] = int(((d.high < d[['open', 'close']].max(axis=1)) | (d.low > d[['open', 'close']].min(axis=1)) | (d.high < d.low)).sum())
x = d[(t >= '2026-10-28') & (t < '2026-10-29')]
chk['2026-10-28'] = dict(根數=int(len(x)), 零成交量=int((x.volume == 0).sum()), 來源='月檔' + ('＋日檔補' if any('2026-10-28' in f['日檔'] and f['補回分鐘數'] for f in filled) else ''))
# 與既有 15 分 K 比對
g = d.assign(b=d.ts // 900000 * 900000).groupby('b').agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'), close=('close', 'last'), volume=('volume', 'sum'), n=('ts', 'size'))
k15 = pd.read_csv(os.path.join(ROOT, 'data', 'ETHUSDT_15m_2020_2026.csv'), parse_dates=['datetime'])
k15['b'] = (k15.datetime - pd.Timedelta(hours=8)).values.astype('datetime64[ms]').astype('int64')
j = k15.set_index('b').join(g, rsuffix='_1m', how='inner')
cmp = {'比對根數': int(len(j)), '15分K在範圍內根數': int(((k15.b >= full[0]) & (k15.b <= full[-1])).sum()), '1分K不足15根的15分K': int((j.n != 15).sum())}
for c in ['open', 'high', 'low', 'close']:
    cmp[c + '不同根數'] = int((np.abs(j[c] - j[c + '_1m']) > 1e-9).sum())
cmp['volume相對差>1e-6根數'] = int((np.abs(j.volume - j.volume_1m) > 1e-6 * np.maximum(j.volume, 1)).sum())
bad = j[(np.abs(j.open - j.open_1m) > 1e-9) | (np.abs(j.high - j.high_1m) > 1e-9) | (np.abs(j.low - j.low_1m) > 1e-9) | (np.abs(j.close - j.close_1m) > 1e-9)]
bad.assign(t=pd.to_datetime(bad.index, unit='ms')).to_csv(os.path.join(OUT, 'data_15m_mismatch.csv'), index=False)
chk['與15分K比對'] = cmp
chk['CHECKSUM全部相符'] = bool(man.get('官方CHECKSUM相符', pd.Series([False])).all()) and bool((man.status == 'OK').all())
np.savez_compressed(os.path.join(ROOT, 'data', 'ETHUSDT_1m_2020_2026.npz'), ts=d.ts.values.astype('int64'),
                    open=d.open.values, high=d.high.values, low=d.low.values, close=d.close.values, volume=d.volume.values)
h = hashlib.sha256(open(os.path.join(ROOT, 'data', 'ETHUSDT_1m_2020_2026.npz'), 'rb').read()).hexdigest()
chk['彙整檔'] = dict(path='data/ETHUSDT_1m_2020_2026.npz', sha256=hashlib.sha256(d.to_csv(index=False).encode()).hexdigest(), 說明='sha256 為內容轉 CSV（ts,open,high,low,close,volume）後的雜湊，與壓縮無關', 行數=int(len(d)))
json.dump(chk, open(os.path.join(OUT, 'data_checks_1m.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(chk, ensure_ascii=False, indent=1)[:4000])
