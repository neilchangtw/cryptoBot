#!/usr/bin/env python3
"""V87 成交配對與損益橋接（離線匯總器，第二版）。不連網、不開子程序、不讀 .env、不匯入專案模組；只讀使用者指定的本機一般檔案。
配對規則寫死於本檔，見 doc/research_results/v87/v87_plan.md §三、§四（預登記 commit 6b3684a）；第二版修正見 v87_results.md §8。

輸入（都是使用者私人目錄的本機一般檔案，不進 Git；URL、任何 scheme、符號連結、目錄、不存在的路徑都在讀取前拒絕）
  --fills   extract_fills_readonly.py 輸出的 trades.csv（userTrades）
  --orders  同上 orders.csv（allOrders）
  --local   機器人 data_live/trades.csv 的唯讀快照（不要直接讀持續寫入中的檔）
  --klines  ETHUSDT 1h K 線：open_time（毫秒）或 datetime（UTC+8 開盤字串），以及 close
  --klines-source  K 線來源說明文字（選填，只寫進 summary.json）
  --bar-time open|close  本機紀錄時間是 K 線開盤或收盤（必填）
  --out     尚不存在、且不在 git 工作樹內的新目錄

輸出（先在記憶體算完並序列化，再寫入暫存目錄 <out>.incomplete，三檔寫完才改名為 <out>；目錄 700、檔案 600）
  legs_deidentified.csv   逐腿：相容配對狀態、原因、時間差、方向性 bp、價差成本美元、實際與參考名目美元、USDT 手續費與其狀態
  bridge_deidentified.csv 逐筆：實際毛損益、參考價反事實毛損益、價差美元、USDT 手續費、交易所淨（不含 funding，僅手續費完整時）、本機淨、未拆分殘差、缺失成本
  summary.json            彙總＋執行資訊（工具與四個輸入的 SHA-256、版本、bar-time、K 線來源與覆蓋期間）；同樣內容印到 stdout
  不含 orderId、tradeId、單筆成交價、均價、數量欄、金鑰。名目美元可推回約略部位，分享前請自行決定。

結束碼：0 完成；2 輸入被拒（含 URL）；3 資料前提不成立（不輸出）；4 輸出目錄被拒或寫入失敗（不留下完成目錄）

用法：
  python3 -I fill_bridge.py --fills F --orders O --local L --klines K --bar-time open --out DIR
  python3 -I fill_bridge.py --selftest
"""
import argparse, hashlib, io, json, math, os, platform, re, stat, sys
import numpy as np, pandas as pd

H = 3600_000; WIN_MKT = 30 * 60_000; WIN_SN = H; TOL_BP = 1.0; QSTEP = 0.001
POSIX = os.name == 'posix'; O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
EPOCH = pd.Timestamp('1970-01-01')
OK = '相容配對'   # 時間、方向、價格相容；本機紀錄沒有 orderId 與數量，訂單身份（是否為機器人下的單）無法證明
FEE_OK = 'USDT'
_FAIL_AT = None   # 只供 selftest 注入寫檔失敗


class Stop(Exception):
    """資料前提不成立：停止，不輸出統計（結束碼 3）。"""


class Reject(Exception):
    """輸入被拒（結束碼 2）或輸出被拒（結束碼 4）。"""
    def __init__(self, msg, code=2):
        super().__init__(msg); self.code = code


def to_ms(x):
    return (x - EPOCH) // pd.Timedelta(milliseconds=1)


def num(s):
    return pd.to_numeric(s, errors='coerce')


def blank(v):
    return v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == '' or str(v).lower() == 'nan'


# ---------- 0. 輸入：只收本機一般檔案，讀取前驗證 ----------
SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*:')
WINDRIVE = re.compile(r'^[A-Za-z]:[\\/]')


def check_local_path(p):
    """純字串與 lstat 檢查；不開檔、不碰網路。"""
    s = str(p)
    if s.strip() == '' or s == '-': raise Reject(f'拒絕輸入：空白或標準輸入（{s!r}）')
    if '://' in s or (SCHEME.match(s) and not (not POSIX and WINDRIVE.match(s))):
        raise Reject(f'拒絕輸入：看起來是 URL 或帶 scheme 的路徑，只接受本機檔案（{s}）')
    try: st = os.lstat(s)
    except FileNotFoundError: raise Reject(f'拒絕輸入：檔案不存在（{s}）')
    except OSError as e: raise Reject(f'拒絕輸入：無法檢查（{type(e).__name__}，{s}）')
    if stat.S_ISLNK(st.st_mode): raise Reject(f'拒絕輸入：符號連結（{s}）')
    if not stat.S_ISREG(st.st_mode): raise Reject(f'拒絕輸入：不是一般檔案（{s}）')
    return s


def read_local(p):
    """驗證後以 O_NOFOLLOW 開啟、fstat 再確認一般檔案，讀成位元組；回傳 (DataFrame, sha256, 位元組數)。pandas 只拿到記憶體中的位元組。"""
    s = check_local_path(p)
    try: fd = os.open(s, os.O_RDONLY | O_NOFOLLOW)
    except OSError as e: raise Reject(f'拒絕輸入：無法開啟（{type(e).__name__}，{s}）')
    with os.fdopen(fd, 'rb') as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode): raise Reject(f'拒絕輸入：不是一般檔案（{s}）')
        b = fh.read()
    return pd.read_csv(io.BytesIO(b)), hashlib.sha256(b).hexdigest(), len(b)


# ---------- 1. 逐筆成交依 orderId 合併；手續費與 realizedPnl 逐筆驗證 ----------
def _all_or_nan(s):
    return float(s.sum()) if len(s) and s.notna().all() else np.nan


def fee_status(bad, assets):
    """bad＝該訂單任一成交 commission 非有效數值或資產空白；assets＝非空白資產的排序清單。"""
    if bad: return '缺值或無效'
    if len(assets) > 1: return '混合資產（' + ','.join(assets) + '）'
    return FEE_OK if assets == [FEE_OK] else '非USDT（' + assets[0] + '）'


