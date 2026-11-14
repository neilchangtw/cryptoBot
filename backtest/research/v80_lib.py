"""V80 共用：成交模型 F0／F1／F2 的完整狀態機重播（對研究引擎原始碼做字串替換）、期末未平倉、逐 gate 紀錄、帳本指標。"""
import os, sys, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v78_d0 as D, v79_lib as L9
eng = V.B.eng; PH = (0, 15, 30, 45); W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v80'); MODELS = ('F0', 'F1', 'F2')

def _patch():
    s = inspect.getsource(eng.simulate_v14_detailed); a, b = s.split('        # --- S EXIT ---')
    def rep(x, old, new):
        assert x.count(old) == 1, (old, x.count(old)); return x.replace(old, new)
    a = rep(a, "            lp_held += 1\n", "            lp_held += 1\n            if lp_held == 1 and 'h1' in ind: hi, li = ind['h1'][i], ind['l1'][i]\n            else: hi, li = h[i], l[i]\n")
    a = rep(a, "l_mkt = ci * (1 - slip) if realistic else ci", "l_mkt = (ind['fx'][i] if 'fx' in ind else ci) * (1 - slip) if realistic else ci")
    a = rep(a, "            if ex_price > 0:\n", "            if ex_price > 0 and ex_reason != 'SN' and 'dl' in ind and ind['dl'][i] <= sn_lv:\n                ex_price = sn_lv - (sn_lv - ind['dl'][i]) * L_SN_SLIP; ex_reason = 'SN'; ind['dsn'].append(('L', i))\n            if ex_price > 0:\n")
    b = rep(b, "            sp_held += 1\n", "            sp_held += 1\n            if sp_held == 1 and 'h1' in ind: hi, li = ind['h1'][i], ind['l1'][i]\n            else: hi, li = h[i], l[i]\n")
    b = rep(b, "s_mkt = ci * (1 + slip) if realistic else ci", "s_mkt = (ind['fx'][i] if 'fx' in ind else ci) * (1 + slip) if realistic else ci")
    b = rep(b, "            if ex_price > 0:\n", "            if ex_price > 0 and ex_reason != 'SN' and 'dh' in ind and ind['dh'][i] >= sn_lv:\n                ex_price = sn_lv + (ind['dh'][i] - sn_lv) * S_SN_SLIP; ex_reason = 'SN'; ind['dsn'].append(('S', i))\n            if ex_price > 0:\n")
    b = rep(b, "lp_entry = ci * (1 + slip) if realistic else ci", "lp_entry = (ind['fe'][i] if 'fe' in ind else ci) * (1 + slip) if realistic else ci")
    b = rep(b, "sp_entry = ci * (1 - slip) if realistic else ci", "sp_entry = (ind['fe'][i] if 'fe' in ind else ci) * (1 - slip) if realistic else ci")
    b = rep(b, "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):", "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):")
    b = rep(b, "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):", "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):")
    b = rep(b, "        # L entry: signal bar close", "        if 'glog' in ind and brk_up[i] and not np.isnan(pL[i]) and pL[i] < L_GK_TH:\n            ind['glog'].append((i, bool(lp_active), bool(d_pnl <= CB_DAILY * cbs), bool(l_m_pnl <= CB_L_MONTH * cbs), bool(i < consec_end), bool(i - l_last_exit < L_CD), bool(l_m_entries >= L_CAP), bool(hr in L_BLK_H), bool(dw in L_BLK_D), bool(rblk_l is not None and rblk_l[i]), bool('okL' in ind and not ind['okL'][i])))\n        # L entry: signal bar close")
    b = rep(b, "    return trades\n", "    if lp_active: trades.append(dict(side='L', margin=round(lp_ntl / 20.0, 2), entry_bar=lp_bar, exit_bar=n - 1, entry_dt=datetimes[lp_bar], exit_dt=datetimes[n - 1], entry_price=round(lp_entry, 2), exit_price=round(c[n - 1], 2), exit_reason='OPEN', bars_held=lp_held, entry_regime=lp_regime))\n    if sp_active: trades.append(dict(side='S', margin=round(sp_ntl / 20.0, 2), entry_bar=sp_bar, exit_bar=n - 1, entry_dt=datetimes[sp_bar], exit_dt=datetimes[n - 1], entry_price=round(sp_entry, 2), exit_price=round(c[n - 1], 2), exit_reason='OPEN', bars_held=sp_held, entry_regime=sp_regime))\n    return trades\n")
    return a + '        # --- S EXIT ---' + b
