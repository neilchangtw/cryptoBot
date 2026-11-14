"""V72-A 診斷：以 Deribit 選擇權偏斜方向把現行策略進場分為順向／中性／逆向。登記：doc/research_results/v72/v72_plan.md。
用法：python backtest/research/v72a_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v60_d0 as D60
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v72'); V69 = os.path.join(C.ROOT, 'doc', 'research_results', 'v69')

def classify(t, sig):
    """sig：index＝決策日 UTC 00:00。進場時刻 t_in 使用 (t_in − 2h) 所在日的值。"""
    day = (t.t_in - pd.Timedelta(hours=2)).dt.floor('D'); v = sig.reindex(day).fillna(0).values; s = np.where(t.side == 'L', 1, -1); return np.where(v == 0, 0, np.where(v == s, 1, -1))

def stat(t, cls):
    o = cls == -1; return t.net[o].mean() - t.net[~o].mean()

def run(name, t, sig, full):
    cls = classify(t, sig); t = t.assign(cls=cls, year=t.t_out.dt.year); o = t.cls == -1; obs = stat(t, cls); rng = np.random.default_rng(72); n = len(full); sv = sig.reindex(full).fillna(0).values; sims = []
    for k in rng.integers(30, n - 30, 2000): sims.append(stat(t, classify(t, pd.Series(np.roll(sv, k), index=full))))
    p = float((np.array(sims) <= obs).mean())
    g = t.groupby('cls').agg(n=('net', 'size'), net=('net', 'sum'), avg=('net', 'mean'), wr=('net', lambda x: (x > 0).mean() * 100), mh=('reason', lambda x: (x == 'MH').mean() * 100), mhnet=('net', lambda x: x[t.loc[x.index, 'reason'] == 'MH'].sum())).round(1).rename(index={-1: '逆向', 0: '中性', 1: '順向'})
    yr = []
    for y, x in t.groupby('year'): a = x[x.cls == -1]; b = x[x.cls != -1]; yr.append(dict(年=int(y), 逆向筆=len(a), 逆向每筆=round(a.net.mean(), 1) if len(a) else None, 其餘筆=len(b), 其餘每筆=round(b.net.mean(), 1), 逆向合計=round(a.net.sum())))
    Y = pd.DataFrame(yr); v = Y[Y.逆向筆 >= 8]; k2 = int((v.逆向每筆 < v.其餘每筆).sum()); sd = {}
    for s in 'LS': x = t[t.side == s]; sd[s] = dict(逆向筆=int((x.cls == -1).sum()), 逆向每筆=round(x[x.cls == -1].net.mean(), 1), 其餘每筆=round(x[x.cls != -1].net.mean(), 1), 逆向MH比=round((x[x.cls == -1].reason == 'MH').mean() * 100, 1), 其餘MH比=round((x[x.cls != -1].reason == 'MH').mean() * 100, 1))
    K = dict(樣本=bool(o.sum() >= 60 and (Y.逆向筆 >= 8).sum() >= 4), K1=p < 0.05, K2=bool(len(v) >= 4 and k2 >= max(4, int(np.ceil(len(v) * 2 / 3)))), K3=bool((t[o].reason == 'MH').mean() > (t[~o].reason == 'MH').mean()), K4=bool(t[o].net.sum() <= 0), K5=bool(all(sd[s]['逆向每筆'] < sd[s]['其餘每筆'] for s in 'LS')))
    print(f'\n===== {name} ====='); print(g.to_string()); print(Y.to_string(index=False)); print('多空', json.dumps(sd, ensure_ascii=False)); print('主要指標 逆向−其餘 每筆', round(obs, 2), '置換 p', p, '平移中位', round(float(np.median(sims)), 2)); print('判定', K)
    t.to_csv(os.path.join(OUT, f'd0_{name}_classified.csv'), index=False); return dict(版本=name, 主要指標=round(obs, 2), p=p, **K, 逆向筆=int(o.sum()), 逆向合計=round(t[o].net.sum()), 順向每筆=round(t[t.cls == 1].net.mean(), 1), 中性每筆=round(t[t.cls == 0].net.mean(), 1))

if __name__ == '__main__':
    sk = D60.daily_skew(); sig = D60.signal(sk); full = pd.date_range('2021-01-01', '2026-10-31', freq='D'); rows = []
    for name, f in (('Q20W30', 'd0_Q20W30_trades.csv'), ('現行原配置', 'd0_基準_trades.csv')):
        t = pd.read_csv(os.path.join(V69, f), parse_dates=['t_in', 't_out']); rows.append(run(name, t, sig, full))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v72a_d0_summary.csv'), index=False)