def aggregate_fills(f):
    f = f.copy(); q = {}
    n0 = len(f); f = f.drop_duplicates('id'); q['重複成交剔除'] = n0 - len(f)
    for c in ('price', 'qty', 'time', 'commission', 'realizedPnl'):
        f[c] = num(f[c]) if c in f.columns else np.nan
    bad = f.price.isna() | f.qty.isna() | f.time.isna() | (f.price <= 0) | (f.qty <= 0)
    q['零或缺價量時間剔除'] = int(bad.sum()); f = f[~bad].copy()
    f['asset'] = f['commissionAsset'].map(lambda v: '' if blank(v) else str(v).strip()) if 'commissionAsset' in f.columns else ''
    f['pq'] = f.price * f.qty
    g = f.groupby('orderId').agg(qty=('qty', 'sum'), pq=('pq', 'sum'), first_ms=('time', 'min'), last_ms=('time', 'max'), n=('id', 'count'),
                                 rpnl=('realizedPnl', _all_or_nan), n_side=('side', 'nunique'), n_ps=('positionSide', 'nunique'),
                                 side=('side', 'first'), ps=('positionSide', 'first'))
    f['fee_bad'] = ~np.isfinite(f.commission.astype(float)) | (f.asset == '')
    fs = f.groupby('orderId').agg(bad=('fee_bad', 'any'), assets=('asset', lambda s: sorted(set(a for a in s if a != ''))), fee=('commission', _all_or_nan))
    st = pd.Series([fee_status(b, a) for b, a in zip(fs.bad, fs.assets)], index=fs.index); fee = fs.fee
    g['fee_status'] = st; g['fee_usdt'] = np.where(st == FEE_OK, fee, np.nan)
    g['vwap'] = g.pq / g.qty
    q['有效成交'] = len(f); q['合併後訂單'] = len(g)
    q['手續費狀態（訂單數）'] = {str(k): int(v) for k, v in st.value_counts().items()}
    q['realizedPnl 缺值訂單'] = int(g.rpnl.isna().sum())
    return g.drop(columns='pq'), q


# ---------- 2. 訂單屬性只取自 orders.csv ----------
def order_book(fa, orders):
    o = orders.copy()
    dup = o.duplicated('orderId', keep=False); o = o.drop_duplicates('orderId')
    oc = o.set_index('orderId')[[c for c in ('time', 'side', 'positionSide', 'origType', 'stopPrice', 'status') if c in o.columns]].rename(columns={'side': 'side_o', 'positionSide': 'positionSide_o'})
    m = fa.join(oc, how='left')
    m['create_ms'] = num(m['time']) if 'time' in m.columns else np.nan
    m['stop'] = num(m['stopPrice']) if 'stopPrice' in m.columns else np.nan
    m['dup_order_row'] = m.index.isin(set(orders.loc[dup, 'orderId']))
    reason = pd.Series('', index=m.index, dtype=object)
    in_o = m.index.isin(set(o.orderId))
    reason[~in_o] = '缺訂單'
    reason[(reason == '') & m.dup_order_row] = '訂單列重複'
    reason[(reason == '') & ((m.n_side > 1) | (m.n_ps > 1))] = '同訂單多方向或多持倉方向'
    ot = m['origType'] if 'origType' in m.columns else pd.Series(np.nan, index=m.index)
    reason[(reason == '') & ot.map(blank)] = '類型不明（origType 空白）'
    reason[(reason == '') & m.create_ms.isna()] = '缺建單時間'
    so = m['side_o'] if 'side_o' in m.columns else pd.Series(np.nan, index=m.index)
    po = m['positionSide_o'] if 'positionSide_o' in m.columns else pd.Series(np.nan, index=m.index)
    reason[(reason == '') & ((so.astype(str) != m.side.astype(str)) | (po.astype(str) != m.ps.astype(str)))] = '成交與訂單方向不符'
    m['origType'] = ot; m['ineligible'] = reason
    return m


# ---------- 3. 策略腿 ----------
def legs_from_local(local, bar_time):
    if bar_time not in ('open', 'close'): raise Stop('--bar-time 必須是 open 或 close')
    L = []
    for i, r in local.reset_index(drop=True).iterrows():
        d = str(r.get('direction', '')).upper(); ps = 'LONG' if d in ('LONG', 'L', 'BUY') else ('SHORT' if d in ('SHORT', 'S', 'SELL') else None)
        if ps is None: raise Stop(f'第 {i + 1} 筆本機紀錄方向無法判讀')
        for leg, tc, pc in (('進場', 'entry_time_utc', 'entry_price'), ('出場', 'exit_time_utc', 'exit_price')):
            v = r.get(tc)
            if blank(v): continue
            T = int(to_ms(pd.Timestamp(str(v).replace('Z', '')))) + (H if bar_time == 'open' else 0)
            side = ('BUY' if ps == 'LONG' else 'SELL') if leg == '進場' else ('SELL' if ps == 'LONG' else 'BUY')
            et = str(r.get('exit_type', '')) if leg == '出場' else '—'
            L.append(dict(序號=i + 1, 腿=leg, 方向=ps, side=side, 出場類型=et, T_ms=T, 紀錄價=float(num(pd.Series([r.get(pc)]))[0]),
                          safenet='SAFENET' in et.upper() and leg == '出場'))
    if not L: raise Stop('本機紀錄沒有任何腿')
    return pd.DataFrame(L)


def load_klines(kl):
    kl = kl.copy()
    if 'open_time' in kl.columns: kl['open_ms'] = num(kl.open_time)
    elif 'datetime' in kl.columns: kl['open_ms'] = to_ms(pd.to_datetime(kl['datetime']) - pd.Timedelta(hours=8))
    else: raise Stop('K 線缺 open_time 或 datetime')
    if 'close' not in kl.columns: raise Stop('K 線缺 close')
    if kl.open_ms.isna().any(): raise Stop('K 線時間缺值')
    kl['open_ms'] = kl.open_ms.astype('int64')
    kl['close_ms'] = kl.open_ms + H; kl['close'] = num(kl['close'])
    if not (kl.close_ms % H == 0).all(): raise Stop('K 線收盤時刻不在整點')
    if kl.close_ms.duplicated().any(): raise Stop('K 線收盤時刻重複')
    if kl.close.isna().any() or (kl.close <= 0).any(): raise Stop('K 線收盤價缺值或非正')
    return kl.set_index('close_ms').close.sort_index()


def bp_dir(side, px, ref):
    """方向性 bp，正值＝對策略不利。"""
    x = (px / ref - 1) * 1e4; return x if side == 'BUY' else -x


