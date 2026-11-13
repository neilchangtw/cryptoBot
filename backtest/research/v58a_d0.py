"""V58-A D0：低波動狀態下的區間回歸。預登記：doc/research_results/v58/v58_plan.md 第 5 節
用法：python backtest/research/v58a_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V
NTL = 4000.0; COST = 9.0

def mr_trades(df, z, allow, W=48, Z=2.0, MH=48):
    """allow[i]=該根收盤可產生進場訊號。回傳 DataFrame(i_sig, side, i_out, ret)。下一根開盤進；收盤 z 回到另一側或滿 MH 根出。"""
    o = df.open.values; c = df.close.values; n = len(c); out = []; free = {1: -1, -1: -1}
    for i in np.flatnonzero(allow & (np.abs(z) >= Z)):
        s = 1 if z[i] <= -Z else -1
        if i <= free[s] or i + 1 >= n: continue
        ep = o[i + 1]; j = i + 1
        while j < n - 1 and j - i < MH and not (s * z[j] >= 0): j += 1
        out.append((i, s, j, s * (c[j] / ep - 1))); free[s] = j
    return pd.DataFrame(out, columns=['i', 's', 'j', 'ret'])

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); c = df.close; z = ((c - c.rolling(48).mean()) / c.rolling(48).std()).values; z = np.nan_to_num(z)
    rank = V.vol_rank(df); low = rank < 20; ct = (df.datetime - pd.Timedelta(hours=7)); win = ((ct >= '2021-01-01') & (ct < '2026-11-01')).values; known = ~np.isnan(rank)
    T = mr_trades(df, z, low & win); T['year'] = ct.dt.year.values[T.i]; T['net'] = T.ret * NTL - COST
    yt = T.groupby('year').agg(n=('net', 'size'), mean_bp=('ret', lambda x: x.mean() * 1e4), wr=('net', lambda x: (x > 0).mean()), net=('net', 'sum'), hold=('j', 'size')); yt['hold'] = (T.j - T.i).groupby(T.year).mean()
    print(yt.round(2).to_string()); print(T.groupby('s').agg(n=('net', 'size'), mean_bp=('ret', lambda x: x.mean() * 1e4), net=('net', 'sum')).round(1).to_string())
    net = T.net.sum(); mean_bp = T.ret.mean() * 1e4; y10 = yt[yt.n >= 10]; k1 = bool((y10.net > 0).sum() >= 4 and yt.net.get(2023, -1) > 0); k2 = bool(mean_bp > 22.5 and net > 0)
    N = mr_trades(df, z, (~low) & known & win); N['net'] = N.ret * NTL - COST; rng = np.random.default_rng(58)
    d = [T.net.values[rng.integers(0, len(T), len(T))].mean() - N.net.values[rng.integers(0, len(N), len(N))].mean() for _ in range(2000)]; p3 = float((np.array(d) <= 0).mean())
    perm = []
    for _ in range(500):
        k = rng.integers(24 * 30, len(low) - 24 * 30); a = mr_trades(df, z, np.roll(low, k) & win); perm.append((a.ret * NTL - COST).sum())
    res = dict(trades=len(T), mean_bp=round(mean_bp, 1), wr=round((T.net > 0).mean(), 3), net=round(net), K1=k1, K2=k2, nonlow_trades=len(N), nonlow_mean_net=round(N.net.mean(), 2), low_mean_net=round(T.net.mean(), 2), K3_p=p3, K3=bool(T.net.mean() > N.net.mean() and p3 < 0.05),
               perm_median=round(np.median(perm)), perm_p95=round(np.quantile(perm, .95)), K4=bool(net > np.quantile(perm, .95)), share_of_3890=round(net / 3890, 3))
    print(res); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v58'); yt.round(3).to_csv(os.path.join(OUT, 'v58a_d0_yearly.csv')); pd.DataFrame([res]).to_csv(os.path.join(OUT, 'v58a_d0_summary.csv'), index=False)
    f = [k for k in ('K1', 'K2', 'K3', 'K4') if not res[k]]; print('判定', 'PASS→D1' if not f else 'REJECTED（D0）：' + ','.join(f))
