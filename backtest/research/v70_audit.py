"""V70 結果後稽核（非門檻，全部記入試驗紀錄表）：① 趨勢腿不含 2021-01-01 既有趨勢的第一筆；② 訊號延後 1 天執行；③ 避開的虧損／錯過的贏單／新增交易。
用法：python backtest/research/v70_audit.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v70_account as E, v70_recon as RC, v70_run as RN
if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; sig = RC.trend_sig(X['dc']); s0 = sig[sig.index >= E.W0]; first_flip = s0.index[(s0 != s0.iloc[0]).values][0]
    v1 = sig.copy(); v1[v1.index < first_flip] = 0.0; v2 = sig.copy(); v2.index = v2.index + pd.Timedelta(days=1); rows = []
    for nm, sg in (('稽核① 趨勢腿自第一次翻向（%s）才開始' % str(first_flip.date()), v1), ('稽核② 訊號延後 1 天執行', v2)):
        R = E.replay(df, fund, base=X['q20'], base_mode=('risk', 0.03, 5.0), sleeve=dict(sig=sg, mode='rebal', g=0.25, band=0.20, lev=5.0), cross=True); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(nm, R, df); G = RN.gates(d); t = R['tr']; s = t[t.leg == 'trend']
        d.update(G全過=all(G.values()), 未過項='、'.join(k for k, v in G.items() if not v), 趨勢腿淨=round(s.net.sum()), 趨勢腿逐年=json.dumps({int(k): round(v) for k, v in s.groupby(s.t_out.dt.year).net.sum().items()})); rows.append(d)
        print(json.dumps({k: d.get(k) for k in ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '勝率', '最大百分比回撤', '最低權益', 'G全過', '未過項', '趨勢腿淨', '趨勢腿逐年')}, ensure_ascii=False))
    pd.DataFrame(rows).to_csv(os.path.join(RC.OUT, 'v70_audit.csv'), index=False)
    m = pd.read_csv(os.path.join(RC.OUT, 'v70_main_trades.csv'), parse_dates=['t_in', 't_out']); s = m[m.leg == 'trend'].sort_values('net', ascending=False)
    print('趨勢腿前 5 大\n', s[['side', 't_in', 't_out', 'ep', 'xp', 'ntl', 'maxntl', 'nfill', 'net']].head(5).round(1).to_string()); print('趨勢腿：前 3 筆合計', round(s.net.head(3).sum()), '其餘', round(s.net.iloc[3:].sum()))
    b = m[m.leg == 'base']; q = pd.read_csv(os.path.join(RC.OUT, 'q20risk_trades.csv'), parse_dates=['t_in', 't_out']); o = pd.read_csv(os.path.join(RC.OUT, 'origrisk_trades.csv'), parse_dates=['t_in', 't_out'])
    j = b.merge(q, on=['side', 't_in'], suffixes=('', '_q')); print('現行腿同一批訊號', len(j), '/', len(b), '；V70 現行腿淨', round(b.net.sum()), 'Q20W30固定風險', round(q.net.sum()), '差額（錢包變大造成的名目放大）', round(b.net.sum() - q.net.sum()), '平均名目', round(b.ntl.mean()), round(q.ntl.mean()))
    ko = set(zip(o.side, o.t_in)); kq = set(zip(q.side, q.t_in)); rm = o[[k not in kq for k in zip(o.side, o.t_in)]]; nw = q[[k not in ko for k in zip(q.side, q.t_in)]]
    print('相對「現行＋固定風險」：避開', len(rm), '筆，其中虧損', int((rm.net < 0).sum()), '筆', round(rm[rm.net < 0].net.sum()), '；錯過贏單', int((rm.net > 0).sum()), '筆', round(rm[rm.net > 0].net.sum()), '；新增現行腿', len(nw), '筆', round(nw.net.sum()), '；新增趨勢腿', len(s), '筆', round(s.net.sum()))