# ---------- 4. 候選、全局一對一（規則與預登記相同，未改） ----------
def match(legs, m):
    ok = m[m.ineligible == '']
    cand = {}
    for li, g in legs.iterrows():
        c = ok[(ok.side == g.side) & (ok.ps == g.方向)]
        if g.safenet: c = c[(c.origType == 'STOP_MARKET') & (c.first_ms >= g.T_ms - WIN_SN) & (c.first_ms <= g.T_ms + WIN_SN)]
        else: c = c[(c.origType == 'MARKET') & (c.create_ms >= g.T_ms) & (c.create_ms <= g.T_ms + WIN_MKT)]
        inwin = list(c.index)
        c = c[[np.isfinite(g.紀錄價) and abs(bp_dir('BUY', v, g.紀錄價)) <= TOL_BP for v in c.vwap]]
        cand[li] = (list(c.index), inwin)
    used = {}
    for li, (cs, _) in cand.items():
        for oid in cs: used[oid] = used.get(oid, 0) + 1
    res = {}
    for li, (cs, inwin) in cand.items():
        if len(cs) == 1 and used[cs[0]] == 1: res[li] = (OK, cs[0], '')
        elif len(cs) == 0: res[li] = ('未配對', None, '窗口內有同向單但價格與紀錄不一致' if inwin else '缺候選訂單')
        elif len(cs) > 1: res[li] = ('未配對', None, f'模糊：{len(cs)} 張候選')
        else: res[li] = ('未配對', None, '模糊：該訂單同時是多條腿的候選')
    return res, cand


def unmatched_orders(m, res, cand):
    paired = {v[1] for v in res.values() if v[1] is not None}; cand_any = {oid for cs, _ in cand.values() for oid in cs}
    inwin_any = {oid for _, iw in cand.values() for oid in iw}; out = {}
    for oid, r in m.iterrows():
        if oid in paired: continue
        if r.ineligible: k = r.ineligible
        elif oid in cand_any: k = '模糊（未配對）'
        elif oid in inwin_any: k = '窗口內但價格不一致'
        elif r.origType == 'MARKET': k = '市價單未對應策略腿（手動、重試或延遲）'
        else: k = f'其他類型未對應（{r.origType}）'
        out[str(k)] = out.get(str(k), 0) + 1
    return out


# ---------- 5. 逐腿與損益橋接 ----------
def fsum(x):
    """只加總有限值，並回報筆數；不讓 NaN 被當成 0。"""
    x = pd.Series(x, dtype=float); f = x[np.isfinite(x)]
    return dict(筆=int(len(f)), 合計=float(f.sum()) if len(f) else None, 缺值筆=int(len(x) - len(f)))


def analyze(fills, orders, local, kl_raw, bar_time):
    P = load_klines(kl_raw); fa, q = aggregate_fills(fills); m = order_book(fa, orders); legs = legs_from_local(local, bar_time)
    res, cand = match(legs, m); rows = []
    for li, g in legs.iterrows():
        st, oid, why = res[li]
        r = dict(序號=int(g.序號), 腿=g.腿, 方向=g.方向, 出場類型=g.出場類型, 月份=pd.Timestamp(g.T_ms, unit='ms').strftime('%Y-%m'), 狀態=st, 原因=why)
        if oid is not None:
            o = m.loc[oid]; mk = o.origType == 'MARKET'
            if mk:
                if g.T_ms not in P.index: raise Stop(f'第 {int(g.序號)} 筆{g.腿}的 T 不在 K 線內（必要窗口缺漏）')
                ref = float(P.loc[g.T_ms]); refname = 'T收盤價'
            else:
                ref = float(o.stop); refname = '停損價'
                if not np.isfinite(ref) or ref <= 0: ref = np.nan
            b = bp_dir(g.side, o.vwap, ref) if np.isfinite(ref) else np.nan
            r.update(訂單類型=o.origType, 價差參考=refname, 成交筆=int(o.n),
                     建單_T_ms=float(o.create_ms - g.T_ms) if mk else np.nan, 首筆_T_ms=float(o.first_ms - g.T_ms) if mk else np.nan,
                     末筆_T_ms=float(o.last_ms - g.T_ms) if mk else np.nan, 建單到末筆_ms=float(o.last_ms - o.create_ms) if mk else np.nan,
                     掛單存續_ms=np.nan if mk else float(o.first_ms - o.create_ms),
                     方向性bp=b, 價差成本美元=float(o.qty * ref * b / 1e4) if np.isfinite(b) else np.nan,
                     實際名目美元=float(o.qty * o.vwap), 參考名目美元=float(o.qty * ref) if np.isfinite(ref) else np.nan,
                     手續費USDT=float(o.fee_usdt), 手續費狀態=str(o.fee_status), realizedPnl狀態='完整' if np.isfinite(o.rpnl) else '缺值',
                     _qty=float(o.qty), _vwap=float(o.vwap), _ref=ref, _rpnl=float(o.rpnl))
        rows.append(r)
    R = pd.DataFrame(rows)
    B = []; flags = {}
    loc = local.reset_index(drop=True)
    for sn, g in R.groupby('序號'):
        lr = loc.iloc[int(sn) - 1]; e = g[g.腿 == '進場']; x = g[g.腿 == '出場']
        why = ''
        if len(x) == 0: why = '未平倉或無出場紀錄'
        elif len(e) == 0 or not ((e.狀態 == OK).all() and (x.狀態 == OK).all()): why = '有腿未配對'
        else:
            e = e.iloc[0]; x = x.iloc[0]
            if abs(e._qty - x._qty) > QSTEP / 2: why = '數量不一致'
            sbc = num(pd.Series([lr.get('entry_signal_bar_close')]))[0]
            if not why and np.isfinite(sbc) and abs(bp_dir('BUY', sbc, e._ref)) > TOL_BP: why = '收盤價不一致'
        if why:
            flags[why] = flags.get(why, 0) + 1; continue
        s = 1.0 if e.方向 == 'LONG' else -1.0; qq = e._qty
        act = s * qq * (x._vwap - e._vwap); cf = s * qq * (x._ref - e._ref) if np.isfinite(x._ref) else np.nan
        fee_ok = e.手續費狀態 == FEE_OK and x.手續費狀態 == FEE_OK
        fee = e.手續費USDT + x.手續費USDT if fee_ok else np.nan
        exn = act - fee if fee_ok else np.nan
        ln = float(num(pd.Series([lr.get('net_pnl_usd')]))[0])
        resid = ln - exn if fee_ok and np.isfinite(ln) else np.nan
        rp_ok = np.isfinite(x._rpnl)
        miss = ['funding 未取得']
        if not fee_ok: miss.append(f'手續費未驗證（進場 {e.手續費狀態}／出場 {x.手續費狀態}）')
        if not np.isfinite(cf): miss.append('出場參考價缺')
        if not rp_ok: miss.append('realizedPnl 缺值')
        if not np.isfinite(ln): miss.append('本機淨缺值')
        B.append(dict(序號=int(sn), 月份=x.月份, 方向=e.方向, 出場類型=x.出場類型, 出場參考=x.價差參考,
                      實際毛損益=act, 參考價反事實毛損益=cf, 價差美元=act - cf if np.isfinite(cf) else np.nan,
                      進場價差成本美元=e.價差成本美元, 出場價差成本美元=x.價差成本美元,
                      進場手續費USDT=e.手續費USDT, 出場手續費USDT=x.手續費USDT, 手續費USDT=fee,
                      淨損益狀態='已驗證（USDT 手續費完整，不含 funding）' if fee_ok else '未驗證',
                      funding='未取得', 交易所淨_不含funding=exn, 本機淨=ln, 未拆分殘差=resid,
                      realizedPnl核對差=float(x._rpnl - act) if rp_ok else np.nan, 缺失成本='；'.join(miss)))
    B = pd.DataFrame(B, columns=['序號', '月份', '方向', '出場類型', '出場參考', '實際毛損益', '參考價反事實毛損益', '價差美元', '進場價差成本美元', '出場價差成本美元',
                                 '進場手續費USDT', '出場手續費USDT', '手續費USDT', '淨損益狀態', 'funding', '交易所淨_不含funding', '本機淨', '未拆分殘差', 'realizedPnl核對差', '缺失成本'])
    pub = [c for c in R.columns if not c.startswith('_')]
    return R[pub], B, summarize(R, B, q, m, res, cand, flags, bar_time, P)


