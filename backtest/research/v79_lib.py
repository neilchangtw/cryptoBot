"""V79 共用：四相位 K 線、可關閉單邊的固定參數重播、逐筆淨額（含資金費）、區塊拔靴。"""
import os, sys, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v78_d0 as D
eng = V.B.eng; PH = (0, 15, 30, 45); W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v79')
_SRC = inspect.getsource(eng.simulate_v14_detailed)
for old, new in (("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):", "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):"),
                 ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):", "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):")):
    assert _SRC.count(old) == 1; _SRC = _SRC.replace(old, new)

def phases(end='2026-11-01'):
    d15 = D.load_15m(end); return d15, {ph: D.phase_df(d15, ph) for ph in PH}

def indicators(df, tmode='floor'):
    ind = eng.compute_indicators(df)
    if tmode == 'ceil':   # 獨立對照 C1：時段、星期、日、月改用開盤時間進位到下一個整點
        dt = pd.to_datetime(df['datetime']).dt.ceil('h'); ind = dict(ind, hours=dt.dt.hour.values, dows=dt.dt.dayofweek.values, months=(dt.dt.year * 100 + dt.dt.month).values, days=(dt.dt.year * 10000 + dt.dt.month * 100 + dt.dt.day).values)
    return ind

def book(df, ind, side='LS', ov=None, fund=None):
    """side：'LS'、'L'（空方訊號關閉）、'S'。ov：參數覆寫（例如拿掉封鎖）。回傳逐筆（時間為 K 線收盤 UTC）。"""
    ns = dict(eng.__dict__); ns.update(ov or {}); exec(_SRC, ns); n = len(df); ind = dict(ind)
    if side == 'L': ind['okS'] = np.zeros(n, bool)
    if side == 'S': ind['okL'] = np.zeros(n, bool)
    t = pd.DataFrame(ns['simulate_v14_detailed'](ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    if not len(t): return t
    t['t_in'] = pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7); t['t_out'] = pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7); t = t[(t.t_in >= W0) & (t.t_out <= W1)].copy()
    t['ntl'] = t.margin * 20.0; s = np.where(t.side == 'L', 1.0, -1.0); t['gross'] = t.ntl * s * (t.exit_price / t.entry_price - 1); t['cost'] = 0.00225 * t.ntl
    t['fund'] = funding(t, fund) if fund is not None else 0.0; t['net'] = t.gross - t.cost - t.fund; t['year'] = t.t_out.dt.year
    return t.sort_values(['t_in', 'side']).reset_index(drop=True)

def funding(t, fund):
    """(進場, 出場] 內各次結算費率合計 × 進場名目 × 方向（多方付正費率）。"""
    cs = fund.cumsum(); idx = fund.index.values
    a = np.searchsorted(idx, t.t_in.values, side='right'); b = np.searchsorted(idx, t.t_out.values, side='right'); c = np.concatenate([[0.0], cs.values])
    return (c[b] - c[a]) * t.ntl.values * np.where(t.side == 'L', 1.0, -1.0)

def stat(t):
    if not len(t): return dict(筆數=0)
    r = lambda k: t[t.exit_reason == k]
    return dict(筆數=len(t), 勝率=round((t.net > 0).mean() * 100, 2), 淨利=round(t.net.sum()), 每筆淨利=round(t.net.mean(), 2), MH筆=len(r('MH')), MH淨=round(r('MH').net.sum()), 止損筆=len(r('SN')), 止損淨=round(r('SN').net.sum()), TP筆=len(r('TP')), TP淨=round(r('TP').net.sum()), 資金費=round(t.fund.sum(), 1))

def block_boot(x, block=4, n=5000, seed=79):
    """移動區塊拔靴：平均值 ≤0 的單尾 p（以去均值序列重抽，檢定平均 >0）。"""
    x = np.asarray(x, float); m = x.mean(); z = x - m; L = len(x); rng = np.random.default_rng(seed); k = int(np.ceil(L / block)); st = rng.integers(0, L - block + 1, (n, k))
    idx = (st[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :L]; bm = z[idx].mean(axis=1)
    return float(m), float((bm >= m).mean())
