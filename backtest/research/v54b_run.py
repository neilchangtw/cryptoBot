"""V54-B 基差順向：8 組回測＋滾動前推＋年表（預登記見 v54_plan.md）。用法：python backtest/research/v54b_run.py [direction=1|-1]"""
import os, sys, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v54_basis as B
CUT = '2026-11-01'; STOP = 0.04; GRID = list(itertools.product((90, 180), (0.10, 0.20), (3, 7))); DEFAULT = (90, 0.20, 7)

def simulate(pct, df, q, H, direction=1):
    """direction=1：高百分位做多、低百分位做空；-1 相反。"""
    ct = (df.datetime - pd.Timedelta(hours=7)).values; pos = {t: i for i, t in enumerate(ct)}
    hi = df.high.values; lo = df.low.values; cl = df.close.values; free = {'L': pd.Timestamp('2000-01-01'), 'S': pd.Timestamp('2000-01-01')}; out = []
    for t, p in pct.dropna().items():
        if p >= 1 - q: side = 'L' if direction == 1 else 'S'
        elif p <= q: side = 'S' if direction == 1 else 'L'
        else: continue
        tin = t + pd.Timedelta(hours=2); tx = tin + pd.Timedelta(days=H)
        if tin < free[side] or np.datetime64(tin) not in pos or np.datetime64(tx) not in pos: continue
        i0 = pos[np.datetime64(tin)]; i1 = pos[np.datetime64(tx)]; ep = cl[i0]; xp = cl[i1]; iout = i1; reason = 'TIME'
        for i in range(i0 + 1, i1 + 1):
            if side == 'L' and lo[i] <= ep * (1 - STOP):
                s = ep * (1 - STOP); xp = s - 0.25 * (s - lo[i]); iout = i; reason = 'STOP'; break
            if side == 'S' and hi[i] >= ep * (1 + STOP):
                s = ep * (1 + STOP); xp = s + 0.25 * (hi[i] - s); iout = i; reason = 'STOP'; break
        tout = pd.Timestamp(ct[iout]); free[side] = tout if reason == 'TIME' else tout + pd.Timedelta(minutes=1)
        out.append(dict(side=side, t_in=tin, t_out=tout, ep=ep, xp=xp, reason=reason))
    return pd.DataFrame(out)

def run_all(direction=1, pcts=None, df=None, fund=None):
    df = C.load_1h(end=CUT) if df is None else df; fund = C.load_funding() if fund is None else fund
    if pcts is None:
        d = B.daily_basis(); pcts = {W: B.pct_rank(d, W) for W in (90, 180)}
    res = {}
    for W, q, H in GRID:
        tr = simulate(pcts[W], df, q, H, direction); inc, cost = C.mtm_equity(tr, df, fund); cost['reason'] = tr.reason.values; res[(W, q, H)] = (inc, cost)
    return res, df, fund

def walk_forward(res):
    bounds = pd.date_range('2021-01-01', '2027-01-01', freq='6MS'); first = min(c.t_in.min() for _, c in res.values()); parts = []; picks = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if (a - first).days < 365: k = DEFAULT
        else: k = max(GRID, key=lambda g: res[g][1][res[g][1].t_out < a].net.sum())
        c = res[k][1]; parts.append(c[(c.t_in >= a) & (c.t_in < b)]); picks.append((str(a.date()), k))
    wf = pd.concat(parts).sort_values('t_in'); keep = []; free = {'L': pd.Timestamp('2000-01-01'), 'S': pd.Timestamp('2000-01-01')}
    for r in wf.itertuples():
        if r.t_in >= free[r.side]: keep.append(r.Index); free[r.side] = r.t_out
    return wf.loc[keep].reset_index(drop=True), picks

if __name__ == '__main__':
    direction = int(sys.argv[1]) if len(sys.argv) > 1 else 1; OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v54')
    res, df, fund = run_all(direction); rows = []
    for g, (inc, cost) in res.items():
        yt, mt = C.tables(inc, cost, '2026-10'); y = yt.set_index('year').net_mtm
        rows.append(dict(W=g[0], q=g[1], H=g[2], trades=len(cost), stop_rate=round((cost.reason == 'STOP').mean(), 2), **{'y%d' % k: int(v) for k, v in y.items()},
                         p2123=int(y.loc[2021:2023].sum()), p2425=int(y.loc[2024:2025].sum()), L=round(cost[cost.side == 'L'].net.sum()), S=round(cost[cost.side == 'S'].net.sum())))
    g8 = pd.DataFrame(rows); print(g8.to_string(index=False)); g8.to_csv(os.path.join(OUT, 'v54b_grid.csv'), index=False)
    wf, picks = walk_forward(res); print('前推選用', picks)
    inc, cost = C.mtm_equity(wf, df, fund); cost['reason'] = wf.reason.values; yt, mt = C.tables(inc, cost, '2026-10')
    print(yt.to_string(index=False)); c = cost[cost.t_out < '2026-11-01']
    print('前推合計', round(mt.sum()), '負月', int((mt < 0).sum()), '/', len(mt), '筆數', len(c), '止損率', round((c.reason == 'STOP').mean(), 2), '每筆淨', round(c.net.mean(), 1))
    for s in 'LS':
        x = c[c.side == s]; print(s, '合計', round(x.net.sum()), '2021~2025', round(x[x.t_out.dt.year <= 2025].net.sum()), dict(x.groupby(x.t_out.dt.year).net.sum().round()))
    yt.to_csv(os.path.join(OUT, 'v54b_wf_yearly.csv'), index=False); mt.round(2).to_csv(os.path.join(OUT, 'v54b_wf_monthly.csv')); cost.to_csv(os.path.join(OUT, 'v54b_wf_trades.csv'), index=False)
    both = int(((g8.p2123 > 0) & (g8.p2425 > 0)).sum()); print('G4 兩段皆正組數', both, '/ 8')
