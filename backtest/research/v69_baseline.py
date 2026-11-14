"""V69 第一步：1,500U 有限帳戶重跑現行基準與 Q20W30，拆解 MH、止損、連敗、狀態。
用法：python backtest/research/v69_baseline.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v57_metrics as M, v61_finite as F
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v69'); CAP = 1500.0; F.CAP = CAP

def states(df):
    """進場當下已知的狀態（index＝小時線收盤時刻 UTC）：低波動（Q20W30 百分位<20）、30 日報酬、SMA200 斜率方向。"""
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); c = pd.Series(df.close.values, index=ct)
    rank = pd.Series(V.vol_rank(df), index=ct); r30 = c / c.shift(24 * 30) - 1; r7 = c / c.shift(24 * 7) - 1
    return pd.DataFrame(dict(volrank=rank, r30=r30, r7=r7))

def streak_table(c):
    c = c.sort_values(['t_out', 't_in']).reset_index(drop=True); rows = []; cur = []
    for r in c.itertuples():
        if r.net < 0: cur.append(r)
        else:
            if len(cur) >= 3: rows.append(dict(start=cur[0].t_in, end=cur[-1].t_out, n=len(cur), loss=round(sum(x.net for x in cur), 1), mh=sum(x.reason == 'MH' for x in cur), sn=sum(x.reason == 'SN' for x in cur)))
            cur = []
    if len(cur) >= 3: rows.append(dict(start=cur[0].t_in, end=cur[-1].t_out, n=len(cur), loss=round(sum(x.net for x in cur), 1), mh=sum(x.reason == 'MH' for x in cur), sn=sum(x.reason == 'SN' for x in cur)))
    return pd.DataFrame(rows)

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); sim = V.build_sim(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20); S = states(df)
    for name, ok in (('基準', ones), ('Q20W30', okq)):
        tr, eq, cost, sk, it, halted = F.replay(name, df, ind0, sim, fund, ok, None, None); m = F.metrics(name, tr, eq, df, sk)
        c = cost[cost.t_in >= F.W0].reset_index(drop=True); c['reason'] = tr.reason.values[:len(c)]; c['year'] = c.t_out.dt.year; st = S.reindex(c.t_in).reset_index(drop=True); c = pd.concat([c, st], axis=1)
        c['lowvol'] = c.volrank < 20; c['down30'] = c.r30 <= -0.15; c['up30'] = c.r30 >= 0.15; c['counter'] = ((c.side == 'L') & (c.r30 < 0)) | ((c.side == 'S') & (c.r30 > 0))
        pk = np.maximum(eq.cummax(), CAP); dd = pk - eq
        print(f'\n===== {name}（1,500U）=====')
        print(json.dumps({k: m[k] for k in ('淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', 'MDD', '最低淨值', '最低淨值日', '最低可用資金', '筆數', '跳過_現行', '觸及強平價_現行')}, ensure_ascii=False), '停止', halted, '最大百分比回撤', round((dd / pk).max() * 100, 1), '勝率', round((c.net > 0).mean() * 100, 2))
        print('出場別\n', c.groupby('reason').net.agg(['count', 'sum', 'mean']).round(1).T.to_string())
        yr = c.pivot_table(index='year', columns='reason', values='net', aggfunc='sum').round(0).fillna(0); yr['合計'] = yr.sum(axis=1); yr['MH筆'] = c[c.reason == 'MH'].groupby('year').size(); yr['總筆'] = c.groupby('year').size(); yr['MH占比%'] = (yr['MH筆'] / yr['總筆'] * 100).round(1)
        print('逐年×出場\n', yr.to_string()); ys = c.pivot_table(index='year', columns='side', values='net', aggfunc='sum').round(0); print('逐年×多空\n', ys.T.to_string())
        if name == '基準':
            for col in ('lowvol', 'down30', 'up30', 'counter'):
                g = c.groupby([col, 'side']).agg(n=('net', 'size'), net=('net', 'sum'), wr=('net', lambda x: (x > 0).mean()), mh_n=('reason', lambda x: (x == 'MH').sum()), mh_net=('net', lambda x: x[c.loc[x.index, 'reason'] == 'MH'].sum())).round(2); print(f'狀態 {col}\n', g.to_string())
            stt = streak_table(c); stt.to_csv(os.path.join(OUT, 'baseline_streaks_ge3.csv'), index=False); stt['year'] = pd.to_datetime(stt.start).dt.year
            print('連敗段（>=3 筆）逐年\n', stt.groupby('year').agg(段數=('n', 'size'), 最長=('n', 'max'), 合計損失=('loss', 'sum'), 最差=('loss', 'min'), MH筆=('mh', 'sum'), SN筆=('sn', 'sum')).to_string()); print('最差 6 段\n', stt.sort_values('loss').head(6).to_string(index=False))
            c.to_csv(os.path.join(OUT, 'baseline_1500_trades_states.csv'), index=False); eq.rename('equity').to_csv(os.path.join(OUT, 'baseline_1500_equity_hourly.csv'))
            R0, T0 = M.evaluate(C.mtm_equity(tr, df, fund)[0], c, '基準'); print('五年指標', {k: (round(R0[k], 1) if isinstance(R0[k], (int, float)) else R0[k]) for k in ('r12_worst', 'mdd_5y', 'streak_worst_loss', 'streak_max_n', 'r90_worst', 'mdd_recovery_days_5y', 'streak4_per100')})
        else:
            c.to_csv(os.path.join(OUT, 'q20w30_1500_trades_states.csv'), index=False)
