"""V76 D0：強平賣壓耗盡後反向（免費資料：Binance 幣本位 ETHUSD_PERP 清算快照 2023-06-25~2024-10-14；報酬用 ETHUSDT 永續）。
登記：doc/research_results/v76/v76_plan.md。用法：python backtest/research/v76_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v76'); Q = 0.99; LOOK = 90 * 24; MINP = 30 * 24; DELAY = 1; HOLD = 24; COST = 0.00225

def load():
    u = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSD_PERP_cm_liquidation_2023_2024.csv')); u['t'] = pd.to_datetime(u.time, unit='ms'); u['usd'] = u.original_quantity * 10.0
    h = u.groupby([u.t.dt.floor('h') + pd.Timedelta(hours=1), 'side']).usd.sum().unstack(fill_value=0.0)   # index＝該小時結束時刻
    df = C.load_1h(end='2026-11-01'); px = C.close_utc(df); idx = px[(px.index >= '2023-06-25 04:00') & (px.index <= '2024-10-14 06:00')].index
    X = pd.DataFrame(index=idx); X['LL'] = h.get('SELL', pd.Series(dtype=float)).reindex(idx).fillna(0.0); X['SL'] = h.get('BUY', pd.Series(dtype=float)).reindex(idx).fillna(0.0)
    X['ret'] = (px / px.shift(1) - 1).reindex(idx); X['fwd'] = (px.shift(-(DELAY + HOLD)) / px.shift(-DELAY) - 1).reindex(idx); X['r24'] = (px.shift(1) / px.shift(25) - 1).reindex(idx); X['r30d'] = (px.shift(1) / px.shift(721) - 1).reindex(idx)
    ct = df.datetime - pd.Timedelta(hours=7); tr = pd.Series((np.maximum(df.high, df.close.shift(1)) - np.minimum(df.low, df.close.shift(1))).values / df.close.shift(1).values, index=ct); X['atr'] = tr.rolling(14).mean().reindex(idx)
    v = pd.Series(df.volume.values, index=ct); X['volr'] = (v / v.shift(1).rolling(480).mean()).reindex(idx)
    for k in ('LL', 'SL'): X[k + '_th'] = X[k].shift(1).rolling(LOOK, min_periods=MINP).quantile(Q)
    f = C.load_funding(); cf = f.cumsum().reindex(px.index, method='ffill').fillna(0.0); X['fund'] = (cf.shift(-(DELAY + HOLD)) - cf.shift(-DELAY)).reindex(idx)
    return X.dropna(subset=['LL_th', 'SL_th', 'fwd', 'ret'])

def nonoverlap(times):
    out = []; busy = pd.Timestamp('1970-01-01')
    for t in times:
        if t >= busy: out.append(t); busy = t + pd.Timedelta(hours=DELAY + HOLD)
    return out

def ols_cluster(y, X, g):
    b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b; XtXi = np.linalg.inv(X.T @ X); M = np.zeros((X.shape[1], X.shape[1]))
    for k in np.unique(g): s = X[g == k].T @ e[g == k]; M += np.outer(s, s)
    V = XtXi @ M @ XtXi; return b, np.sqrt(np.diag(V))

if __name__ == '__main__':
    from math import erf, sqrt
    X = load(); print('有效小時數', len(X), '期間', X.index[0], '~', X.index[-1], '；有多單強平的小時', int((X.LL > 0).sum()), '有空單強平的小時', int((X.SL > 0).sum()))
    X['sigL'] = (X.LL >= X.LL_th) & (X.LL > 0) & (X.ret < 0); X['sigS'] = (X.SL >= X.SL_th) & (X.SL > 0) & (X.ret > 0); X['side'] = np.where(X.sigL, 1, np.where(X.sigS, -1, 0))
    ev = X.loc[nonoverlap(X.index[X.side != 0])].copy(); ev['gross'] = ev.side * ev.fwd; ev['net'] = ev.gross - COST - ev.side * ev.fund; print('原始訊號小時 多/空', int(X.sigL.sum()), int(X.sigS.sum()), '；不重疊事件', len(ev), '多', int((ev.side > 0).sum()), '空', int((ev.side < 0).sum()))
    # 對照組 A
    ctl = []
    for s, lk, th in ((1, 'LL', 'LL_th'), (-1, 'SL', 'SL_th')):
        e = ev[ev.side == s]
        if len(e) < 5: continue
        lo, hi = e.ret.quantile(.1), e.ret.quantile(.9); c = X[(X.ret >= lo) & (X.ret <= hi) & (X[lk] < X[th]) & (X.side == 0)]; c = X.loc[nonoverlap(c.index)].assign(cs=s); ctl.append(c)
    ctl = pd.concat(ctl); ctl['gross'] = ctl.cs * ctl.fwd
    rng = np.random.default_rng(76); a = ev.gross.values; b = ctl.gross.values; d = np.array([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(2000)]); p2 = float((d <= 0).mean())
    # 迴歸（多空堆疊：反向報酬＝side×fwd；多方樣本 ret<0、空方樣本 ret>0）
    rows = []
    for s, lk, sg in ((1, 'LL', 'sigL'), (-1, 'SL', 'sigS')):
        m = X[(X.ret * s < 0)].dropna(subset=['r24', 'atr', 'volr', 'r30d']); rows.append(pd.DataFrame(dict(y=s * m.fwd, flag=m[sg].astype(float), absret=m.ret.abs(), r24=s * m.r24, atr=m.atr, volr=m.volr, r30d=s * m.r30d, day=m.index.floor('D').astype('int64'), s=s)))
    Rg = pd.concat(rows); reg = {}
    for nm, g in (('合併', Rg), ('多方', Rg[Rg.s == 1]), ('空方', Rg[Rg.s == -1])):
        Z = np.c_[np.ones(len(g)), g[['flag', 'absret', 'r24', 'atr', 'volr', 'r30d']].values]; bb, se = ols_cluster(g.y.values, Z, g.day.values); t = bb[1] / se[1]; reg[nm] = dict(樣本=len(g), 旗標數=int(g.flag.sum()), 係數bp=round(bb[1] * 1e4, 1), t=round(float(t), 2), 單尾p=round(0.5 * (1 - erf(t / sqrt(2))), 4))
    mid = X.index[0] + (X.index[-1] - X.index[0]) / 2; h1 = ev[ev.index < mid].gross; h2 = ev[ev.index >= mid].gross
    res = dict(事件數=len(ev), 多=int((ev.side > 0).sum()), 空=int((ev.side < 0).sum()), 平均毛報酬bp=round(ev.gross.mean() * 1e4, 1), 平均淨報酬bp=round(ev.net.mean() * 1e4, 1), 勝率=round((ev.net > 0).mean() * 100, 1), 中位毛bp=round(ev.gross.median() * 1e4, 1),
               多方毛bp=round(ev[ev.side > 0].gross.mean() * 1e4, 1), 空方毛bp=round(ev[ev.side < 0].gross.mean() * 1e4, 1), 對照A事件數=len(ctl), 對照A平均毛bp=round(ctl.gross.mean() * 1e4, 1), 事件減對照bp=round((ev.gross.mean() - ctl.gross.mean()) * 1e4, 1), p對照=p2, 迴歸=reg,
               前半=dict(n=len(h1), 毛bp=round(h1.mean() * 1e4, 1) if len(h1) else None), 後半=dict(n=len(h2), 毛bp=round(h2.mean() * 1e4, 1) if len(h2) else None), 扣最佳5個平均淨bp=round((ev.net.sum() - ev.net.nlargest(5).sum()) / max(len(ev) - 5, 1) * 1e4, 1), 事件訊號小時平均報酬pct=round(ev.ret.abs().mean() * 100, 2), 事件平均強平名目=round(float(np.where(ev.side > 0, ev.LL, ev.SL).mean())),
               逐季=ev.groupby(ev.index.to_period('Q').astype(str)).gross.agg(['count', 'mean']).assign(mean=lambda z: (z['mean'] * 1e4).round(1)).rename(columns={'count': 'n', 'mean': '毛bp'}).to_dict('index'))
    K = dict(K0=len(ev) >= 40, K1=ev.net.mean() > 0, K2=p2 < 0.10, K3=bool(reg['合併']['係數bp'] > 0 and reg['合併']['單尾p'] < 0.10), K4=bool(len(h1) and len(h2) and h1.mean() > 0 and h2.mean() > 0), K5=res['扣最佳5個平均淨bp'] > 0); res['K'] = {k: bool(v) for k, v in K.items()}
    res['判定'] = ('樣本不足，無法判定' if not K['K0'] else ('PASS' if all(K.values()) else 'REJECTED：' + ','.join(k for k, v in K.items() if not v)))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str)); json.dump(res, open(os.path.join(OUT, 'v76_d0.json'), 'w'), ensure_ascii=False, indent=1, default=str); ev.to_csv(os.path.join(OUT, 'v76_d0_events.csv'))
