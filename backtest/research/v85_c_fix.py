"""V85：V83-C 的資料可見時間更正與完整重跑（工程稽核，不是新獲利假說；不調參、不救回任何窗口）。
唯一可用性條件：該期報告的公開時間 ≤ 策略決策時刻。
公開時間依證據分級：官方特別公告（實際補發日）／2026 官方排程（排程，非實際發布證據）／推定規則（無實際紀錄，不確定）。
規則、門檻、持有期、成本、成交全部沿用 V83 預登記，只改「哪些週可用」。
用法：python backtest/research/v85_c_fix.py"""
import os, sys, re, json, html, math
import numpy as np, pandas as pd
from statistics import NormalDist
from pandas.tseries.holiday import USFederalHolidayCalendar
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v53_common as C
ROOT = C.ROOT; OUT = os.path.join(ROOT, 'doc', 'research_results', 'v85'); os.makedirs(OUT, exist_ok=True)
V83 = os.path.join(ROOT, 'doc', 'research_results', 'v83'); RAW = os.path.join(ROOT, 'data', 'v84')
W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); HC = 0.001125; NOW = pd.Timestamp('2026-11-15')
fund = C.load_funding(); fi = fund.index.values; fc = np.concatenate([[0.0], fund.cumsum().values])
def px(t, k=1): return float(U.px_at(pd.DatetimeIndex([t]), k)[0])
def fsum(a, b): return float(fc[np.searchsorted(fi, np.datetime64(b), side='right')] - fc[np.searchsorted(fi, np.datetime64(a), side='right')])
h = pd.read_csv(os.path.join(ROOT, 'data', 'ETHUSDT_1h_2020_2026.csv'), parse_dates=['datetime']); P = pd.Series(h.close.values, index=h.datetime - pd.Timedelta(hours=7))
R = {}

# ---------------- 1. 每期報告的公開時間與證據 ----------------
def clean(f):
    t = open(os.path.join(RAW, f), encoding='utf-8', errors='replace').read(); t = re.sub(r'(?s)<(script|style).*?</\1>', '', t)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
sa = clean('HistoricalSpecialAnnouncements.html')
blk = sa[sa.find('December 9, 2025:'):sa.find('November 18, 2025:')]
actual = {pd.Timestamp(a): (pd.Timestamp(c_), '官方特別公告 2025-12-09 補發表') for a, b, c_ in re.findall(r'(\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4})', blk)}
actual[pd.Timestamp('2025-01-07')] = (pd.Timestamp('2025-01-13'), '官方特別公告 2025-01-07（國喪日改週一）')
# 2023 ION：公告逐日寫明「今日發布原定某日的報告」；原定發布日的持倉日＝該週五前 3 天
for pub, orig in (('2023-02-24', '2023-02-03'), ('2023-03-03', '2023-02-10'), ('2023-03-08', '2023-02-17'), ('2023-03-10', '2023-02-24'), ('2023-03-14', '2023-03-03'), ('2023-03-16', '2023-03-10'), ('2023-03-21', '2023-03-17')):
    actual[pd.Timestamp(orig) - pd.Timedelta(days=3)] = (pd.Timestamp(pub), '官方特別公告 2023 ION 事件逐日公告')
