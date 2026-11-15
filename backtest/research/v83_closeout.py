"""V83 收尾（不調參；探索性描述，已列入台帳）：C 的時間線分段、兩份程式對帳、循環平移的完整列舉、集中度與事件群、前瞻檢定力換算。
用法：python backtest/research/v83_closeout.py"""
import os, sys, json, math
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v83_feas as FE
OUT = FE.OUT; A = {}
h = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_1h_2020_2026.csv'), parse_dates=['datetime']); P = pd.Series(h.close.values, index=h.datetime - pd.Timedelta(hours=7))
c = pd.read_csv(os.path.join(FE.D, 'cot_ether_146021.csv'), parse_dates=['rd']).set_index('rd'); ns = (c.Lev_Money_Positions_Short_All - c.Lev_Money_Positions_Long_All) / c.Open_Interest_All; dn = ns.diff(); z = (dn / dn.rolling(26).std().shift(1)).dropna()
ex = lambda rd: (pd.Timestamp('2023-01-31') <= rd <= pd.Timestamp('2023-03-21')) or (pd.Timestamp('2025-09-30') <= rd <= pd.Timestamp('2025-11-18'))
# ---- 1. 時間線：報告日星期分布、發布時刻、分段報酬
A['持倉基準日星期分布'] = {str(k): int(v) for k, v in pd.Series(c.index.dayofweek).map({0: '週一', 1: '週二', 2: '週三', 3: '週四', 4: '週五'}).value_counts().items()}
def rel(rd): return (pd.Timestamp((rd + pd.Timedelta(days=3)).date()).tz_localize('America/New_York') + pd.Timedelta(hours=15, minutes=30)).tz_convert('UTC').tz_localize(None)
lagh = [((rd + pd.Timedelta(days=7)) - rel(rd)).total_seconds() / 3600 for rd in z.index if rd.dayofweek == 1]; A['決策距正常發布時刻_小時'] = dict(最小=round(min(lagh), 1), 最大=round(max(lagh), 1), 說明='正常發布＝持倉日後週五 15:30 美東；決策＝持倉日後第 7 天 00:00 UTC')
ev = [(rd, 1.0 if x >= 1 else -1.0) for rd, x in z.items() if abs(x) >= 1 and not ex(rd) and rd + pd.Timedelta(days=21) in P.index and rd + pd.Timedelta(days=21) <= pd.Timestamp('2026-10-31 23:00')]
seg = [(-7, 0, '持倉變動所在的那一週（訊號形成期，不可交易）'), (0, 3, '持倉日後 0～3 天（尚未發布）'), (3, 4, '發布當日前後（持倉日後 3～4 天）'), (4, 7, '發布後約 0.2～3.2 天'), (7, 9, '發布後約 3.2～5.2 天'), (9, 11, '發布後約 5.2～7.2 天'), (11, 14, '發布後約 7.2～10.2 天'), (14, 21, '發布後約 10.2～17.2 天')]
S = []
for a, b, nm in seg:
    r = np.array([s * (P[rd + pd.Timedelta(days=b)] / P[rd + pd.Timedelta(days=a)] - 1) for rd, s in ev]); S.append(dict(區間_持倉日後天數=f'{a}～{b}', 說明=nm, 事件數=len(r), 順訊號毛報酬合計_每千U=round(float(r.sum()) * 1000, 1), 每日平均bp=round(float(r.mean()) * 1e4 / (b - a), 1), t=round(float(r.mean() / r.std(ddof=1) * math.sqrt(len(r))), 2)))
