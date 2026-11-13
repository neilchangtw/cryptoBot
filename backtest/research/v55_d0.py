"""V55 D0 診斷：美元溢價（ETHUSD 指數對現貨 ETHUSDT）方向訊號。預登記：doc/research_results/v55/v55_plan.md
用法：python backtest/research/v55_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
NTL = 4000.0; COST = 9.0

def daily_premium():
    s = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_spot_1h.csv'), parse_dates=['open_time_utc']).set_index('open_time_utc').close
    ix = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSD_index_1h.csv'), parse_dates=['open_time_utc']).set_index('open_time_utc').close
    p = np.log(ix / s).dropna(); p.index = p.index + pd.Timedelta(hours=1)  # 收盤時間
    d = p.groupby((p.index - pd.Timedelta(seconds=1)).floor('D') + pd.Timedelta(days=1)).agg(['median', 'size'])
    return d['median'][d['size'] >= 12]

def signal(d, R=90, q=0.2, base=30, minp=60):
    """回傳每日 +1／-1／0（index=決策日 UTC 00:00）。只用當日以前資料。"""
    s = d - d.shift(1).rolling(base, min_periods=base).median()
    out = pd.Series(0, index=d.index)
    for i, t in enumerate(s.index):
        if np.isnan(s.iloc[i]): continue
        h = s[(s.index < t) & (s.index >= t - pd.Timedelta(days=R))].dropna()
        if len(h) < minp: continue
        pc = (h < s.iloc[i]).mean(); out.iloc[i] = 1 if pc >= 1 - q else (-1 if pc <= q else 0)
    return out

def pos_pnl(sig, r):
    """合併連續同向日為部位；回傳 (總淨利, 多方淨利, 空方淨利, 部位數)。sig、r 為對齊的 numpy。"""
    gross_l = NTL * r[sig == 1].sum(); gross_s = -NTL * r[sig == -1].sum()
    prev = np.r_[0, sig[:-1]]; nl = int(((sig == 1) & (prev != 1)).sum()); ns = int(((sig == -1) & (prev != -1)).sum())
    return gross_l + gross_s - COST * (nl + ns), gross_l - COST * nl, gross_s - COST * ns, nl + ns

def evaluate(df, label):
    sig = df.sig.values; r = df.r.values; L = df[df.sig == 1].r; S = df[df.sig == -1].r
    tot, pl, ps, n = pos_pnl(sig, r); rng = np.random.default_rng(55); perm = []; perml = []
    for _ in range(2000):
        k = rng.integers(30, len(sig) - 30); sh = np.roll(sig, k); a = pos_pnl(sh, r); perm.append(a[0]); perml.append(a[1])
    yr = {}
    for y in sorted(set(df.index.year)):
        x = df[df.index.year == y]; a = pos_pnl(x.sig.values, x.r.values)
        yr[y] = dict(spread_bp=round((x[x.sig == 1].r.mean() - x[x.sig == -1].r.mean()) * 1e4, 1), long_days=int((x.sig == 1).sum()), short_days=int((x.sig == -1).sum()),
                     eth_day_bp=round(x.r.mean() * 1e4, 1), long_bp=round(x[x.sig == 1].r.mean() * 1e4, 1), short_bp=round(x[x.sig == -1].r.mean() * 1e4, 1), net=round(a[0]), positions=a[3],
                     need_per_pos=round(3600 / max(a[3], 1), 1), got_per_pos=round(a[0] / max(a[3], 1), 1))
    yt = pd.DataFrame(yr).T; print(f'== {label}：樣本 {len(df)} 天 =='); print(yt.to_string())
    k1 = int((yt.spread_bp > 0).sum()); p2 = (np.array(perm) >= tot).mean(); p3 = (np.array(perml) >= pl).mean()
    res = dict(label=label, days=len(df), long_bp=round(L.mean() * 1e4, 1), short_bp=round(S.mean() * 1e4, 1), all_bp=round(df.r.mean() * 1e4, 1), K1_pos_years=k1,
               net=round(tot), net_long=round(pl), net_short=round(ps), positions=n, perm_p95=round(np.quantile(perm, .95)), perm_median=round(np.median(perm)), K2_p=round(p2, 4), long_p=round(p3, 4))
    res['K1'] = k1 >= 4; res['K2'] = bool(tot > np.quantile(perm, .95)); res['K3'] = bool(L.mean() > df.r.mean() and S.mean() < df.r.mean() and p3 < 0.10); res['K4'] = bool(tot > 0)
    print(res); return res, yt

if __name__ == '__main__':
    d = daily_premium(); sig = signal(d); px = C.close_utc(C.load_1h(end='2026-11-01'))
    f = sig.index + pd.Timedelta(hours=2); r = px.reindex(f + pd.Timedelta(days=1)).values / px.reindex(f).values - 1
    df = pd.DataFrame({'sig': sig.values, 'r': r, 'd': d.values}, index=sig.index)
    # 訊號缺日（資料缺）視為 0；報酬缺則剔除
    full = pd.date_range('2021-01-01', '2025-12-31', freq='D'); df = df.reindex(full); df['sig'] = df.sig.fillna(0).astype(int); df = df.dropna(subset=['r'])
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v55')
    res, yt = evaluate(df, '全期 2021~2025'); yt.to_csv(os.path.join(OUT, 'v55_d0_yearly.csv'))
    ex = df.copy()
    for a, b in (('2022-05-09', '2022-05-15'), ('2023-03-08', '2023-03-18')): ex.loc[a:b, 'sig'] = 0
    res2, _ = evaluate(ex, '剔除兩段極端日（揭露用）')
    pd.DataFrame([res, res2]).to_csv(os.path.join(OUT, 'v55_d0_summary.csv'), index=False)
    print('判定', 'PASS→D1' if all(res[k] for k in ('K1', 'K2', 'K3', 'K4')) else 'REJECTED（D0）：' + ','.join(k for k in ('K1', 'K2', 'K3', 'K4') if not res[k]))