def d(x):
    x = pd.Series(x, dtype=float).dropna()
    return dict(n=int(len(x)), 中位=float(x.median()) if len(x) else None, p90=float(x.quantile(.9)) if len(x) else None, 最小=float(x.min()) if len(x) else None, 最大=float(x.max()) if len(x) else None)


def summarize(R, B, q, m, res, cand, flags, bar_time, P):
    ok = R[R.狀態 == OK]
    if '訂單類型' not in ok.columns: ok = ok.assign(訂單類型=pd.Series(dtype=object))
    mk = ok[ok.訂單類型 == 'MARKET']; sn = ok[ok.訂單類型 == 'STOP_MARKET']
    V = B[B.淨損益狀態 != '未驗證'] if len(B) else B
    S = dict(清洗=q, 腿數=int(len(R)), 相容配對腿=int(len(ok)), 未配對腿=int((R.狀態 != OK).sum()), 未配對比例=round(float((R.狀態 != OK).mean()), 4) if len(R) else None,
             未配對腿原因={str(k): int(v) for k, v in R[R.狀態 != OK].原因.value_counts().items()}, 未配對訂單=unmatched_orders(m, res, cand),
             市價腿=[{'腿': k[0], '方向': k[1], '建單−T_ms': d(g.建單_T_ms), '末筆−T_ms': d(g.末筆_T_ms), '建單到末筆_ms（毫秒解析度）': d(g.建單到末筆_ms),
                    '方向性bp': d(g.方向性bp), '價差成本美元': d(g.價差成本美元)} for k, g in mk.groupby(['腿', '方向'])],
             SafeNet腿=dict(n=int(len(sn)), 掛單存續_ms=d(sn.掛單存續_ms), 對停損價bp=d(sn.方向性bp), 說明='對停損價比較，不是收盤價反事實；掛單存續不是撮合延遲，不併入市價腿'),
             橋接=dict(交易筆=int(len(B)), 排除={str(k): int(v) for k, v in flags.items()},
                     價格橋接={c: fsum(B[c]) for c in ('實際毛損益', '參考價反事實毛損益', '價差美元')},
                     USDT淨損益=dict(已驗證筆=int(len(V)), 未驗證筆=int(len(B) - len(V)),
                                  **{c: fsum(V[c]) for c in ('手續費USDT', '交易所淨_不含funding', '本機淨', '未拆分殘差')}),
                     realizedPnl核對差_最大絕對=float(B.realizedPnl核對差.abs().max()) if len(B) and B.realizedPnl核對差.notna().any() else None,
                     realizedPnl缺值筆=int(B.realizedPnl核對差.isna().sum()) if len(B) else 0,
                     逐月=[dict(月份=k, 筆=int(len(g)), 價差美元=fsum(g.價差美元), USDT已驗證筆=int((g.淨損益狀態 != '未驗證').sum()),
                              交易所淨_不含funding_已驗證=fsum(g[g.淨損益狀態 != '未驗證'].交易所淨_不含funding)) for k, g in B.groupby('月份')] if len(B) else [],
                     最差價差事件=B[np.isfinite(B.價差美元)].nsmallest(3, '價差美元')[['序號', '月份', '方向', '出場類型', '價差美元']].to_dict('records') if len(B) else []),
             未取得=['funding（/fapi/v1/income 不在允許清單，未查詢）', '漏訊號與拒單（需 signal.log／alerts.log，本工具不讀）'],
             K線覆蓋=dict(根數=int(len(P)), 首根收盤UTC=pd.Timestamp(int(P.index.min()), unit='ms').isoformat(), 末根收盤UTC=pd.Timestamp(int(P.index.max()), unit='ms').isoformat()),
             說明=dict(bar_time=bar_time, 相容配對='時間、方向與價格（≤1bp）相容且全局一對一；本機紀錄沒有 orderId 與數量，無法證明訂單是機器人下的單（手動單身份可能無法排除）',
                     價差正負='方向性 bp 正值＝對策略不利；逐腿價差成本美元正值＝成本；橋接價差美元＝實際−參考價反事實＝−(進場＋出場價差成本美元)',
                     參考價='市價腿以 T 的 K 線收盤價為參考；SafeNet 出場以停損價為參考（不是收盤價反事實）',
                     手續費='只有每筆成交 commission 為有效數值且 commissionAsset 全為 USDT 的訂單才計入 USDT 手續費；非 USDT、混合、空白或無效者標未驗證，該筆交易所淨與殘差留空，不當作 0',
                     殘差='未拆分殘差＝本機淨−交易所淨（不含 funding），包含 funding、四捨五入與其他差異，不是 funding 本身',
                     重算='逐腿 方向性bp＝(實際名目/參考名目−1)×1e4（買）或其負值（賣）；價差成本美元＝參考名目×bp/1e4',
                     價格容差='1bp 是看過使用者回報（0.0bp）後訂的工程容差，不是事前依據', 毫秒='0 ms 只表示交易所時間戳在同一毫秒內',
                     樣本='已曝光 2026 樣本，不外推到 2021～2026', 隱私='逐腿名目美元可推回約略部位'))
    return S


