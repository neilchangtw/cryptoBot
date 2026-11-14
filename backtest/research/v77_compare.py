"""V77 公平比較：現行原配置對 Q20W30＋固定風險（逐倉 5 倍），含共同曝險拆解與門檻逐項標示。
用法：python backtest/research/v77_compare.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC, v70_run as RN, v74_run as R74, v75_recon as R75, v71_run as R71
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v77'); RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

if __name__ == '__main__':
    X = R74.setup('2026-11-01'); df, fund = X['df'], X['fund']; rows = []; wr = []; keep = {}
    def run(nm, book, mode, fixed=False):
        R = E.replay(df, fund, base=X[book], base_mode=mode, cross=False, fixed_basis=fixed); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(nm, R, df); t = R['tr']; mx, c4 = R71.sstats(t)
        d.update(R74.weak(R['acc'].equity)); d.update(PF=round(t[t.net > 0].net.sum() / -t[t.net < 0].net.sum(), 2), 平均名目=round(t.ntl.mean()), MH筆=int((t.reason == 'MH').sum()), MH淨=round(t[t.reason == 'MH'].net.sum()), SN筆=int((t.reason == 'SN').sum()), SN淨=round(t[t.reason == 'SN'].net.sum())); rows.append(d); keep[nm] = R; return R, d
    R0, d0 = run('現行原配置', 'base', FIX); R1, d1 = run('候選：Q20W30＋固定風險', 'q20', RISK)
    run('拆解①：Q20W30 訊號＋現行名目規則', 'q20', FIX); run('拆解②：全部訊號＋固定風險（固定基數）', 'base', RISK, True); run('拆解③：Q20W30＋固定風險（固定基數）', 'q20', RISK, True); run('拆解④：全部訊號＋固定風險（複利）', 'base', RISK)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'compare_summary.csv'), index=False)
    cols = ['版本', '淨利', '月均', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝率', '平均贏單', '平均輸單', '每筆平均淨利', 'PF', '平均名目', 'MH筆', 'MH淨', 'SN筆', 'SN淨', '最長連敗', '連敗4以上每百筆', '連敗6以上每百筆', '最差連敗段_五年口徑', '最低權益', '小時內保守最低權益', '最大百分比回撤', 'MDD金額', '五年MDD', '最差滾動12月', '最差90天', '回復天數', '強平觸及_逐倉估算', '跳過', '弱_2021', '弱_2023', '弱_2026下半']
    pd.set_option('display.width', 300); pd.set_option('display.max_columns', 60); print(T[cols].T.to_string())
    g = {}
    for nm, d in (('現行原配置', d0), ('候選', d1)):
        g[nm] = {'勝率>50%': d['勝率'] > 50, **{f'{y}年為正': d[f'y{y}'] > 0 for y in range(2021, 2026)}, '2026年初至10月為正': d['y2026'] > 0, '全期淨利≥現行': d['淨利'] >= d0['淨利'], '最低權益≥1000': d['最低權益'] >= 1000, '百分比回撤≤40%': d['最大百分比回撤'] <= 40, '零強平觸及': d['強平觸及_逐倉估算'] == 0, '五年MDD≤1400': d['五年MDD'] <= 1400, '最差滾動12月≥-900': d['最差滾動12月'] >= -900, '最差連敗段≥-500': d['最差連敗段_五年口徑'] >= -500}
    G = pd.DataFrame(g).replace({True: '通過', False: '未通過'}); G.to_csv(os.path.join(OUT, 'gate_flags.csv')); print(G.to_string())
    for nm, R in (('現行原配置', R0), ('候選', R1)): wr += RC.wr_tables(nm, R['tr'], 54.35)
    pd.DataFrame(wr).to_csv(os.path.join(OUT, 'winrate_tables.csv'), index=False)
    mo = pd.DataFrame({k: R['acc'].equity.diff().fillna(R['acc'].equity.iloc[0] - E.CAP).groupby(lambda t: t.strftime('%Y-%m')).sum().round(1) for k, R in (('現行', R0), ('候選', R1))}); mo['候選減現行'] = (mo['候選'] - mo['現行']).round(1)
    for k, R in (('現行', R0), ('候選', R1)): t = R['tr']; a = t.groupby(t.t_out.dt.strftime('%Y-%m')).net.agg(n='count', w=lambda x: (x > 0).sum()); mo[f'{k}_筆數'] = a.n; mo[f'{k}_勝'] = a.w
    mo = mo.fillna(0); mo.to_csv(os.path.join(OUT, 'monthly_compare.csv')); print('月為正比例 現行/候選', round((mo['現行'] > 0).mean() * 100, 1), round((mo['候選'] > 0).mean() * 100, 1), '零交易月 候選', int((mo['候選_筆數'] == 0).sum()), '最差月', mo['現行'].min(), mo['候選'].min())
    R1['tr'].to_csv(os.path.join(OUT, 'candidate_trades.csv'), index=False); R1['acc'].to_csv(os.path.join(OUT, 'candidate_account_hourly.csv')); R0['tr'].to_csv(os.path.join(OUT, 'orig_trades.csv'), index=False)
    # 暫估：截至 2026-11-13（資金費為溢價指數估計與壓力情境）
    X2 = R74.setup('2026-11-14'); L = []
    for fn, f in R75.fund_variants(X2['fund']).items():
        for nm, kw in (('現行原配置', dict(base=X2['base'], base_mode=FIX)), ('候選', dict(base=X2['q20'], base_mode=RISK))):
            R = E.replay(X2['df'], f, end=R74.LATEST, cross=False, **kw); e = R['acc'].equity; L.append(dict(資金費假設=fn, 版本=nm, 全期淨利=round(e.iloc[-1] - E.CAP, 1), 年初至今2026=round(e.iloc[-1] - e[:'2025-12-31 23:00'].iloc[-1], 1), 筆數=len(R['tr'])))
    P = pd.DataFrame(L); P.to_csv(os.path.join(OUT, 'provisional_nov.csv'), index=False); print(P.to_string(index=False))
