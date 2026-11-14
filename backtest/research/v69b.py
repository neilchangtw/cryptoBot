"""V69-B：日線 200 日均線多空對稱趨勢腿（名目＝g×錢包餘額，無槓桿）＋ Q20W30 固定風險比例現行腿，1,500U 帳戶。
登記：doc/research_results/v69/v69b_plan.md。用法：python backtest/research/v69b.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v69a as A
OUT = A.OUT; CAP = A.CAP; W0 = A.W0; W1 = A.W1

def daily_close(df):
    px = C.close_utc(df); d = px[px.index.hour == 0]
    s = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_spot_1d_2020H1.csv'), header=None, names=['ot', 'c']); s.index = pd.to_datetime(s.ot, unit='ms') + pd.Timedelta(days=1); s = s.c[s.index < d.index[0]]
    return px, pd.concat([s, d]).sort_index()

def trend_trades(px, dc, n=200, mode='LS'):
    sma = dc.rolling(n, min_periods=n).mean(); sg = np.sign(dc - sma).replace(0, np.nan).ffill(); sg = sg[sg.index >= W0]
    if mode == 'L': sg = sg.clip(lower=0)
    if mode == 'S': sg = sg.clip(upper=0)
    rows = []; cur = 0; t0 = None; ep = None; h = pd.Timedelta(hours=1)
    for t, v in sg.items():
        te = t + h
        if te > W1 or te not in px.index or np.isnan(v): continue
        if v != cur:
            if cur != 0: rows.append(dict(side='L' if cur > 0 else 'S', t_in=t0, t_out=te, ep=ep, xp=px[te]))
            cur = v; t0 = te; ep = px[te]
    if cur != 0: rows.append(dict(side='L' if cur > 0 else 'S', t_in=t0, t_out=W1, ep=ep, xp=px[W1]))
    tr = pd.DataFrame(rows); tr['reason'] = 'TREND'; tr['bar'] = 0; tr['ntl'] = 1000.0; tr['m'] = 1.0; tr['snm'] = 1.0; return tr

def hourly_pos(tr, idx):
    p = np.zeros(len(idx)); ip = {t: i for i, t in enumerate(idx)}
    for r in tr.itertuples(): p[ip[r.t_in]:ip[r.t_out]] = 1 if r.side == 'L' else -1
    return p

def perm_test(tr, px, fund, draws=2000, seed=69):
    idx = px[W0:W1].index; p = hourly_pos(tr, idx); v = px[W0:W1].values; r = np.r_[np.diff(v) / v[:-1], 0.0]; fr = fund.reindex(idx).fillna(0).values
    def net(q): return 1000 * ((q * r).sum() - (q * fr).sum() - 0.00225 * np.abs(np.diff(np.r_[0, q, 0])).sum() / 2)
    obs = net(p); rng = np.random.default_rng(seed); lo = 30 * 24; ks = rng.integers(lo, len(p) - lo, draws); sim = np.array([net(np.roll(p, k)) for k in ks])
    return obs, float((sim >= obs).mean()), float(np.median(sim)), float(p.mean())

def joint(base_c, tr_c, f, g, cap=CAP):
    """共用錢包：現行腿名目＝f×餘額÷止損距離（5 倍），趨勢腿名目＝g×餘額（2 倍）。"""
    b = base_c.assign(leg='base', lev=5.0); t = tr_c.assign(leg='trend', lev=2.0); c = pd.concat([b, t], ignore_index=True).sort_values(['t_in', 'leg'], kind='stable').reset_index(drop=True)
    net_ref = c.net.values; ntl_ref = c.ntl.values; out_t = c.t_out.values; in_t = c.t_in.values; act = np.zeros(len(c)); an = np.zeros(len(c)); done = np.zeros(len(c), bool)
    for i in range(len(c)):
        w = cap + an[done & (out_t <= in_t[i])].sum()
        n = f * w / ((0.035 if c.side[i] == 'L' else 0.040) * c.snm[i]) if c.leg[i] == 'base' else g * w
        q = np.floor(n / c.ep[i] / 0.001) * 0.001; n = q * c.ep[i]; n = n if n >= 20 else 0.0; act[i] = n; an[i] = net_ref[i] * n / ntl_ref[i]; done[i] = True
    c['ntl'] = act; c['mg'] = act / c.lev; return c[c.ntl > 0][['side', 't_in', 't_out', 'ep', 'xp', 'reason', 'bar', 'ntl', 'm', 'snm', 'leg', 'lev', 'mg']].reset_index(drop=True)

def checks(tr, eq, df):
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values; idx = eq.index; ip = {t: i for i, t in enumerate(idx)}; mg = np.zeros(len(idx)); liq = 0
    for r in tr.itertuples():
        a = ip[r.t_in]; b = ip.get(r.t_out, len(idx)); mg[a:b] += r.mg; s = 1 if r.side == 'L' else -1; lp = r.ep * (1 - s / r.lev) / (1 - s * A.MMR); i0 = pos[r.t_in]; i1 = pos[r.t_out]
        if i1 > i0: liq += bool((lo[i0 + 1:i1 + 1].min() <= lp) if s == 1 else (hi[i0 + 1:i1 + 1].max() >= lp))
    free = eq.values - mg; return dict(最低可用資金=round(free.min()), 最高保證金占用=round(mg.max()), 觸及強平價=liq, 保證金不足小時=int((free < 0).sum()))

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); px, dc = daily_close(df); rows = []
    print('== D0：趨勢腿單獨，固定名目 1,000U ==')
    for n, mode in ((200, 'LS'), (150, 'LS'), (250, 'LS'), (200, 'L'), (200, 'S')):
        tr = trend_trades(px, dc, n, mode); inc, c = C.mtm_equity(tr, df, fund); y = inc[W0:W1].groupby(inc[W0:W1].index.year).sum().round(0); obs, p, med, expo = perm_test(tr, px, fund)
        d = dict(版本=f'均線{n} {mode}', 筆數=len(tr), 淨利=round(inc[W0:W1].sum()), **{f'y{k}': int(v) for k, v in y.items()}, 勝率=round((c.net > 0).mean() * 100, 1), 資金費=round(c.funding.sum()), 平移對照p=round(p, 4), 平移中位數=round(med), 平均淨曝險=round(expo, 3)); rows.append(d); print(json.dumps(d, ensure_ascii=False), flush=True)
        if (n, mode) == (200, 'LS'): c.assign(reason='TREND').to_csv(os.path.join(OUT, 'v69b_d0_trend_trades.csv'), index=False)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v69b_d0_summary.csv'), index=False)
    if len(sys.argv) > 1 and sys.argv[1] == 'd1':
        sim = A.build_sim(); ind0 = V.B.eng.compute_indicators(df); okq = ~(V.vol_rank(df) < 20); ref = A.ref_book(df, ind0, sim, okq); _, bc = A.costed(ref, df, fund); rows = []
        for n, mode, f, g in ((200, 'LS', .03, .25), (200, 'LS', .03, .15), (200, 'LS', .03, .35), (150, 'LS', .03, .25), (250, 'LS', .03, .25), (200, 'L', .03, .25), (200, 'S', .03, .25), (200, 'LS', .03, 0.0)):
            tt = trend_trades(px, dc, n, mode); _, tc = C.mtm_equity(tt, df, fund); tc = tc.reset_index(drop=True)
            for k in ('reason', 'ntl', 'm', 'snm', 'bar'): tc[k] = tt[k].values
            tr = joint(bc, tc, f, g); inc, c2 = C.mtm_equity(tr, df, fund); c2 = c2.reset_index(drop=True)
            for k in ('reason', 'ntl', 'm', 'snm', 'bar', 'leg'): c2[k] = tr[k].values
            name = f'Q20W30 f={f:.0%}＋趨勢腿 均線{n} {mode} g={g:.0%}'; d, eq = A.summary(name, inc, c2); d.update(checks(tr, eq, df)); d['現行腿淨'] = round(c2[c2.leg == 'base'].net.sum()); d['趨勢腿淨'] = round(c2[c2.leg == 'trend'].net.sum())
            yl = c2.assign(y=c2.t_out.dt.year).pivot_table(index='y', columns='leg', values='net', aggfunc='sum').round(0); d['逐年分腿(依平倉年)'] = {int(k): {a: int(b) for a, b in v.items() if b == b} for k, v in yl.iterrows()}
            rows.append(d); print(json.dumps(d, ensure_ascii=False), flush=True); tag = f'ma{n}_{mode}_g{int(g*100)}'; tr.to_csv(os.path.join(OUT, f'v69b_d1_{tag}_trades.csv'), index=False); eq.rename('equity').to_csv(os.path.join(OUT, f'v69b_d1_{tag}_equity_hourly.csv'))
        pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v69b_d1_summary.csv'), index=False)
