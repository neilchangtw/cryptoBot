"""V69-A：以波動標準化交易幾何（止損、止盈、MFE、條件 MH 門檻 × m，名目 ÷ m）＋固定風險比例下單，1,500U 帳戶。
登記：doc/research_results/v69/v69_plan.md。用法：python backtest/research/v69a.py [d0|d1]"""
import os, sys, json, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v57_metrics as M, v66_volstop as V66
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v69'); CAP = 1500.0; W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); LEV = 5.0; MMR = 0.005

def build_sim():
    src = inspect.getsource(V.B.eng.simulate_v14_detailed)
    G = "(ind['gm'][lp_bar] if 'gm' in ind else 1.0)"; GS = "(ind['gm'][sp_bar] if 'gm' in ind else 1.0)"
    reps = [("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):", "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):", 1),
            ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):", "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):", 1),
            ("sn_lv = ep * (1 - L_SN)", "sn_lv = ep * (1 - L_SN * (ind['snm'][lp_bar] if 'snm' in ind else 1.0))", 1),
            ("sn_lv = ep * (1 + S_SN)", "sn_lv = ep * (1 + S_SN * (ind['snm'][sp_bar] if 'snm' in ind else 1.0))", 1),
            ("l_tp_eff = _L_TP_BR.get(lp_regime, L_TP)", f"l_tp_eff = _L_TP_BR.get(lp_regime, L_TP) * {G}", 1),
            ("if lp_mfe >= L_MFE_ACT and (lp_mfe - cpnl) >= L_MFE_TR and bh >= 1:", f"if lp_mfe >= L_MFE_ACT * {G} and (lp_mfe - cpnl) >= L_MFE_TR * {G} and bh >= 1:", 1),
            ("if bh == L_CMH_BAR and cpnl <= L_CMH_TH:", f"if bh == L_CMH_BAR and cpnl <= L_CMH_TH * {G}:", 1),
            ("(1 - S_TP)", f"(1 - S_TP * {GS})", 2),
            ("    sim_start = max(start_bar, WARMUP)", "    if 'scale' in ind: scale_arr = ind['scale']\n    sim_start = max(start_bar, WARMUP)", 1)]
    for old, new, k in reps:
        assert src.count(old) == k, (old, src.count(old)); src = src.replace(old, new)
    ns = dict(V.B.eng.__dict__); exec(src, ns); return ns['simulate_v14_detailed']

def ref_book(df, ind0, sim, ok, m=None, geo=False, stop=False, scale=True):
    """參考帳本：名目 4000/m。geo＝全部幾何×m；stop＝只有止損×m（V66）；兩者皆否＝只縮名目。"""
    ind = {**ind0, 'okL': ok, 'okS': ok}
    if m is not None:
        if scale: ind['scale'] = 1.0 / m
        if geo or stop: ind['snm'] = m
        if geo: ind['gm'] = m
    t = pd.DataFrame(sim(ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    tr = pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, bar=t.entry_bar, ntl=t.margin * 20.0))
    tr['m'] = (m[tr.bar.values] if m is not None else 1.0); tr['snm'] = tr.m if (geo or stop) else 1.0
    return tr[tr.t_in >= W0].sort_values(['t_in', 'side']).reset_index(drop=True)

def costed(tr, df, fund):
    inc, c = C.mtm_equity(tr, df, fund); c = c.reset_index(drop=True)
    for k in ('reason', 'ntl', 'm', 'snm', 'bar'): c[k] = tr[k].values
    return inc, c

def summary(name, inc, c, cap=CAP, extra=None):
    eq = cap + inc[W0:W1].cumsum(); pk = np.maximum(eq.cummax(), cap); dd = pk - eq; j = dd.idxmax(); pct = dd / pk
    ye = eq.groupby(eq.index.year).last(); yr = ye.diff(); yr.iloc[0] = ye.iloc[0] - cap
    R, T = M.evaluate(inc, c, name); s = T['streaks']
    d = dict(版本=name, 淨利=round(eq.iloc[-1] - cap), **{f'y{int(k)}': round(v) for k, v in yr.items()}, 筆數=len(c), 勝率=round((c.net > 0).mean() * 100, 2),
             MH筆=int((c.reason == 'MH').sum()), MH淨=round(c[c.reason == 'MH'].net.sum()), SN筆=int((c.reason == 'SN').sum()), SN淨=round(c[c.reason == 'SN'].net.sum()), TP淨=round(c[c.reason == 'TP'].net.sum()), 平均名目=round(c.ntl.mean()),
             MDD金額=round(dd.max()), MDD谷底=str(j.date()), MDD占當時高點=round(dd.max() / pk[j] * 100, 1), MDD占初始=round(dd.max() / cap * 100, 1), 最大百分比回撤=round(pct.max() * 100, 1), 最低淨值=round(eq.min()), 最低淨值日=str(eq.idxmin().date()),
             五年MDD=round(R['mdd_5y']), 最差滾動12月=round(R['r12_worst']), 最差連敗=round(R['streak_worst_loss']), 最長連敗=int(R['streak_max_n']), 連敗4以上每百筆=R['streak4_per100'], 回復天數=R['mdd_recovery_days_5y'], 連敗3以上段數=int((s.n >= 3).sum()))
    if extra: d.update(extra)
    return d, eq

