"""V79 C：多方事件群配對、缺席原因、共同進場差異拆解、集中度、封鎖條件貢獻。
用法：python backtest/research/v79_events.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v79_lib as L, v53_common as C
OUT = L.OUT; pd.set_option('display.width', 320); pd.set_option('display.max_columns', 60); GAP = pd.Timedelta(hours=3); E = L.eng

def why_absent(df, ind, tr, t0):
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); a, b = np.searchsorted(ct, t0 - GAP), np.searchsorted(ct, t0 + GAP, side='right'); ix = np.arange(a, b)
    brk = ind['brk_up'][ix]; comp = ind['pctile_L'][ix] < E.L_GK_TH; blk = np.isin(ind['hours'][ix], list(E.L_BLK_H)) | np.isin(ind['dows'][ix], list(E.L_BLK_D)); reg = ind['regime_block_l'][ix]
    if (brk & comp & ~blk & ~reg).any():
        t = ct[ix][(brk & comp & ~blk & ~reg)][0]; inpos = ((tr.t_in < t) & (tr.t_out >= t)).any(); last = tr.t_out[tr.t_out < t].max() if (tr.t_out < t).any() else pd.NaT
        if inpos: return '狀態：持倉中'
        if pd.notna(last) and (t - last) < pd.Timedelta(hours=E.L_CD): return '狀態：冷卻'
        return '狀態：熔斷或月上限'
    if (brk & comp & ~blk).any(): return '趨勢閘門'
    if (brk & comp).any(): return '時段或星期封鎖'
    if brk.any(): return '有突破但壓縮不過'
    return '無突破'

if __name__ == '__main__':
    d15, P = L.phases(); fund = C.load_funding(); IND = {ph: L.indicators(df) for ph, df in P.items()}; T = {ph: L.book(P[ph], IND[ph], 'L', fund=fund).assign(ph=ph) for ph in L.PH}
    A = pd.concat(T.values()).sort_values('t_in').reset_index(drop=True); A['cl'] = (A.t_in.diff() > GAP).cumsum()
    g = A.groupby('cl'); cl = pd.DataFrame(dict(t0=g.t_in.min(), t1=g.t_in.max(), n=g.size(), phs=g.ph.apply(lambda x: tuple(sorted(set(x)))), dup=g.ph.apply(lambda x: x.duplicated().any())))
    cl['類別'] = np.where(cl.phs.apply(lambda p: p == (0,)), '只有整點', np.where(cl.phs.apply(lambda p: 0 in p), '整點與偏移都進場', '只有偏移'))
    net0 = A[A.ph == 0].groupby('cl').net.sum(); neto = A[A.ph != 0].groupby('cl').net.sum() / 3.0; cl['整點淨'] = net0.reindex(cl.index).fillna(0.0); cl['偏移平均淨'] = neto.reindex(cl.index).fillna(0.0); cl['增量'] = cl.整點淨 - cl.偏移平均淨
    cl['year'] = cl.t0.dt.year; cl['ym'] = cl.t0.dt.strftime('%Y-%m'); cl['per'] = np.where(cl.t0 < '2024-07-01', 'A 2021-01~2024-06', np.where(cl.t0 < '2026-05-01', 'B 研究期', 'C 2026-05~10'))
    R = {'事件群數': len(cl), '含同相位兩筆的群': int(cl.dup.sum()), '群內進場時間跨度中位數(小時)': float(((cl.t1 - cl.t0).dt.total_seconds() / 3600).median()), '增量合計': round(cl.增量.sum())}
    S = cl.groupby('類別').agg(群數=('n', 'size'), 整點淨=('整點淨', 'sum'), 偏移平均淨=('偏移平均淨', 'sum'), 增量=('增量', 'sum')).round(0); print(S.to_string()); R['類別'] = S.reset_index().to_dict('records')
    print(cl.pivot_table(index='類別', columns='per', values='增量', aggfunc='sum').round(0).to_string()); print(cl.pivot_table(index='類別', columns='year', values='增量', aggfunc='sum').round(0).to_string())
    R['類別×期間'] = cl.pivot_table(index='類別', columns='per', values='增量', aggfunc='sum').round(0).to_dict(); R['類別×年'] = cl.pivot_table(index='類別', columns='year', values='增量', aggfunc='sum').round(0).to_dict()
    # 集中度
    s = cl.增量.sort_values(ascending=False); R['移除最大5群後增量'] = round(s.iloc[5:].sum()); R['移除最大10群後增量'] = round(s.iloc[10:].sum()); R['最大10群合計'] = round(s.iloc[:10].sum()); R['最大10群'] = [dict(t0=str(cl.t0[i])[:16], 類別=cl.類別[i], 增量=round(v)) for i, v in s.iloc[:10].items()]
    mo = cl.groupby('ym').增量.sum().sort_values(ascending=False); R['增量為正的月數/有交易月數'] = [int((mo > 0).sum()), len(mo)]; R['最大5個月'] = {k: round(v) for k, v in mo.iloc[:5].items()}; R['移除最大5個月後增量'] = round(mo.iloc[5:].sum()); R['逐年增量'] = {int(k): round(v) for k, v in cl.groupby('year').增量.sum().items()}
    o = cl[cl.per.str.startswith('B') == False].增量.sort_values(ascending=False); R['研究期外增量'] = round(o.sum()); R['研究期外移除最大5群'] = round(o.iloc[5:].sum())
    # 只有整點：缺席原因
    rs = []
    for c, r in cl[cl.類別 == '只有整點'].iterrows():
        for ph in (15, 30, 45): rs.append(dict(cl=c, ph=ph, 原因=why_absent(P[ph], IND[ph], T[ph], r.t0), 整點淨=r.整點淨, per=r.per))
    W = pd.DataFrame(rs); wt = W.groupby('原因').agg(次數=('cl', 'size'), 對應整點淨額合計除以3=('整點淨', lambda x: round(x.sum() / 3))); print(wt.to_string()); R['只有整點_缺席原因'] = wt.reset_index().to_dict('records'); W.to_csv(os.path.join(OUT, 'absent_reasons.csv'), index=False)
    o0 = cl[cl.類別 == '只有整點']; R['只有整點_整點交易'] = dict(群數=len(o0), 勝率=round(float((A[(A.ph == 0) & A.cl.isin(o0.index)].net > 0).mean() * 100), 1), 淨=round(o0.整點淨.sum()))
    oo = cl[cl.類別 == '只有偏移']; x = A[A.cl.isin(oo.index)]; R['只有偏移_偏移交易'] = dict(群數=len(oo), 筆數=len(x), 勝率=round(float((x.net > 0).mean() * 100), 1), 淨合計=round(x.net.sum()), 每筆=round(x.net.mean(), 1))
    # 只有偏移：整點為何缺席
    rs = [dict(cl=c, 原因=why_absent(P[0], IND[0], T[0], r.t0)) for c, r in oo.iterrows()]; R['只有偏移_整點缺席原因'] = pd.DataFrame(rs).原因.value_counts().to_dict()
    # 共同進場：差異拆解
    pr = []
    for c in cl.index[cl.類別 == '整點與偏移都進場']:
        x = A[A.cl == c]; z = x[x.ph == 0].iloc[0]
        for _, y in x[x.ph != 0].drop_duplicates('ph').iterrows():
            cf = y.ntl * (y.exit_price / z.entry_price - 1) - y.cost - y.fund
            pr.append(dict(cl=c, ph=y.ph, t0=z.t_in, 進場時間差分=(y.t_in - z.t_in).total_seconds() / 60, 進場價差bp=(y.entry_price / z.entry_price - 1) * 1e4, 整點淨=z.net, 偏移淨=y.net, 進場價影響=y.net - cf, 出場影響=cf - z.net, 整點出場=z.exit_reason, 偏移出場=y.exit_reason, 同出場類型=z.exit_reason == y.exit_reason, 同出場時刻附近=abs((y.t_out - z.t_out).total_seconds()) <= 3600))
    Pp = pd.DataFrame(pr); Pp.to_csv(os.path.join(OUT, 'shared_pairs.csv'), index=False)
    R['共同進場配對'] = dict(配對數=len(Pp), 偏移較晚進場比例=round(float((Pp.進場時間差分 > 0).mean()), 3), 進場時間差中位數分=float(Pp.進場時間差分.median()), 進場價差平均bp=round(float(Pp.進場價差bp.mean()), 2), 整點淨平均=round(float(Pp.整點淨.mean()), 2), 偏移淨平均=round(float(Pp.偏移淨.mean()), 2), 進場價影響平均=round(float(Pp.進場價影響.mean()), 2), 出場影響平均=round(float(Pp.出場影響.mean()), 2), 同出場類型比例=round(float(Pp.同出場類型.mean()), 3))
    R['共同進場_依先後'] = Pp.assign(先後=np.where(Pp.進場時間差分 > 0, '偏移較晚', '偏移較早')).groupby('先後').agg(配對=('cl', 'size'), 進場價差bp=('進場價差bp', 'mean'), 整點淨=('整點淨', 'mean'), 偏移淨=('偏移淨', 'mean'), 進場價影響=('進場價影響', 'mean'), 出場影響=('出場影響', 'mean')).round(2).reset_index().to_dict('records')
    R['共同進場_出場類型對照'] = pd.crosstab(Pp.整點出場, Pp.偏移出場).to_dict()
    # 封鎖條件貢獻（多方單獨）
    blk = []
    for nm, ov in (('現行', {}), ('拿掉多方時段封鎖', dict(L_BLK_H=set())), ('拿掉多方星期封鎖', dict(L_BLK_D=set()))):
        v = {ph: L.book(P[ph], IND[ph], 'L', ov=ov, fund=fund) for ph in L.PH}; n = {ph: round(x.net.sum()) for ph, x in v.items()}; perB = {ph: round(x[(x.t_out >= '2024-07-01') & (x.t_out < '2026-05-01')].net.sum()) for ph, x in v.items()}
        blk.append(dict(設定=nm, **{f':{ph:02d}': n[ph] for ph in L.PH}, 整點增量=round(n[0] - np.mean([n[15], n[30], n[45]])), 研究期內整點增量=round(perB[0] - np.mean([perB[15], perB[30], perB[45]])), 筆數整點=len(v[0])))
    Bk = pd.DataFrame(blk); print(Bk.to_string(index=False)); R['封鎖條件貢獻'] = blk; Bk.to_csv(os.path.join(OUT, 'block_contribution.csv'), index=False)
    cl.assign(phs=cl.phs.astype(str)).to_csv(os.path.join(OUT, 'event_clusters.csv')); A.to_csv(os.path.join(OUT, 'long_trades_clustered.csv'), index=False)
    json.dump(R, open(os.path.join(OUT, 'events_summary.json'), 'w'), ensure_ascii=False, indent=1, default=str); print(json.dumps({k: v for k, v in R.items() if k not in ('類別', '類別×期間', '類別×年', '封鎖條件貢獻')}, ensure_ascii=False, indent=1, default=str))
