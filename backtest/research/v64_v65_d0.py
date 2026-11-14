"""V64 日內時段動能、V65 高成交量日隔日反轉 D0。登記：doc/research_results/v64/v64_plan.md
用法：python backtest/research/v64_v65_d0.py"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D, v57b_lowvol as V
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v64')

def nomerge(sig, r):
    gl = D.NTL * r[sig == 1].sum(); gs = -D.NTL * r[sig == -1].sum(); nl = int((sig == 1).sum()); ns = int((sig == -1).sum()); return gl + gs - D.COST * (nl + ns), gl - D.COST * nl, gs - D.COST * ns, nl + ns

def lowvol_months(df1):
    rank = pd.Series(V.vol_rank(df1), index=(df1.datetime - pd.Timedelta(hours=7)).values); ms = pd.date_range('2021-01-01', '2025-12-01', freq='MS'); st = rank.reindex(ms, method='ffill'); return set(st[st < 20].index.to_period('M'))

def finish(name, tag, df, df1, pnl):
    keep = D.pos_pnl; D.pos_pnl = pnl
    try:
        res, yt = D.evaluate(df, name); x = df[df.index.to_period('M').isin(lowvol_months(df1))]; lown = pnl(x.sig.values, x.r.values)[0]
    finally: D.pos_pnl = keep
    res['lowvol_net'] = round(lown); res['K5'] = bool(yt.loc[2021, 'net'] > 0 and yt.loc[2023, 'net'] > 0 and lown > 0); fl = [q for q in ('K1', 'K2', 'K3', 'K4', 'K5') if not res[q]]
    res['verdict'] = 'PASS→有限帳戶回測' if not fl else 'REJECTED（D0）：' + ','.join(fl); print('低波動月', round(lown), '判定', res['verdict'])
    yt.to_csv(os.path.join(OUT, f'{tag}_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, f'{tag}_d0_summary.csv'), index=False); df.to_csv(os.path.join(OUT, f'{tag}_d0_daily.csv')); return res

def v64(px, q=0.67):
    """px：小時線收盤價，index＝收盤時刻 UTC。美東時間：r1＝09:00→11:00；持倉 14:00→16:00。"""
    et = px.copy(); et.index = et.index.tz_localize('UTC').tz_convert('America/New_York'); days = pd.date_range('2020-10-01', '2025-12-31', freq='D'); rows = []
    for d in days:
        if d.weekday() >= 5: continue
        g = lambda h: et.get(pd.Timestamp(d.date()).tz_localize('America/New_York') + pd.Timedelta(hours=h), np.nan)
        p9, p11, p14, p16 = g(9), g(11), g(14), g(16); rows.append((d, p11 / p9 - 1, p16 / p14 - 1))
    x = pd.DataFrame(rows, columns=['d', 'r1', 'r']).set_index('d').dropna(); thr = x.r1.abs().shift(1).rolling(90, min_periods=60).quantile(q)
    x['sig'] = np.where(x.r1.abs() > thr, np.sign(x.r1), 0).astype(int); return x[x.index >= '2021-01-01']

def v65(df1, ratio=1.5):
    ct = pd.DatetimeIndex((df1.datetime - pd.Timedelta(hours=7)).values); px = pd.Series(df1.close.values, index=ct); usd = pd.Series(df1.volume.values * df1.close.values, index=ct)
    dv = usd.groupby((usd.index - pd.Timedelta(hours=1)).floor('D')).sum()   # 收盤時刻落在 (d 00:00, d+1 00:00] 的 24 根屬於日 d
    dc = px[px.index.hour == 0]; dc.index = dc.index - pd.Timedelta(days=1); rd = dc / dc.shift(1) - 1   # 日 d 收盤對收盤（d+1 00:00 的價）
    med = dv.shift(1).rolling(30, min_periods=30).median(); hot = (dv / med) >= ratio; sig_d = (-np.sign(rd)).where(hot.reindex(rd.index).fillna(False), 0).fillna(0).astype(int)
    full = pd.date_range('2021-01-01', '2025-12-31', freq='D'); dec = full - pd.Timedelta(days=1)   # 決策日 t（00:00）用日 t-1 的資料
    f = full + pd.Timedelta(hours=1); return pd.DataFrame({'sig': sig_d.reindex(dec).fillna(0).astype(int).values, 'r': px.reindex(f + pd.Timedelta(days=1)).values / px.reindex(f).values - 1, 'vol_ratio': (dv / med).reindex(dec).values, 'rd': rd.reindex(dec).values}, index=full).dropna(subset=['r'])

if __name__ == '__main__':
    df1 = C.load_1h(end='2026-11-01'); px = C.close_utc(df1)
    a = v64(px); print('V64 交易日數', int((a.sig != 0).sum()), '／', len(a)); finish('V64 日內時段動能（美東 09~11 → 14~16，|r1|>p67）', 'v64', a, df1, nomerge)
    b = v65(df1); print('\nV65 訊號日數', int((b.sig != 0).sum()), '／', len(b)); finish('V65 高成交量日隔日反轉（量比>=1.5）', 'v65', b, df1, D.pos_pnl)
