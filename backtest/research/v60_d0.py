"""V60 D0：Deribit 選擇權偏斜方向訊號。預登記：doc/research_results/v60/v60_plan.md
用法：python backtest/research/v60_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D, v57b_lowvol as V

def daily_skew(dte=(7, 45)):
    t = pd.read_csv(os.path.join(C.ROOT, 'data', 'deribit_eth_option_trades_22_24.csv')); t['ts'] = pd.to_datetime(t.ts_ms, unit='ms'); p = t.instrument.str.split('-', expand=True)
    t['exp'] = pd.to_datetime(p[1], format='%d%b%y') + pd.Timedelta(hours=8); t['m'] = np.log(pd.to_numeric(p[2]) / t.index_price); t['cp'] = p[3]; t['dte'] = (t.exp - t.ts) / pd.Timedelta(days=1)
    x = t[(t.dte >= dte[0]) & (t.dte <= dte[1]) & (t.iv > 0)].copy(); x['d'] = x.ts.dt.floor('D') + pd.Timedelta(days=1)  # 次日 00:00 決策
    def side(mask):
        g = x[mask].assign(w=lambda z: z.iv * z.amount).groupby('d'); return g.w.sum() / g.amount.sum(), g.size()
    pi, pn = side((x.cp == 'P') & (x.m < -0.03) & (x.m > -0.25)); ci, cn = side((x.cp == 'C') & (x.m > 0.03) & (x.m < 0.25))
    j = pd.concat([pi.rename('p'), ci.rename('c'), pn.rename('pn'), cn.rename('cn')], axis=1, sort=True); j = j[(j.pn >= 3) & (j.cn >= 3)]; return (j.p - j.c).dropna()

def signal(sk, R=180, q=0.2, minp=90):
    out = pd.Series(0, index=sk.index)
    for i, t in enumerate(sk.index):
        h = sk[(sk.index < t) & (sk.index >= t - pd.Timedelta(days=R))]
        if len(h) < minp: continue
        pc = (h < sk.iloc[i]).mean(); out.iloc[i] = -1 if pc >= 1 - q else (1 if pc <= q else 0)
    return out

if __name__ == '__main__':
    sk = daily_skew(); sig = signal(sk); df1 = C.load_1h(end='2026-11-01'); px = C.close_utc(df1)
    full = pd.date_range('2021-01-01', '2025-12-31', freq='D'); f = full + pd.Timedelta(hours=2)
    df = pd.DataFrame({'sig': sig.reindex(full).fillna(0).astype(int).values, 'r': px.reindex(f + pd.Timedelta(days=1)).values / px.reindex(f).values - 1}, index=full).dropna(subset=['r'])
    print('偏斜日值數', len(sk), '各年中位（波動點）', sk.groupby(sk.index.year).median().round(1).to_dict())
    res, yt = D.evaluate(df, 'V60 全期 2021~2025')
    rank = pd.Series(V.vol_rank(df1), index=(df1.datetime - pd.Timedelta(hours=7)).values); ms = pd.date_range('2021-01-01', '2025-12-01', freq='MS'); st = rank.reindex(ms, method='ffill')
    lowm = set(st[st < 20].index.to_period('M')); x = df[df.index.to_period('M').isin(lowm)]; lown = D.pos_pnl(x.sig.values, x.r.values)[0]
    k5 = bool(yt.loc[2021, 'net'] > 0 and yt.loc[2023, 'net'] > 0 and lown > 0); res['lowvol_net'] = round(lown); res['K5'] = k5; print('低波動月淨利', round(lown), 'K5', k5)
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v60'); yt.to_csv(os.path.join(OUT, 'v60_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, 'v60_d0_summary.csv'), index=False); sk.rename('skew').to_csv(os.path.join(OUT, 'v60_daily_skew.csv'))
    f = [k for k in ('K1', 'K2', 'K3', 'K4', 'K5') if not res[k]]; print('判定', 'PASS→D1' if not f else 'REJECTED（D0）：' + ','.join(f))
