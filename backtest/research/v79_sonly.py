"""V79 第二部分：只做空方的完整帳戶驗收（S1、S2、S3）對現行原配置與 V77。
用法：python backtest/research/v79_sonly.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC, v74_run as R74, v69a as A, v57b_lowvol as V
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v79'); RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

def sbook(df, ind0, sim, ok):
    n = len(df); ind = {**ind0, 'okL': np.zeros(n, bool), 'okS': ok}
    t = pd.DataFrame(sim(ind, df['datetime'].values, realistic=True, slip_bps=0.0, extra_cost=5.0))
    tr = pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, bar=t.entry_bar, ntl=t.margin * 20.0)); tr['m'] = 1.0; tr['snm'] = 1.0
    return tr[tr.t_in >= A.W0].sort_values(['t_in', 'side']).reset_index(drop=True)

if __name__ == '__main__':
    X = R74.setup('2026-11-01'); df, fund = X['df'], X['fund']; sim = A.build_sim(); ind0 = V.B.eng.compute_indicators(df); n = len(df); okq = ~(V.vol_rank(df) < 20)
    X['s_all'] = sbook(df, ind0, sim, np.ones(n, bool)); X['s_q20'] = sbook(df, ind0, sim, okq); rows = []; keep = {}
    def run(nm, book, mode, fixed=False):
        R = E.replay(df, fund, base=X[book], base_mode=mode, cross=False, fixed_basis=fixed); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(nm, R, df); t = R['tr']; e = R['acc'].equity
        mo = e.diff().fillna(e.iloc[0] - E.CAP).groupby(lambda x: x.strftime('%Y-%m')).sum(); tm = t.groupby(t.t_out.dt.strftime('%Y-%m')).size().reindex(mo.index).fillna(0); mh = t[t.reason == 'MH']; mhd = mh.groupby(mh.t_out.dt.floor('D')).net.sum().reindex(pd.date_range('2021-01-01', '2026-10-31')).fillna(0.0)
        pk = np.maximum(e.cummax(), E.CAP); inc = t.groupby(t.t_out.dt.floor('D')).net.sum(); cs = inc.cumsum()
        d.update(R74.weak(e)); d.update(PF=round(t[t.net > 0].net.sum() / -t[t.net < 0].net.sum(), 2), 平均名目=round(t.ntl.mean()), MH筆=len(mh), MH淨=round(mh.net.sum()), MH最差滾動30日=round(mhd.rolling(30).sum().min()), SN筆=int((t.reason == 'SN').sum()), SN淨=round(t[t.reason == 'SN'].net.sum()), 零交易月=int((tm == 0).sum()), 月為正比例=round(float((mo > 0).mean() * 100), 1), 最差月=round(mo.min()),
                 回撤_逐小時含浮虧全期=round((pk - e).max()), 回撤_平倉日已實現全期=round((np.maximum(cs.cummax(), 0) - cs).max()), 多方腿淨=round(t[t.side == 'L'].net.sum()), 空方腿淨=round(t[t.side == 'S'].net.sum()), 單筆最大虧損占錢包pct=round(float((-t.net / t.wallet).max() * 100), 2))
        rows.append(d); keep[nm] = R; return R, d
    R0, d0 = run('現行原配置', 'base', FIX); R1, d1 = run('V77：Q20W30＋停損價差預算3%', 'q20', RISK); Ra, da = run('S1：空方單獨＋現行名目', 's_all', FIX); Rb, db = run('S2：空方單獨＋Q20W30＋預算3%', 's_q20', RISK); Rc, dc = run('S3：S2 固定資金基數', 's_q20', RISK, True); run('對照：V77 固定資金基數', 'q20', RISK, True)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'sonly_summary.csv'), index=False); pd.set_option('display.width', 320); pd.set_option('display.max_columns', 60); pd.set_option('display.max_rows', 200); print(T.T.to_string())
    g = {}
    for nm, d in (('現行原配置', d0), ('V77', d1), ('S1', da), ('S2', db)):
        g[nm] = {'勝率>50%': d['勝率'] > 50, **{f'{y}年為正': d[f'y{y}'] > 0 for y in range(2021, 2026)}, '2026年初至10月為正': d['y2026'] > 0, '全期淨利≥現行': d['淨利'] >= d0['淨利'], '最低權益≥1000': d['最低權益'] >= 1000, '百分比回撤≤40%': d['最大百分比回撤'] <= 40, '零強平觸及': d['強平觸及_逐倉估算'] == 0, '五年MDD≤1400': d['五年MDD'] <= 1400, '最差滾動12月≥-900': d['最差滾動12月'] >= -900, '最差連敗段≥-500': d['最差連敗段_五年口徑'] >= -500}
    G = pd.DataFrame(g).replace({True: '通過', False: '未通過'}); G.to_csv(os.path.join(OUT, 'sonly_gates.csv')); print(G.to_string())
    for k, R in (('s1', Ra), ('s2', Rb)): R['tr'].to_csv(os.path.join(OUT, f'{k}_trades.csv'), index=False); R['acc'].round(4).to_csv(os.path.join(OUT, f'{k}_account_hourly.csv'))
    # 逐筆變化：V77 對 S2
    a = R1['tr']; b = Rb['tr']; ka = set(zip(a.t_in, a.side)); kb = set(zip(b.t_in, b.side)); gone = a[[(x, y) not in kb for x, y in zip(a.t_in, a.side)]]; new = b[[(x, y) not in ka for x, y in zip(b.t_in, b.side)]]
    ch = dict(V77有而S2無=dict(筆=len(gone), 多方=int((gone.side == 'L').sum()), 空方=int((gone.side == 'S').sum()), 贏單=int((gone.net > 0).sum()), 贏單淨=round(gone[gone.net > 0].net.sum()), 輸單=int((gone.net <= 0).sum()), 輸單淨=round(gone[gone.net <= 0].net.sum())), S2新增=dict(筆=len(new), 淨=round(new.net.sum())))
    print(ch); json.dump(ch, open(os.path.join(OUT, 'sonly_changes.json'), 'w'), ensure_ascii=False, indent=1); pd.concat([gone.assign(變化='V77有而S2無'), new.assign(變化='S2新增')]).to_csv(os.path.join(OUT, 'sonly_trade_changes.csv'), index=False)