st = clean('index.htm.html'); seg = st[st.find('2026 Release Schedule'):st.find('*Delayed release date')]
mon = {m: i + 1 for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'])}
sched26 = [pd.Timestamp(2026, mon[m], int(d[:2])) for m, body in re.findall(r'(January|February|March|April|May|June|July|August|September|October|November|December) ([0-9\* ]+?)(?= [A-Z]|$)', seg) for d in re.findall(r'\d{2}\*?', body)]
hol = set(USFederalHolidayCalendar().holidays('2020-12-01', '2027-01-31'))
def rule_release(rd):
    fri = rd + pd.Timedelta(days=(4 - rd.weekday()) % 7 or 7); rel = fri
    # 只有週三～週五的假日會讓發布順延；週二假日時持倉日改為週一，發布仍在週五（官方 2025-11-10 持倉日原定 11-14 發布可證）
    if any((rd + pd.Timedelta(days=i)) in hol and (rd + pd.Timedelta(days=i)).weekday() in (2, 3, 4) for i in range(1, (fri - rd).days + 1)):
        rel = fri + pd.Timedelta(days=3)
        while rel in hol or rel.weekday() >= 5: rel += pd.Timedelta(days=1)
    return rel
def et_utc(d): return (d + pd.Timedelta(hours=15, minutes=30)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)
V83EX = lambda rd: (pd.Timestamp('2023-01-31') <= rd <= pd.Timestamp('2023-03-21')) or (pd.Timestamp('2025-09-30') <= rd <= pd.Timestamp('2025-11-18'))

c = pd.read_csv(os.path.join(ROOT, 'data', 'v83', 'cot_ether_146021.csv'), parse_dates=['rd']).set_index('rd')
ns = (c.Lev_Money_Positions_Short_All - c.Lev_Money_Positions_Long_All) / c.Open_Interest_All; dn = ns.diff(); z = (dn / dn.rolling(26).std().shift(1)).dropna()
rows = []
for rd in c.index:
    dec = rd + pd.Timedelta(days=7); rr = rule_release(rd)
    if rd in actual: rel, src = actual[rd]; kind = '官方實際（特別公告；時刻假設 15:30 美東）'
    elif rr in sched26 and rr.year == 2026: rel, src = rr, 'CFTC 2026 發布排程'; kind = '排程（已過日期，非實際發布證據）' if rr <= NOW else '排程（未來日期）'
    else: rel, src = rr, '推定規則（週五 15:30 美東，聯邦假日順延）'; kind = '推定（無實際發布紀錄，不確定）'
    ru = et_utc(rel); avail = ru <= dec
    rows.append(dict(持倉日=rd.date(), 推定或預定發布=rr.date(), 採用發布日=rel.date(), 採用發布_UTC=ru, 證據等級=kind, 證據來源=src, 決策_UTC=dec, 成交_UTC=dec + pd.Timedelta(minutes=1),
                     發布到決策_小時=round((dec - ru).total_seconds() / 3600, 1), 可用=bool(avail), V83預登記剔除=bool(V83EX(rd)), 假日順延週=bool(rr.weekday() != 4),
                     z=(round(float(z[rd]), 3) if rd in z.index else None), 是訊號=bool(rd in z.index and abs(z[rd]) >= 1)))
av = pd.DataFrame(rows)
chk = [(d, rule_release(d - pd.Timedelta(days=(d.weekday() - 1) % 7 or 7) if d.weekday() != 0 else d - pd.Timedelta(days=6)) == d) for d in sched26]
R['規則對2026排程'] = dict(次數=len(chk), 一致=sum(x for _, x in chk), 說明='這是排程核對，不是實際發布證據；未來日期尚未發生')
orig = {pd.Timestamp(a): pd.Timestamp(b) for a, b, c_ in re.findall(r'(\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4})', blk)}
R['規則對2025原定發布日'] = dict(次數=len(orig), 一致=int(sum(rule_release(k) == v for k, v in orig.items())), 不一致=[(str(k.date()), str(v.date()), str(rule_release(k).date())) for k, v in orig.items() if rule_release(k) != v])
av.to_csv(os.path.join(OUT, 'C_availability_all_weeks.csv'), index=False)
AVAIL = {pd.Timestamp(r.持倉日): r.可用 for r in av.itertuples()}; HOLI = {pd.Timestamp(r.持倉日): r.假日順延週 for r in av.itertuples()}
R['可用性'] = dict(週數=len(av), 證據等級={k: int(v) for k, v in av.證據等級.value_counts().items()}, 不可用週=av.loc[~av.可用, '持倉日'].astype(str).tolist(),
                 不可用且未被V83剔除=av.loc[~av.可用 & ~av.V83預登記剔除, '持倉日'].astype(str).tolist(),
                 V83剔除但依官方紀錄可用=av.loc[av.可用 & av.V83預登記剔除, '持倉日'].astype(str).tolist())

# ---------------- 2. 排除規則：V83 預登記剔除 ∪ 依證據不可用 ----------------
EX_OLD = V83EX
EX_NEW = lambda rd: V83EX(rd) or not AVAIL.get(rd, False)
EX_SENS = lambda rd: EX_NEW(rd) or HOLI.get(rd, False)   # 敏感度：再去掉只靠推定的假日順延週
def events(zz, ex, thr=1.0): return [(rd + pd.Timedelta(days=7), (1.0 if x >= thr else -1.0), rd) for rd, x in zz.items() if abs(x) >= thr and not ex(rd)]
def trade_d0(ev, hh=168):  # 與 v83_d0.trade 相同：T+1 分成交、扣資金費、22.5bp
    out = []; busy = pd.Timestamp('1900-01-01')
    for T, s, rd in ev:
        x = T + pd.Timedelta(hours=hh)
        if T < W0 or x + pd.Timedelta(minutes=1) > W1 or T < busy: continue
        pe = px(T); po = px(x); g = s * (po / pe - 1); f = s * fsum(T + pd.Timedelta(minutes=1), x + pd.Timedelta(minutes=1))
        out.append(dict(rd=rd, T=T, s=s, gross=g, fund=f, net=g - 2 * HC - f)); busy = x
    return pd.DataFrame(out)
def trade_ind(ev, hh=168):  # 與 v83_c_audit 獨立版相同：T 收盤（1h K）、不扣資金費
    out = []
    for T, s, rd in ev:
        X = T + pd.Timedelta(hours=hh)
        if T < W0 or T not in P.index or X not in P.index or X > W1: continue
        g = s * (P[X] / P[T] - 1); out.append(dict(rd=rd, T=T, s=s, gross=g, net=g - 0.00225))
    return pd.DataFrame(out)
def stat(e):
    y = e.groupby(e['T'].dt.year).net.sum()
    return dict(筆數=len(e), 淨合計_每千U=round(e.net.sum() * 1000, 1), 平均淨bp=round(e.net.mean() * 1e4, 1), 標準差bp=round(e.net.std(ddof=1) * 1e4, 1), 中位數bp=round(float(e.net.median()) * 1e4, 1),
                勝率=round((e.net > 0).mean() * 100, 1), 多方淨=round(e[e.s > 0].net.sum() * 1000, 1), 空方淨=round(e[e.s < 0].net.sum() * 1000, 1), 逐年={int(k): round(float(y.get(k, 0.0)) * 1000, 1) for k in range(2021, 2027)},
                正年數=int(sum(y.get(k, 0.0) > 0 for k in range(2021, 2027))), 偏度=round(float(e.net.skew()), 2))
res = {}
for nm, ex in (('V83原始', EX_OLD), ('更正', EX_NEW), ('敏感度_再去假日週', EX_SENS)):
    d0 = trade_d0(events(z, ex)); ind = trade_ind(events(z, ex)); res[nm] = (d0, ind)
    R[nm] = dict(D0口徑=stat(d0), 獨立口徑=stat(ind))
old_d0, old_ind = res['V83原始']; new_d0, new_ind = res['更正']
new_d0.assign(T=new_d0['T'].astype(str)).to_csv(os.path.join(OUT, 'C_trades_corrected_d0.csv'), index=False)
new_ind.to_csv(os.path.join(OUT, 'C_trades_corrected_independent.csv'), index=False)
# 交易集合差異
drop = sorted(set(old_d0.rd) - set(new_d0.rd)); add = sorted(set(new_d0.rd) - set(old_d0.rd))
R['交易差異'] = dict(移除=[dict(持倉日=str(r.date()), 方向='多' if old_d0.set_index('rd').s[r] > 0 else '空', D0淨bp=round(old_d0.set_index('rd').net[r] * 1e4, 1), 獨立淨bp=round(old_ind.set_index('rd').net[r] * 1e4, 1)) for r in drop], 新增=[str(r.date()) for r in add],
                  其餘逐筆是否相同=bool(np.allclose(old_d0.set_index('rd').net.drop(drop).values, new_d0.set_index('rd').net.values)))
# 對帳
j = new_d0.merge(new_ind, on='rd', suffixes=('_d0', '_ind'))
R['兩口徑對帳_更正後'] = dict(D0淨=round(new_d0.net.sum() * 1000, 1), 獨立淨=round(new_ind.net.sum() * 1000, 1), 差=round((new_d0.net.sum() - new_ind.net.sum()) * 1000, 1),
                        成交價_T加1分對T收盤=round((j.gross_d0 - j.gross_ind).sum() * 1000, 1), 資金費=round(-j.fund.sum() * 1000, 1),
                        殘差=round(((j.net_d0 - j.net_ind) - (j.gross_d0 - j.gross_ind) + j.fund).sum() * 1000, 3), 配對筆數=len(j), 方向全同=bool((j.s_d0 == j.s_ind).all()))
R['更正前後對帳'] = dict(
    D0口徑=dict(V83=round(old_d0.net.sum() * 1000, 1), V84扣一筆的估算=2281.9, V85完整重跑=round(new_d0.net.sum() * 1000, 1), 差_V83減V85=round((old_d0.net.sum() - new_d0.net.sum()) * 1000, 1)),
    獨立口徑=dict(V83=round(old_ind.net.sum() * 1000, 1), V85完整重跑=round(new_ind.net.sum() * 1000, 1), 差=round((old_ind.net.sum() - new_ind.net.sum()) * 1000, 1)),
    說明='固定名目 1,000U（每千U）、來回成本 22.5bp（0.001125×2）、D0 另扣實際資金費；分母都是名目 1,000U。V84 的 +2,281.9 是 D0 口徑 +2,294.1 減該筆 121.6bp÷10 的估算，與完整重跑應一致。')

# ---------------- 3. 年度、事件群、集中度、持有期時間線（獨立口徑，與 V83 收尾相同） ----------------
def clusters(t):
    t = t.sort_values('T').reset_index(drop=True); cid = ((t['T'].diff() > pd.Timedelta(days=7)) | (t.s != t.s.shift())).cumsum(); t['群'] = cid
    g = t.groupby('群').agg(起=('T', 'min'), 迄=('T', 'max'), 筆=('net', 'size'), 方向=('s', 'first'), 淨=('net', 'sum')).sort_values('淨', ascending=False); g['淨'] = (g.淨 * 1000).round(1)
    top = t.sort_values('net', ascending=False).head(10); srt = t.sort_values('net', ascending=False)
    return g, dict(事件群數=len(g), 單筆群數=int((g.筆 == 1).sum()), 最大群筆數=int(g.筆.max()), 為正的群比例=round(float((g.淨 > 0).mean()), 3), 最佳10筆分屬幾個群=int(top.群.nunique()),
                   最佳5筆合計=round(float(srt.net.head(5).sum()) * 1000, 1), 去最佳5筆=round(float(srt.net.iloc[5:].sum()) * 1000, 1), 去最佳10筆=round(float(srt.net.iloc[10:].sum()) * 1000, 1),
                   最佳10筆年度={int(k): int(v) for k, v in top['T'].dt.year.value_counts().sort_index().items()})
for nm in ('V83原始', '更正'):
    g, cs = clusters(res[nm][1]); R[nm]['集中度_獨立口徑'] = cs
    if nm == '更正': g.to_csv(os.path.join(OUT, 'C_clusters_corrected.csv'))
segs = [(-7, 0), (0, 3), (3, 4), (4, 7), (7, 9), (9, 11), (11, 14), (14, 21)]
def segments(ex):
    ev = [(rd, 1.0 if x >= 1 else -1.0) for rd, x in z.items() if abs(x) >= 1 and not ex(rd) and rd + pd.Timedelta(days=21) in P.index and rd + pd.Timedelta(days=21) <= W1]
    out = []
    for a, b in segs:
        r = np.array([s * (P[rd + pd.Timedelta(days=b)] / P[rd + pd.Timedelta(days=a)] - 1) for rd, s in ev])
        out.append(dict(區間=f'{a}～{b}', 事件數=len(r), 毛報酬合計_每千U=round(float(r.sum()) * 1000, 1), 每日平均bp=round(float(r.mean()) * 1e4 / (b - a), 1), t=round(float(r.mean() / r.std(ddof=1) * math.sqrt(len(r))), 2)))
    return out
sg = pd.DataFrame({'V83原始_' + k: v for k, v in pd.DataFrame(segments(EX_OLD)).set_index('區間').items()}).join(pd.DataFrame({'更正_' + k: v for k, v in pd.DataFrame(segments(EX_NEW)).set_index('區間').items()}))
sg.to_csv(os.path.join(OUT, 'C_segments_before_after.csv')); R['持有期時間線'] = sg.reset_index().to_dict('records')

# ---------------- 4. 循環平移：193 個替代平移完整列舉（不重抽） ----------------
def shift_all(ex, basis):
    vals = []
    for k in range(8, 201):
        zz = pd.Series(np.roll(z.values, k), index=z.index); ev = events(zz, ex)
        t = trade_d0(ev) if basis == 'd0' else trade_ind(ev); vals.append(t.net.sum() * 1000 if len(t) else 0.0)
    return np.array(vals)
dist = {}; R['循環平移'] = {}
for nm, ex in (('V83原始', EX_OLD), ('更正', EX_NEW)):
    for basis in ('d0', 'ind'):
        t = res[nm][0 if basis == 'd0' else 1]; obs = t.net.sum() * 1000; v = shift_all(ex, basis); dist[f'{nm}_{basis}'] = v; k_ = int((v >= obs).sum())
        R['循環平移'][f'{nm}_{"D0口徑" if basis == "d0" else "獨立口徑"}'] = dict(觀測=round(obs, 1), 替代平移數=len(v), 達到或超過=k_, 精確p=round((1 + k_) / (len(v) + 1), 5), 最小可達p=round(1 / (len(v) + 1), 5),
                                                                          最大替代=round(float(v.max()), 1), 第二大=round(float(np.sort(v)[-2]), 1), 分位_5_50_95=[round(float(np.quantile(v, q)), 1) for q in (.05, .5, .95)], 觀測減最大=round(obs - float(v.max()), 1))
pd.DataFrame(dict(平移週數=np.arange(8, 201), **{k: np.round(v, 2) for k, v in dist.items()})).to_csv(os.path.join(OUT, 'C_shift_distribution_193.csv'), index=False)

# ---------------- 5. 檢定力（更正後逐筆分布；沿用獨立同分布、常態、單尾、固定效果的假設） ----------------
zq = NormalDist().inv_cdf; e = new_ind; mu = e.net.mean() * 1e4; sd = e.net.std(ddof=1) * 1e4
yrs = (e['T'].max() - e['T'].min()).days / 365.25; g, _ = clusters(e); rs, rc = len(e) / yrs, len(g) / yrs
pw = []
for lab, a in (('未校正', 0.05), ('V85 本輪無假說，不列', None), ('3 項', 0.05 / 3), ('6 項', 0.05 / 6), ('44 項', 0.05 / 44)):
    if a is None: continue
    for eff_nm, eff in (('歷史估計', mu), ('一半', mu / 2), ('100bp', 100.0), ('50bp', 50.0)):
        n = math.ceil(((zq(1 - a) + zq(0.8)) * sd / eff) ** 2); pw.append(dict(家族=lab, alpha=round(a, 5), 效果=eff_nm, 效果bp=round(eff, 1), 所需事件數=n, 年數_訊號=round(n / rs, 1), 年數_事件群=round(n / rc, 1)))
pd.DataFrame(pw).to_csv(os.path.join(OUT, 'C_power_corrected.csv'), index=False)
R['檢定力'] = dict(逐筆平均bp=round(mu, 1), 標準差bp=round(sd, 1), 事件數=len(e), 每年訊號=round(rs, 1), 每年事件群=round(rc, 1), 表=pw,
                假設='逐筆獨立同分布、常態近似、單尾、檢定力 80%、真實效果固定；這些假設不成立時（厚尾、相依、衰減），所需事件數只會更多，因此不給時程')
R['判定'] = 'V83-C 維持 REJECTED（更正後仍不過 K2 年度條件；循環平移最小可達 p 過不了 44 項家族門檻）。本更正是資料可見時間的工程稽核，不登記為新獲利假說，不救回第 9～11 天窗口，不建影子。'
json.dump(R, open(os.path.join(OUT, 'v85_c_fix.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: R[k] for k in ('可用性', '交易差異', '兩口徑對帳_更正後', '更正前後對帳', '循環平移')}, ensure_ascii=False, indent=1, default=str))
for nm in ('V83原始', '更正', '敏感度_再去假日週'): print(nm, json.dumps(R[nm], ensure_ascii=False, default=str))