_SRC = _patch()

def load(end='2026-11-14'):
    """15 分 K（到資料末端，供延遲成交查價）與四相位 1 小時 K（只留收盤不晚於 W1 者）。回傳 d15, P, K（各相位每根對應的 15 分 K 起始索引）。"""
    d15 = D.load_15m(end); ct15 = pd.DatetimeIndex((d15.datetime - pd.Timedelta(hours=8) + pd.Timedelta(minutes=15)).values); P = {}; K = {}
    for ph in PH:
        df = D.phase_df(d15, ph); cu = df.datetime - pd.Timedelta(hours=7); df = df[cu <= W1].reset_index(drop=True); P[ph] = df; K[ph] = int(ph / 15) + 4 * np.arange(len(df))
    return d15, ct15, P, K

def fill_arrays(d15, k0, model):
    """k0：各 1 小時 K 的第一根 15 分 K 索引。回傳加入 ind 的陣列。"""
    if model == 'F0': return {}
    o, h, l, c = (d15[x].values for x in ('open', 'high', 'low', 'close')); nx = k0 + 4
    if model == 'F1': return dict(fe=o[nx], fx=o[nx])
    h1 = np.maximum.reduce([h[k0 + 1], h[k0 + 2], h[k0 + 3]]); l1 = np.minimum.reduce([l[k0 + 1], l[k0 + 2], l[k0 + 3]])
    return dict(fe=c[nx], fx=c[nx], h1=h1, l1=l1, dl=l[nx], dh=h[nx], dsn=[])

def book(d15, ct15, df, k0, ind0, model='F0', side='LS', ov=None, fund=None, ok=None, glog=False):
    ns = dict(eng.__dict__); ns.update(ov or {}); exec(_SRC, ns); n = len(df); ind = dict(ind0); ind.update(fill_arrays(d15, k0, model))
    if side == 'L': ind['okS'] = np.zeros(n, bool)
    if side == 'S': ind['okL'] = np.zeros(n, bool)
    if ok is not None:
        for k in ('okL', 'okS'): ind[k] = ok & ind[k] if k in ind else ok
    if glog: ind['glog'] = []
    t = pd.DataFrame(ns['simulate_v14_detailed'](ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0)); dly = pd.Timedelta(minutes=15 if model == 'F2' else 0)
    t['t_sig'] = pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7); t['t_in'] = t.t_sig + dly; t['t_out'] = pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7) + pd.to_timedelta(np.where(t.exit_reason.isin(['SN', 'OPEN']), 0, 15 if model == 'F2' else 0), unit='m')
    if model == 'F2' and ind['dsn']:   # 延遲期內觸及止損：出場時刻記為 T+15 分
        for sd, i in ind['dsn']: t.loc[(t.side == sd) & (t.exit_bar == i), 't_out'] += dly
    opn = t.exit_reason == 'OPEN'; pW1 = float(d15.close.values[np.searchsorted(ct15, W1)]); t.loc[opn, 'exit_price'] = pW1; t.loc[opn, 't_out'] = W1
    t = t[(t.t_sig >= W0) & (t.t_in <= W1) & (t.t_out <= W1)].copy(); t['ntl'] = t.margin * 20.0; s = np.where(t.side == 'L', 1.0, -1.0); t['gross'] = t.ntl * s * (t.exit_price / t.entry_price - 1); t['cost'] = np.where(t.exit_reason == 'OPEN', 0.001125, 0.00225) * t.ntl
    t['fund'] = L9.funding(t, fund) if fund is not None else 0.0; t['net'] = t.gross - t.cost - t.fund; t['year'] = t.t_out.dt.year; t['model'] = model
    t = t.sort_values(['t_in', 'side']).reset_index(drop=True)
    return (t, ind['glog']) if glog else t

