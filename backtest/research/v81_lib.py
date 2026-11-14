"""V81 共用：1 分 K 載入、分鐘延遲情境（T 決策、T+k 分成交）的替換引擎重播。"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C
eng = L.eng; PH = L.PH; W0 = L.W0; W1 = L.W1; OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v81'); DELAYS = (0, 1, 2, 5, 15)
_M = None

def m1():
    global _M
    if _M is None:
        z = np.load(os.path.join(C.ROOT, 'data', 'ETHUSDT_1m_2020_2026.npz')); _M = {k: z[k] for k in z.files}; _M['t0'] = int(_M['ts'][0])
        assert (np.diff(_M['ts']) == 60000).all()
    return _M

def mi(t):
    """UTC 時刻 → 開盤時間等於該時刻的 1 分 K 索引。"""
    return ((pd.DatetimeIndex(t).values.astype('datetime64[ms]').astype('int64') - m1()['t0']) // 60000).astype(int)

def px_at(t, k=0):
    """T+k 分的價格＝開盤時間在 [T+k−1, T+k) 的 1 分 K 收盤價。"""
    return m1()['close'][mi(t) + k - 1]

def close_utc(df): return pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values)

def minute_arrays(df, k):
    """替換引擎所需陣列。k=0 不給（沿用原引擎）。"""
    if k == 0: return {}
    M = m1(); i0 = mi(close_utc(df)); n1 = len(M['ts']); assert i0.min() >= 60 and i0.max() + k <= n1
    fe = M['close'][i0 + k - 1]
    a = (i0 - 60 + k)[:, None] + np.arange(60 - k)[None, :]; h1 = M['high'][a].max(axis=1); l1 = M['low'][a].min(axis=1)
    w = i0[:, None] + np.arange(k)[None, :]; dl = M['low'][w].min(axis=1); dh = M['high'][w].max(axis=1)
    return dict(fe=fe, fx=fe, h1=h1, l1=l1, dl=dl, dh=dh, dsn=[])

def book(df, ind0, k=0, side='LS', ov=None, fund=None, ok=None, defer=None, glog=False):
    """defer：None＝k>0 時延後入帳、k=0 即時入帳（主語意）；True／False 強制指定（對照用）。"""
    ns = dict(eng.__dict__); ns.update(ov or {}); exec(L._SRC, ns); n = len(df); ind = dict(ind0); ind.update(minute_arrays(df, k))
    if defer is None: defer = k > 0
    if defer:
        ind['defer'] = True
        if 'dsn' not in ind: ind['dsn'] = []
    if side == 'L': ind['okS'] = np.zeros(n, bool)
    if side == 'S': ind['okL'] = np.zeros(n, bool)
    if ok is not None:
        for kk in ('okL', 'okS'): ind[kk] = ok & ind[kk] if kk in ind else ok
    if glog: ind['glog'] = []; ind['glogS'] = []
    t = pd.DataFrame(ns['simulate_v14_detailed'](ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0)); dly = pd.Timedelta(minutes=k)
    t['t_sig'] = pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7); t['t_in'] = t.t_sig + dly; t['t_dec'] = pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7)
    t['t_out'] = t.t_dec + pd.to_timedelta(np.where(t.exit_reason.isin(['SN', 'OPEN']), 0, k), unit='m'); t['dsn'] = False
    for sd, i in ind.get('dsn', []):
        j = (t.side == sd) & (t.exit_bar == i); t.loc[j, 't_out'] += dly; t.loc[j, 'dsn'] = True
    opn = t.exit_reason == 'OPEN'; t.loc[opn, 'exit_price'] = float(px_at([W1])[0]); t.loc[opn, 't_out'] = W1
    t = t[(t.t_sig >= W0) & (t.t_in <= W1) & (t.t_out <= W1)].copy(); t['ntl'] = t.margin * 20.0; s = np.where(t.side == 'L', 1.0, -1.0); t['gross'] = t.ntl * s * (t.exit_price / t.entry_price - 1); t['cost'] = np.where(t.exit_reason == 'OPEN', 0.001125, 0.00225) * t.ntl
    t['fund'] = L9.funding(t, fund) if fund is not None else 0.0; t['net'] = t.gross - t.cost - t.fund; t['year'] = t.t_out.dt.year; t['k'] = k
    t = t.sort_values(['t_in', 'side']).reset_index(drop=True)
    return (t, ind['glog'], ind['glogS']) if glog else t
