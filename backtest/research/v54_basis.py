"""V54-A 季度合約基差：訊號建構＋D0 診斷（預登記見 doc/research_results/v54/v54_plan.md）。
用法：python backtest/research/v54_basis.py d0"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C

def daily_basis():
    q = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSD_quarterly_1h.csv'), parse_dates=['open_time_utc', 'expiry_utc'])
    ix = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSD_index_1h.csv'), parse_dates=['open_time_utc']).set_index('open_time_utc').close
    q['dte'] = (q.expiry_utc - q.open_time_utc) / pd.Timedelta(days=1) - 1 / 24  # 收盤時距到期
    f = q[q.dte >= 14].sort_values(['open_time_utc', 'dte']).drop_duplicates('open_time_utc').set_index('open_time_utc')
    b = ((f.close / ix.reindex(f.index) - 1) * 365 / f.dte).dropna()
    b.index = b.index + pd.Timedelta(hours=1)  # 以收盤時間標記
    # 日值：UTC 00:00（含）前 24 根已收盤小時的中位數，至少 12 根
    d = b.groupby((b.index - pd.Timedelta(seconds=1)).floor('D') + pd.Timedelta(days=1)).agg(['median', 'size'])
    return d['median'][d['size'] >= 12]

def pct_rank(d, W, minp=60):
    out = pd.Series(np.nan, index=d.index)
    for i, t in enumerate(d.index):
        h = d[(d.index < t) & (d.index >= t - pd.Timedelta(days=W))]
        if len(h) >= minp: out.iloc[i] = (h < d.iloc[i]).mean()
    return out

def rank_corr(a, b):
    return np.corrcoef(a.rank(), b.rank())[0, 1]

if __name__ == '__main__':
    d = daily_basis(); p = pct_rank(d, 180)
    px = C.close_utc(C.load_1h(end='2026-11-01'))  # index=收盤 UTC
    rows = pd.DataFrame({'basis': d, 'pct': p})
    fill = rows.index + pd.Timedelta(hours=2)  # 00:00 決策，01:00 那根收盤(02:00)成交
    p0 = px.reindex(fill).values
    for H in (3, 7): rows[f'fwd{H}'] = px.reindex(fill + pd.Timedelta(days=H)).values / p0 - 1
    dev = rows[(rows.index >= '2021-01-01') & (rows.index < '2026-01-01')].dropna()
    print('日樣本', len(dev), dev.index.min().date(), '~', dev.index.max().date(), '基差中位', round(dev.basis.median(), 4))
    out = []
    for H in (3, 7):
        r = dev[f'fwd{H}']; row = dict(H=H, all=round(rank_corr(dev.pct, r), 3))
        for y in range(2021, 2026):
            s = dev[dev.index.year == y]; row[y] = round(rank_corr(s.pct, s[f'fwd{H}']), 3)
        top = dev[dev.pct >= 0.8][f'fwd{H}']; bot = dev[dev.pct <= 0.2][f'fwd{H}']
        row['neg_years'] = sum(row[y] < 0 for y in range(2021, 2026))
        row['top_mean%'] = round(top.mean() * 100, 3); row['bot_mean%'] = round(bot.mean() * 100, 3)
        row['ls_gross_per_trade%'] = round((-top.sum() + bot.sum()) / (len(top) + len(bot)) * 100, 3)
        row['n_top'] = len(top); row['n_bot'] = len(bot)
        for y in range(2021, 2026):
            t = top[top.index.year == y]; b = bot[bot.index.year == y]
            row[f'ls{y}%'] = round((-t.sum() + b.sum()) / max(len(t) + len(b), 1) * 100, 2)
        row['K1'] = row['neg_years'] >= 4; row['K2'] = row['ls_gross_per_trade%'] > 0.225; out.append(row)
    o = pd.DataFrame(out); print(o.T.to_string())
    o.to_csv(os.path.join(C.ROOT, 'doc', 'research_results', 'v54', 'v54a_d0.csv'), index=False)
    print('判定', 'PASS→D1' if ((o.K1 & o.K2).any()) else 'REJECTED（D0）')
