"""V80：事件群敏感度（錨點 3 小時）、引擎逐 gate 紀錄的缺席原因、逐年增量兩種口徑、M1b 區塊拔靴口徑補查。
用法：python backtest/research/v80_events.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C
OUT = L.OUT; GAP = pd.Timedelta(hours=3); E = L.eng; GN = ['持倉中', '日虧熔斷', '月虧熔斷', '連虧冷卻', '進場冷卻', '月進場上限', '時段封鎖', '星期封鎖', '趨勢閘門', '外加遮罩']

def link_single(t): return (t.t_in.diff() > GAP).cumsum().values
def link_anchor(t):
    out = []; c = -1; anchor = None
    for x in t.t_in:
        if anchor is None or x - anchor > GAP: c += 1; anchor = x
        out.append(c)
    return np.array(out)

def summarize(A, col):
    g = A.groupby(col); cl = pd.DataFrame(dict(t0=g.t_in.min(), t1=g.t_in.max(), phs=g.ph.apply(lambda x: tuple(sorted(set(x)))), dup=g.ph.apply(lambda x: x.duplicated().any())))
    cl['類別'] = np.where(cl.phs.apply(lambda p: p == (0,)), '只有整點', np.where(cl.phs.apply(lambda p: 0 in p), '整點與偏移都進場', '只有偏移'))
    cl['增量'] = A[A.ph == 0].groupby(col).net.sum().reindex(cl.index).fillna(0.0) - A[A.ph != 0].groupby(col).net.sum().reindex(cl.index).fillna(0.0) / 3.0
    cl['研究期'] = (cl.t0 >= '2024-07-01') & (cl.t0 < '2026-05-01'); s = cl.增量.sort_values(ascending=False); o = cl[~cl.研究期].增量.sort_values(ascending=False); span = (cl.t1 - cl.t0).dt.total_seconds() / 3600
    return cl, dict(群數=len(cl), 跨度超過3小時的群=int((span > 3).sum()), 最長跨度小時=float(span.max()), 含同相位兩筆的群=int(cl.dup.sum()), 類別增量={k: round(v) for k, v in cl.groupby('類別').增量.sum().items()}, 類別群數=cl.類別.value_counts().to_dict(), 增量合計=round(s.sum()),
                    全期去最大5群=round(s.iloc[5:].sum()), 全期去最大10群=round(s.iloc[10:].sum()), 研究期外增量=round(o.sum()), 研究期外去最大5群=round(o.iloc[5:].sum()), 研究期外去最大10群=round(o.iloc[10:].sum()), 研究期內增量=round(cl[cl.研究期].增量.sum()), 逐年_按事件群進場年={int(k): round(v) for k, v in cl.groupby(cl.t0.dt.year).增量.sum().items()})

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); IND = {ph: L9.indicators(P[ph]) for ph in L.PH}; T = {}; G = {}; R = {}
    for ph in L.PH:
        t, gl = L.book(d15, ct15, P[ph], K[ph], IND[ph], 'F0', 'L', fund=fund, glog=True); T[ph] = L.closed(t).assign(ph=ph); ct = pd.DatetimeIndex((P[ph].datetime - pd.Timedelta(hours=7)).values)
        g = pd.DataFrame(gl, columns=['bar'] + GN); g['t'] = ct[g.bar.values]; g = g[(g.t >= L.W0) & (g.t <= L.W1)]; g['進場'] = ~g[GN].any(axis=1); g['ph'] = ph; G[ph] = g
    GL = pd.concat(G.values()); GL.to_csv(os.path.join(OUT, 'gate_log_long.csv.gz'), index=False)
    R['逐gate紀錄'] = {ph: dict(候選訊號根數=len(g), 進場=int(g.進場.sum()), 交易筆數=len(T[ph]), **{k: int(g[k].sum()) for k in GN}) for ph, g in G.items()}; print(pd.DataFrame(R['逐gate紀錄']).to_string())
    A = pd.concat(T.values()).sort_values('t_in').reset_index(drop=True); A['cl_single'] = link_single(A); A['cl_anchor'] = link_anchor(A)
    c1, R['V79原規則_單連結'] = summarize(A, 'cl_single'); c2, R['敏感度_錨點3小時'] = summarize(A, 'cl_anchor'); print(json.dumps({k: R[k] for k in ('V79原規則_單連結', '敏感度_錨點3小時')}, ensure_ascii=False, indent=1, default=str))
    wk = A.groupby([A.ph, A.t_out.dt.to_period('W-SUN')]).net.sum().unstack(0).fillna(0.0); Dw = wk[0] - wk[[15, 30, 45]].mean(axis=1); R['逐年_按平倉週'] = {int(k): round(v) for k, v in Dw.groupby(Dw.index.end_time.year).sum().items()}
    # 缺席原因（引擎紀錄）：只有整點的事件群（原規則）
    def reason(ph, t0):
        g = G[ph]; w = g[(g.t >= t0 - GAP) & (g.t <= t0 + GAP)]
        if len(w):
            r = w.iloc[0]
            for k in GN:
                if r[k]: return k
            return '有進場紀錄（不應發生）'
        ct = pd.DatetimeIndex((P[ph].datetime - pd.Timedelta(hours=7)).values); a, b = np.searchsorted(ct, t0 - GAP), np.searchsorted(ct, t0 + GAP, side='right')
        return '有突破但壓縮不過' if IND[ph]['brk_up'][a:b].any() else '無突破'
    rs = [dict(cl=c, ph=ph, 原因=reason(ph, r.t0)) for c, r in c1[c1.類別 == '只有整點'].iterrows() for ph in (15, 30, 45)]; W = pd.DataFrame(rs); W.to_csv(os.path.join(OUT, 'absent_reasons_gatelog.csv'), index=False); R['只有整點_偏移缺席原因_引擎紀錄'] = W.原因.value_counts().to_dict()
    rs = [dict(cl=c, 原因=reason(0, r.t0)) for c, r in c1[c1.類別 == '只有偏移'].iterrows()]; R['只有偏移_整點缺席原因_引擎紀錄'] = pd.DataFrame(rs).原因.value_counts().to_dict(); print(R['只有整點_偏移缺席原因_引擎紀錄'], R['只有偏移_整點缺席原因_引擎紀錄'])
    # M1b 6 根：區塊拔靴口徑
    wi = pd.period_range('2021-01-04', '2026-11-01', freq='W-SUN'); S = {}; N = {}
    for ph in L.PH:
        ind = IND[ph]; c = P[ph].close.values; ct = pd.DatetimeIndex((P[ph].datetime - pd.Timedelta(hours=7)).values); msk = ind['brk_up'] & (ind['pctile_L'] < 25) & (ct >= L.W0) & (ct <= L.W1); f = np.full(len(c), np.nan); f[:-6] = (c[6:] / c[:-6] - 1) * 1e4
        s = pd.Series(f[msk], index=ct[msk]).dropna(); g = s.groupby(s.index.to_period('W-SUN')); S[ph] = g.sum().reindex(wi).fillna(0.0).values; N[ph] = g.count().reindex(wi).fillna(0.0).values
    So = S[15] + S[30] + S[45]; No = N[15] + N[30] + N[45]; stat = lambda ix: S[0][ix].sum() / max(N[0][ix].sum(), 1) - So[ix].sum() / max(No[ix].sum(), 1); n = len(wi); obs = stat(np.arange(n)); rng = np.random.default_rng(80); kk = int(np.ceil(n / 4)); bs = []
    for _ in range(5000): st = rng.integers(0, n - 3, kk); bs.append(stat((st[:, None] + np.arange(4)[None, :]).ravel()[:n]))
    bs = np.array(bs); R['M1b_6根'] = dict(觀測差bp=round(float(obs), 2), bootstrap_tail_fraction_未置中=round(float((bs <= 0).mean()), 4), p_置中虛無_單尾=round(float(((bs - bs.mean()) >= obs).mean()), 4), p_置中虛無_雙尾=round(float((np.abs(bs - bs.mean()) >= abs(obs)).mean()), 4), 區間90=[round(float(np.quantile(bs, .05)), 2), round(float(np.quantile(bs, .95)), 2)], 區間95=[round(float(np.quantile(bs, .025)), 2), round(float(np.quantile(bs, .975)), 2)], 比較項數=12, 邦費洛尼門檻=round(0.05 / 12, 4)); print(R['M1b_6根'])
    c1.assign(phs=c1.phs.astype(str)).to_csv(os.path.join(OUT, 'clusters_single.csv')); c2.assign(phs=c2.phs.astype(str)).to_csv(os.path.join(OUT, 'clusters_anchor.csv')); json.dump(R, open(os.path.join(OUT, 'v80_events.json'), 'w'), ensure_ascii=False, indent=1, default=str)
