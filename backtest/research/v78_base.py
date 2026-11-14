"""V78 第一步：重現比較基準、釐清回撤口徑（1,833 對 1,747）、共同曝險帳本拆解。
用法：python backtest/research/v78_base.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC, v74_run as R74, v71_run as R71, v57_metrics as M
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v78'); RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

def dd_bases(R):
    """同一批交易、四種回撤口徑。"""
    e = R['acc'].equity; t = R['tr']; o = {}
    pk = np.maximum(e.cummax(), E.CAP); o['逐小時權益_全期_高點含初始'] = round((pk - e).max(), 1); o['谷底'] = str((pk - e).idxmax())[:13]
    e5 = e['2021-11-01':]; o['逐小時權益_2021-11起'] = round((e5.cummax() - e5).max(), 1)
    d = e.resample('D').last().dropna(); o['日終權益_全期'] = round((np.maximum(d.cummax(), E.CAP) - d).max(), 1)
    inc = t.groupby(t.t_out.dt.floor('D')).net.sum(); cs = inc.cumsum(); o['平倉日已實現_全期'] = round((np.maximum(cs.cummax(), 0) - cs).max(), 1)
    c5 = inc['2021-11-01':].cumsum(); o['平倉日已實現_2021-11起（五年口徑）'] = round((np.maximum(c5.cummax(), 0) - c5).max(), 1); o['五年口徑谷底'] = str((np.maximum(c5.cummax(), 0) - c5).idxmax())[:10]
    return o

if __name__ == '__main__':
    X = R74.setup('2026-11-01'); df, fund = X['df'], X['fund']; rows = []; keep = {}
    def run(nm, book, mode, fixed=False):
        R = E.replay(df, fund, base=X[book], base_mode=mode, cross=False, fixed_basis=fixed); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(nm, R, df); t = R['tr']
        d.update(R74.weak(R['acc'].equity)); d.update(PF=round(t[t.net > 0].net.sum() / -t[t.net < 0].net.sum(), 2), 平均名目=round(t.ntl.mean()), MH筆=int((t.reason == 'MH').sum()), MH淨=round(t[t.reason == 'MH'].net.sum()), SN筆=int((t.reason == 'SN').sum()), SN淨=round(t[t.reason == 'SN'].net.sum())); rows.append(d); keep[nm] = R; return R
    R0 = run('現行原配置', 'base', FIX); R1 = run('V77：Q20W30＋停損價差預算3%', 'q20', RISK); R2 = run('Q20W30＋現行名目', 'q20', FIX); R3 = run('全部訊號＋預算3%（固定基數）', 'base', RISK, True); R4 = run('Q20W30＋預算3%（固定基數）', 'q20', RISK, True)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'baseline_summary.csv'), index=False); pd.set_option('display.width', 300); pd.set_option('display.max_columns', 60); print(T.T.to_string())
    B = pd.DataFrame({k: dd_bases(R) for k, R in (('現行原配置', R0), ('V77', R1))}); B.to_csv(os.path.join(OUT, 'drawdown_bases.csv')); print(B.to_string())
    # 共同曝險拆解：現行名目 4,000U 的全部訊號帳本（R0），每筆標上是否被 Q20W30 停手
    t0 = R0['tr'].copy(); k2 = set(zip(R2['tr'].t_in, R2['tr'].side)); t0['kept'] = [(a, b) in k2 for a, b in zip(t0.t_in, t0.side)]
    t0['rs'] = t0.reason.replace({'MHx': 'MH延長', 'BE': 'MH延長'}); t0['win'] = t0.net > 0
    t0.to_csv(os.path.join(OUT, 'common_book_trades.csv'), index=False)
    def tab(g): return pd.Series(dict(筆=len(g), 淨=round(g.net.sum()), 勝率=round(g.win.mean() * 100, 1) if len(g) else np.nan))
    print('年×出場'); print(t0.groupby(['year', 'rs']).apply(tab).unstack(1).to_string())
    print('年×方向'); print(t0.groupby(['year', 'side']).apply(tab).unstack(1).to_string())
    print('年×是否保留(Q20W30)'); print(t0.groupby(['year', 'kept']).apply(tab).unstack(1).to_string())
    pz = t0[~t0.kept]; print('被停手：', tab(pz).to_dict(), '其中贏單', int(pz.win.sum()), round(pz[pz.win].net.sum()), '輸單', int((~pz.win).sum()), round(pz[~pz.win].net.sum())); print(pz.groupby(['year', 'rs']).apply(tab).unstack(1).to_string())
    print('保留者 年×出場'); print(t0[t0.kept].groupby(['year', 'rs']).apply(tab).unstack(1).to_string())
    print('2021 SN 明細'); print(t0[(t0.year == 2021) & (t0.reason == 'SN')][['side', 't_in', 't_out', 'ep', 'xp', 'net', 'volrank', 'r30', 'kept']].to_string())
    # R2 帳本與 R0 交易不一定一對一（熔斷與冷卻路徑不同）：列出 R2 有、R0 沒有者
    k0 = set(zip(t0.t_in, t0.side)); new = R2['tr'][[(a, b) not in k0 for a, b in zip(R2['tr'].t_in, R2['tr'].side)]]; print('Q20W30 帳本新增（路徑改變）', len(new), round(new.net.sum()))
    # 縮小部位的影響：同一批交易（Q20W30 帳本），現行名目對預算 3% 固定基數
    a = R2['tr'].set_index(['t_in', 'side']); b = R4['tr'].set_index(['t_in', 'side']); j = a[['net', 'ntl', 'reason', 'year']].join(b[['net', 'ntl']], rsuffix='_fr', how='outer'); j['ratio'] = j.ntl_fr / j.ntl
    print('名目比 中位', round(j.ratio.median(), 3), '未成對', int(j.net_fr.isna().sum()), int(j.net.isna().sum())); print(j.groupby('year').agg(筆=('net', 'count'), 現行名目淨=('net', 'sum'), 固定基數淨=('net_fr', 'sum'), 名目比=('ratio', 'median')).round(1).to_string())
    print(j.groupby('reason').agg(筆=('net', 'count'), 現行名目淨=('net', 'sum'), 固定基數淨=('net_fr', 'sum')).round(0).to_string())
