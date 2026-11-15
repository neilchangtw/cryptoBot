"""V82 最小診斷：A CME 週末缺口回補、B Coinbase 成交量占比、C 已實現偏度。1 倍名目，bp 與每 1,000U 名目的 U。
成交＝決策後 1 分鐘（T+1 分收盤價）；成本來回 22.5bp；資金費用實際結算費率。用法：python backtest/research/v82_d0.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v53_common as C
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v82'); W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); HC = 0.001125; M = U.m1(); fund = C.load_funding(); fcs = fund.cumsum(); fi = fund.index.values; fc = np.concatenate([[0.0], fcs.values]); R = {}
def px(t, k=1): return U.px_at(pd.DatetimeIndex(t), k)
def fsum(a, b): return fc[np.searchsorted(fi, pd.DatetimeIndex(b).values, side='right')] - fc[np.searchsorted(fi, pd.DatetimeIndex(a).values, side='right')]
def yr(t, net): s = pd.Series(net, index=pd.DatetimeIndex(t).year).groupby(level=0).sum(); return {int(y): round(float(s.get(y, 0.0)) * 1000, 1) for y in range(2021, 2027)}
def blockboot(stat, n, block=4, B=5000, seed=82):
    rng = np.random.default_rng(seed); k = int(np.ceil(n / block)); st = rng.integers(0, n - block + 1, (B, k)); idx = (st[:, :, None] + np.arange(block)[None, None, :]).reshape(B, -1)[:, :n]; return np.array([stat(i) for i in idx])

# ================= A：CME 週末缺口 =================
def cme_events(theta, h, weekday=4):
    """weekday=4（週五）為主事件；0~3 為安慰劑（同鐘點、同 49 小時跨度）。"""
    days = pd.date_range('2021-02-08', '2026-10-31', freq='D'); days = days[days.weekday == weekday]; rows = []
    for d in days:
        c = (pd.Timestamp(d.date()).tz_localize('America/Chicago') + pd.Timedelta(hours=16)).tz_convert('UTC').tz_localize(None)
        o = (pd.Timestamp((d + pd.Timedelta(days=2)).date()).tz_localize('America/Chicago') + pd.Timedelta(hours=17)).tz_convert('UTC').tz_localize(None); x = o + pd.Timedelta(hours=h)
        if c < W0 or x + pd.Timedelta(minutes=1) > W1: continue
        pc = px([c], 0)[0]; po = px([o], 0)[0]; g = np.log(po / pc)
        if abs(g) < theta: continue
        pe = px([o], 1)[0]; pxx = px([x], 1)[0]; s = -np.sign(g); gross = s * (pxx / pe - 1); f = s * fsum([o + pd.Timedelta(minutes=1)], [x + pd.Timedelta(minutes=1)])[0]
        rows.append(dict(週五收盤UTC=c, 重開UTC=o, 出場UTC=x, 缺口pct=round(g * 100, 3), 方向='空' if s < 0 else '多', 進場價=pe, 出場價=pxx, gross=gross, fund=f, net=gross - 2 * HC - f, week=o.to_period('W-SAT')))
    return pd.DataFrame(rows)
def a_stat(e): return dict(事件數=len(e), 平均毛bp=round(e.gross.mean() * 1e4, 1), 平均淨bp=round(e.net.mean() * 1e4, 1), 淨合計_每千U=round(e.net.sum() * 1000, 1), 勝率=round((e.net > 0).mean() * 100, 1), 缺口向上做空_淨=round(e[e.方向 == '空'].net.sum() * 1000, 1), 缺口向下做多_淨=round(e[e.方向 == '多'].net.sum() * 1000, 1), 空筆=int((e.方向 == '空').sum()), 多筆=int((e.方向 == '多').sum()), 逐年=yr(e.出場UTC, e.net.values)) if len(e) else dict(事件數=0)
A = {}; main = cme_events(0.02, 24); main.to_csv(os.path.join(OUT, 'A_events_main.csv'), index=False); A['主要_θ2%_h24'] = a_stat(main)
for nm, th, h in (('鄰_θ1%_h24', .01, 24), ('鄰_θ3%_h24', .03, 24), ('鄰_θ2%_h12', .02, 12), ('鄰_θ2%_h48', .02, 48)): A[nm] = a_stat(cme_events(th, h))
plc = pd.concat([cme_events(0.02, 24, w) for w in range(4)]); plc.to_csv(os.path.join(OUT, 'A_events_placebo.csv'), index=False); A['安慰劑_週一至週四'] = a_stat(plc)
wk = pd.period_range('2021-02-08', '2026-11-01', freq='W-SAT'); sm = main.groupby('week').gross.agg(['sum', 'count']).reindex(wk).fillna(0.0).values; sp = plc.groupby('week').gross.agg(['sum', 'count']).reindex(wk).fillna(0.0).values
def dstat(i): a = sm[i]; b = sp[i]; return (a[:, 0].sum() / max(a[:, 1].sum(), 1)) - (b[:, 0].sum() / max(b[:, 1].sum(), 1))
bs = blockboot(dstat, len(wk)); obs = main.gross.mean() - plc.gross.mean(); A['主要減安慰劑_平均毛bp'] = round(obs * 1e4, 1); A['主要減安慰劑_90區間bp'] = [round(float(np.quantile(bs, .05)) * 1e4, 1), round(float(np.quantile(bs, .95)) * 1e4, 1)]
m = A['主要_θ2%_h24']; nb = [A[k] for k in A if k.startswith('鄰')]
A['K'] = {'A-K1 事件≥40且平均淨>0': bool(m['事件數'] >= 40 and m['平均淨bp'] > 0), 'A-K2 六年至少五年為正': bool(sum(v > 0 for v in m['逐年'].values()) >= 5), 'A-K3 兩邊各自為正': bool(m['缺口向上做空_淨'] > 0 and m['缺口向下做多_淨'] > 0), 'A-K4 高於安慰劑且區間下界>0': bool(A['主要減安慰劑_90區間bp'][0] > 0), 'A-K5 鄰域至少3個為正': bool(sum(x.get('淨合計_每千U', -1) > 0 for x in nb) >= 3)}
A['判定'] = 'PASS' if all(A['K'].values()) else 'REJECTED'; R['A'] = A; print(json.dumps(A, ensure_ascii=False, indent=1, default=str))

# ================= 日頻共用 =================
days = pd.date_range('2020-09-02', '2026-10-31', freq='D'); p0 = pd.Series(px(days, 0), index=days); p1 = pd.Series(px(days, 1), index=days)   # 00:00 與 00:01 價格
ret = (p1.shift(-1) / p1 - 1)   # 持有日 d（00:01 → 次日 00:01）的報酬
fday = pd.Series(fsum(days + pd.Timedelta(minutes=1), days + pd.Timedelta(days=1, minutes=1)), index=days)
def daily_eval(pos):
    """pos：index＝持有日，值 ±1／0。回傳逐日淨額（1 倍名目）、統計。"""
    pos = pos.reindex(days).fillna(0.0); pos = pos[(pos.index >= W0) & (pos.index + pd.Timedelta(days=1, minutes=1) <= W1)]; r = ret.reindex(pos.index); f = fday.reindex(pos.index); prev = pos.shift(1).fillna(0.0); nxt = pos.shift(-1).fillna(0.0)
    cin = HC * ((pos != 0) & (pos != prev)); cout = HC * ((pos != 0) & (pos != nxt)); net = pos * r - cin - cout - pos * f; g = pos * r; h = pos != 0; ntr = int(((pos != 0) & (pos != prev)).sum())
    return net, dict(持倉日數=int(h.sum()), 交易筆數=ntr, 多日=int((pos > 0).sum()), 空日=int((pos < 0).sum()), 平均毛bp_每持倉日=round(float(g[h].mean()) * 1e4, 1) if h.any() else 0.0, 淨合計_每千U=round(float(net.sum()) * 1000, 1), 多方日淨=round(float(net[pos > 0].sum()) * 1000, 1), 空方日淨=round(float(net[pos < 0].sum()) * 1000, 1), 成本合計=round(float((cin + cout).sum()) * 1000, 1), 資金費合計=round(float((pos * f).sum()) * 1000, 1), 逐年=yr(net.index, net.values))
def pct_prior(x, win=180):
    v = x.values; out = np.full(len(v), np.nan)
    for i in range(win, len(v)):
        w = v[i - win:i]; w = w[~np.isnan(w)]
        if len(w) >= win // 2 and not np.isnan(v[i]): out[i] = (w < v[i]).mean()
    return pd.Series(out, index=x.index)
def shift_p(make_pos, series, obs, n=1000, seed=82):
    rng = np.random.default_rng(seed); L = len(series); sh = rng.integers(30, 336, n); v = []
    for k in sh: s2 = pd.Series(np.roll(series.values, int(k)), index=series.index); v.append(daily_eval(make_pos(s2))[0].sum() * 1000)
    v = np.array(v); return round(float((v >= obs).mean()), 4), [round(float(np.quantile(v, .05)), 1), round(float(np.quantile(v, .5)), 1), round(float(np.quantile(v, .95)), 1)]

# ================= B：Coinbase 成交量占比 =================
cb = pd.read_csv(os.path.join(C.ROOT, 'data', 'coinbase_ethusd_1d.csv'), parse_dates=['date']).set_index('date').volume
sp_ = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_spot_1h.csv'), parse_dates=['open_time_utc']); bn = sp_.groupby(sp_.open_time_utc.dt.floor('D')).volume.sum()
vol = pd.DataFrame(dict(cb=cb, bn=bn)).dropna(); share = (vol.cb.rolling(3).sum() / (vol.cb.rolling(3).sum() + vol.bn.rolling(3).sum())); pshare = pct_prior(share)   # index＝資料日 t（日結束後可得）
def mom(n): return np.sign(np.log(p0 / p0.shift(n)))   # index＝決策時刻 00:00 的那一天（持有日）
def b_pos(ps, thr=0.8, n=7):
    cond = (ps >= thr).astype(float); cond.index = cond.index + pd.Timedelta(days=1)   # 資料日 t → 持有日 t+1
    return (cond.reindex(days).fillna(0.0) * mom(n).reindex(days).fillna(0.0))
B = {}; netB, B['主要_p≥0.8_動能7日'] = daily_eval(b_pos(pshare)); netB.to_csv(os.path.join(OUT, 'B_daily_net_main.csv'))
for nm, th, n in (('鄰_p≥0.7', .7, 7), ('鄰_p≥0.9', .9, 7), ('鄰_動能3日', .8, 3), ('鄰_動能14日', .8, 14)): B[nm] = daily_eval(b_pos(pshare, th, n))[1]
netC0, B['對照甲_不看占比每日依7日方向'] = daily_eval(mom(7).reindex(days).fillna(0.0))
pm = b_pos(pshare).reindex(days); pm = pm[(pm.index >= W0)]; gm = (pm * ret.reindex(pm.index)); ga = (mom(7).reindex(pm.index) * ret.reindex(pm.index))
B['有條件持倉日平均毛bp'] = round(float(gm[pm != 0].mean()) * 1e4, 1); B['對照甲每日平均毛bp'] = round(float(ga.mean()) * 1e4, 1)
B['循環平移p'], B['循環平移淨額分位_5_50_95'] = shift_p(lambda s: b_pos(s), pshare, B['主要_p≥0.8_動能7日']['淨合計_每千U'])
B['占比描述'] = dict(起=str(share.dropna().index[0])[:10], 平均=round(float(share.mean()), 3), 逐年平均={int(y): round(float(v), 3) for y, v in share.groupby(share.index.year).mean().items()})
m = B['主要_p≥0.8_動能7日']; nb = [B[k] for k in B if k.startswith('鄰')]
B['K'] = {'B-K1 持倉日≥150且淨>0': bool(m['持倉日數'] >= 150 and m['淨合計_每千U'] > 0), 'B-K2 六年至少五年為正': bool(sum(v > 0 for v in m['逐年'].values()) >= 5), 'B-K3 多空日各自為正': bool(m['多方日淨'] > 0 and m['空方日淨'] > 0), 'B-K4 高於對照甲且平移p<0.05': bool(B['有條件持倉日平均毛bp'] > B['對照甲每日平均毛bp'] and B['循環平移p'] < 0.05), 'B-K5 鄰域至少3個為正': bool(sum(x['淨合計_每千U'] > 0 for x in nb) >= 3)}
B['判定'] = 'PASS' if all(B['K'].values()) else 'REJECTED'; R['B'] = B; print(json.dumps(B, ensure_ascii=False, indent=1, default=str))

# ================= C：已實現偏度 =================
lr = np.diff(np.log(M['close'])); t1 = pd.to_datetime(M['ts'][1:], unit='ms'); dd = t1.floor('D'); g = pd.DataFrame(dict(d=dd, r=lr)); g['r2'] = g.r ** 2; g['r3'] = g.r ** 3; a = g.groupby('d').agg(n=('r', 'size'), s2=('r2', 'sum'), s3=('r3', 'sum')); a = a[a.n >= 1400]
rs = np.sqrt(a.n) * a.s3 / a.s2 ** 1.5; rs.to_csv(os.path.join(OUT, 'C_realized_skew_daily.csv'))
def c_pos(series, hi=0.8, lo=0.2, hold=1):
    q = pct_prior(series); sig = pd.Series(np.where(q >= hi, -1.0, np.where(q <= lo, 1.0, 0.0)), index=series.index); sig.index = sig.index + pd.Timedelta(days=1); sig = sig.reindex(days).fillna(0.0)
    if hold == 2: sig = sig.where(sig != 0, sig.shift(1).fillna(0.0))
    return sig
Cc = {}; netC, Cc['主要_0.8／0.2_持有1天'] = daily_eval(c_pos(rs)); netC.to_csv(os.path.join(OUT, 'C_daily_net_main.csv'))
for nm, kw, ser in (('鄰_0.7／0.3', dict(hi=.7, lo=.3), rs), ('鄰_0.9／0.1', dict(hi=.9, lo=.1), rs), ('鄰_近3日平均', {}, rs.rolling(3).mean()), ('鄰_持有2天', dict(hold=2), rs)): Cc[nm] = daily_eval(c_pos(ser, **kw))[1]
Cc['循環平移p'], Cc['循環平移淨額分位_5_50_95'] = shift_p(lambda s: c_pos(s), rs, Cc['主要_0.8／0.2_持有1天']['淨合計_每千U'])
q = pct_prior(rs); dret = (p0.shift(-1) / p0 - 1); x = pd.DataFrame(dict(q=q, r0=dret.reindex(q.index), r1=ret.shift(-1).reindex(q.index))).dropna(); x = x[x.index >= W0 - pd.Timedelta(days=1)]; x['ter'] = pd.qcut(x.r0, 3, labels=['低', '中', '高']); ter = {}
for k, gg in x.groupby('ter'): ter[str(k)] = dict(低偏度日數=int((gg.q <= .2).sum()), 高偏度日數=int((gg.q >= .8).sum()), 低減高_次日bp=round(float((gg[gg.q <= .2].r1.mean() - gg[gg.q >= .8].r1.mean()) * 1e4), 1))
Cc['前一日報酬三分位內_低偏度減高偏度'] = ter; Cc['偏度與當日報酬相關'] = round(float(x.q.corr(x.r0)), 3)
m = Cc['主要_0.8／0.2_持有1天']; nb = [Cc[k] for k in Cc if k.startswith('鄰')]
Cc['K'] = {'C-K1 交易≥150且淨>0': bool(m['交易筆數'] >= 150 and m['淨合計_每千U'] > 0), 'C-K2 六年至少五年為正': bool(sum(v > 0 for v in m['逐年'].values()) >= 5), 'C-K3 多空各自為正': bool(m['多方日淨'] > 0 and m['空方日淨'] > 0), 'C-K4 平移p<0.05且三分位至少兩個為正': bool(Cc['循環平移p'] < 0.05 and sum(v['低減高_次日bp'] > 0 for v in ter.values()) >= 2), 'C-K5 鄰域至少3個為正': bool(sum(x_['淨合計_每千U'] > 0 for x_ in nb) >= 3)}
Cc['判定'] = 'PASS' if all(Cc['K'].values()) else 'REJECTED'; R['C'] = Cc; print(json.dumps(Cc, ensure_ascii=False, indent=1, default=str))
json.dump(R, open(os.path.join(OUT, 'd0_results.json'), 'w'), ensure_ascii=False, indent=1, default=str)
