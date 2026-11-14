"""V80 追加拆解（結果後登記，描述性）：F2 的影響拆成「只延後進場」與「只延後市價出場」，各自完整重播；以及 F0 出場時刻後 15 分鐘的價格漂移。
用法：python backtest/research/v80_decomp.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C
if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); orig = L.fill_arrays
    def fa(d15, k0, model):
        if model == 'F2e': x = orig(d15, k0, 'F2'); return {k: x[k] for k in ('fe', 'h1', 'l1')}
        if model == 'F2x': x = orig(d15, k0, 'F2'); return {k: x[k] for k in ('fx', 'dl', 'dh', 'dsn')}
        return orig(d15, k0, model)
    rows = []; dr = []; c15 = pd.Series(d15.close.values, index=ct15)
    for ph in L.PH:
        ind = L9.indicators(P[ph]); n = len(P[ph])
        for side in ('L', 'S'):
            r = dict(相位=f':{ph:02d}', 邊=side)
            for m in ('F0', 'F2e', 'F2x', 'F2'):
                ns = dict(L.eng.__dict__); exec(L._SRC, ns); i2 = dict(ind); i2.update(fa(d15, K[ph], m)); i2['okS' if side == 'L' else 'okL'] = np.zeros(n, bool)
                t = pd.DataFrame(ns['simulate_v14_detailed'](i2, P[ph]['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0)); t = t[t.exit_reason != 'OPEN']; t = t[pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7) >= L.W0]
                s = np.where(t.side == 'L', 1.0, -1.0); r[dict(F0='F0', F2e='只延後進場', F2x='只延後市價出場', F2='F2 兩者')[m] + '_淨（不含資金費）'] = round(float((t.margin * 20 * s * (t.exit_price / t.entry_price - 1) - 9).sum())); r[m + '_筆'] = len(t)
            rows.append(r)
        t = L.closed(L.book(d15, ct15, P[ph], K[ph], ind, 'F0', 'LS', fund=fund)); e = c15.reindex(t.t_in + pd.Timedelta(minutes=15)).values / c15.reindex(t.t_in).values - 1; s = np.where(t.side == 'L', 1, -1)
        for sd in ('L', 'S'): dr.append(dict(相位=f':{ph:02d}', 邊=sd, 進場後15分順向bp=round(float((s * e)[t.side == sd].mean() * 1e4), 2), 筆數=int((t.side == sd).sum())))
    R = pd.DataFrame(rows); R.to_csv(os.path.join(L.OUT, 'f2_decomposition.csv'), index=False); print(R.to_string(index=False)); Dr = pd.DataFrame(dr); Dr.to_csv(os.path.join(L.OUT, 'post_entry_drift.csv'), index=False); print(Dr.to_string(index=False))
