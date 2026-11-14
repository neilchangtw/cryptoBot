"""V70：趨勢腿每日區間再平衡＋Q20W30 固定風險現行腿，全倉 5 倍、1,500U。登記：doc/research_results/v70/v70_plan.md。
用法：python backtest/research/v70_run.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v70_account as E, v70_recon as RC
OUT = RC.OUT

def gates(d):
    g = dict(勝率=d['勝率'] > 50, 年度=all(d[f'y{y}'] > 0 for y in range(2021, 2027)), 最低權益=d['最低權益'] >= 1000, 百分比回撤=d['最大百分比回撤'] <= 40, 強平=d['強平觸及_全倉帳戶'] == 0 and d['強平觸及_逐倉估算'] == 0, 跳過=d['跳過'] == 0,
             五年MDD=d['五年MDD'] <= 1400, 滾動12月=d['最差滾動12月'] >= -900, 連敗段=d['最差連敗段_五年口徑'] >= -500); return g

if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; rows = []; wr = []
    def run(name, key, n=200, g=0.25, band=0.20, mode='rebal', base=True, sleeve=True):
        kw = dict(cross=True)
        if base: kw.update(base=X['q20'], base_mode=('risk', 0.03, 5.0))
        if sleeve: kw.update(sleeve=dict(sig=RC.trend_sig(X['dc'], n), mode=mode, g=g, band=band, lev=5.0))
        R = E.replay(df, fund, **kw); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(name, R, df); G = gates(d); d['G全過'] = all(G.values()); d['未過項'] = '、'.join(k for k, v in G.items() if not v)
        t = R['tr']; s = t[t.leg == 'trend']
        if len(s):
            d.update(趨勢腿筆數=len(s), 趨勢腿淨=round(s.net.sum()), 趨勢腿扣最佳一筆=round(s.net.sum() - s.net.max()), 趨勢腿成交次數=int(s.nfill.sum() + len(s)), 趨勢腿成本=round(s.cost.sum()), 趨勢腿資金費=round(s.fund.sum()), 趨勢腿逐年=json.dumps({int(k): round(v) for k, v in s.groupby(s.t_out.dt.year).net.sum().items()}), 現行腿淨=round(t[t.leg == 'base'].net.sum()))
        rows.append(d); RC.save(name, R, key); return R, d
    R, d = run('V70 主要（均線200、25%、±20%）', 'v70_main')
    print(json.dumps(d, ensure_ascii=False)); print('合併部位檢查', E.merged_check(R, df)); wr += RC.wr_tables('V70 主要', R['tr'], 54.35)
    a = R['acc']; e = a.equity; pk = np.maximum(e.cummax(), E.CAP); k = ((pk - e) / pk).idxmax(); p = e[:k].idxmax(); print('回撤拆解', {t: dict(時刻=str(x), 權益=round(a.equity[x], 1), 錢包=round(a.wallet[x], 1), 未實現=round(a.unrealized[x], 1), 名目=round(a.notional[x])) for t, x in (('高點', p), ('谷底', k))})
    s = a['2021-05-10':'2021-05-25']; print('2021-05 壓力段', dict(期初權益=round(s.equity.iloc[0], 1), 最低收盤權益=round(s.equity.min(), 1), 最低時刻=str(s.equity.idxmin()), 小時內保守最低=round(s.equity_intrahour.min(), 1), 最大名目對權益=round((s.notional / s.equity).max(), 2), 最低可用資金=round(s.free.min(), 1)))
    ew = a.equity_intrahour; pkw = np.maximum(e.cummax(), E.CAP); print('小時內保守口徑：最低權益', round(ew.min(), 1), '最大百分比回撤', round(((pkw - ew) / pkw).max() * 100, 1))
    for nm, key, kw in (('鄰域 均線150', 'v70_ma150', dict(n=150)), ('鄰域 均線250', 'v70_ma250', dict(n=250)), ('鄰域 比例15%', 'v70_g15', dict(g=.15)), ('鄰域 比例35%', 'v70_g35', dict(g=.35)), ('鄰域 區間10%', 'v70_b10', dict(band=.10)), ('鄰域 區間30%', 'v70_b30', dict(band=.30)),
                        ('對照 不再平衡（V69-B，全倉）', 'v70_hold', dict(mode='hold')), ('對照 只有現行腿（全倉）', 'v70_baseonly', dict(sleeve=False)), ('對照 只有趨勢腿', 'v70_trendonly', dict(base=False))):
        R2, d2 = run(nm, key, **kw); print(json.dumps({k: d2.get(k) for k in ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '勝率', '最大百分比回撤', '最低權益', 'MDD金額', '五年MDD', '最差滾動12月', '最差連敗段_五年口徑', '強平觸及_全倉帳戶', 'G全過', '未過項', '趨勢腿淨', '趨勢腿扣最佳一筆', '趨勢腿逐年', '現行腿淨')}, ensure_ascii=False), flush=True)
        if key in ('v70_trendonly',): wr += RC.wr_tables(nm, R2['tr'], 54.35)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v70_summary.csv'), index=False); w = pd.DataFrame(wr); w.to_csv(os.path.join(OUT, 'v70_winrate.csv'), index=False); print(w.to_string())
    nb = rows[1:7]; print('R1 鄰域：年度過', sum(all(x[f'y{y}'] > 0 for y in range(2021, 2027)) for x in nb), '/6；全部 G 過', sum(x['G全過'] for x in nb), '/6')
