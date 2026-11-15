"""V83-C 結果後稽核（登記後追加、探索性；不改變 D0 判定）。目的：確認 +2,294U／千U 不是程式錯誤或資訊外洩，並拆出與 ETH 自身動能的關係。
用法：python backtest/research/v83_c_audit.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v83_feas as FE
OUT = FE.OUT; A = {}
# 獨立重算：不用 v83_d0 的函式，價格改用既有 1 小時 K 收盤（T 收盤價成交），成本 22.5bp，不計資金費
h = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_1h_2020_2026.csv'), parse_dates=['datetime']); P = pd.Series(h.close.values, index=h.datetime - pd.Timedelta(hours=7))   # 收盤 UTC
c = pd.read_csv(os.path.join(FE.D, 'cot_ether_146021.csv'), parse_dates=['rd']).set_index('rd'); ns = (c.Lev_Money_Positions_Short_All - c.Lev_Money_Positions_Long_All) / c.Open_Interest_All; dn = ns.diff(); z = (dn / dn.rolling(26).std().shift(1)).dropna()
ex = lambda rd: (pd.Timestamp('2023-01-31') <= rd <= pd.Timestamp('2023-03-21')) or (pd.Timestamp('2025-09-30') <= rd <= pd.Timestamp('2025-11-18'))
def run(zz, lag_days, hold_days=7, thr=1.0, hour=0):
    rows = []
    for rd, x in zz.items():
        T = rd + pd.Timedelta(days=lag_days, hours=hour); X = T + pd.Timedelta(days=hold_days)
        if ex(rd) or abs(x) < thr or T not in P.index or X not in P.index or X > pd.Timestamp('2026-10-31 23:00') or T < pd.Timestamp('2021-01-01'): continue
        s = 1.0 if x > 0 else -1.0; rows.append(dict(rd=rd, Td=T, s=s, z=x, gross=s * (P[X] / P[T] - 1), net=s * (P[X] / P[T] - 1) - 0.00225))
    return pd.DataFrame(rows)
def summ(t): return dict(筆數=len(t), 淨合計_每千U=round(t.net.sum() * 1000, 1), 平均毛bp=round(t.gross.mean() * 1e4, 1), 勝率=round((t.net > 0).mean() * 100, 1), 逐年={int(k): round(float(v) * 1000, 1) for k, v in t.groupby(t.Td.dt.year).net.sum().items()}) if len(t) else dict(筆數=0)
m = run(z, 7); A['獨立重算_下週二00UTC_持有7天'] = summ(m)
# 決策時點敏感度：週五發布後（持倉日+4 天 00:00 UTC＝週六）、持倉日+5、+6；以及刻意外洩的安慰劑（持倉日當下、持倉日前 7 天）
for nm, lag in (('持倉日+4天（週六00UTC，正常發布後約8小時）', 4), ('持倉日+5天', 5), ('持倉日+6天', 6), ('持倉日+14天（再晚一週）', 14), ('外洩對照_持倉日當下（不可交易）', 0), ('外洩對照_持倉日前7天（不可交易）', -7)): A[nm] = summ(run(z, lag))
# 與 ETH 自身報酬的關係
rows = []
for rd, x in z.items():
    T = rd + pd.Timedelta(days=7)
    if ex(rd) or T + pd.Timedelta(days=7) not in P.index or rd - pd.Timedelta(days=7) not in P.index or T + pd.Timedelta(days=7) > pd.Timestamp('2026-10-31 23:00'): continue
    rows.append(dict(rd=rd, z=x, s=float(np.sign(x)) if abs(x) >= 1 else 0.0, fwd=P[T + pd.Timedelta(days=7)] / P[T] - 1, r_pos=P[rd] / P[rd - pd.Timedelta(days=7)] - 1, r_wait=P[T] / P[rd] - 1))
X = pd.DataFrame(rows); A['相關'] = dict(z與持倉週ETH報酬=round(float(X.z.corr(X.r_pos)), 3), z與等待週ETH報酬=round(float(X.z.corr(X.r_wait)), 3), z與後續週ETH報酬=round(float(X.z.corr(X.fwd)), 3), 持倉週與後續週ETH報酬=round(float(X.r_pos.corr(X.fwd)), 3), 等待週與後續週ETH報酬=round(float(X.r_wait.corr(X.fwd)), 3))
def nw(y, Xm, lag=4):
    Xm = np.column_stack([np.ones(len(y)), Xm]); b = np.linalg.lstsq(Xm, y, rcond=None)[0]; u = y - Xm @ b; S = (Xm * u[:, None]).T @ (Xm * u[:, None])
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1); G = (Xm[L:] * u[L:, None]).T @ (Xm[:-L] * u[:-L, None]); S += w * (G + G.T)
    Xi = np.linalg.inv(Xm.T @ Xm); return b, b / np.sqrt(np.diag(Xi @ S @ Xi))
b, t = nw(X.fwd.values, X[['s', 'r_pos', 'r_wait']].values); A['迴歸_控制持倉週與等待週報酬'] = dict(訊號係數bp=round(float(b[1]) * 1e4, 1), 訊號t=round(float(t[1]), 2), 持倉週報酬t=round(float(t[2]), 2), 等待週報酬t=round(float(t[3]), 2), 樣本=len(X))
# 同曝險對照：以「持倉週 ETH 報酬的符號」「等待週 ETH 報酬的符號」取代訊號，在相同事件週交易
ev = X[X.s != 0]; A['同事件週_改用ETH自身方向'] = dict(用訊號=round(float((ev.s * ev.fwd - 0.00225).sum()) * 1000, 1), 用持倉週報酬符號=round(float((np.sign(ev.r_pos) * ev.fwd - 0.00225).sum()) * 1000, 1), 用等待週報酬符號=round(float((np.sign(ev.r_wait) * ev.fwd - 0.00225).sum()) * 1000, 1), 常多=round(float((ev.fwd - 0.00225).sum()) * 1000, 1), 常空=round(float((-ev.fwd - 0.00225).sum()) * 1000, 1), 事件週數=len(ev))
allw = X; A['全部週_ETH自身動能'] = dict(每週依持倉週報酬符號=round(float((np.sign(allw.r_pos) * allw.fwd - 0.00225).sum()) * 1000, 1), 每週依等待週報酬符號=round(float((np.sign(allw.r_wait) * allw.fwd - 0.00225).sum()) * 1000, 1), 週數=len(allw))
# 集中度
g = m.sort_values('net', ascending=False); A['集中度'] = dict(最大5筆合計=round(g.net.head(5).sum() * 1000, 1), 去最大5筆=round(g.net.iloc[5:].sum() * 1000, 1), 去最大10筆=round(g.net.iloc[10:].sum() * 1000, 1), 最大單筆=round(g.net.iloc[0] * 1000, 1), 最差單筆=round(g.net.iloc[-1] * 1000, 1), 持有期最大不利單筆毛pct=round(float(m.gross.min()) * 100, 1))
A['分段'] = {k: summ(m[(m.Td >= a) & (m.Td < b_)]) for k, a, b_ in (('2021-04~2024-06（ETF 前）', '2021-01-01', '2024-07-01'), ('2024-07~2026-10（現貨 ETF 後）', '2024-07-01', '2027-01-01'))}
m.to_csv(os.path.join(OUT, 'C_audit_independent_trades.csv'), index=False); json.dump(A, open(os.path.join(OUT, 'C_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str); print(json.dumps(A, ensure_ascii=False, indent=1, default=str))