def sized(ref, c, df, f, cap=CAP):
    """實際下單：名目＝f×錢包餘額÷(止損距離×snm)，數量取 0.001 ETH；錢包餘額＝初始＋已平倉淨損益。"""
    o = np.argsort(c.t_in.values, kind='stable'); net_ref = c.net.values; ntl_ref = c.ntl.values; out_t = c.t_out.values; in_t = c.t_in.values
    act_ntl = np.zeros(len(c)); act_net = np.zeros(len(c)); wal = np.zeros(len(c)); done = np.zeros(len(c), bool)
    for i in o:
        w = cap + act_net[(out_t <= in_t[i]) & done].sum(); d = (0.035 if c.side[i] == 'L' else 0.040) * c.snm[i]
        q = np.floor(f * w / d / c.ep[i] / 0.001) * 0.001; n = q * c.ep[i]
        if n < 20: n = 0.0
        act_ntl[i] = n; act_net[i] = net_ref[i] * n / ntl_ref[i]; wal[i] = w; done[i] = True
    tr = ref.copy(); tr['ntl'] = act_ntl; tr['wallet'] = wal; tr['mg'] = act_ntl / LEV; return tr[tr.ntl > 0].reset_index(drop=True), int((act_ntl == 0).sum())

def account_checks(tr, eq, df):
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values
    idx = eq.index; ip = {t: i for i, t in enumerate(idx)}; mg = np.zeros(len(idx)); liq = 0; maxlev = 0
    for r in tr.itertuples():
        a = ip[r.t_in]; b = ip.get(r.t_out, len(idx)); mg[a:b] += r.mg; s = 1 if r.side == 'L' else -1; lp = r.ep * (1 - s / LEV) / (1 - s * MMR); i0 = pos[r.t_in]; i1 = pos[r.t_out]
        if i1 > i0: liq += bool((lo[i0 + 1:i1 + 1].min() <= lp) if s == 1 else (hi[i0 + 1:i1 + 1].max() >= lp))
    free = eq.values - mg; return dict(最低可用資金=round(free.min()), 最高保證金占用=round(mg.max()), 觸及強平價=liq, 最大單筆名目=round(tr.ntl.max()), 最小單筆名目=round(tr.ntl.min()), 保證金不足=int((free < 0).sum()))

def books(df, ind0, sim):
    ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20); m2 = V66.vol_mult(df, 2.0)
    return {'基準': (ones, None, {}), 'Q20W30': (okq, None, {}), 'Q20W30｜只縮名目(上限2)': (okq, m2, {}), 'Q20W30｜V66只放寬止損(上限2)': (okq, m2, dict(stop=True)),
            'V69-A 主要(Q20W30＋幾何×m 上限2)': (okq, m2, dict(geo=True)), 'V69-A 上限1.5': (okq, V66.vol_mult(df, 1.5), dict(geo=True)), 'V69-A 上限3': (okq, V66.vol_mult(df, 3.0), dict(geo=True)), 'V69-A 無Q20W30(上限2)': (ones, m2, dict(geo=True))}

if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'd0'
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); sim = build_sim(); ind0 = V.B.eng.compute_indicators(df); B = books(df, ind0, sim); rows = []; keep = {}
    for name, (ok, m, kw) in B.items():
        ref = ref_book(df, ind0, sim, ok, m, **kw); inc, c = costed(ref, df, fund); keep[name] = (ref, c)
        if mode == 'd0':
            d, eq = summary(name, inc, c); d['MH加SN每4000名目'] = round((d['MH淨'] + d['SN淨']) / d['平均名目'] * 4000); rows.append(d)
            print(json.dumps({k: d[k] for k in ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝率', 'MH筆', 'MH淨', 'SN筆', 'SN淨', 'TP淨', '平均名目', 'MH加SN每4000名目', 'MDD金額', '最差連敗', '最長連敗', '最差滾動12月')}, ensure_ascii=False), flush=True)
            tag = name.replace('｜', '_').replace(' ', '').replace('(', '_').replace(')', '').replace('＋', '_').replace('×', 'x'); c.to_csv(os.path.join(OUT, f'd0_{tag}_trades.csv'), index=False)
    if mode == 'd0': pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v69a_d0_summary.csv'), index=False)
    if mode == 'd1':
        todo = sys.argv[2:] or ['基準', 'Q20W30']
        for name in todo:
            for f in (0.03, 0.02, 0.04):
                ref, c = keep[name]; tr, nskip = sized(ref, c, df, f); inc, c2 = costed(tr, df, fund); d, eq = summary(f'{name}｜f={f:.0%}｜5倍逐倉', inc, c2); d.update(account_checks(tr, eq, df)); d['名目不足未下單'] = nskip; rows.append(d)
                print(json.dumps(d, ensure_ascii=False), flush=True); tag = f"{name}_f{int(f*100)}".replace('｜', '_').replace(' ', '').replace('(', '_').replace(')', '').replace('＋', '_').replace('×', 'x')
                tr.to_csv(os.path.join(OUT, f'd1_{tag}_trades.csv'), index=False); eq.rename('equity').to_csv(os.path.join(OUT, f'd1_{tag}_equity_hourly.csv'))
        pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v69a_d1_summary.csv'), index=False)