A['分段報酬_毛_不扣成本'] = S; pd.DataFrame(S).to_csv(os.path.join(OUT, 'C_closeout_segments.csv'), index=False)
d = dn.dropna(); A['淨空單週變動的自相關'] = {f'落後{k}週': round(float(d.autocorr(k)), 3) for k in (1, 2, 3, 4)}
zs = pd.Series(np.where(z >= 1, 1.0, np.where(z <= -1, -1.0, 0.0)), index=z.index); A['訊號方向的自相關'] = {f'落後{k}週': round(float(zs.autocorr(k)), 3) for k in (1, 2, 3)}
# 訊號是否只是預告了兩週後的持倉變動（解釋用；Δ(t+2) 在決策時未知）
X = pd.DataFrame(dict(s=zs, d2=dn.shift(-2), fwd=pd.Series([P.get(rd + pd.Timedelta(days=14), np.nan) / P.get(rd + pd.Timedelta(days=7), np.nan) - 1 for rd in zs.index], index=zs.index))).dropna(); X = X[[not ex(r) for r in X.index]]
A['解釋用相關'] = dict(訊號與兩週後持倉變動=round(float(X.s.corr(X.d2)), 3), 兩週後持倉變動與同週ETH報酬=round(float(X.d2.corr(X.fwd)), 3), 訊號與該週ETH報酬=round(float(X.s.corr(X.fwd)), 3), 樣本=len(X))
A['重疊持倉'] = '主要配置每週至多一個訊號、持有 7 天，持倉不重疊（跳過 0 筆）。'
# ---- 2. 對帳
m1 = pd.read_csv(os.path.join(OUT, 'C_events_main.csv'), parse_dates=['T']); m2 = pd.read_csv(os.path.join(OUT, 'C_audit_independent_trades.csv'), parse_dates=['Td']); j = m1.merge(m2, left_on='T', right_on='Td', suffixes=('_d0', '_ind'))
A['兩份程式對帳'] = dict(D0筆數=len(m1), 獨立版筆數=len(m2), 配對筆數=len(j), D0淨=round(m1.net.sum() * 1000, 1), 獨立版淨=round(m2.net.sum() * 1000, 1), 差=round((m1.net.sum() - m2.net.sum()) * 1000, 1), 來自成交價_T加1分對T收盤=round((j.gross_d0 - j.gross_ind).sum() * 1000, 1), 來自資金費_D0有扣獨立版沒扣=round(-j.fund.sum() * 1000, 1), 殘差=round(((j.net_d0 - j.net_ind) - (j.gross_d0 - j.gross_ind) + j.fund).sum() * 1000, 3), 方向全同=bool((j.s_d0 == j.s_ind).all()))
# ---- 循環平移：完整列舉 8～200 週（共 193 個相異平移）
def net_of(zz):
    tot = 0.0
    for rd, x in zz.items():
        T = rd + pd.Timedelta(days=7); Xx = T + pd.Timedelta(days=7)
        if ex(rd) or abs(x) < 1 or T not in P.index or Xx not in P.index or Xx > pd.Timestamp('2026-10-31 23:00'): continue
        tot += (1.0 if x > 0 else -1.0) * (P[Xx] / P[T] - 1) - 0.00225
    return tot * 1000
obs = net_of(z); allv = np.array([net_of(pd.Series(np.roll(z.values, k), index=z.index)) for k in range(8, 201)]); full = np.array([net_of(pd.Series(np.roll(z.values, k), index=z.index)) for k in range(1, len(z))])
A['循環平移'] = dict(觀測=round(obs, 1), 登記範圍_相異平移數=len(allv), 登記範圍_達到或超過觀測=int((allv >= obs).sum()), 登記範圍_p=round((1 + (allv >= obs).sum()) / (len(allv) + 1), 4), 登記範圍_最大=round(float(allv.max()), 1), 全部平移數=len(full), 全部_達到或超過=int((full >= obs).sum()), 全部_p=round((1 + (full >= obs).sum()) / (len(full) + 1), 4),
    說明='D0 的 1,000 次是從 193 個相異平移中重複抽樣，零次超過只代表 p 不大於約 1/194；p 以 (1＋超過數)÷(相異平移數＋1) 估計，解析度約 0.005。循環平移保留訊號序列自身的時間相依，但不保留訊號與價格的共同結構變化。')