# ---------- 6. 序列化（先於任何寫檔） ----------
def clean(o):
    """轉成標準 JSON 型別；字典鍵必須是字串（tuple 鍵直接報錯），NaN／inf 轉 null。"""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if not isinstance(k, str): raise TypeError(f'JSON 鍵必須是字串：{k!r}')
            out[k] = clean(v)
        return out
    if isinstance(o, (list, tuple)): return [clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)): return bool(o)
    if isinstance(o, (int, np.integer)): return int(o)
    if isinstance(o, (float, np.floating)): return float(o) if math.isfinite(float(o)) else None
    if o is None or isinstance(o, str): return o
    if isinstance(o, pd.Timestamp): return o.isoformat()
    raise TypeError(f'無法序列化的型別：{type(o).__name__}')


def to_json(S):
    return json.dumps(clean(S), ensure_ascii=False, indent=1, allow_nan=False)


# ---------- 7. 輸出目錄：暫存目錄寫完才改名 ----------
def inside_git(path):
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')): return True
        n = os.path.dirname(p)
        if n == p: return False
        p = n


def no_symlink_components(path):
    p = os.path.abspath(path)
    while True:
        if os.path.islink(p): raise Reject(f'拒絕輸出：路徑含符號連結（{p}）', 4)
        n = os.path.dirname(p)
        if n == p: return
        p = n


def check_out(out):
    """只檢查、不建立。"""
    out = os.path.abspath(out); stg = out + '.incomplete'
    no_symlink_components(out)
    if inside_git(out): raise Reject(f'拒絕輸出：位於 git 工作樹內（{out}）', 4)
    if os.path.lexists(out): raise Reject(f'拒絕輸出：目錄已存在（{out}），不覆寫', 4)
    if os.path.lexists(stg): raise Reject(f'拒絕輸出：前次未完成的暫存目錄仍在（{stg}），請先人工檢查', 4)
    return out, stg


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    if POSIX: os.chmod(path, 0o600)


def write_outputs(out, files):
    """files＝[(檔名, 文字)]，內容須已全部算好。失敗時刪掉本次建立的檔與暫存目錄，不留下完成目錄。"""
    out, stg = check_out(out)
    parent = os.path.dirname(out)
    if not os.path.isdir(parent): os.makedirs(parent, mode=0o700)
    os.mkdir(stg, 0o700)
    if POSIX: os.chmod(stg, 0o700)
    made = []
    try:
        for i, (name, text) in enumerate(files):
            if _FAIL_AT == i: raise OSError('selftest 注入的寫檔失敗')
            p = os.path.join(stg, name); write_private(p, text); made.append(p)
        if os.path.lexists(out): raise Reject(f'拒絕輸出：目錄在執行期間出現（{out}）', 4)
        os.rename(stg, out)   # 同一檔案系統內的改名；改名前一刻再檢查一次目標不存在
    except BaseException:
        for p in made:
            try: os.unlink(p)
            except OSError: pass
        try: os.rmdir(stg)
        except OSError: pass
        raise
    return out


# ---------- 8. 執行 ----------
def run_info(a, shas):
    me = os.path.abspath(__file__)
    with open(me, 'rb') as fh: tool = hashlib.sha256(fh.read()).hexdigest()
    return dict(工具=dict(檔名=os.path.basename(me), sha256=tool), 版本=dict(python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__, 平台=os.name),
                bar_time=a.bar_time, K線來源=a.klines_source or '未說明（使用者提供的本機檔）',
                輸入={k: dict(檔名=os.path.basename(getattr(a, k)), sha256=s, 位元組=n) for k, (s, n) in shas.items()},
                說明='只記錄檔名與雜湊，不記錄完整路徑；原始成交、訂單、實盤交易紀錄與金鑰不進 Git')


def run(a):
    try:
        check_out(a.out)
        dfs = {}; shas = {}
        for k in ('fills', 'orders', 'local', 'klines'):
            df, s, n = read_local(getattr(a, k)); dfs[k] = df; shas[k] = (s, n)
    except Reject as e:
        print(f'拒絕：{e}', file=sys.stderr); sys.exit(e.code)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as e:
        print(f'停止：CSV 無法解析（{type(e).__name__}）（未輸出統計）', file=sys.stderr); sys.exit(3)
    try:
        R, B, S = analyze(dfs['fills'], dfs['orders'], dfs['local'], dfs['klines'], a.bar_time)
    except Stop as e:
        print(f'停止：{e}（未輸出統計）', file=sys.stderr); sys.exit(3)
    except KeyError as e:
        print(f'停止：缺必要欄位 {e}（未輸出統計）', file=sys.stderr); sys.exit(3)
    S = dict(執行資訊=run_info(a, shas), **S)
    js = to_json(S); legs = R.to_csv(index=False); bridge = B.to_csv(index=False)   # 全部算好、序列化成功後才碰檔案系統
    try:
        write_outputs(a.out, [('legs_deidentified.csv', legs), ('bridge_deidentified.csv', bridge), ('summary.json', js)])
    except Reject as e:
        print(f'拒絕：{e}', file=sys.stderr); sys.exit(e.code)
    except OSError as e:
        print(f'寫檔失敗：{e}（未留下輸出目錄）', file=sys.stderr); sys.exit(4)
    print(js)


