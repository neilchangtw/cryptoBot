"""V83 資料可行性檢查（只看資料本身與同時點對照，不計算任何 ETH 後續報酬）。用法：python backtest/research/v83_feas.py"""
import os, sys, io, json, zipfile, lzma, struct
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v53_common as C
D = os.path.join(C.ROOT, 'data', 'v83'); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v83'); F = {}
def yahoo(name):
    j = json.load(open(os.path.join(D, name))); r = j['chart']['result'][0]; q = r['indicators']['quote'][0]; m = r['meta']
    d = pd.DataFrame(dict(ts=pd.to_datetime(r['timestamp'], unit='s'), open=q['open'], high=q['high'], low=q['low'], close=q['close'], volume=q['volume'])); return d, m
def chi(d, hh, mm=0): return (pd.Timestamp(d).tz_localize('America/Chicago') + pd.Timedelta(hours=hh, minutes=mm)).tz_convert('UTC').tz_localize(None)

# ---- 1. CME 以太幣期貨（Yahoo ETH=F 日線）
e, m = yahoo('yahoo_ethf_1d.json'); e['date'] = e.ts.dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.date; e = e[(e.ts >= '2021-02-08') & (e.ts < '2026-11-01')].reset_index(drop=True)
bd = pd.bdate_range(e.date.min(), e.date.max()); miss = [str(x.date()) for x in bd if x.date() not in set(e.date)]
rows = []
for r in e.dropna(subset=['close', 'open']).itertuples():
    prev = pd.Timestamp(r.date) - pd.Timedelta(days=1)
    try: rows.append(dict(date=r.date, close=r.close, open=r.open, p1500=U.px_at([chi(r.date, 15)], 0)[0], p1600=U.px_at([chi(r.date, 16)], 0)[0], p_prev1700=U.px_at([chi(prev, 17)], 0)[0]))
    except Exception: pass
x = pd.DataFrame(rows); a = lambda s: round(float(np.median(np.abs(s)) * 1e4), 1)
F['CME_ETH期貨_Yahoo日線'] = dict(來源='query1.finance.yahoo.com v8 chart ETH=F interval=1d', 交易所=m.get('exchangeName'), 類型=m.get('instrumentType'), 時區=m.get('exchangeTimezoneName'), 期間=[str(e.date.min()), str(e.date.max())], 根數=len(e), 開收為空=int(e[['open', 'close']].isna().any(axis=1).sum()), 缺少的工作日數=len(miss), 缺少的工作日_前10=miss[:10],
    收盤對永續_芝加哥15點_中位絕對差bp=a(x.close / x.p1500 - 1), 收盤對永續_芝加哥16點_中位絕對差bp=a(x.close / x.p1600 - 1), 收盤對永續15點_中位有號差bp=round(float(np.median(x.close / x.p1500 - 1) * 1e4), 1), 開盤對永續_前一日芝加哥17點_中位絕對差bp=a(x.open / x.p_prev1700 - 1),
    合約識別='無。ETH=F 是 Yahoo 拼接的近月連續序列，沒有合約代碼與換月日；是否為結算價未註明', 連續調整='未做回溯調整（價格水準與現貨一致）；換月日的跨日變動含月差', 回補風險='Yahoo 不保證不修訂，無版本紀錄', 日內='1 小時只有近 730 天（2024-06 起）、1 分鐘只有近 7 天，不足以涵蓋 2021 起')
x.to_csv(os.path.join(OUT, 'feas_cme_vs_perp.csv'), index=False)
h, _ = yahoo('yahoo_ethf_1h.json'); h = h.dropna(subset=['open']); hs = h[h.ts.dt.dayofweek == 6]; first = hs.groupby(hs.ts.dt.date).ts.min(); lt = first.dt.tz_localize('UTC').dt.tz_convert('America/Chicago').dt.strftime('%H:%M').value_counts().to_dict()
fri = h[h.ts.dt.tz_localize('UTC').dt.tz_convert('America/Chicago').dt.dayofweek == 4]; last = fri.groupby(fri.ts.dt.date).ts.max().dt.tz_localize('UTC').dt.tz_convert('America/Chicago').dt.strftime('%H:%M').value_counts().to_dict()
F['CME_ETH期貨_Yahoo小時線'] = dict(期間=[str(h.ts.min()), str(h.ts.max())], 根數=len(h), 週日第一根的芝加哥時間分布=lt, 週五最後一根的芝加哥時間分布=last)
# ---- 2. VIX
v, mv = yahoo('yahoo_vix_1d.json'); v = v[(v.ts >= '2020-06-01') & (v.ts < '2026-11-01')]; F['VIX_Yahoo日線'] = dict(來源='^VIX interval=1d', 期間=[str(v.ts.min())[:10], str(v.ts.max())[:10]], 根數=len(v), 收盤為空=int(v.close.isna().sum()), 發布時間='指數於美股收盤後約 16:15 美東時間定值；本研究在 17:00 美東時間之後才使用', 回補風險='指數值極少修訂；Yahoo 無版本紀錄')
# ---- 3. CFTC TFF（期貨）
cot = []
for y in range(2021, 2027):
    z = zipfile.ZipFile(os.path.join(D, f'cot_fin_{y}.zip')); t = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), low_memory=False); cot.append(t[t['CFTC_Contract_Market_Code'].astype(str).str.strip() == '146021'])