net = m2.sort_values('Td').net.values; n = len(net); mu = net.mean(); sd = net.std(ddof=1); rho = float(pd.Series(net).autocorr(1)); A['逐筆淨報酬分布'] = dict(筆數=n, 平均bp=round(mu * 1e4, 1), 標準差bp=round(sd * 1e4, 1), 中位數bp=round(float(np.median(net)) * 1e4, 1), 落後1筆自相關=round(rho, 3), 簡單t=round(mu / sd * math.sqrt(n), 2), 偏度=round(float(pd.Series(net).skew()), 2))
A['多重比較'] = dict(V83內3個主要假說_門檻=round(0.05 / 3, 4), V82加V83共6個_門檻=round(0.05 / 6, 4), 自V46起事前登記方向性假說約31個_門檻=round(0.05 / 31, 4), 判讀='登記範圍 p 約 0.005：通過本輪 3 個與兩輪 6 個的校正門檻，未通過以全部 31 個假說計的門檻 0.0016。另有本輪鄰域 4 個與稽核約 15 項未列入校正。全部歷史已曝光，p 值僅供描述。')
# ---- 集中度與事件群（相鄰週、同方向的連續訊號視為同一行情事件）
t = m2.sort_values('Td').reset_index(drop=True); cid = ((t.Td.diff() > pd.Timedelta(days=7)) | (t.s != t.s.shift())).cumsum(); t['群'] = cid; g = t.groupby('群').agg(起=('Td', 'min'), 迄=('Td', 'max'), 筆=('net', 'size'), 方向=('s', 'first'), 淨=('net', 'sum')).sort_values('淨', ascending=False); g['淨'] = (g.淨 * 1000).round(1); g.to_csv(os.path.join(OUT, 'C_closeout_clusters.csv'))
top = t.sort_values('net', ascending=False).head(10); A['集中度'] = dict(事件群數=len(g), 單筆群數=int((g.筆 == 1).sum()), 最大群筆數=int(g.筆.max()), 最大5群合計=round(float(g.淨.head(5).sum()), 1), 去最大5群=round(float(g.淨.iloc[5:].sum()), 1), 為正的群比例=round(float((g.淨 > 0).mean()), 3), 最佳10筆分屬幾個群=int(top.群.nunique()), 最佳10筆=[dict(決策=str(r.Td)[:10], 方向='多' if r.s > 0 else '空', 淨_每千U=round(r.net * 1000, 1), 群=int(r.群)) for r in top.itertuples()], 最佳10筆年度分布={int(k): int(v) for k, v in top.Td.dt.year.value_counts().sort_index().items()})
# ---- 3. 前瞻檢定力（單尾 α=0.05、檢定力 0.8；以歷史逐筆標準差換算）
per_year = n / ((t.Td.max() - t.Td.min()).days / 365.25); cl_per_year = len(g) / ((t.Td.max() - t.Td.min()).days / 365.25); pw = []
for nm, delta in (('等於歷史估計（偏樂觀，含選擇偏誤）', mu), ('歷史估計的一半', mu / 2), ('每筆淨 +100bp', 0.01), ('每筆淨 +50bp', 0.005)):
    need = ((1.645 + 0.842) * sd / delta) ** 2; pw.append(dict(假設真實效果=nm, 每筆bp=round(delta * 1e4, 1), 所需獨立事件數=int(math.ceil(need)), 以每年訊號數換算年數=round(need / per_year, 1), 以每年事件群數換算年數=round(need / cl_per_year, 1)))
A['前瞻檢定力'] = dict(每年訊號數=round(per_year, 1), 每年事件群數=round(cl_per_year, 1), 換算=pw, 假設='逐筆淨報酬獨立同分布、標準差等於歷史值；實際有厚尾與群聚，所需樣本只會更多')
json.dump(A, open(os.path.join(OUT, 'C_closeout.json'), 'w'), ensure_ascii=False, indent=1, default=str); print(json.dumps(A, ensure_ascii=False, indent=1, default=str))
