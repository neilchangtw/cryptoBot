"""V56 D0：標記價對成交價偏離事件後的回復。預登記：doc/research_results/v56/v56_plan.md
用法：python backtest/research/v56_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
NTL = 4000.0; COST = 9.0

def load():
    df = C.load_1h(end='2026-11-01'); k = df.set_index(df.datetime - pd.Timedelta(hours=8))[['open', 'high', 'low', 'close']]
    m = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_mark_1h.csv'), parse_dates=['open_time_utc']).set_index('open_time_utc')
    j = k.join(m[['h', 'l']], how='left')
    j['dn'] = (j.l - j.low) / j.close * 1e4; j['up'] = (j.high - j.h) / j.close * 1e4
    j['lw'] = (j[['open', 'close']].min(axis=1) - j.low) / j.close * 1e4; j['uw'] = (j.high - j[['open', 'close']].max(axis=1)) / j.close * 1e4
    return j

def fwd(j, H):
    """第 i 根收盤後：下一根開盤進、持有 H 根、於第 i+H 根收盤出。回傳做多報酬。"""
    return (j.close.shift(-H) / j.open.shift(-1) - 1).values

def trades(flag_l, flag_s, r, H):
    """併發限制：每邊一筆，持倉中忽略同向事件。回傳 (索引, 方向, 報酬)。"""
    out = []; free = {1: -1, -1: -1}
    for i in np.flatnonzero(flag_l | flag_s):
        if flag_l[i] and flag_s[i]: continue
        s = 1 if flag_l[i] else -1
        if i <= free[s] or np.isnan(r[i]): continue
        out.append((i, s, s * r[i])); free[s] = i + H
    return out

if __name__ == '__main__':
    X = 30; H = 6; j = load(); r = fwd(j, H); yrs = j.index.year.values
    dev = (j.index >= '2021-01-01') & (j.index < '2026-01-01')
    fl = (j.dn.values > X) & dev; fs = (j.up.values > X) & dev
    tr = trades(fl, fs, r, H); T = pd.DataFrame(tr, columns=['i', 's', 'ret']); T['year'] = yrs[T.i]
    yt = T.groupby('year').agg(n=('ret', 'size'), mean_bp=('ret', lambda x: x.mean() * 1e4), win=('ret', lambda x: (x > 0).mean()), long_n=('s', lambda x: (x == 1).sum()))
    yt['net'] = T.groupby('year').ret.sum() * NTL - COST * yt.n; yt['need_per_trade'] = 3600 / yt.n; yt['got_per_trade'] = yt.net / yt.n
    print(yt.round(2).to_string())
    for s, nm in ((1, '多'), (-1, '空')):
        x = T[T.s == s]; print(nm, 'n', len(x), 'mean_bp', round(x.ret.mean() * 1e4, 1), 'net', round(x.ret.sum() * NTL - COST * len(x)))
    mean_bp = T.ret.mean() * 1e4; net = T.ret.sum() * NTL - COST * len(T)
    k1 = int((yt.mean_bp > 0).sum()); k2 = bool(mean_bp > 22.5 and net > 0)
    # K3 配對對照
    ctrl = np.zeros(len(T)); quiet = (j.dn.values < 10) & (j.up.values < 10) & ~np.isnan(r)
    for n, (i, s, _) in enumerate(tr):
        w = j.lw.values if s == 1 else j.uw.values; pool = np.flatnonzero(quiet & (yrs == yrs[i]))
        near = pool[np.argsort(np.abs(w[pool] - w[i]))[:5]]; ctrl[n] = (s * r[near]).mean()
    diff = T.ret.values - ctrl; rng = np.random.default_rng(56); bs = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(2000)]
    p3 = float((np.array(bs) <= 0).mean())
    print('事件均', round(mean_bp, 1), 'bp；配對對照均', round(ctrl.mean() * 1e4, 1), 'bp；差', round(diff.mean() * 1e4, 1), 'bp；p', p3)
    print('事件下影線中位', round(np.median([j.lw.values[i] for i, s, _ in tr if s == 1]), 1), '對照池可用', int(quiet.sum()))
    # K4 環狀平移
    perm = []
    for _ in range(2000):
        k = rng.integers(24 * 30, len(j) - 24 * 30); a = trades(np.roll(fl, k) & dev, np.roll(fs, k) & dev, r, H); perm.append(sum(x[2] for x in a) * NTL - COST * len(a))
    p4 = float((np.array(perm) >= net).mean())
    res = dict(X=X, H=H, trades=len(T), mean_bp=round(mean_bp, 1), net=round(net), K1_pos_years=k1, K1=k1 >= 4, K2=k2, ctrl_bp=round(ctrl.mean() * 1e4, 1), K3_p=p3, K3=bool(diff.mean() > 0 and p3 < 0.05),
               perm_median=round(np.median(perm)), perm_p95=round(np.quantile(perm, .95)), K4_p=p4, K4=bool(net > np.quantile(perm, .95)))
    print(res); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v56'); yt.round(3).to_csv(os.path.join(OUT, 'v56_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, 'v56_d0_summary.csv'), index=False)
    fails = [k for k in ('K1', 'K2', 'K3', 'K4') if not res[k]]; print('判定', 'PASS→D1' if not fails else 'REJECTED（D0）：' + ','.join(fails))
