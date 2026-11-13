"""V55 D1：美元溢價方向訊號 12 組回測＋前推＋年表。預登記：doc/research_results/v55/v55_plan.md
用法：python backtest/research/v55_d1.py [額外滑價bp，預設0]"""
import os, sys, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D
CUT = '2026-11-01'; STOP = 0.04; GRID = list(itertools.product((90, 180), (0.10, 0.20, 0.30), (1, 3))); DEFAULT = (90, 0.20, 1)

def simulate(sig, df, H, slip_bp=0.0):
    ct = (df.datetime - pd.Timedelta(hours=7)).values; pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values; cl = df.close.values
    out = []; P = {'L': None, 'S': None}; sl = slip_bp / 1e4
    def close(side, i, xp, reason):
        p = P[side]; xp = xp * (1 - sl) if side == 'L' else xp * (1 + sl)
        out.append(dict(side=side, t_in=pd.Timestamp(ct[p['i0']]), t_out=pd.Timestamp(ct[i]), ep=p['ep'], xp=xp, reason=reason)); P[side] = None
    for t, s in sig.items():
        k = np.datetime64(t + pd.Timedelta(hours=2))
        if k not in pos: continue
        i = pos[k]
        for side, want in (('L', 1), ('S', -1)):
            p = P[side]
            if p is not None:
                stopped = False
                for j in range(p['chk'] + 1, i + 1):
                    if side == 'L' and lo[j] <= p['ep'] * (1 - STOP):
                        st = p['ep'] * (1 - STOP); close(side, j, st - 0.25 * (st - lo[j]), 'STOP'); stopped = True; break
                    if side == 'S' and hi[j] >= p['ep'] * (1 + STOP):
                        st = p['ep'] * (1 + STOP); close(side, j, st + 0.25 * (hi[j] - st), 'STOP'); stopped = True; break
                if not stopped:
                    p['chk'] = i
                    if s == want: p['x'] = i + 24 * H
                    elif i >= p['x']: close(side, i, cl[i], 'TIME')
            if P[side] is None and s == want and not (p is not None and out and out[-1]['t_out'] == pd.Timestamp(ct[i]) and out[-1]['reason'] == 'STOP' and out[-1]['side'] == side):
                ep = cl[i] * (1 + sl) if side == 'L' else cl[i] * (1 - sl); P[side] = dict(i0=i, ep=ep, x=i + 24 * H, chk=i)
    for side in 'LS':  # 截止時未平倉：以最後一根收盤結清
        if P[side] is not None: close(side, len(cl) - 1, cl[-1], 'END')
    return pd.DataFrame(out)

def run_all(d, df, fund, slip_bp=0.0, shift=0):
    res = {}; sigs = {}
    for R, q, H in GRID:
        if (R, q) not in sigs:
            s = D.signal(d, R=R, q=q)
            if shift: s = pd.Series(np.roll(s.values, shift), index=s.index)
            sigs[(R, q)] = s
        tr = simulate(sigs[(R, q)], df, H, slip_bp); inc, cost = C.mtm_equity(tr, df, fund); cost['reason'] = tr.reason.values; res[(R, q, H)] = (inc, cost)
    return res

def walk_forward(res):
    bounds = pd.date_range('2021-01-01', '2027-01-01', freq='6MS'); first = min(c.t_in.min() for _, c in res.values()); parts = []; picks = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        k = DEFAULT if (a - first).days < 365 else max(GRID, key=lambda g: res[g][1][res[g][1].t_out < a].net.sum())
        c = res[k][1]; parts.append(c[(c.t_in >= a) & (c.t_in < b)]); picks.append((str(a.date()), k))
    wf = pd.concat(parts).sort_values('t_in'); keep = []; free = {'L': pd.Timestamp('2000-01-01'), 'S': pd.Timestamp('2000-01-01')}
    for r in wf.itertuples():
        if r.t_in >= free[r.side]: keep.append(r.Index); free[r.side] = r.t_out
    return wf.loc[keep].reset_index(drop=True), picks

if __name__ == '__main__':
    slip = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0; OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v55'); tag = '' if slip == 0 else f'_slip{int(slip)}'
    df = C.load_1h(end=CUT); fund = C.load_funding(); d = D.daily_premium(); res = run_all(d, df, fund, slip); rows = []
    for g, (inc, cost) in res.items():
        yt, mt = C.tables(inc, cost, '2026-10'); y = yt.set_index('year').net_mtm; c = cost[cost.t_in >= '2021-01-01']
        rows.append(dict(R=g[0], q=g[1], H=g[2], trades=len(c), stop_rate=round((c.reason == 'STOP').mean(), 2), **{'y%d' % k: int(v) for k, v in y.items()},
                         p2123=int(y.loc[2021:2023].sum()), p2425=int(y.loc[2024:2025].sum()), L=round(c[c.side == 'L'].net.sum()), S=round(c[c.side == 'S'].net.sum())))
    g = pd.DataFrame(rows); print(g.to_string(index=False)); g.to_csv(os.path.join(OUT, f'v55_d1_grid{tag}.csv'), index=False)
    wf, picks = walk_forward(res); print('前推選用', picks)
    inc, cost = C.mtm_equity(wf, df, fund); cost['reason'] = wf.reason.values; yt, mt = C.tables(inc, cost, '2026-10'); print(yt.to_string(index=False))
    c = cost[(cost.t_out < CUT) & (cost.t_in >= '2021-01-01')]
    print('前推合計', round(mt.sum()), '負月', int((mt < 0).sum()), '/', len(mt), '筆數', len(c), '止損率', round((c.reason == 'STOP').mean(), 2), '每筆淨', round(c.net.mean(), 1))
    for s in 'LS':
        x = c[c.side == s]; print(s, '合計', round(x.net.sum()), {int(k): round(v) for k, v in x.groupby(x.t_out.dt.year).net.sum().items()})
    print('最佳月', mt.idxmax(), round(mt.max()), '去最佳月合計', round(mt.sum() - mt.max()), '；兩段皆正組數', int(((g.p2123 > 0) & (g.p2425 > 0)).sum()), '/ 12')
    yt.to_csv(os.path.join(OUT, f'v55_wf_yearly{tag}.csv'), index=False); mt.round(2).to_csv(os.path.join(OUT, f'v55_wf_monthly{tag}.csv')); cost.to_csv(os.path.join(OUT, f'v55_wf_trades{tag}.csv'), index=False)
