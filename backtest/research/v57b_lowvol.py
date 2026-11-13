"""V57-B：以 V57 口徑評估已鎖定的低波動暫停 Q20W30（定義取自 v44_r15_lowvolpause.py，零新參數）。預登記：v57_plan.md 第 7 節
用法：python backtest/research/v57b_lowvol.py"""
import os, sys, json, importlib.util
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v53_baseline as B, v57_metrics as M

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py')); m = importlib.util.module_from_spec(spec); return spec, m

def run_masked(df, okL, okS, sim):
    ind = B.eng.compute_indicators(df); ind = {**ind, 'okL': okL, 'okS': okS}
    t = pd.DataFrame(sim(ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    tr = pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason))
    inc, cost = C.mtm_equity(tr, df); cost['reason'] = tr.reason.values; return inc, cost

def build_sim():
    import inspect
    src = inspect.getsource(B.eng.simulate_v14_detailed)
    for old, new in [("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):", "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):"),
                     ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):", "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):")]:
        assert src.count(old) == 1, old; src = src.replace(old, new)
    ns = dict(B.eng.__dict__); exec(src, ns); return ns['simulate_v14_detailed']

def vol_rank(df, w=30):
    h, l, c = (df[k].to_numpy(float) for k in ('high', 'low', 'close')); pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); atrp = pd.Series(tr).rolling(14, min_periods=14).mean() / c * 100
    v = atrp.shift(1).rolling(w * 24, min_periods=w * 24).median(); return (v.rolling(730 * 24, min_periods=180 * 24).rank(pct=True) * 100).to_numpy()

THRESH = [('total', '>=', 10500), ('years_positive', '>=', 6), ('wr', '>', 0.5), ('r12_worst', '>=', -900), ('r12_pos_share', '>=', 0.80), ('half1_2021-11~2024-04', '>=', 2000), ('half2_2024-05~2026-10', '>', 0),
          ('r90_worst', '>=', -750), ('mdd_5y', '<=', 1400), ('dd_longest_days_5y', '<=', 550), ('mdd_recovery_days_5y', '<=', 180), ('streak_worst_loss', '>=', -500), ('streak_max_n', '<=', 8),
          ('streak4_per100', '<=', 2.75), ('streak6_per100', '<=', 0.80)]
def judge(R, base):
    rows = []
    for k, op, th in THRESH:
        v = R[k]; ok = {'>=': v >= th, '>': v > th, '<=': v <= th}[op]
        if k == 'mdd_recovery_days_5y': ok = ok and v >= 0
        rows.append(dict(metric=k, baseline=base[k], candidate=v, threshold=f'{op} {th}', passed=bool(ok)))
    rows.append(dict(metric='boot_mdd_p95', baseline=base['boot_mdd_p50_p95'][1], candidate=R['boot_mdd_p50_p95'][1], threshold='< 基準', passed=bool(R['boot_mdd_p50_p95'][1] < base['boot_mdd_p50_p95'][1])))
    rows.append(dict(metric='boot_streakloss_p95', baseline=base['boot_streakloss_p50_p95'][1], candidate=R['boot_streakloss_p50_p95'][1], threshold='> 基準', passed=bool(R['boot_streakloss_p50_p95'][1] > base['boot_streakloss_p50_p95'][1])))
    gain = {y: R['yearly'][y] - base['yearly'][y] for y in base['yearly']}; tot = sum(gain.values()); pos = sum(v > 0 for v in gain.values())
    rows.append(dict(metric='gain_years_positive／max_share', baseline='-', candidate=f"{pos}／{round(max(gain.values()) / tot, 2) if tot > 0 else 'n/a'}", threshold='>=4 且 <=0.60', passed=bool(tot > 0 and pos >= 4 and max(gain.values()) / tot <= 0.6)))
    return pd.DataFrame(rows), gain

if __name__ == '__main__':
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v57'); df = C.load_1h(end='2026-11-01'); sim = build_sim()
    ones = np.ones(len(df), bool); inc0, c0 = run_masked(df, ones, ones, sim); R0, T0 = M.evaluate(inc0, c0, '基準（遮罩全開，核對）'); print('核對基準', R0['total'], R0['trades'])
    rank = vol_rank(df); ok = ~(rank < 20); inc, c = run_masked(df, ok, ok, sim); R, T = M.evaluate(inc, c, 'Q20W30 低波動暫停')
    J, gain = judge(R, R0); print(J.to_string(index=False)); print('年度', R['yearly'], '增益', gain); print(T['year'].to_string()); print(T['side'].to_string())
    # 歸因
    ct = df.datetime - pd.Timedelta(hours=7); win = (ct >= '2021-01-01') & (ct < '2026-11-01'); print('暫停時間佔比', round((~ok[win.values]).mean(), 3))
    b = M.order(c0); cc = M.order(c); key = lambda x: x.side + x.t_in.astype(str); kb = set(key(b)); kc = set(key(cc))
    skipped = b[~key(b).isin(kc)]; added = cc[~key(cc).isin(kb)]
    print('少做', len(skipped), '筆 合計', round(skipped.net.sum()), '：其中贏單', int((skipped.net > 0).sum()), '筆', round(skipped[skipped.net > 0].net.sum()), '；虧單', int((skipped.net < 0).sum()), '筆', round(skipped[skipped.net < 0].net.sum()))
    print('連鎖新增', len(added), '筆 合計', round(added.net.sum())); print('每筆平均：基準', round(b.net.mean(), 2), '候選', round(cc.net.mean(), 2), '；無交易月', R['zero_trade_months'], 'vs', R0['zero_trade_months'])
    low = ['2021-11', '2021-12', '2022-04', '2022-05', '2022-11', '2022-12'] + [f'2023-{i:02d}' for i in range(1, 11)] + ['2025-10', '2026-05', '2026-06', '2026-08', '2026-09', '2026-10']
    for nm, t in (('基準', T0), ('候選', T)):
        m = t['monthly']; print(nm, '22 個低波動月合計', round(m[m.index.astype(str).isin(low)].sum()), '其餘', round(m[~m.index.astype(str).isin(low)].sum()))
    # 恢復後表現：每段暫停結束後 90 天的候選損益
    p = pd.Series(~ok, index=ct.values)[win.values]; ends = p.index[(p.shift(1, fill_value=False)) & (~p)]; daily = inc.groupby(lambda t: t.floor('D')).sum()
    print('暫停段數', len(ends), '恢復後 90 天損益', [(str(e.date()), round(daily[e:e + pd.Timedelta(days=90)].sum())) for e in ends if (p[e - pd.Timedelta(days=7):e].mean() > 0.9)])
    json.dump(dict(baseline=R0, candidate=R, gain=gain), open(os.path.join(OUT, 'v57b_metrics.json'), 'w'), ensure_ascii=False, indent=1, default=str); J.to_csv(os.path.join(OUT, 'v57b_judgement.csv'), index=False)
    T['year_side'].to_csv(os.path.join(OUT, 'v57b_year_side.csv')); T['monthly'].round(2).to_csv(os.path.join(OUT, 'v57b_monthly.csv')); c.to_csv(os.path.join(OUT, 'v57b_trades.csv'), index=False)