c = pd.concat(cot); c['rd'] = pd.to_datetime(c['Report_Date_as_YYYY-MM-DD']); c = c.sort_values('rd').drop_duplicates('rd'); keep = ['rd', 'Open_Interest_All', 'Lev_Money_Positions_Long_All', 'Lev_Money_Positions_Short_All', 'Asset_Mgr_Positions_Long_All', 'Asset_Mgr_Positions_Short_All', 'Dealer_Positions_Long_All', 'Dealer_Positions_Short_All']
c[keep].to_csv(os.path.join(C.ROOT, 'data', 'v83', 'cot_ether_146021.csv'), index=False); gaps = c.rd.diff().dt.days; wk = pd.date_range(c.rd.min(), c.rd.max(), freq='7D')
F['CFTC_TFF_以太幣期貨'] = dict(來源='www.cftc.gov/files/dea/history/fut_fin_txt_YYYY.zip，合約代碼 146021（ETHER CASH SETTLED, CME）', 期間=[str(c.rd.min())[:10], str(c.rd.max())[:10]], 週數=len(c), 應有週數=len(wk), 非7天間隔=int((gaps.dropna() != 7).sum()), 非7天間隔明細=[f'{str(a)[:10]}:{int(b)}' for a, b in zip(c.rd[1:], gaps[1:]) if b != 7][:12], 名稱變更=sorted(c['Market_and_Exchange_Names'].str.strip().unique().tolist()),
    發布時間='持倉日為週二，正常於當週五 15:30 美東時間發布；假日順延。檔案不含實際發布時間', 已知延遲='2023-01-31～2023-03 初（ION 事件，報告延後數週）；2025-10-01 起聯邦政府停擺期間停發、事後補發（確切補發日未逐期核對，列為未確認）', 修訂風險='CFTC 會更正歷史報告；歷史檔是目前版本')
# ---- 4. Dukascopy
b = open(os.path.join(D, 'duka_hour_test.bi5'), 'rb').read(); d = lzma.decompress(b); n = len(d) // 24; r0 = struct.unpack('>5if', d[:24])
F['Dukascopy_跨資產日內'] = dict(來源='datafeed.dukascopy.com/datafeed/{代碼}/{年}/{月-1}/BID_candles_hour_1.bi5 與 /{日}/BID_candles_min_1.bi5', 實測='USATECHIDXUSD 2021-01 小時檔可解（%d 根）；USA500IDXUSD、XAUUSD、ETHUSD 的 2021-01-04 分鐘檔各 1,440 根；連線不穩，約三成請求回 503 或中斷' % n, 時區='UTC（檔內為距月初或日初的秒數）', 性質='單一券商的差價合約報價（買價），不是交易所成交價；無成交量意義；週末無資料', 回補風險='未確認', 本輪使用='未使用（見假說選擇）')
F['未取得'] = dict(CME官網='www.cmegroup.com 結算價與交易時間頁回 403（阻擋自動存取），未繞過', stooq='回傳需瀏覽器驗證的頁面，未繞過', FRED='連線中斷，未取得', CME逐筆或分鐘歷史='免費來源未找到；CME DataMine 為付費', CME假日與提前收盤表='未取得，改以 Yahoo 日線缺日辨識')
if __name__ == "__main__": json.dump(F, open(os.path.join(OUT, 'data_feasibility.json'), 'w'), ensure_ascii=False, indent=1, default=str); print("ok")
