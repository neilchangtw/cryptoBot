"""V71-A：只在過去 30 日報酬為負時進場（多空相同）＋Q20W30＋固定風險下單。登記：doc/research_results/v71/v71_plan.md。
用法：python backtest/research/v71_run.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v69a as A, v70_account as E, v70_recon as RC, v70_run as RN
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v71')

def sstats(t):
    x = (t.sort_values(['t_out', 't_in']).net < 0).values; mx = n = c4 = 0
    for v in x:
        if v: n += 1
        else: mx = max(mx, n); c4 += n >= 4; n = 0
    mx = max(mx, n); c4 += n >= 4; return mx, c4

def weak(R):
    e = R['acc'].equity; d = e.diff().fillna(e.iloc[0] - E.CAP); return dict(弱_2021=round(d['2021'].sum()), 弱_2023=round(d['2023'].sum()), 弱_2026下半=round(d['2026-07':'2026-10'].sum()))

if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; c = df.close.values; rows = []; keep = {}
    def r_ok(days, th): r = pd.Series(c) / pd.Series(c).shift(24 * days) - 1; return (r < th).fillna(False).values
    def book(okL, okS): return A.ref_book(df, X['ind0'], X['sim'], X['okq'] & okL) if okL is okS else book2(okL, okS)
    def book2(okL, okS):
        ind = {**X['ind0'], 'okL': X['okq'] & okL, 'okS': X['okq'] & okS}; t = pd.DataFrame(X['sim'](ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
        tr = pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, bar=t.entry_bar, ntl=t.margin * 20.0)); tr['m'] = 1.0; tr['snm'] = 1.0; return tr[tr.t_in >= E.W0].sort_values(['t_in', 'side']).reset_index(drop=True)
    ones = np.ones(len(df), bool)
    def run(name, key, ref, fixed, sleeve=None, cross=False):
        kw = dict(base=ref, base_mode=('risk', 0.03, 5.0), fixed_basis=fixed, cross=cross)
        if sleeve: kw['sleeve'] = sleeve
        R = E.replay(df, fund, **kw); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(name, R, df); mx, c4 = sstats(R['tr']); d.update(最長連敗_全部=mx, 四連敗以上段數=c4, 每百筆四連敗=round(c4 / len(R['tr']) * 100, 2), **weak(R)); G = RN.gates(d); d['G全過'] = all(G.values()); d['未過項'] = '、'.join(k for k, v in G.items() if not v)
        rows.append(d); keep[key] = R; R['tr'].to_csv(os.path.join(OUT, f'{key}_trades.csv'), index=False); R['acc'].round(4).to_csv(os.path.join(OUT, f'{key}_account_hourly.csv')); return R, d
    P = ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝率', '平均贏單', '平均輸單', '每筆平均淨利', '最長連敗_全部', '四連敗以上段數', '每百筆四連敗', '最差連敗段', '弱_2021', '弱_2023', '弱_2026下半', '最大百分比回撤', '最低權益', 'MH筆', 'MH淨', 'G全過', '未過項')
    ok30 = r_ok(30, 0.0); refA = book(ok30, ok30)
    Rc, dc = run('主要對照 Q20W30＋固定風險（固定基數）', 'ctrl_fixed', X['q20'], True); Ra, da = run('V71-A（固定基數，共同曝險）', 'v71a_fixed', refA, True)
    Rcf, dcf = run('主要對照（完整配置）', 'ctrl_full', X['q20'], False); Raf, daf = run('V71-A（完整配置）', 'v71a_full', refA, False)
    for d in (dc, da, dcf, daf): print(json.dumps({k: d[k] for k in P}, ensure_ascii=False), flush=True)
    # C4：從對照隨機刪除相同筆數
    tc = Rc['tr'].sort_values(['t_out', 't_in']); x = (tc.net < 0).values; k = len(Ra['tr']); rng = np.random.default_rng(71); sim = []
    for _ in range(5000):
        idx = np.sort(rng.choice(len(x), k, replace=False)); y = x[idx]; mx = n = c4 = 0
        for v in y:
            if v: n += 1
            else: mx = max(mx, n); c4 += n >= 4; n = 0
        sim.append((max(mx, n), c4 + (n >= 4)))
    sim = np.array(sim); oa = sstats(Ra['tr']); print('C4 隨機刪除對照：最長連敗 中位', np.median(sim[:, 0]), 'p(<=候選)', (sim[:, 0] <= oa[0]).mean(), '；四連敗段數 中位', np.median(sim[:, 1]), 'p(<=候選)', (sim[:, 1] <= oa[1]).mean())
    C = dict(C1=da['最長連敗_全部'] <= 6, C2=da['每百筆四連敗'] <= 1.8, C3=da['最差連敗段'] >= dc['最差連敗段'] * 0.8, C4=bool((sim[:, 0] <= oa[0]).mean() <= 0.10 and (sim[:, 1] <= oa[1]).mean() <= 0.10), C5=all(da[k] >= dc[k] for k in ('弱_2021', '弱_2023', '弱_2026下半')), C6=da['勝率'] > 50 and da['勝率'] - dc['勝率'] >= 3); print('連敗標準', C)
    a = Ra['tr']; b = Rc['tr']; ka = set(zip(a.side, a.t_in)); kb = set(zip(b.side, b.t_in)); rm = b[[q not in ka for q in zip(b.side, b.t_in)]]; nw = a[[q not in kb for q in zip(a.side, a.t_in)]]
    print('相對主要對照（固定基數）：避開', len(rm), '筆；虧損', int((rm.net < 0).sum()), '筆', round(rm[rm.net < 0].net.sum()), '；錯過贏單', int((rm.net > 0).sum()), '筆', round(rm[rm.net > 0].net.sum()), '；新增', len(nw), '筆', round(nw.net.sum()), '；避開者勝率', round((rm.net > 0).mean() * 100, 1))
    print('集中度：扣前 3 大', round(a.net.sum() - a.net.nlargest(3).sum()), '；各年占比', {int(y): round(v / a.net.sum() * 100) for y, v in a.groupby(a.t_out.dt.year).net.sum().items()})
    nbr = [('鄰域 20日', r_ok(20, 0), None), ('鄰域 45日', r_ok(45, 0), None), ('鄰域 門檻-5%', r_ok(30, -0.05), None), ('鄰域 門檻+5%', r_ok(30, 0.05), None), ('鄰域 只過濾空單', ones, ok30), ('鄰域 只過濾多單', ok30, ones)]; npass = 0
    for i, (nm, okL, okS) in enumerate(nbr):
        ref = book(okL, okL) if okS is None else book2(okL, okS); R, d = run(nm + '（固定基數）', f'nb{i}', ref, True); p = d['最長連敗_全部'] <= 6 and d['每百筆四連敗'] <= 1.8; npass += p; print(json.dumps({k: d[k] for k in P[:21]}, ensure_ascii=False), 'C1&C2', bool(p), flush=True)
    print('R 鄰域 C1&C2 通過', npass, '/6')
    Rs, ds = run('參考對照：V71-A＋V70 趨勢腿（完整配置，全倉）', 'v71a_sleeve', refA, False, sleeve=dict(sig=RC.trend_sig(X['dc']), mode='rebal', g=0.25, band=0.20, lev=5.0), cross=True); print(json.dumps({k: ds[k] for k in P}, ensure_ascii=False))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v71a_summary.csv'), index=False); wr = []
    for nm, key in (('主要對照（完整配置）', 'ctrl_full'), ('V71-A（完整配置）', 'v71a_full'), ('V71-A（固定基數）', 'v71a_fixed'), ('主要對照（固定基數）', 'ctrl_fixed')): wr += RC.wr_tables(nm, keep[key]['tr'], 54.35)
    pd.DataFrame(wr).to_csv(os.path.join(OUT, 'v71a_winrate.csv'), index=False)
