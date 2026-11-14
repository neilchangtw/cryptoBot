"""V67 放量下跌後隔日做多：訊號診斷與四組對照。登記：doc/research_results/v67/v67_plan.md
用法：python backtest/research/v67_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v67'); NTL = 4000.0; COST = 9.0

def daily(df1):
    """回傳日表（index＝日 d，UTC）：rd、量比、vol20、trend、nxt（d+1 01:00 → d+2 01:00 報酬）。全部欄位於 d+1 00:00 已知，nxt 除外。"""
    ct = pd.DatetimeIndex((df1.datetime - pd.Timedelta(hours=7)).values); px = pd.Series(df1.close.values, index=ct); usd = pd.Series(df1.volume.values * df1.close.values, index=ct)
    dv = usd.groupby((usd.index - pd.Timedelta(hours=1)).floor('D')).sum(); n = usd.groupby((usd.index - pd.Timedelta(hours=1)).floor('D')).size(); dv = dv[n == 24]
    c0 = px[px.index.hour == 0]; dc = pd.Series(c0.values, index=c0.index - pd.Timedelta(days=1))   # 日 d 的收盤＝d+1 00:00 的價
    rd = dc / dc.shift(1) - 1; ratio = dv / dv.shift(1).rolling(30, min_periods=30).median()
    vol20 = rd.shift(1).rolling(20, min_periods=20).std(); trend = dc / dc.shift(1).rolling(50, min_periods=50).mean() - 1
    ent = dc.index + pd.Timedelta(days=1, hours=1); nxt = pd.Series(px.reindex(ent + pd.Timedelta(days=1)).values / px.reindex(ent).values - 1, index=dc.index)
    return pd.DataFrame(dict(rd=rd, ratio=ratio, vol20=vol20, trend=trend, nxt=nxt)).dropna()

def pos_net(sig, r, extra_bp=0.0):
    """連續訊號日合併為一筆；回傳（淨利, 筆數, 逐筆淨利陣列）。extra_bp 為額外單邊滑價。"""
    sig = np.asarray(sig, bool); r = np.asarray(r, float); c = COST + 2 * extra_bp / 1e4 * NTL; out = []; cur = None
    for s, x in zip(sig, r):
        if s: cur = (cur or 0.0) + NTL * x
        elif cur is not None: out.append(cur - c); cur = None
    if cur is not None: out.append(cur - c)
    out = np.array(out); return out.sum(), len(out), out

def nw_ols(y, X, lag=5):
    X = np.column_stack([np.ones(len(y)), X]); b = np.linalg.lstsq(X, y, rcond=None)[0]; e = y - X @ b; XtXi = np.linalg.inv(X.T @ X); S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1); G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None]); S += w * (G + G.T)
    V = XtXi @ S @ XtXi; se = np.sqrt(np.diag(V)); return b, se, b / se

def norm_sf(t):
    from math import erf, sqrt
    return 0.5 * (1 - erf(t / sqrt(2)))

if __name__ == '__main__':
    df1 = C.load_1h(end='2026-11-01'); D = daily(df1); D['hv'] = D.ratio >= 1.5; D['down'] = D.rd < 0; D['sig'] = D.hv & D.down
    A = D['2021-01-01':'2025-12-31'].copy(); R = {}
    print('樣本日數', len(A), '下跌日', int(A.down.sum()), '放量下跌日', int(A.sig.sum()), '一般量下跌日', int((A.down & ~A.hv).sum()))
    net, n, tr = pos_net(A.sig, A.nxt); R['main'] = dict(net=round(net), positions=n, days=int(A.sig.sum()), mean_bp=round(A.nxt[A.sig].mean() * 1e4, 1), wr=round((tr > 0).mean(), 3)); print('主要規則', R['main'])
    # C1 一般跌後做多
    n1, k1, _ = pos_net(A.down, A.nxt); R['C1_all_down'] = dict(net=round(n1), positions=k1, days=int(A.down.sum()), mean_bp=round(A.nxt[A.down].mean() * 1e4, 1), mean_bp_normal_vol_down=round(A.nxt[A.down & ~A.hv].mean() * 1e4, 1)); print('C1', R['C1_all_down'])
    # C2a 迴歸（下跌日內）
    d = A[A.down]; b, se, t = nw_ols(d.nxt.values, np.column_stack([d.hv.astype(float), d.rd.abs(), d.vol20, d.trend]))
    R['C2a'] = dict(n=len(d), coef_bp={k: round(v * 1e4, 1) for k, v in zip(['const', 'HV', 'abs_rd(每1.0)', 'vol20(每1.0)', 'trend(每1.0)'], b)}, t={k: round(v, 2) for k, v in zip(['const', 'HV', 'abs_rd', 'vol20', 'trend'], t)}, HV_p_one_sided=round(norm_sf(t[1]), 4)); print('C2a', R['C2a'])
    # C2b 配對
    hv = d[d.hv]; nv = d[~d.hv]; mu = d[['rd', 'vol20']].abs().mean(); sd = d[['rd', 'vol20']].abs().std(); pairs = []
    for t0, r in hv.iterrows():
        pool = nv[np.sign(nv.trend) == np.sign(r.trend)]
        if not len(pool): continue
        dist = np.sqrt(((pool.rd.abs() - abs(r.rd)) / sd.rd) ** 2 + ((pool.vol20 - r.vol20) / sd.vol20) ** 2); j = dist.idxmin()
        if dist[j] <= 1.0: pairs.append((t0, j, r.nxt, pool.nxt[j], dist[j], abs(r.rd), abs(pool.rd[j])))
    P = pd.DataFrame(pairs, columns=['hv_day', 'match_day', 'hv_nxt', 'match_nxt', 'dist', 'hv_absrd', 'match_absrd']); P.to_csv(os.path.join(OUT, 'v67_matched_pairs.csv'), index=False); diff = P.hv_nxt - P.match_nxt
    rng = np.random.default_rng(67); boot = [diff.sample(len(diff), replace=True, random_state=int(rng.integers(1e9))).mean() for _ in range(2000)]
    R['C2b'] = dict(hv_days=len(hv), matched=len(P), matched_share=round(len(P) / len(hv), 3), unique_controls=int(P.match_day.nunique()), mean_diff_bp=round(diff.mean() * 1e4, 1), share_positive=round((diff > 0).mean(), 3), boot_p_le0=round((np.array(boot) <= 0).mean(), 4),
                    mean_absrd_hv=round(P.hv_absrd.mean() * 100, 2), mean_absrd_match=round(P.match_absrd.mean() * 100, 2)); print('C2b', R['C2b'])
    # C3 隨機做多（環狀平移）
    s = A.sig.values; perm = []
    for _ in range(2000): perm.append(pos_net(np.roll(s, int(rng.integers(30, len(s) - 30))), A.nxt.values)[0])
    R['C3'] = dict(real=round(net), p95=round(np.quantile(perm, .95)), median=round(np.median(perm)), p=round((np.array(perm) >= net).mean(), 4)); print('C3', R['C3'])
    # C4 多頭曝險
    frac = A.sig.mean(); bh = NTL * A.nxt.sum(); R['C4'] = dict(time_in_market=round(frac, 3), buy_hold_full_gross=round(bh), buy_hold_scaled_gross=round(bh * frac), signal_gross=round(NTL * A.nxt[A.sig].sum()), all_day_mean_bp=round(A.nxt.mean() * 1e4, 1)); print('C4', R['C4'])
    # 逐年
    rows = []
    for y in range(2021, 2026):
        x = A[A.index.year == y]; nn, kk, _ = pos_net(x.sig, x.nxt)
        rows.append(dict(year=y, sig_days=int(x.sig.sum()), positions=kk, net=round(nn), hv_down_bp=round(x.nxt[x.sig].mean() * 1e4, 1), normal_down_bp=round(x.nxt[x.down & ~x.hv].mean() * 1e4, 1), all_down_bp=round(x.nxt[x.down].mean() * 1e4, 1), all_day_bp=round(x.nxt.mean() * 1e4, 1), eth_year_ret=round((1 + x.rd).prod() - 1, 3)))
    Y = pd.DataFrame(rows); Y.to_csv(os.path.join(OUT, 'v67_d0_yearly.csv'), index=False); print(Y.to_string(index=False))
    G = dict(G1=bool(b[1] > 0 and R['C2a']['HV_p_one_sided'] < 0.05), G2=bool(diff.mean() > 0 and R['C2b']['matched_share'] >= 0.70), G3=bool((Y.hv_down_bp > Y.normal_down_bp).sum() >= 4), G4=bool(net > 0 and net > R['C3']['p95']), G5=bool((Y.hv_down_bp > Y.all_day_bp).sum() >= 4))
    R['gates'] = G; fails = [k for k, v in G.items() if not v]; R['verdict'] = '診斷通過→配置與有限帳戶' if not fails else '停止：' + ','.join(fails) + ' 未過'; print('GATES', G, R['verdict'])
    # 穩健性（只報告）
    rob = {}
    for nm, m in (('trend>0', A.trend > 0), ('trend<=0', A.trend <= 0)):
        x = A[m]; nn, kk, _ = pos_net(x.sig, x.nxt); rob[nm] = dict(net=round(nn), positions=kk, hv_down_bp=round(x.nxt[x.sig].mean() * 1e4, 1), normal_down_bp=round(x.nxt[x.down & ~x.hv].mean() * 1e4, 1))
    for bp in (0, 2, 5): rob[f'extra_{bp}bp'] = round(pos_net(A.sig, A.nxt, bp)[0])
    srt = np.sort(tr)[::-1]; rob['drop_top5'] = round(srt[5:].sum()); rob['drop_top10'] = round(srt[10:].sum()); rob['top5_sum'] = round(srt[:5].sum())
    for q in (1.25, 2.0):
        sg = (A.ratio >= q) & A.down; nn, kk, _ = pos_net(sg, A.nxt); rob[f'ratio_{q}'] = dict(net=round(nn), positions=kk, mean_bp=round(A.nxt[sg].mean() * 1e4, 1))
    B = D['2026-01-01':'2026-10-30']; nn, kk, t26 = pos_net(B.sig, B.nxt); rob['2026_01_10_single_look'] = dict(net=round(nn), positions=kk, sig_days=int(B.sig.sum()), hv_down_bp=round(B.nxt[B.sig].mean() * 1e4, 1) if B.sig.any() else None, normal_down_bp=round(B.nxt[B.down & ~B.hv].mean() * 1e4, 1), all_day_bp=round(B.nxt.mean() * 1e4, 1))
    R['robustness'] = rob; print('穩健性', json.dumps(rob, ensure_ascii=False))
    D.to_csv(os.path.join(OUT, 'v67_daily_table.csv')); json.dump(R, open(os.path.join(OUT, 'v67_d0_summary.json'), 'w'), ensure_ascii=False, indent=1, default=str)
