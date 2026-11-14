"""V80：1,500U 帳戶回放（相位 0；現行原配置與 V77 配置；F0／F1／F2），回放格點 15 分鐘，逐時指標取整點值。
用法：python backtest/research/v80_account.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C, v70_account as E, v57b_lowvol as V
OUT = L.OUT; RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); df = P[0]; ind = L9.indicators(df); okq = ~(V.vol_rank(df) < 20)
    g = d15.copy(); g['datetime'] = g.datetime + pd.Timedelta(minutes=15) - pd.Timedelta(hours=1)   # 使 datetime−7h＝該 15 分 K 的收盤 UTC
    rows = []; keep = {}
    for m in L.MODELS:
        for nm, ok, mode in (('現行原配置', None, FIX), ('V77 配置（Q20W30＋停損價差預算3%）', okq, RISK)):
            t = L.closed(L.book(d15, ct15, df, K[0], ind, m, 'LS', fund=fund, ok=ok)); bk = pd.DataFrame(dict(side=t.side, t_in=t.t_in, t_out=t.t_out, ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, snm=1.0)).reset_index(drop=True)
            R = E.replay(g, fund, base=bk, base_mode=mode, cross=False); full = R['acc']; R['acc'] = full[full.index.minute == 0]; d = E.summary(f'{m}｜{nm}', R, g); tr = R['tr']; e = R['acc'].equity; pk = np.maximum(e.cummax(), E.CAP); mh = tr[tr.reason == 'MH']
            d.update(成交模型=m, 配置=nm, 回撤_全期逐時市價權益=round((pk - e).max()), 十五分格點最低權益=round(full.equity.min()), MH筆=len(mh), MH淨=round(mh.net.sum()), SN筆=int((tr.reason == 'SN').sum()), SN淨=round(tr[tr.reason == 'SN'].net.sum()), 單筆最大虧損占錢包pct=round(float((-tr.net / tr.wallet).max() * 100), 2), 跳過筆=len(R['skips'])); rows.append(d); keep[(m, nm)] = R
            tr.to_csv(os.path.join(OUT, f'acct_{m}_{"orig" if ok is None else "v77"}_trades.csv'), index=False); R['acc'].round(3).to_csv(os.path.join(OUT, f'acct_{m}_{"orig" if ok is None else "v77"}_equity_hourly.csv.gz'))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'acct_summary.csv'), index=False); pd.set_option('display.width', 330); pd.set_option('display.max_columns', 70); pd.set_option('display.max_rows', 200)
    cols = ['淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝', '敗', '平', '勝率', '平均贏單', '平均輸單', '每筆平均淨利', 'MH筆', 'MH淨', 'SN筆', 'SN淨', '最長連敗', '最差連敗段_五年口徑', '最低權益', '十五分格點最低權益', '最大百分比回撤', '回撤_全期逐時市價權益', '五年MDD', '最差滾動12月', '最差90天', '強平觸及_逐倉估算', '跳過筆', '單筆最大虧損占錢包pct']
    print(T.set_index(['配置', '成交模型'])[cols].T.to_string())
    G = {}
    for d in rows:
        G[d['版本']] = {'勝率>50%': d['勝率'] > 50, **{f'{y}年為正': d[f'y{y}'] > 0 for y in range(2021, 2026)}, '2026年初至10月為正': d['y2026'] > 0, '全期淨利≥F0現行': d['淨利'] >= rows[0]['淨利'], '最低權益≥1000': d['最低權益'] >= 1000, '百分比回撤≤40%': d['最大百分比回撤'] <= 40, '簡化強平線零觸及': d['強平觸及_逐倉估算'] == 0, '五年MDD≤1400': d['五年MDD'] <= 1400, '最差滾動12月≥-900': d['最差滾動12月'] >= -900, '最差連敗段≥-500': d['最差連敗段_五年口徑'] >= -500}
    Gd = pd.DataFrame(G).replace({True: '通過', False: '未通過'}); Gd.to_csv(os.path.join(OUT, 'acct_gates.csv')); print(Gd.to_string())
