"""V78 追加描述性診斷：V47 參數格點在四個取樣相位的淨利（取樣時刻依賴是否為現行參數特有）。
用法：python backtest/research/v78_grid.py   （約 10~20 分）"""
import os, sys, json, inspect, itertools, warnings
import numpy as np, pandas as pd
from multiprocessing import Pool
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v78_d0 as D
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v78'); eng = V.B.eng
LG = [dict(L_GK_TH=g, L_BRK=b, L_TP=tp, L_MH=mh) for g, b, tp, mh in itertools.product((15, 25, 35), (10, 15, 20), (0.025, 0.035, 0.045), (4, 6, 8))]
SG = [dict(S_GK_TH=g, S_BRK=b, S_TP=tp, S_MH=mh) for g, b, tp, mh in itertools.product((25, 35, 45), (10, 15, 20), (0.015, 0.02, 0.03), (8, 10, 12))]
DF = {}
def init():
    d15 = D.load_15m()
    for ph in D.PH: DF[ph] = D.phase_df(d15, ph)
def run(ov, ph):
    ns = dict(eng.__dict__); ns.update(ov); exec(inspect.getsource(eng.compute_indicators), ns); exec(inspect.getsource(eng.simulate_v14_detailed), ns)
    df = DF[ph]; t = pd.DataFrame(ns['simulate_v14_detailed'](ns['compute_indicators'](df), df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    t['x'] = pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7); t['e'] = pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7); return t[(t.e >= '2021-01-01') & (t.x <= '2026-10-31 23:00')]
def job(a):
    side, k, ov, ph = a; t = run(ov, ph); s = t if side == 'LS' else t[t.side == side]; y = s.groupby(s.x.dt.year).pnl_usd.sum()
    return dict(side=side, k=k, ph=ph, **{kk: str(v) for kk, v in ov.items()}, net=round(s.pnl_usd.sum(), 1), n=len(s), wr=round((s.pnl_usd > 0).mean() * 100, 2), **{f'y{yy}': round(v) for yy, v in y.items()})
if __name__ == '__main__':
    jobs = [('L', k, o, ph) for k, o in enumerate(LG) for ph in D.PH] + [('S', k, o, ph) for k, o in enumerate(SG) for ph in D.PH]
    jobs += [('LS', 'live', {}, ph) for ph in D.PH] + [('LS', 'noHourBlock', dict(L_BLK_H=set(), S_BLK_H=set()), ph) for ph in D.PH] + [('LS', 'noHourDayBlock', dict(L_BLK_H=set(), S_BLK_H=set(), L_BLK_D=set(), S_BLK_D=set()), ph) for ph in D.PH]
    with Pool(4, initializer=init) as p: res = p.map(job, jobs, chunksize=4)
    R = pd.DataFrame(res); R.to_csv(os.path.join(OUT, 'grid_phases.csv'), index=False); S = {}
    for side, live in (('L', dict(L_GK_TH='25', L_BRK='15', L_TP='0.035', L_MH='6')), ('S', dict(S_GK_TH='35', S_BRK='15', S_TP='0.02', S_MH='10'))):
        g = R[R.side == side]; P = g.pivot(index='k', columns='ph', values='net'); off = P[[15, 30, 45]].mean(axis=1); lk = g[(g[list(live)] == pd.Series(live)).all(axis=1)].k.iloc[0]
        S[side] = dict(相位0最佳比例=round(float((P.idxmax(axis=1) == 0).mean()), 3), 相位0最差比例=round(float((P.idxmin(axis=1) == 0).mean()), 3), 各相位格點中位數={int(c): round(float(P[c].median())) for c in P}, 各相位獲利組比例={int(c): round(float((P[c] > 0).mean()), 3) for c in P},
                       相位0減偏移平均_中位數=round(float((P[0] - off).median())), 現行參數各相位淨利={int(c): round(float(P.loc[lk, c])) for c in P}, 現行參數各相位名次={int(c): int((P[c] > P.loc[lk, c]).sum() + 1) for c in P})
    for k in ('live', 'noHourBlock', 'noHourDayBlock'): g = R[R.k == k]; S[k] = {int(r.ph): [r.net, int(r.n), r.wr] for r in g.itertuples()}
    print(json.dumps(S, ensure_ascii=False, indent=1)); json.dump(S, open(os.path.join(OUT, 'grid_summary.json'), 'w'), ensure_ascii=False, indent=1)
