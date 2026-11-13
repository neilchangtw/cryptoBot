"""V59-A D0：穩定幣淨發行（DefiLlama 總流通量 7 日變動率的百分位）方向訊號。預登記：doc/research_results/v59/v59_plan.md
用法：python backtest/research/v59a_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D, v57b_lowvol as V

def signal(chg=7, R=180, q=0.2, minp=90):
    s = pd.read_csv(os.path.join(C.ROOT, 'data', 'stablecoins_total_daily.csv'), parse_dates=['date_utc']).set_index('date_utc').circulating_usd
    g = s.pct_change(chg).shift(1)  # 決策日 D 只用戳記 D-1 以前的值
    out = pd.Series(0, index=g.index)
    for i, t in enumerate(g.index):
        if np.isnan(g.iloc[i]): continue
        h = g[(g.index < t) & (g.index >= t - pd.Timedelta(days=R))].dropna()
        if len(h) < minp: continue
        pc = (h < g.iloc[i]).mean(); out.iloc[i] = 1 if pc >= 1 - q else (-1 if pc <= q else 0)
    return out

if __name__ == '__main__':
    sig = signal(); df1 = C.load_1h(end='2026-11-01'); px = C.close_utc(df1)
    full = pd.date_range('2021-01-01', '2025-12-31', freq='D'); f = full + pd.Timedelta(hours=2)
    df = pd.DataFrame({'sig': sig.reindex(full).fillna(0).astype(int).values, 'r': px.reindex(f + pd.Timedelta(days=1)).values / px.reindex(f).values - 1}, index=full).dropna(subset=['r'])
    res, yt = D.evaluate(df, 'V59-A 全期 2021~2025')
    rank = pd.Series(V.vol_rank(df1), index=(df1.datetime - pd.Timedelta(hours=7)).values); ms = pd.date_range('2021-01-01', '2025-12-01', freq='MS'); st = rank.reindex(ms, method='ffill')
    lowm = set(st[st < 20].index.to_period('M')); x = df[df.index.to_period('M').isin(lowm)]; lown = D.pos_pnl(x.sig.values, x.r.values)[0]
    k5 = bool(yt.loc[2021, 'net'] > 0 and yt.loc[2023, 'net'] > 0 and lown > 0); res['lowvol_months'] = len(lowm); res['lowvol_net'] = round(lown); res['K5'] = k5; print('低波動月', len(lowm), '淨利', round(lown), 'K5', k5)
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v59'); yt.to_csv(os.path.join(OUT, 'v59a_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, 'v59a_d0_summary.csv'), index=False)
    f = [k for k in ('K1', 'K2', 'K3', 'K4', 'K5') if not res[k]]; print('判定', 'PASS→D1' if not f else 'REJECTED（D0）：' + ','.join(f))
