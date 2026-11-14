"""V62 選擇權期限結構、V63 買賣權成交量比 D0。預登記：doc/research_results/v61/v61_plan.md 第 3、4 節
用法：python backtest/research/v62_v63_d0.py"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D, v57b_lowvol as V, v60_d0 as S

def load():
    t = pd.read_csv(os.path.join(C.ROOT, 'data', 'deribit_eth_option_trades_22_24.csv')); t['ts'] = pd.to_datetime(t.ts_ms, unit='ms'); p = t.instrument.str.split('-', expand=True)
    t['exp'] = pd.to_datetime(p[1], format='%d%b%y') + pd.Timedelta(hours=8); t['m'] = np.log(pd.to_numeric(p[2]) / t.index_price); t['cp'] = p[3]; t['dte'] = (t.exp - t.ts) / pd.Timedelta(days=1)
    t['d'] = t.ts.dt.floor('D') + pd.Timedelta(days=1); return t[t.iv > 0]

def term(t):
    x = t[t.m.abs() < 0.05]
    def leg(a, b):
        g = x[(x.dte >= a) & (x.dte <= b)].assign(w=lambda z: z.iv * z.amount).groupby('d'); return g.w.sum() / g.amount.sum(), g.size()
    ni, nn = leg(7, 21); fi, fn = leg(30, 90); j = pd.concat([ni.rename('n'), fi.rename('f'), nn.rename('nn'), fn.rename('fn')], axis=1, sort=True); j = j[(j.nn >= 3) & (j.fn >= 3)]; return (j.n - j.f).dropna()

def pcr(t):
    x = t[(t.dte >= 7) & (t.dte <= 45)]; g = x.groupby('d'); pv = x[x.cp == 'P'].groupby('d').amount.sum(); tot = g.amount.sum(); n = g.size(); r = (pv.reindex(tot.index).fillna(0) / tot); return r[n >= 20].dropna()

def run(name, series, tag, df1, px, sk_sig, OUT):
    sig = S.signal(series); full = pd.date_range('2021-01-01', '2025-12-31', freq='D'); f = full + pd.Timedelta(hours=2)
    df = pd.DataFrame({'sig': sig.reindex(full).fillna(0).astype(int).values, 'r': px.reindex(f + pd.Timedelta(days=1)).values / px.reindex(f).values - 1}, index=full).dropna(subset=['r'])
    print(f'\n##### {name}：日值數 {len(series)}（2021 起 {int((series.index >= "2021-01-01").sum())}），最早 {series.index.min().date()}，各年中位', series.groupby(series.index.year).median().round(3).to_dict())
    res, yt = D.evaluate(df, name)
    rank = pd.Series(V.vol_rank(df1), index=(df1.datetime - pd.Timedelta(hours=7)).values); ms = pd.date_range('2021-01-01', '2025-12-01', freq='MS'); st = rank.reindex(ms, method='ffill')
    lowm = set(st[st < 20].index.to_period('M')); x = df[df.index.to_period('M').isin(lowm)]; lown = D.pos_pnl(x.sig.values, x.r.values)[0]
    res['lowvol_net'] = round(lown); res['K5'] = bool(yt.loc[2021, 'net'] > 0 and yt.loc[2023, 'net'] > 0 and lown > 0)
    k = sk_sig.reindex(full).fillna(0).astype(int).values; both = (df.sig.values != 0) & (k[:len(df)] != 0) if len(df) == len(k) else None
    a = pd.DataFrame({'s': df.sig, 'k': pd.Series(k, index=full).reindex(df.index)}); b = a[(a.s != 0) & (a.k != 0)]; res['overlap_days'] = len(b); res['same_dir_share'] = round((b.s == b.k).mean(), 3) if len(b) else None
    fl = [q for q in ('K1', 'K2', 'K3', 'K4', 'K5') if not res[q]]; res['verdict'] = 'PASS→D1' if not fl else 'REJECTED（D0）：' + ','.join(fl); print('低波動月', round(lown), '與偏斜同向比例', res['same_dir_share'], '重疊日', len(b), '判定', res['verdict'])
    yt.to_csv(os.path.join(OUT, f'{tag}_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, f'{tag}_d0_summary.csv'), index=False); series.rename(tag).to_csv(os.path.join(OUT, f'{tag}_daily.csv'))

if __name__ == '__main__':
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v61'); t = load(); df1 = C.load_1h(end='2026-11-01'); px = C.close_utc(df1); sk_sig = S.signal(S.daily_skew())
    run('V62 期限結構（近減遠，高做空低做多）', term(t), 'v62', df1, px, sk_sig, OUT)
    run('V63 賣權成交量占比（高做空低做多）', pcr(t), 'v63', df1, px, sk_sig, OUT)