# ---------- 9. 合成資料與反例測試 ----------
def synthetic():
    """固定合成資料（不是實盤）：回傳 fills、orders、local、klines 四個 DataFrame。"""
    T0 = 1_762_000_000_000 // H * H; ts = lambda ms: pd.Timestamp(ms, unit='ms').strftime('%Y-%m-%d %H:%M:%S')
    F = []; O = []; nid = [0]

    def order(oid, create, side, ps, otype, fills, stop=0, rp=0.0):
        O.append(dict(orderId=oid, time=create, side=side, positionSide=ps, origType=otype, type='MARKET', stopPrice=stop, status='FILLED'))
        for t, p, qq in fills:
            nid[0] += 1; F.append(dict(time=t, id=nid[0], orderId=oid, side=side, positionSide=ps, price=p, qty=qq, commission=round(p * qq * 0.0005, 6), commissionAsset='USDT', realizedPnl=rp / len(fills)))
    # 交易 1：多，進場部分成交 2 筆、出場 1 筆，相容配對
    order(101, T0 + 10_800, 'BUY', 'LONG', 'MARKET', [(T0 + 10_800, 2000.0, 0.5), (T0 + 10_800, 2001.0, 0.5)])
    order(102, T0 + 5 * H + 10_700, 'SELL', 'LONG', 'MARKET', [(T0 + 5 * H + 10_700, 2020.0, 1.0)], rp=1.0 * (2020.0 - 2000.5))
    # 交易 2：空，SafeNet 預掛，觸發成交
    order(201, T0 + 7 * H + 10_000, 'SELL', 'SHORT', 'MARKET', [(T0 + 7 * H + 10_000, 2010.0, 1.0)])
    order(202, T0 + 7 * H + 10_500, 'BUY', 'SHORT', 'STOP_MARKET', [(T0 + 9 * H + 1_234_000, 2091.0, 1.0)], stop=2090.0, rp=-81.0)
    # 交易 3：同刻兩張同向市價單（重試）→ 模糊
    order(301, T0 + 12 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 12 * H + 10_500, 2050.0, 1.0)])
    order(302, T0 + 12 * H + 11_000, 'BUY', 'LONG', 'MARKET', [(T0 + 12 * H + 11_000, 2050.0, 1.0)])
    # 交易 4：窗口內只有一張同向市價單，但價格差 >1bp（手動單）→ 不配對
    order(401, T0 + 15 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 15 * H + 10_500, 2100.0, 1.0)])
    # 交易 5：缺 origType；交易 6：成交有、訂單列缺
    order(501, T0 + 18 * H + 10_500, 'BUY', 'LONG', '', [(T0 + 18 * H + 10_500, 2200.0, 1.0)])
    nid[0] += 1; F.append(dict(time=T0 + 20 * H + 10_500, id=nid[0], orderId=601, side='BUY', positionSide='LONG', price=2300.0, qty=1.0, commission=1.0, commissionAsset='USDT', realizedPnl=0))
    # 交易 7、8：同一 T 兩筆本機紀錄、只有一張單 → 全局一對一不配對
    order(701, T0 + 22 * H + 10_500, 'SELL', 'SHORT', 'MARKET', [(T0 + 22 * H + 10_500, 2400.0, 1.0)])
    # 交易 9：進出場數量不一致
    order(801, T0 + 24 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 24 * H + 10_500, 2500.0, 1.0)])
    order(802, T0 + 26 * H + 10_500, 'SELL', 'LONG', 'MARKET', [(T0 + 26 * H + 10_500, 2510.0, 0.9)])
    # 交易 10：同一 orderId 兩個 positionSide
    order(901, T0 + 28 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 28 * H + 10_500, 2600.0, 1.0)])
    nid[0] += 1; F.append(dict(time=T0 + 28 * H + 10_600, id=nid[0], orderId=901, side='BUY', positionSide='SHORT', price=2600.0, qty=0.1, commission=0.1, commissionAsset='USDT', realizedPnl=0))
    F.append(dict(F[0]))   # 重複成交列（同 tradeId）
    loc = lambda tin, tout, dr, ep, xp, et, net, sbc: dict(entry_time_utc=ts(tin - H), exit_time_utc=ts(tout - H) if tout else '', direction=dr, entry_price=ep, exit_price=xp, exit_type=et, net_pnl_usd=net, entry_signal_bar_close=sbc)
    local = pd.DataFrame([
        loc(T0, T0 + 5 * H, 'LONG', 2000.5, 2020.0, 'MFE-trail', 17.0, 2000.0),
        loc(T0 + 7 * H, T0 + 9 * H, 'SHORT', 2010.0, 2091.0, 'SafeNet', -83.0, 2010.0),
        loc(T0 + 12 * H, None, 'LONG', 2050.0, '', '', '', 2050.0),
        loc(T0 + 15 * H, None, 'LONG', 2090.0, '', '', '', 2090.0),
        loc(T0 + 18 * H, None, 'LONG', 2200.0, '', '', '', 2200.0),
        loc(T0 + 20 * H, None, 'LONG', 2300.0, '', '', '', 2300.0),
        loc(T0 + 22 * H, None, 'SHORT', 2400.0, '', '', '', 2400.0),
        loc(T0 + 22 * H, None, 'SHORT', 2400.0, '', '', '', 2400.0),
        loc(T0 + 24 * H, T0 + 26 * H, 'LONG', 2500.0, 2510.0, 'MaxHold', 5.0, 2500.0),
        loc(T0 + 28 * H, None, 'LONG', 2600.0, '', '', '', 2600.0),
    ])
    n = 60; om = T0 - 10 * H + np.arange(n) * H; cl = np.full(n, 2000.0)
    for t, c in ((T0, 2000.0), (T0 + 5 * H, 2021.0), (T0 + 7 * H, 2010.0), (T0 + 24 * H, 2500.0), (T0 + 26 * H, 2512.0)):
        cl[(om + H) == t] = c
    return pd.DataFrame(F), pd.DataFrame(O), local, pd.DataFrame(dict(open_time=om, close=cl))


