"""V84 稽核：V83 收尾的時間軸、發布日推定規則驗證、多重比較家族、檢定力重算。
不產生任何交易規則、不調參。只讀 V83 既有輸出與 CFTC 官方 2026 發布表。
用法：python backtest/research/v84_audit.py"""
import os, re, json, html, csv
import numpy as np, pandas as pd
from statistics import NormalDist
from pandas.tseries.holiday import USFederalHolidayCalendar

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
V83 = os.path.join(ROOT, 'doc', 'research_results', 'v83'); OUT = os.path.join(ROOT, 'doc', 'research_results', 'v84'); os.makedirs(OUT, exist_ok=True)
RAW = os.path.join(ROOT, 'data', 'v84')
R = {}

# ---------- 1. CFTC 官方 2026 發布表 ----------
t = open(os.path.join(RAW, 'index.htm.html'), encoding='utf-8', errors='replace').read()
t = re.sub(r'(?s)<(script|style).*?</\1>', '', t); t = html.unescape(re.sub(r'<[^>]+>', ' ', t)); t = re.sub(r'\s+', ' ', t)
seg = t[t.find('2026 Release Schedule'):t.find('*Delayed release date')]
mon = {m: i + 1 for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'])}
pub = []
for m, body in re.findall(r'(January|February|March|April|May|June|July|August|September|October|November|December) ([0-9\* ]+?)(?= [A-Z]|$)', seg):
    for d in re.findall(r'\d{2}\*?', body): pub.append((pd.Timestamp(2026, mon[m], int(d[:2])), d.endswith('*')))
hol = set(USFederalHolidayCalendar().holidays('2020-12-01', '2027-01-31'))

def nominal_release(rd):
    """推定規則：持倉日後的週五 15:30 美東；持倉日次日至該週五之間有聯邦假日則順延到下一個非假日工作日（通常是週一）。"""
    fri = rd + pd.Timedelta(days=(4 - rd.weekday()) % 7 or 7)
    rel = fri
    if any(rd + pd.Timedelta(days=i) in hol for i in range(1, (fri - rd).days + 1)):
        rel = fri + pd.Timedelta(days=3)
        while rel in hol or rel.weekday() >= 5: rel += pd.Timedelta(days=1)
    return rel

# 驗證推定規則：用 2026 表（含 2025-12-30 持倉日→2026-01-05）
chk = []
for rel, star in pub:
    rd = rel - pd.Timedelta(days=(rel.weekday() - 1) % 7 or 7)  # 該發布之前最近的週二
    if rel.weekday() == 0: rd = rel - pd.Timedelta(days=6)
    chk.append(dict(官方發布日=rel.date(), 官方標註順延=star, 推定持倉日=rd.date(), 規則推定發布日=nominal_release(rd).date(), 一致=nominal_release(rd) == rel))
chk = pd.DataFrame(chk); chk.to_csv(os.path.join(OUT, 'cftc_2026_schedule_check.csv'), index=False)
R['發布日推定規則對2026官方表'] = dict(官方發布次數=len(chk), 一致=int(chk.一致.sum()), 不一致=chk.loc[~chk.一致, ['官方發布日', '規則推定發布日']].astype(str).values.tolist(), 官方順延次數=int(chk.官方標註順延.sum()))

# ---------- 2. C 逐事件時間軸 ----------
ev = pd.read_csv(os.path.join(V83, 'C_events_main.csv'), parse_dates=['T', '出場'])
cot = pd.read_csv(os.path.join(ROOT, 'data', 'v83', 'cot_ether_146021.csv'), parse_dates=['rd'])
rows = []
for _, e in ev.iterrows():
    rd = e['T'] - pd.Timedelta(days=7)
    rel = nominal_release(rd); rel_utc = (rel + pd.Timedelta(hours=15, minutes=30)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)
    basis = '2026 官方表' if rel.year == 2026 and any(rel == p for p, _ in pub) else '推定（週五 15:30 美東＋聯邦假日順延規則）'
    rows.append(dict(持倉日=rd.date(), 持倉日星期=rd.day_name(), 在COT檔中=bool((cot.rd == rd).any()), 推定發布_美東=f'{rel.date()} 15:30', 推定發布_UTC=rel_utc, 發布依據=basis, 是否假日順延=rel.weekday() != 4,
                     決策_UTC=e['T'], 成交_UTC=e['T'] + pd.Timedelta(minutes=1), 發布到決策_小時=round((e['T'] - rel_utc).total_seconds() / 3600, 1),
                     出場_UTC=e['出場'] + pd.Timedelta(minutes=1), 方向=e['方向'], z=e['附註'], 淨bp=round(e['net'] * 1e4, 1)))
tl = pd.DataFrame(rows); tl.to_csv(os.path.join(OUT, 'C_event_timeline.csv'), index=False)
R['C時間軸'] = dict(事件數=len(tl), 持倉日星期={k: int(v) for k, v in tl.持倉日星期.value_counts().items()}, 推定假日順延事件=int(tl.是否假日順延.sum()),
                  發布到決策_小時_最小=float(tl.發布到決策_小時.min()), 最大=float(tl.發布到決策_小時.max()), 決策早於推定發布=int((tl.發布到決策_小時 <= 0).sum()),
                  使用2026官方表事件=int((tl.發布依據 == '2026 官方表').sum()), 不在COT檔=int((~tl.在COT檔中).sum()),
                  說明='實際發布時間戳（含當週是否另有延誤）無逐筆官方紀錄可核對；2021~2025 依規則推定，規則已對 2026 官方表驗證。')

# ---------- 2b. 官方特別公告的非假日延後（2025 停擺補發表、2025-01 國喪） ----------
s = open(os.path.join(RAW, 'HistoricalSpecialAnnouncements.html'), encoding='utf-8', errors='replace').read()
s = re.sub(r'(?s)<(script|style).*?</\1>', '', s); s = html.unescape(re.sub(r'<[^>]+>', ' ', s)); s = re.sub(r'\s+', ' ', s)
blk = s[s.find('December 9, 2025:'):s.find('November 18, 2025:')]
late = {pd.Timestamp(a): pd.Timestamp(c) for a, b, c in re.findall(r'(\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4}) (\d\d/\d\d/\d{4})', blk)}
late[pd.Timestamp('2025-01-07')] = pd.Timestamp('2025-01-13')  # 國喪日，公告：改於週一發布
sp = []
for rd, pubd in sorted(late.items()):
    pu = (pubd + pd.Timedelta(hours=15, minutes=30)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None); dec = rd + pd.Timedelta(days=7)
    hit = tl[pd.to_datetime(tl.持倉日) == rd]
    sp.append(dict(持倉日=rd.date(), 官方實際發布=pubd.date(), 決策_UTC=dec, 決策早於實際發布=bool(dec < pu), V83已剔除=bool(pd.Timestamp('2025-09-30') <= rd <= pd.Timestamp('2025-11-18')),
                   是C的交易=len(hit) > 0, 該筆淨bp=(float(hit.淨bp.iloc[0]) if len(hit) else None)))
sp = pd.DataFrame(sp); sp.to_csv(os.path.join(OUT, 'cftc_special_delays_check.csv'), index=False)
bad = sp[sp.決策早於實際發布 & ~sp.V83已剔除]
R['特別公告延後'] = dict(列數=len(sp), 決策早於實際發布且未剔除=bad.astype(str).to_dict('records'),
                    受影響的C交易=bad[bad.是C的交易].astype(str).to_dict('records'),
                    移除受影響交易後淨額_每千U=round(ev.net.sum() * 1000 - sum(x / 10 for x in bad[bad.是C的交易].該筆淨bp), 1),
                    說明='V83 只剔除持倉日到 2025-11-18；官方 2025-12-09 補發表顯示持倉日 11-25～12-16 也延後發布。其中持倉日 2025-12-09 是 C 的一筆交易，實際 12-19 發布、決策 12-16，屬於看到未公開資料。資料檔也可能是事後修正版（2023 ION 期間官方調整過未平倉量）。')

# ---------- 3. 循環平移 p 的精確意義 ----------
co = json.load(open(os.path.join(V83, 'C_closeout.json'), encoding='utf-8'))['循環平移']
N = co['登記範圍_相異平移數']; k = co['登記範圍_達到或超過觀測']; Na = co['全部平移數']; ka = co['全部_達到或超過']
R['循環平移'] = dict(登記範圍=f'8~200 週，共 {N} 個「替代」平移，不含觀測排列（平移 0）', 精確p=f'(1+{k})/({N}+1) = {(1 + k) / (N + 1):.5f}', 最小可達p=f'1/{N + 1} = {1 / (N + 1):.5f}',
                 全部平移=f'1~{Na} 週共 {Na} 個替代平移，{ka} 個達到或超過觀測；p=(1+{ka})/({Na}+1) = {(1 + ka) / (Na + 1):.5f}；最小可達 {1 / (Na + 1):.5f}',
                 D0原始寫法='1,000 次有放回抽樣自 193 個平移，0 次超過，寫成 p=0.0（錯誤，已撤回）', 觀測=co['觀測'], 登記範圍最大=co['登記範圍_最大'], 差=round(co['觀測'] - co['登記範圍_最大'], 1))

# ---------- 4. 多重比較家族 ----------
led = {r['id']: r for r in csv.DictReader(open(os.path.join(ROOT, 'doc', 'research_results', 'trial_ledger.csv'), encoding='utf-8'))}
fam_v46 = ['T001', 'T003', 'T004', 'T005', 'T006', 'T007', 'T008', 'T009', 'T010', 'T011', 'T014', 'T016', 'T017', 'T021', 'T024', 'T026', 'T029', 'T031', 'T032', 'T033',  # 台帳記載的 20 個
           'T038', 'T039', 'T043', 'T044', 'T046',  # 至 V67 累計 25
           'T048', 'T050', 'T054', 'T060', 'T061', 'T062', 'T063', 'T064',  # 至 V73 累計 33（V68 T047 雲端不可核對，不計）
           'T065', 'T068', 'T070',  # 至 V76 累計 36
           'T073', 'T083',  # V78、V79-S
           'T098', 'T099', 'T100', 'T101', 'T102', 'T103']
fam = {'V83 本輪 3 項': ['T101', 'T102', 'T103'], 'V82＋V83 共 6 項': ['T098', 'T099', 'T100', 'T101', 'T102', 'T103'], 'V46 以來有方向性或執行面意義的事前登記假說（台帳計數規則）': fam_v46}
R['多重比較家族'] = {}
for nm, ids in fam.items():
    a = 0.05 / len(ids)
    R['多重比較家族'][nm] = dict(項數=len(ids), Bonferroni門檻=round(a, 5), C的p_0_0052是否通過=bool(1 / 194 < a), 最小可達p是否可能通過=bool(1 / 194 < a),
                              試驗=[f"{i} {led[i]['round']}" for i in ids])
R['多重比較家族_說明'] = ('V83 收尾寫的「約 31 個」是低估。台帳自身在 V76 已記「累計預登記假說 36 個」，之後加上 V78、V79-S、V82 三項、V83 三項，'
                    '依同一計數規則為 44 個（雲端可核對；含 V68 為 45）。門檻 0.05/44≈0.0011。不論 31 或 44，最小可達 p≈0.0052 都過不了。'
                    '另有歷史 H001（V15~V45 約 40 輪）不可重建、未列入；鄰域、稽核與切段亦未列入，因此真實的有效檢定數只會更多。')

# ---------- 5. 檢定力 ----------
z = NormalDist().inv_cdf
sd = 926.0; d0 = 293.0; rate_sig = 15.8; rate_cl = 14.6
pw = []
for lab, a in (('未校正 單尾 0.05', 0.05), ('3 項 0.0167', 0.05 / 3), ('6 項 0.0083', 0.05 / 6), ('31 項 0.0016', 0.05 / 31), ('44 項 0.0011', 0.05 / 44)):
    for eff in (d0, d0 / 2, 100.0, 50.0):
        n = ((z(1 - a) + z(0.8)) * sd / eff) ** 2
        pw.append(dict(檢定=lab, alpha=round(a, 5), 效果bp=round(eff, 1), 所需事件數=int(np.ceil(n)), 年數_以訊號15點8每年=round(np.ceil(n) / rate_sig, 1), 年數_以事件群14點6每年=round(np.ceil(n) / rate_cl, 1)))
pw = pd.DataFrame(pw); pw.to_csv(os.path.join(OUT, 'C_power_table.csv'), index=False)
R['檢定力'] = dict(公式='n = ((z_(1-α) + z_(0.8)) × σ / δ)²，單尾、檢定力 80%、逐筆獨立同分布、常態近似', σ_bp=sd, 歷史δ_bp=d0,
                事件頻率='約 15.8 個訊號／年（78 筆÷約 4.9 個有效年）；相鄰同向併群後約 14.6 群／年',
                限制=['報酬右偏厚尾（偏度 1.0），常態近似下樣本數偏少', '逐筆可能相依（事件群只處理相鄰週）', '效果可能衰減或隨市場結構改變（ETF、CFTC 改為一週兩次發布）',
                    'σ 本身是 78 筆的估計，有抽樣誤差', '歷史 δ 含選擇偏誤，是樂觀上限', '因此不給固定時程'],
                主要列=pw[pw.效果bp.isin([d0, d0 / 2])].to_dict('records'))

# ---------- 6. 對帳 ----------
cc = json.load(open(os.path.join(V83, 'C_closeout.json'), encoding='utf-8'))['兩份程式對帳']
R['對帳'] = dict(D0淨=cc['D0淨'], 獨立版淨=cc['獨立版淨'], 差=cc['差'], 資金費=cc['來自資金費_D0有扣獨立版沒扣'], 成交價=cc['來自成交價_T加1分對T收盤'], 殘差=cc['殘差'],
              核對=round(cc['來自資金費_D0有扣獨立版沒扣'] + cc['來自成交價_T加1分對T收盤'] + cc['殘差'], 1) == round(cc['差'], 1) or '符號口徑：差＝獨立版−D0 的負值',
              說明='+2,294.1（D0，T+1 分成交、扣資金費）與 +2,285.8（獨立版，T 收盤、不扣資金費）差 8.3＝資金費 +12.2 加成交價 −3.9，殘差 0。循環平移用的是獨立版口徑，所以觀測值寫 2,285.8，最大替代平移 2,203.6，差 82.2。')
json.dump(R, open(os.path.join(OUT, 'v84_audit.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: R[k] for k in ('發布日推定規則對2026官方表', 'C時間軸', '循環平移')}, ensure_ascii=False, indent=1, default=str))
print(pw.to_string())