def closed(t): return t[t.exit_reason != 'OPEN']

def mtm_hourly(t, d15, ct15):
    """逐時市價權益增量（已實現淨額於出場時入帳，持倉期間計未實現毛損益）。"""
    hrs = pd.date_range(W0, W1, freq='h'); px = pd.Series(d15.close.values, index=ct15).reindex(hrs).ffill().values; eq = np.zeros(len(hrs)); hv = hrs.values
    for r in t.itertuples():
        a = np.searchsorted(hv, np.datetime64(r.t_in), side='left'); b = np.searchsorted(hv, np.datetime64(r.t_out), side='left'); s = 1.0 if r.side == 'L' else -1.0
        if b > a: eq[a:b] += r.ntl * s * (px[a:b] / r.entry_price - 1)
        if r.exit_reason != 'OPEN': eq[b:] += r.net
        else: eq[b:] += r.gross - r.cost - r.fund
    return pd.Series(eq, index=hrs)

def metrics(t, d15=None, ct15=None):
    c = closed(t).sort_values(['t_out', 't_in']); o = dict(筆數=len(c), 勝=int((c.net > 0).sum()), 敗=int((c.net < 0).sum()), 平=int((c.net == 0).sum()))
    if not len(c): return o
    r = lambda k: c[c.exit_reason == k]; w = 0.0; s = 0.0; mn = 0; n = 0
    for x in c.net.values:
        if x < 0: s += x; n += 1; w = min(w, s); mn = max(mn, n)
        else: s = 0.0; n = 0
    mo = c.groupby(c.t_out.dt.to_period('M')).net.sum().reindex(pd.period_range('2021-01', '2026-10', freq='M')).fillna(0.0); m5 = mo[mo.index >= '2021-11']; d5 = c[c.t_out >= '2021-11-01'].groupby(c.t_out.dt.floor('D')).net.sum().cumsum()
    per = np.where(c.t_out < '2024-07-01', 'A', np.where(c.t_out < '2026-05-01', 'B', 'C'))
    o.update(勝率=round((c.net > 0).mean() * 100, 2), 淨利=round(c.net.sum()), 平均贏=round(c[c.net > 0].net.mean(), 1), 平均輸=round(c[c.net < 0].net.mean(), 1), 每筆淨利=round(c.net.mean(), 2), MH筆=len(r('MH')), MH淨=round(r('MH').net.sum()), 止損筆=len(r('SN')), 止損淨=round(r('SN').net.sum()), TP筆=len(r('TP')),
             **{f'y{y}': round(v) for y, v in c.groupby('year').net.sum().items()}, 研究期內=round(c[per == 'B'].net.sum()), 研究期外=round(c[per != 'B'].net.sum()), 最長連敗=mn, 最差連敗段=round(w), 最差滾動12月_五年口徑=round(m5.rolling(12).sum().min()), 回撤_五年已實現口徑=round((np.maximum(d5.cummax(), 0) - d5).max()), 資金費=round(c.fund.sum(), 1))
    op = t[t.exit_reason == 'OPEN']; o.update(期末未平倉筆=len(op), 期末未實現毛=round(op.gross.sum(), 1), 含期末估值淨利=round(t.net.sum()))
    if d15 is not None: e = mtm_hourly(t, d15, ct15); o['回撤_全期逐時市價權益'] = round((np.maximum(e.cummax(), 0) - e).max())
    return o
