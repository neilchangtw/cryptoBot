"""V83 最小診斷：A′ CME 日線週末缺口（補查）、B VIX 衝擊後延遲去槓桿、C CFTC 槓桿基金淨空單週變動。
1 倍名目；T+1 分成交；成本來回 22.5bp；實際資金費。用法：python backtest/research/v83_d0.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v53_common as C, v83_feas as FE
D = FE.D; OUT = FE.OUT; W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); HC = 0.001125; fund = C.load_funding(); fi = fund.index.values; fc = np.concatenate([[0.0], fund.cumsum().values]); R = {}
def px(t, k=1): return float(U.px_at(pd.DatetimeIndex([t]), k)[0])
def fsum(a, b): return float(fc[np.searchsorted(fi, np.datetime64(b), side='right')] - fc[np.searchsorted(fi, np.datetime64(a), side='right')])
def loc(d, tz, hh): return (pd.Timestamp(d).tz_localize(tz) + pd.Timedelta(hours=hh)).tz_convert('UTC').tz_localize(None)
def trade(ev, h):
    """ev：[(決策時刻T, 方向s, 附註)]，依時間排序。同時只持一個部位。"""
    rows = []; busy = pd.Timestamp('1900-01-01'); skip = 0
    for T, s, note in ev:
        x = T + pd.Timedelta(hours=h)
        if T < W0 or x + pd.Timedelta(minutes=1) > W1: continue
        if T < busy: skip += 1; continue
        a = T + pd.Timedelta(minutes=1); b = x + pd.Timedelta(minutes=1); pe = px(T); po = px(x); g = s * (po / pe - 1); f = s * fsum(a, b); rows.append(dict(T=T, 出場=x, 方向='多' if s > 0 else '空', s=s, 進場價=pe, 出場價=po, gross=g, fund=f, net=g - 2 * HC - f, 附註=note)); busy = x
    return pd.DataFrame(rows), skip
def stat(e, skip=0):
    if not len(e): return dict(交易筆數=0, 淨合計_每千U=0.0, 多方淨=0.0, 空方淨=0.0, 逐年={})
    y = e.groupby(e.出場.dt.year).net.sum()
    return dict(交易筆數=len(e), 跳過_持倉中=skip, 平均毛bp=round(e.gross.mean() * 1e4, 1), 平均淨bp=round(e.net.mean() * 1e4, 1), 淨合計_每千U=round(e.net.sum() * 1000, 1), 勝率=round((e.net > 0).mean() * 100, 1), 多筆=int((e.s > 0).sum()), 空筆=int((e.s < 0).sum()), 多方淨=round(e[e.s > 0].net.sum() * 1000, 1), 空方淨=round(e[e.s < 0].net.sum() * 1000, 1), 逐年={int(k): round(float(y.get(k, 0.0)) * 1000, 1) for k in range(2021, 2027)})
def nw_t(y, X, lag):
    X = np.column_stack([np.ones(len(y)), X]); b = np.linalg.lstsq(X, y, rcond=None)[0]; u = y - X @ b; n = len(y); S = (X * u[:, None]).T @ (X * u[:, None])
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1); G = (X[L:] * u[L:, None]).T @ (X[:-L] * u[:-L, None]); S += w * (G + G.T)
    Xi = np.linalg.inv(X.T @ X); V = Xi @ S @ Xi; return b, b / np.sqrt(np.diag(V))
def shift_p(make, z, lo, hi, obs, n=1000, seed=83):
    rng = np.random.default_rng(seed); v = []
    for k in rng.integers(lo, hi + 1, n): v.append(make(pd.Series(np.roll(z.values, int(k)), index=z.index)))
    v = np.array(v); return round(float((v >= obs).mean()), 4), [round(float(np.quantile(v, q)), 1) for q in (.05, .5, .95)]
def kyears(d): return sum(v > 0 for v in d.values())

# ================= A′ =================
e, _ = FE.yahoo('yahoo_ethf_1d.json'); e['date'] = pd.to_datetime(e.ts.dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.date); e = e.dropna(subset=['open', 'close']).set_index('date')
def a_events(theta, include_roll=False):
    ev = []; info = dict(週末數=0, 缺日剔除=0, 換月剔除=0)
    for fri in pd.date_range('2021-02-12', '2026-10-30', freq='W-FRI'):
        mon = fri + pd.Timedelta(days=3); info['週末數'] += 1
        if fri not in e.index or mon not in e.index: info['缺日剔除'] += 1; continue
        roll = (fri + pd.Timedelta(days=7)).month != fri.month
        if roll and not include_roll: info['換月剔除'] += 1; continue
        g = np.log(e.open[mon] / e.close[fri])
        if abs(g) >= theta: ev.append((loc(fri + pd.Timedelta(days=2), 'America/Chicago', 17), -np.sign(g), f'缺口{g * 100:.2f}%'))
    return ev, info
A = {}; ev, info = a_events(0.02); mainA, sk = trade(ev, 24); mainA.to_csv(os.path.join(OUT, 'A_events_main.csv'), index=False); A['主要_θ2%_h24'] = stat(mainA, sk); A['事件篩選'] = info
for nm, th, h in (('鄰_θ1%_h24', .01, 24), ('鄰_θ3%_h24', .03, 24), ('鄰_θ2%_h12', .02, 12), ('鄰_θ2%_h48', .02, 48)): t, s_ = trade(a_events(th)[0], h); A[nm] = stat(t, s_)
t, s_ = trade(a_events(0.02, True)[0], 24); A['敏感度_含換月週末'] = stat(t, s_)
plc = pd.read_csv(os.path.join(C.ROOT, 'doc', 'research_results', 'v82', 'A_events_placebo.csv'), parse_dates=['重開UTC']); v82 = pd.read_csv(os.path.join(C.ROOT, 'doc', 'research_results', 'v82', 'A_events_main.csv'), parse_dates=['重開UTC'])
wk = pd.period_range('2021-02-08', '2026-11-01', freq='W-SAT'); sm = mainA.assign(week=mainA['T'].dt.to_period('W-SAT')).groupby('week').gross.agg(['sum', 'count']).reindex(wk).fillna(0.0).values; sp = plc.assign(week=plc.重開UTC.dt.to_period('W-SAT')).groupby('week').gross.agg(['sum', 'count']).reindex(wk).fillna(0.0).values
rng = np.random.default_rng(83); n = len(wk); k = int(np.ceil(n / 4)); st = rng.integers(0, n - 3, (5000, k)); idx = (st[:, :, None] + np.arange(4)[None, None, :]).reshape(5000, -1)[:, :n]; bs = np.array([sm[i][:, 0].sum() / max(sm[i][:, 1].sum(), 1) - sp[i][:, 0].sum() / max(sp[i][:, 1].sum(), 1) for i in idx])
A['主要減安慰劑_平均毛bp'] = round((mainA.gross.mean() - plc.gross.mean()) * 1e4, 1); A['主要減安慰劑_90區間bp'] = [round(float(np.quantile(bs, q)) * 1e4, 1) for q in (.05, .95)]
both = set(mainA['T']) & set(v82.重開UTC); A['與V82代理定義的事件重疊'] = dict(本定義事件=len(mainA), V82事件=len(v82), 相同週末=len(both))
m = A['主要_θ2%_h24']; nb = [A[x] for x in A if x.startswith('鄰')]
A['K'] = {'K1 事件≥40且平均淨>0': bool(m['交易筆數'] >= 40 and m['平均淨bp'] > 0), 'K2 六年至少五年為正': bool(kyears(m['逐年']) >= 5), 'K3 兩邊各自為正': bool(m['多方淨'] > 0 and m['空方淨'] > 0), 'K4 高於安慰劑且下界>0': bool(A['主要減安慰劑_90區間bp'][0] > 0), 'K5 鄰域至少3個為正': bool(sum(x['淨合計_每千U'] > 0 for x in nb) >= 3)}
A['判定'] = 'PASS' if all(A['K'].values()) else 'REJECTED'; R['A′'] = A; print(json.dumps(A, ensure_ascii=False, indent=1, default=str))

# ================= B =================
v, _ = FE.yahoo('yahoo_vix_1d.json'); v['date'] = pd.to_datetime(v.ts.dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.date); v = v.dropna(subset=['close']).set_index('date'); d = np.log(v.close / v.close.shift(1)); zB = d / d.rolling(60).std().shift(1); zB = zB.dropna()
def b_events(z, thr): return [(loc(dt, 'America/New_York', 17), (-1.0 if x >= thr else 1.0), f'z{x:.2f}') for dt, x in z.items() if abs(x) >= thr]
B = {}; mainB, sk = trade(b_events(zB, 1.5), 48); mainB.to_csv(os.path.join(OUT, 'B_events_main.csv'), index=False); B['主要_|z|≥1.5_h48'] = stat(mainB, sk)
for nm, th, h in (('鄰_|z|≥1.0', 1.0, 48), ('鄰_|z|≥2.0', 2.0, 48), ('鄰_h24', 1.5, 24), ('鄰_h72', 1.5, 72)): t, s_ = trade(b_events(zB, th), h); B[nm] = stat(t, s_)
rows = []
for dt, x in zB.items():
    T = loc(dt, 'America/New_York', 17)
    if T < W0 + pd.Timedelta(days=6) or T + pd.Timedelta(hours=48, minutes=1) > W1: continue
    p = px(T); rows.append(dict(fwd=px(T + pd.Timedelta(hours=48)) / p - 1, s=(-1.0 if x >= 1.5 else (1.0 if x <= -1.5 else 0.0)), r1=p / px(T - pd.Timedelta(hours=24)) - 1, r5=p / px(T - pd.Timedelta(days=5)) - 1))
X = pd.DataFrame(rows); b, tv = nw_t(X.fwd.values, X[['s', 'r1', 'r5']].values, 5); B['迴歸_訊號係數bp'] = round(float(b[1]) * 1e4, 1); B['迴歸_訊號t'] = round(float(tv[1]), 2); B['迴歸_ETH當日報酬t'] = round(float(tv[2]), 2); B['迴歸樣本'] = len(X)
B['循環平移p'], B['循環平移淨額分位_5_50_95'] = shift_p(lambda z: trade(b_events(z, 1.5), 48)[0].net.sum() * 1000, zB, 20, 400, B['主要_|z|≥1.5_h48']['淨合計_每千U'])
m = B['主要_|z|≥1.5_h48']; nb = [B[x] for x in B if x.startswith('鄰')]
B['K'] = {'K1 交易≥40且淨>0': bool(m['交易筆數'] >= 40 and m['淨合計_每千U'] > 0), 'K2 六年至少五年為正': bool(kyears(m['逐年']) >= 5), 'K3 多空各自為正': bool(m['多方淨'] > 0 and m['空方淨'] > 0), 'K4 迴歸t>1.65且平移p<0.05': bool(B['迴歸_訊號係數bp'] > 0 and B['迴歸_訊號t'] > 1.65 and B['循環平移p'] < 0.05), 'K5 鄰域至少3個為正': bool(sum(x['淨合計_每千U'] > 0 for x in nb) >= 3)}
B['判定'] = 'PASS' if all(B['K'].values()) else 'REJECTED'; R['B'] = B; print(json.dumps(B, ensure_ascii=False, indent=1, default=str))

# ================= C =================
c = pd.read_csv(os.path.join(D, 'cot_ether_146021.csv'), parse_dates=['rd']).set_index('rd'); ns = (c.Lev_Money_Positions_Short_All - c.Lev_Money_Positions_Long_All) / c.Open_Interest_All; dn = ns.diff()
def zc(win): return (dn / dn.rolling(win).std().shift(1)).dropna()
def excl(rd): return (pd.Timestamp('2023-01-31') <= rd <= pd.Timestamp('2023-03-21')) or (pd.Timestamp('2025-09-30') <= rd <= pd.Timestamp('2025-11-18'))
def c_events(z, thr): return [(rd + pd.Timedelta(days=7), (1.0 if x >= thr else -1.0), f'z{x:.2f}') for rd, x in z.items() if abs(x) >= thr and not excl(rd)]
Cc = {}; z26 = zc(26); mainC, sk = trade(c_events(z26, 1.0), 168); mainC.to_csv(os.path.join(OUT, 'C_events_main.csv'), index=False); Cc['主要_|z|≥1_26週_持有7天'] = stat(mainC, sk)
for nm, zz, th, h in (('鄰_|z|≥0.5', z26, .5, 168), ('鄰_|z|≥1.5', z26, 1.5, 168), ('鄰_52週', zc(52), 1.0, 168), ('鄰_持有14天', z26, 1.0, 336)): t, s_ = trade(c_events(zz, th), h); Cc[nm] = stat(t, s_)
rows = []
for rd, x in z26.items():
    T = rd + pd.Timedelta(days=7)
    if excl(rd) or T < W0 + pd.Timedelta(days=8) or T + pd.Timedelta(hours=168, minutes=1) > W1: continue
    p = px(T); rows.append(dict(fwd=px(T + pd.Timedelta(days=7)) / p - 1, s=(1.0 if x >= 1 else (-1.0 if x <= -1 else 0.0)), r7=p / px(T - pd.Timedelta(days=7)) - 1))
X = pd.DataFrame(rows); b, tv = nw_t(X.fwd.values, X[['s', 'r7']].values, 4); Cc['迴歸_訊號係數bp'] = round(float(b[1]) * 1e4, 1); Cc['迴歸_訊號t'] = round(float(tv[1]), 2); Cc['迴歸樣本'] = len(X)
Cc['循環平移p'], Cc['循環平移淨額分位_5_50_95'] = shift_p(lambda z: trade(c_events(z, 1.0), 168)[0].net.sum() * 1000, z26, 8, 200, Cc['主要_|z|≥1_26週_持有7天']['淨合計_每千U'])
Cc['資料'] = dict(可用週數=len(z26), 剔除_延後發布=int(sum(excl(r) for r in z26.index)), 淨空單占比_逐年平均={int(y): round(float(x), 3) for y, x in ns.groupby(ns.index.year).mean().items()})
m = Cc['主要_|z|≥1_26週_持有7天']; nb = [Cc[x] for x in Cc if x.startswith('鄰')]
Cc['K'] = {'K1 交易≥40且淨>0': bool(m['交易筆數'] >= 40 and m['淨合計_每千U'] > 0), 'K2 六年至少五年為正': bool(kyears(m['逐年']) >= 5), 'K3 多空各自為正': bool(m['多方淨'] > 0 and m['空方淨'] > 0), 'K4 迴歸t>1.65且平移p<0.05': bool(Cc['迴歸_訊號係數bp'] > 0 and Cc['迴歸_訊號t'] > 1.65 and Cc['循環平移p'] < 0.05), 'K5 鄰域至少3個為正': bool(sum(x['淨合計_每千U'] > 0 for x in nb) >= 3)}
Cc['判定'] = 'PASS' if all(Cc['K'].values()) else 'REJECTED'; R['C'] = Cc; print(json.dumps(Cc, ensure_ascii=False, indent=1, default=str))
json.dump(R, open(os.path.join(OUT, 'd0_results.json'), 'w'), ensure_ascii=False, indent=1, default=str)
