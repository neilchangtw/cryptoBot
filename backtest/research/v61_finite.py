"""有限帳戶驗收（2,000U、禁止追加）。規則：doc/research_results/v61/finite_account_plan.md
用法：python backtest/research/v61_finite.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v60_d0 as S, v61_run as R, v60b_run as B60
CAP = 2000.0; MMR = 0.005; W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v61')
PICKS = {'2021-01-01': 'S8', '2021-07-01': 'S8', '2022-01-01': 'S8', '2022-07-01': 'S4', '2023-01-01': 'S8', '2023-07-01': 'S4', '2024-01-01': 'S4', '2024-07-01': 'S4', '2025-01-01': 'S4', '2025-07-01': 'S4', '2026-01-01': 'S8', '2026-07-01': 'S4'}

def run_base(df, ind0, sim, okL, okS):
    t = pd.DataFrame(sim({**ind0, 'okL': okL, 'okS': okS}, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    return pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, bar=t.entry_bar, ntl=4000.0, mg=200.0, mod='base', pri=np.where(t.side == 'L', 0, 1)))

def run_mod(sig0, df, cfg, blocked):
    """cfg: 'S8'/'S12'/'WF'。blocked: {(cfg名, 決策日)}。"""
    def one(k):
        sg = sig0.copy(); bl = [d for c, d in blocked if c == k]; sg.loc[sg.index.isin(bl)] = 0
        t, _ = R.mod(sg, df, R.CFG[k], globals()["EMPTY"]); t['cfgk'] = k; return t
    if cfg != 'WF': t = one(cfg)
    else:
        parts = []
        for a, k in PICKS.items():
            a = pd.Timestamp(a); x = one(k); parts.append(x[(x.t_in >= a) & (x.t_in < a + pd.DateOffset(months=6))])
        t = pd.concat(parts)
    t = t.sort_values('t_in').reset_index(drop=True); t['mg'] = 300.0; t['mod'] = 'skew'; t['pri'] = np.where(t.side == 'L', 2, 3); t['bar'] = -1; return t

def account(tr, df, fund):
    inc, cost = C.mtm_equity(tr, df, fund); eq = CAP + inc[W0:].cumsum(); return eq, cost

def first_violation(tr, eq):
    ev = tr[(tr.t_in >= W0)].sort_values(['t_in', 'pri']); act = []
    for r in ev.itertuples():
        act = [(o, m) for o, m in act if o > r.t_in]; used = sum(m for _, m in act); e = eq.get(r.t_in, np.nan)
        if not (e - used >= r.mg): return r, e, used
        act.append((r.t_out, r.mg))
    return None, None, None

def replay(name, df, ind0, sim, fund, ok0, sig0, cfg):
    n = len(df); okL = ok0.copy(); okS = ok0.copy(); blocked = set(); skips = []; halted = None; ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values)
    for it in range(3000):
        b = run_base(df, ind0, sim, okL, okS); m = run_mod(sig0, df, cfg, blocked) if cfg else None
        tr = pd.concat([b, m], ignore_index=True) if cfg else b; eq, cost = account(tr, df, fund); v, e, used = first_violation(tr, eq)
        if v is None: break
        if used == 0 and e < 200:   # 無持倉且淨值不足以開任何一筆：帳戶停止，之後的交易全部不存在
            rest = tr[(tr.t_in >= v.t_in)]; skips += [dict(版本=name, 模組=x.mod, side=x.side, t_in=x.t_in, 所需保證金=x.mg, 當時淨值=round(e, 1), 已占用保證金=0, 可用=round(e, 1), 帳戶已停止=True) for x in rest.itertuples() if x.t_in <= W1]
            tr = tr[tr.t_in < v.t_in]; eq, cost = account(tr, df, fund); halted = v.t_in; break
        skips.append(dict(版本=name, 模組=v.mod, side=v.side, t_in=v.t_in, 所需保證金=v.mg, 當時淨值=round(e, 1), 已占用保證金=used, 可用=round(e - used, 1)))
        if v.mod == 'base':
            msk = okL if v.side == 'L' else okS; cand = [v.bar, v.bar - 1]; done = False
            for cb in cand:
                if not msk[cb]: continue
                msk[cb] = False; b2 = run_base(df, ind0, sim, okL, okS); 
                if not ((b2.side == v.side) & (b2.t_in == v.t_in)).any(): done = True; break
                msk[cb] = True
            assert done, ('無法遮罩', v)
        else:
            blocked.add((v.cfgk, v.t_in - pd.Timedelta(hours=2)))
    tr = tr[tr.t_in >= W0].reset_index(drop=True); return tr, eq[:W1], cost, pd.DataFrame(skips), it, halted

def metrics(name, tr, eq, df, skips):
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values; cl = df.close.values
    idx = eq.index; ip = {t: i for i, t in enumerate(idx)}; mg = np.zeros(len(idx)); ex = np.zeros(len(idx)); worst = np.zeros(len(idx)); mm = np.zeros(len(idx)); liq = {'base': 0, 'skew': 0}
    for r in tr.itertuples():
        if r.t_in not in ip: continue
        a = ip[r.t_in]; b = ip.get(r.t_out, len(idx)); mg[a:b] += r.mg; ex[a:b] += r.ntl; mm[a:b] += r.ntl * MMR; s = 1 if r.side == 'L' else -1; L = r.ntl / r.mg; q = r.ntl / r.ep
        i0 = pos[r.t_in]; i1 = pos[r.t_out]; lp = r.ep * (1 - s / L) / (1 - s * MMR)
        if i1 > i0:
            hh = hi[i0 + 1:i1 + 1]; ll = lo[i0 + 1:i1 + 1]; cc = cl[i0 + 1:i1 + 1]; liq[r.mod] += bool((ll.min() <= lp) if s == 1 else (hh.max() >= lp))
            adv = (cc - ll) * q if s == 1 else (hh - cc) * q; k = min(len(adv), len(idx) - a - 1); worst[a + 1:a + 1 + k] += adv[:k]   # 收盤淨值再往不利方向扣到該小時極值
    eqw = eq.values - worst; pk = eq.cummax(); dd = pk - eq; j = dd.values.argmax(); ddw = pk.values - eqw; jw = ddw.argmax(); yr = eq.groupby(eq.index.year).last().diff(); yr.iloc[0] = eq.groupby(eq.index.year).last().iloc[0] - CAP
    mon = eq.resample('ME').last().diff(); stop = eq[eq < 200]; free = eq.values - mg
    return dict(版本=name, 期末淨值=round(eq.iloc[-1]), 淨利=round(eq.iloc[-1] - CAP), **{f'y{int(k)}': round(v) for k, v in yr.items()}, 正年數=int((yr > 0).sum()), MDD=round(dd.max()), MDD百分比=round(dd.max() / pk.iloc[j] * 100, 1), MDD谷底=str(idx[j].date()),
                最低淨值=round(eq.min()), 最低淨值日=str(eq.idxmin().date()), 最差月=round(mon.min()), 最高保證金占用=round(mg.max()), 最大名目=round(ex.max()), 最低可用資金=round(free.min()), 淨值低於維持保證金小時=int((eq.values < mm).sum()),
                筆數=len(tr[tr.t_out <= W1]), 跳過_現行=int((skips.模組 == 'base').sum()) if len(skips) else 0, 跳過_模組=int((skips.模組 == 'skew').sum()) if len(skips) else 0, 觸及強平價_現行=liq['base'], 觸及強平價_模組=liq['skew'], 停止交易='否',
                小時內保守MDD=round(ddw.max()), 小時內保守MDD百分比=round(ddw.max() / pk.iloc[jw] * 100, 1), 小時內最低淨值=round(eqw.min()))

if __name__ == '__main__':
    df = C.load_1h(end=R.CUT); fund = C.load_funding(); sim = V.build_sim(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20)
    b0 = run_base(df, ind0, sim, ones, ones); EMPTY = b0.iloc[0:0][['side', 't_in', 't_out', 'ep']]; sig0 = R.cal_sig(S.daily_skew((7, 45))); rows = []; allsk = []
    for name, ok, cfg in (('基準', ones, None), ('Q20W30', okq, None), ('A＋S8', ones, 'S8'), ('B＋S8', okq, 'S8'), ('B＋S12', okq, 'S12'), ('B＋前推', okq, 'WF')):
        tr, eq, cost, sk, it, halted = replay(name, df, ind0, sim, fund, ok, sig0, cfg); m = metrics(name, tr, eq, df, sk); m['重播次數'] = it + 1; m['停止交易'] = str(halted) if halted is not None else m['停止交易']; rows.append(m); allsk.append(sk)
        tag = name.replace('＋', '_'); tr.to_csv(os.path.join(OUT, f'finite_{tag}_trades.csv'), index=False); eq.rename('equity').to_csv(os.path.join(OUT, f'finite_{tag}_equity_hourly.csv')); print(json.dumps(m, ensure_ascii=False), flush=True)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'finite_summary.csv'), index=False); sk = pd.concat(allsk) if any(len(x) for x in allsk) else pd.DataFrame(); sk.to_csv(os.path.join(OUT, 'finite_skipped.csv'), index=False)
    print(T.T.to_string()); print(sk.to_string(index=False) if len(sk) else '無跳過')
