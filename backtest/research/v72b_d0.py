"""V72-B 診斷：突破時現貨成交占比（3 根合計）在過去 720 根的百分位，≤1/3 為合約帶動。登記：doc/research_results/v72/v72b_plan.md。
用法：python backtest/research/v72b_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v72'); V69 = os.path.join(C.ROOT, 'doc', 'research_results', 'v69')

def feature(win=3, look=720):
    df = C.load_1h(end='2026-11-01'); pv = pd.Series(df.volume.values, index=pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values))   # index＝小時線收盤 UTC
    sp = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_spot_1h.csv'), parse_dates=['open_time_utc']); sv = pd.Series(sp.volume.values, index=sp.open_time_utc + pd.Timedelta(hours=1)).reindex(pv.index)
    sh = sv.rolling(win).sum() / (sv.rolling(win).sum() + pv.rolling(win).sum()); x = sh.values; pct = np.full(len(x), np.nan)
    for i in range(look, len(x)):
        h = x[i - look:i]; h = h[~np.isnan(h)]
        if len(h) >= look // 2 and not np.isnan(x[i]): pct[i] = (h < x[i]).mean()
    return pd.Series(pct, index=pv.index), sh

def cls_of(t, pct, lo=1 / 3, hi=2 / 3):
    v = pct.reindex(t.t_in).values; return np.where(np.isnan(v), 0, np.where(v <= lo, -1, np.where(v >= hi, 1, 0)))

def run(name, t, pct, labels=('合約帶動', '中性', '現貨帶動'), tag='b'):
    cls = cls_of(t, pct); t = t.assign(cls=cls, year=t.t_out.dt.year); o = t.cls == -1; f = lambda c: t.net[c == -1].mean() - t.net[c != -1].mean(); obs = f(cls); rng = np.random.default_rng(72); pv = pct[(pct.index >= '2020-12-01')]; sims = []
    for k in rng.integers(720, len(pv) - 720, 2000): sims.append(f(cls_of(t, pd.Series(np.roll(pv.values, k), index=pv.index))))
    p = float((np.array(sims) <= obs).mean())
    g = t.groupby('cls').agg(n=('net', 'size'), net=('net', 'sum'), avg=('net', 'mean'), wr=('net', lambda x: (x > 0).mean() * 100), mh=('reason', lambda x: (x == 'MH').mean() * 100), mhnet=('net', lambda x: x[t.loc[x.index, 'reason'] == 'MH'].sum())).round(1).rename(index={-1: labels[0], 0: labels[1], 1: labels[2]})
    yr = []
    for y, x in t.groupby('year'): a = x[x.cls == -1]; b = x[x.cls != -1]; yr.append(dict(年=int(y), 目標筆=len(a), 目標每筆=round(a.net.mean(), 1) if len(a) else None, 其餘筆=len(b), 其餘每筆=round(b.net.mean(), 1), 目標合計=round(a.net.sum())))
    Y = pd.DataFrame(yr); v = Y[Y.目標筆 >= 8]; k2 = int((v.目標每筆 < v.其餘每筆).sum()); sd = {}
    for s in 'LS': x = t[t.side == s]; sd[s] = dict(目標筆=int((x.cls == -1).sum()), 目標每筆=round(x[x.cls == -1].net.mean(), 1), 其餘每筆=round(x[x.cls != -1].net.mean(), 1), 目標MH比=round((x[x.cls == -1].reason == 'MH').mean() * 100, 1), 其餘MH比=round((x[x.cls != -1].reason == 'MH').mean() * 100, 1))
    K = dict(樣本=bool(o.sum() >= 60 and (Y.目標筆 >= 8).sum() >= 4), K1=p < 0.05, K2=bool(len(v) >= 4 and k2 >= max(4, int(np.ceil(len(v) * 2 / 3)))), K3=bool((t[o].reason == 'MH').mean() > (t[~o].reason == 'MH').mean()), K4=bool(t[o].net.sum() <= 0), K5=bool(all(sd[s]['目標每筆'] < sd[s]['其餘每筆'] for s in 'LS')))
    print(f'\n===== {name} ====='); print(g.to_string()); print(Y.to_string(index=False)); print('多空', json.dumps(sd, ensure_ascii=False)); print('主要指標 目標−其餘 每筆', round(obs, 2), '置換 p', p, '平移中位', round(float(np.median(sims)), 2)); print('判定', K)
    t.to_csv(os.path.join(OUT, f'd0{tag}_{name}_classified.csv'), index=False); return dict(版本=name, 主要指標=round(obs, 2), p=p, **K, 目標筆=int(o.sum()), 目標合計=round(t[o].net.sum()))

if __name__ == '__main__':
    pct, sh = feature(); print('現貨占比 各年中位', sh.groupby(sh.index.year).median().round(3).to_dict(), '缺值', int(sh['2021':].isna().sum())); rows = []
    for name, f in (('Q20W30', 'd0_Q20W30_trades.csv'), ('現行原配置', 'd0_基準_trades.csv')):
        t = pd.read_csv(os.path.join(V69, f), parse_dates=['t_in', 't_out']); rows.append(run(name, t, pct))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v72b_d0_summary.csv'), index=False)
