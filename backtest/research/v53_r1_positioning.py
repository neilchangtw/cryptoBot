"""V53 R1：大戶對散戶持倉差，多空都做。用法：python backtest/research/v53_r1_positioning.py [dev|holdout]"""
import os, sys, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
MODE = sys.argv[1] if len(sys.argv) > 1 else 'dev'
END = '2026-01-01' if MODE == 'dev' else '2026-11-01'
df = C.load_1h(END); T = pd.DatetimeIndex(df.datetime); c = df.close.values; hi = df.high.values; lo = df.low.values; n = len(c)
m = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_metrics_1h_2021_2026.csv'), index_col=0, parse_dates=True)
D = (m.count_toptrader_long_short_ratio - m.count_long_short_ratio).reindex(T).values   # 開盤時已知的值，收盤才用 = 延後 1 小時
RK = {W: pd.Series(D).rolling(W, min_periods=W // 2).rank(pct=True).values for W in (720, 1440)}
GRID = list(itertools.product((720, 1440), (0.1, 0.2), (24, 72), (0.05, 0.08)))
def sim(W, q, H, SL, slip_bps=2):
    r = RK[W]; slip = slip_bps / 1e4; out = []; pos = 0; last = -99
    i0 = int(np.searchsorted(T.values, np.datetime64('2021-12-01')))
    for i in range(i0, n):
        sig = 0 if np.isnan(r[i]) else (1 if r[i] > 1 - q else (-1 if r[i] < q else 0))
        if pos:
            x = None
            if pos > 0 and lo[i] <= ep * (1 - SL): x = ep * (1 - SL * 1.25)
            elif pos < 0 and hi[i] >= ep * (1 + SL): x = ep * (1 + SL * 1.25)
            elif i >= due:
                if sig == pos: due = i + H
                else: x = c[i] * (1 - pos * slip)
            if x is not None:
                out.append(('L' if pos > 0 else 'S', T[eb] - pd.Timedelta(hours=7), T[i] - pd.Timedelta(hours=7), ep, x)); pos = 0; last = i
            continue
        if sig and i - last >= 6: pos = sig; eb = i; ep = c[i] * (1 + sig * slip); due = i + H
    return pd.DataFrame(out, columns=['side', 't_in', 't_out', 'ep', 'xp'])
def net(t):
    s = np.where(t.side == 'L', 1, -1); return s * (t.xp / t.ep - 1) * C.NTL - C.FEE - C.EXTRA   # 選參用，不含 funding
def wf(G, folds):
    parts = []; picks = []
    for a, b in zip(folds[:-1], folds[1:]):
        best = max(G, key=lambda k: net(G[k][G[k].t_out < a]).sum()); t = G[best]; parts.append(t[(t.t_in >= a) & (t.t_in < b)]); picks.append((str(a.date()), best))
    return pd.concat(parts).sort_values('t_in').reset_index(drop=True), picks
if __name__ == '__main__':
    folds = [pd.Timestamp(f'{y}-{mm:02d}-01') for y in range(2022, 2028) for mm in (1, 7)][1:]; folds = [f for f in folds if f <= pd.Timestamp('2026-01-01' if MODE == 'dev' else '2027-01-01')]
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v53'); fund = C.load_funding()
    for slip in (2, 5):
        G = {g: sim(*g, slip_bps=slip) for g in GRID}; w, picks = wf(G, folds)
        inc, cost = C.mtm_equity(w, df, fund); cost['half'] = cost.t_in.dt.year.astype(str) + 'H' + ((cost.t_in.dt.month > 6) + 1).astype(str)
        seg = lambda a, b: cost[(cost.t_in >= a) & (cost.t_in < b)].net.sum()
        print(f'[{MODE}] slip {slip}bp 前推 筆數 {len(cost)} 淨利 {cost.net.sum():.0f} 多 {cost[cost.side=="L"].net.sum():.0f} 空 {cost[cost.side=="S"].net.sum():.0f} funding {cost.funding.sum():.0f}')
        print('  半年', cost.groupby('half').net.sum().round().to_dict())
        print('  D1 段', round(seg('2022-07-01', '2024-01-01')), round(seg('2024-01-01', '2026-01-01')), '保留期', round(seg('2026-01-01', '2027-01-01')))
        if slip == 2:
            print('  picks', picks)
            a = np.array([net(G[g][G[g].t_in < '2024-01-01']).sum() for g in GRID]); b = np.array([net(G[g][(G[g].t_in >= '2024-01-01') & (G[g].t_in < '2026-01-01')]).sum() for g in GRID])
            print('  D2 兩段都賺 %', round(((a > 0) & (b > 0)).mean() * 100), 'A>0', round((a > 0).mean() * 100), 'B>0', round((b > 0).mean() * 100), '中位', round(np.median(a)), round(np.median(b)))
            if MODE != 'dev':
                h = np.array([net(G[g][G[g].t_in >= '2026-01-01']).sum() for g in GRID]); print('  H2 保留期 >0 %', round((h > 0).mean() * 100), '中位', round(np.median(h)))
            # D4 隨機時點
            d = cost[cost.t_in < '2026-01-01']; px = C.close_utc(df); pv = px.values; i_lo = px.index.searchsorted(pd.Timestamp('2022-07-01')); i_hi = px.index.searchsorted(pd.Timestamp('2026-01-01'))
            hrs = ((d.t_out - d.t_in).dt.total_seconds() / 3600).astype(int).values; sg = np.where(d.side == 'L', 1, -1); rng = np.random.default_rng(53); sims = []
            for _ in range(1000):
                s0 = rng.integers(i_lo, i_hi - 200, len(d)); e0 = np.minimum(s0 + hrs, i_hi - 1); sims.append((sg * (pv[e0] / pv[s0] - 1) * C.NTL - 9).sum())
            print('  D4 隨機 97.5 百分位', round(np.percentile(sims, 97.5)), '平均', round(np.mean(sims)), '實際(開發期)', round(d.net.sum()))
            yt, mt = C.tables(inc, cost, last_full_month='2025-12' if MODE == 'dev' else '2026-10'); print(yt[yt.year >= 2022].to_string(index=False))
            cost.round(4).to_csv(os.path.join(OUT, f'r1_trades_{MODE}.csv'), index=False); yt.to_csv(os.path.join(OUT, f'r1_yearly_{MODE}.csv'), index=False)