def selftest():
    import tempfile
    global _FAIL_AT
    fills, orders, local, kl = synthetic(); T0 = int(kl.open_time.iloc[10])
    R, B, S = analyze(fills, orders, local, kl, 'open'); g = R.set_index(['序號', '腿']); done = []
    e1 = g.loc[(1, '進場')]; assert e1.狀態 == OK and e1.成交筆 == 2 and e1.建單_T_ms == 10_800 and abs(e1.方向性bp - 2.5) < 1e-9; done.append('部分成交合併')
    x2 = g.loc[(2, '出場')]; assert x2.狀態 == OK and x2.訂單類型 == 'STOP_MARKET' and pd.isna(x2.建單_T_ms) and x2.掛單存續_ms == 2 * H - 10_500 + 1_234_000; done.append('SafeNet 掛單存續分開')
    assert S['SafeNet腿']['n'] == 1 and sum(v['方向性bp']['n'] for v in S['市價腿']) == 5; done.append('市價腿不含 SafeNet')
    assert g.loc[(3, '進場')].原因.startswith('模糊：2'); done.append('重試模糊')
    assert g.loc[(4, '進場')].原因 == '窗口內有同向單但價格與紀錄不一致'; done.append('同窗口價格不符不配對')
    assert g.loc[(5, '進場')].狀態 == '未配對' and S['未配對訂單'].get('類型不明（origType 空白）') == 1; done.append('缺 origType')
    assert g.loc[(6, '進場')].狀態 == '未配對' and S['未配對訂單'].get('缺訂單') == 1; done.append('缺訂單不以首筆代建單')
    assert g.loc[(7, '進場')].原因 == g.loc[(8, '進場')].原因 == '模糊：該訂單同時是多條腿的候選'; done.append('全局一對一')
    assert S['橋接']['排除'].get('數量不一致') == 1; done.append('數量不一致不入橋接')
    assert g.loc[(10, '進場')].狀態 == '未配對' and S['未配對訂單'].get('同訂單多方向或多持倉方向') == 1; done.append('多持倉方向衝突')
    assert S['清洗']['重複成交剔除'] == 1; done.append('重複成交剔除')
    bi = B.set_index('序號'); b1 = bi.loc[1]
    assert abs(b1.實際毛損益 - 19.5) < 1e-9 and abs(b1.參考價反事實毛損益 - 21.0) < 1e-9 and abs(b1.價差美元 + 1.5) < 1e-9 and abs(b1.realizedPnl核對差) < 1e-9; done.append('橋接算術與 realizedPnl 核對')
    assert bi.loc[2].出場參考 == '停損價' and abs(bi.loc[2].價差美元 + 1.0) < 1e-9; done.append('SafeNet 以停損價為參考（非收盤價反事實）')
    okr = R[R.狀態 == OK]; sgn = np.where(((okr.腿 == '進場') & (okr.方向 == 'LONG')) | ((okr.腿 == '出場') & (okr.方向 == 'SHORT')), 1.0, -1.0)
    assert np.allclose(sgn * (okr.實際名目美元 / okr.參考名目美元 - 1) * 1e4, okr.方向性bp) and np.allclose(okr.參考名目美元 * okr.方向性bp / 1e4, okr.價差成本美元)
    assert np.allclose(B.價差美元, -(B.進場價差成本美元 + B.出場價差成本美元)) and np.allclose(B.交易所淨_不含funding, B.實際毛損益 - B.手續費USDT); done.append('去識別輸出可重算')
    assert (B.funding == '未取得').all() and B.缺失成本.str.contains('funding 未取得').all() and '不是 funding' in S['說明']['殘差']; done.append('funding 未取得、殘差不稱 funding')
    U = S['橋接']['USDT淨損益']; assert U['已驗證筆'] == 2 and abs(U['手續費USDT']['合計'] - B.手續費USDT.sum()) < 1e-9; done.append('全部 USDT 時合計')
    # JSON：tuple 鍵必須報錯，摘要可往返
    try: to_json({('進場', 'LONG'): 1}); raise AssertionError('tuple 鍵應報錯')
    except TypeError: pass
    try: json.dumps({('進場', 'LONG'): 1}, default=str); raise AssertionError('舊版寫法本來就會失敗')
    except TypeError: pass
    assert json.loads(to_json(S))['相容配對腿'] == S['相容配對腿'] and 'NaN' not in to_json(S); done.append('JSON 字串鍵、無 NaN、可往返')
    # 手續費：非 USDT、混合、空白、無效、資產空白
    fee2 = round(2010 * 0.0005, 6) + round(2091 * 0.0005, 6)
    for desc, mut, st in (('非USDT', lambda f: f.assign(commissionAsset=np.where(f.orderId == 102, 'BNB', f.commissionAsset)), '非USDT（BNB）'),
                          ('混合資產', lambda f: f.assign(commissionAsset=np.where((f.orderId == 101) & (f.price == 2001.0), 'BNB', f.commissionAsset)), '混合資產（BNB,USDT）'),
                          ('手續費空白', lambda f: f.assign(commission=np.where((f.orderId == 101) & (f.price == 2001.0), '', f.commission.astype(str))), '缺值或無效'),
                          ('手續費無效', lambda f: f.assign(commission=np.where((f.orderId == 102), 'abc', f.commission.astype(str))), '缺值或無效'),
                          ('資產空白', lambda f: f.assign(commissionAsset=np.where(f.orderId == 102, '', f.commissionAsset)), '缺值或無效')):
        Rx, Bx, Sx = analyze(mut(fills.copy()), orders, local, kl, 'open'); bx = Bx.set_index('序號').loc[1]
        leg = Rx.set_index(['序號', '腿']).loc[(1, '進場' if desc in ('混合資產', '手續費空白') else '出場')]
        assert leg.手續費狀態 == st and pd.isna(leg.手續費USDT), (desc, leg.手續費狀態)
        assert bx.淨損益狀態 == '未驗證' and pd.isna(bx.手續費USDT) and pd.isna(bx.交易所淨_不含funding) and pd.isna(bx.未拆分殘差) and '手續費未驗證' in bx.缺失成本, desc
        assert np.isfinite(bx.價差美元), desc   # 價格橋接仍列
        Ux = Sx['橋接']['USDT淨損益']; assert Ux['已驗證筆'] == 1 and Ux['未驗證筆'] == 1 and abs(Ux['手續費USDT']['合計'] - fee2) < 1e-9, desc   # 未驗證筆不被當成 0 加進合計
        assert Sx['橋接']['價格橋接']['價差美元']['筆'] == 2, desc
    done.append('非 USDT／混合／空白／無效／資產空白手續費標未驗證且不入合計')
    f3 = fills.copy(); f3['realizedPnl'] = np.where(f3.orderId == 102, np.nan, f3.realizedPnl)
    _, B3, S3 = analyze(f3, orders, local, kl, 'open'); b3 = B3.set_index('序號').loc[1]
    assert pd.isna(b3.realizedPnl核對差) and 'realizedPnl 缺值' in b3.缺失成本 and S3['橋接']['realizedPnl缺值筆'] == 1; done.append('realizedPnl 缺值標示')
    # K 線停止條件、bar-time、兩種 K 線格式
    for bad, msg in ((kl.assign(open_time=kl.open_time + 60_000), '整點'), (pd.concat([kl, kl.iloc[[3]]]), '重複'), (kl[(kl.open_time + H) != T0 + 5 * H], 'T 不在'), (kl.drop(columns='open_time'), '缺'), (kl.drop(columns='close'), '缺 close')):
        try: analyze(fills, orders, local, bad, 'open'); raise AssertionError('應停止：' + msg)
        except Stop as e: assert msg in str(e), (msg, str(e))
    try: analyze(fills, orders, local, kl, 'x'); raise AssertionError('bar-time 必填')
    except Stop: pass
    kl2 = pd.DataFrame(dict(datetime=[(pd.Timestamp(int(v), unit='ms') + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') for v in kl.open_time], close=kl.close))
    R2, B2, _ = analyze(fills, orders, local, kl2, 'open'); assert np.allclose(R2.方向性bp.fillna(-9e9), R.方向性bp.fillna(-9e9)) and np.allclose(B2.價差美元, B.價差美元)
    done.append('K 線停止條件、bar-time 必填、兩種格式一致')
    # 輸入：URL 與 scheme 在任何讀取前拒絕；只收一般檔案
    tmp = tempfile.mkdtemp(); real_read = pd.read_csv; real_open = os.open
    def boom(*a, **k): raise AssertionError('不應走到讀取')
    pd.read_csv = boom; os.open = boom
    try:
        for u in ('https://example.invalid/a.csv', 'http://127.0.0.1:9/a.csv', 'file:///etc/hostname', 's3://bucket/key.csv', 'ftp://x/a.csv', 'HTTPS://X/A', '-', ''):
            try: read_local(u); raise AssertionError('應拒絕 ' + u)
            except Reject as e: assert e.code == 2
        try: read_local(os.path.join(tmp, 'nope.csv')); raise AssertionError('應拒絕不存在')
        except Reject: pass
        try: read_local(tmp); raise AssertionError('應拒絕目錄')
        except Reject: pass
    finally:
        pd.read_csv = real_read; os.open = real_open
    p = os.path.join(tmp, 'a.csv'); open(p, 'w').write('x,y\n1,2\n')
    df, sh, nb = read_local(p); assert df.shape == (1, 2) and sh == hashlib.sha256(b'x,y\n1,2\n').hexdigest() and nb == 8
    if POSIX:
        os.symlink(p, os.path.join(tmp, 'l.csv'))
        try: read_local(os.path.join(tmp, 'l.csv')); raise AssertionError('應拒絕符號連結輸入')
        except Reject: pass
    done.append('URL／scheme／標準輸入／不存在／目錄／符號連結輸入在讀取前拒絕')
    # 輸出：拒 git、既有、符號連結；寫一半失敗不留目錄；不覆寫
    gd = os.path.join(tmp, 'g'); os.makedirs(os.path.join(gd, '.git'))
    for bad in (os.path.join(gd, 'out'), tmp):
        try: write_outputs(bad, [('a', 'x')]); raise AssertionError('應拒絕 ' + bad)
        except Reject as e: assert e.code == 4
    if POSIX:
        os.symlink(tmp, os.path.join(tmp, 'ln'))
        try: write_outputs(os.path.join(tmp, 'ln', 'x'), [('a', 'x')]); raise AssertionError('應拒絕符號連結')
        except Reject: pass
    files = [('legs_deidentified.csv', R.to_csv(index=False)), ('bridge_deidentified.csv', B.to_csv(index=False)), ('summary.json', to_json(S))]
    for k in (0, 1, 2):
        _FAIL_AT = k; out = os.path.join(tmp, f'fail{k}')
        try: write_outputs(out, files); raise AssertionError('應失敗')
        except OSError: pass
        finally: _FAIL_AT = None
        assert not os.path.lexists(out) and not os.path.lexists(out + '.incomplete'), k
    done.append('寫到第 1／2／3 檔失敗都不留下目錄')
    out = write_outputs(os.path.join(tmp, 'ok', 'run'), files)
    assert sorted(os.listdir(out)) == sorted(n for n, _ in files)
    hdr = set(R.columns) | set(B.columns)
    for bad in ('orderId', 'tradeId', 'id', 'price', 'qty', 'vwap', 'stopPrice', 'avgPrice'): assert bad not in hdr, bad
    txt = ''.join(open(os.path.join(out, n), encoding='utf-8').read() for n, _ in files); assert '2001.0' not in txt
    if POSIX: assert all((os.stat(os.path.join(out, n)).st_mode & 0o077) == 0 for n, _ in files) and (os.stat(out).st_mode & 0o077) == 0
    before = {n: open(os.path.join(out, n), 'rb').read() for n, _ in files}
    try: write_outputs(out, files); raise AssertionError('應拒絕既有')
    except Reject: pass
    assert before == {n: open(os.path.join(out, n), 'rb').read() for n, _ in files}
    done.append('輸出無識別碼價格數量、700／600、拒 git／既有／符號連結、不覆寫')
    assert not any(m in sys.modules for m in ('requests', 'urllib.request', 'http.client', 'binance', 'executor', 'strategy', 'main_eth', 'paths')); done.append('未載入網路請求或專案模組')
    # subprocess 模組由 pandas 匯入時一併載入（本檔不匯入、不呼叫）；用守衛證明整段分析與寫檔沒有開子程序
    sp = sys.modules.get('subprocess'); saved_popen = sp.Popen if sp else None; saved_system = os.system
    if sp: sp.Popen = boom
    os.system = boom
    try:
        Rg, Bg, Sg = analyze(fills, orders, local, kl, 'open'); write_outputs(os.path.join(tmp, 'guard'), [('summary.json', to_json(Sg))])
    finally:
        if sp: sp.Popen = saved_popen
        os.system = saved_system
    done.append('分析與寫檔期間未開子程序（Popen／os.system 守衛）')
    print('selftest ok（%s，%d 項）：%s' % ('POSIX' if POSIX else '非 POSIX：權限與符號連結項 SKIP', len(done), '、'.join(done)))
    print(json.dumps(dict(未配對腿原因=S['未配對腿原因'], 未配對訂單=S['未配對訂單'], 橋接排除=S['橋接']['排除']), ensure_ascii=False))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest': selftest(); return
    ap = argparse.ArgumentParser()
    for k in ('fills', 'orders', 'local', 'klines', 'out'): ap.add_argument('--' + k, required=True)
    ap.add_argument('--klines-source', default='')
    ap.add_argument('--bar-time', required=True, choices=['open', 'close']); run(ap.parse_args())


if __name__ == '__main__':
    main()
