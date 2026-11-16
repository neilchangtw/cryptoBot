#!/usr/bin/env python3
"""V87 成交配對與損益橋接（離線匯總器）。不連網、不讀 .env、不匯入專案模組；只讀使用者指定的本機 CSV。
規則寫死於本檔，見 doc/research_results/v87/v87_plan.md §三、§四（預登記 commit 6b3684a）。

輸入（都是使用者私人目錄的檔案，不進 Git）
  --fills   extract_fills_readonly.py 輸出的 trades.csv（userTrades）
  --orders  同上 orders.csv（allOrders）
  --local   機器人本機 data_live/trades.csv
  --klines  ETHUSDT 1h K 線：open_time（毫秒）或 datetime（UTC+8 開盤字串），以及 close
  --bar-time open|close  本機紀錄時間是 K 線開盤或收盤（必填；2026-11-16 小樣本使用者回報為 open）
  --out     尚不存在、且不在 git 工作樹內的新目錄

輸出（目錄 700、檔案 600；不含 orderId、tradeId、單筆成交價、均價、數量欄、金鑰；含每腿名目美元＝數量×價格，供重算）
  legs_deidentified.csv  逐腿：配對狀態、原因、腿別、方向、訂單類型、建單／首筆／末筆−T 毫秒、掛單存續、成交筆、方向性 bp、價差成本美元、實際與參考名目美元、手續費
  bridge_deidentified.csv 逐筆交易：實際毛損益、收盤價反事實毛損益、價差美元與進出場分項、手續費分項、funding（未取得）、交易所淨（不含 funding）、本機淨、未拆分殘差、缺失成本
  summary.json           上述彙總；同時印到 stdout，可貼給研究端

用法：
  python3 -I fill_bridge.py --fills F --orders O --local L --klines K --bar-time open --out ~/fill_bridge_out/run1
  python3 -I fill_bridge.py --selftest
"""
import argparse, json, os, sys
import numpy as np, pandas as pd

H = 3600_000; WIN_MKT = 30 * 60_000; WIN_SN = H; TOL_BP = 1.0; QSTEP = 0.001
POSIX = os.name == 'posix'; O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
EPOCH = pd.Timestamp('1970-01-01')


class Stop(Exception):
    """資料前提不成立：停止，不輸出統計。"""


def to_ms(x):
    return (x - EPOCH) // pd.Timedelta(milliseconds=1)


def num(s):
    return pd.to_numeric(s, errors='coerce')


def blank(v):
    return v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == '' or str(v).lower() == 'nan'


# ---------- 1. 逐筆成交依 orderId 合併 ----------
def aggregate_fills(f):
    f = f.copy(); q = {}
    n0 = len(f); f = f.drop_duplicates('id'); q['重複成交剔除'] = n0 - len(f)
    for c in ('price', 'qty', 'time', 'commission', 'realizedPnl'):
        f[c] = num(f[c]) if c in f.columns else np.nan
    bad = f.price.isna() | f.qty.isna() | f.time.isna() | (f.price <= 0) | (f.qty <= 0)
    q['零或缺價量時間剔除'] = int(bad.sum()); f = f[~bad]
    if 'commissionAsset' not in f.columns: f['commissionAsset'] = ''
    f['pq'] = f.price * f.qty
    g = f.groupby('orderId').agg(qty=('qty', 'sum'), pq=('pq', 'sum'), first_ms=('time', 'min'), last_ms=('time', 'max'), n=('id', 'count'),
                                 fee=('commission', 'sum'), rpnl=('realizedPnl', 'sum'), n_side=('side', 'nunique'), n_ps=('positionSide', 'nunique'),
                                 side=('side', 'first'), ps=('positionSide', 'first'),
                                 fee_assets=('commissionAsset', lambda s: ','.join(sorted(set(map(str, s))))))
    g['vwap'] = g.pq / g.qty; g['fee_ok'] = g.fee_assets == 'USDT'
    q['有效成交'] = len(f); q['合併後訂單'] = len(g)
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
    return pd.DataFrame(L)


def load_klines(kl):
    kl = kl.copy()
    if 'open_time' in kl.columns: kl['open_ms'] = num(kl.open_time).astype('int64')
    elif 'datetime' in kl.columns: kl['open_ms'] = to_ms(pd.to_datetime(kl['datetime']) - pd.Timedelta(hours=8)).astype('int64')
    else: raise Stop('K 線缺 open_time 或 datetime')
    kl['close_ms'] = kl.open_ms + H; kl['close'] = num(kl['close'])
    if not (kl.close_ms % H == 0).all(): raise Stop('K 線收盤時刻不在整點')
    if kl.close_ms.duplicated().any(): raise Stop('K 線收盤時刻重複')
    if kl.close.isna().any() or (kl.close <= 0).any(): raise Stop('K 線收盤價缺值或非正')
    return kl.set_index('close_ms').close


def bp_dir(side, px, ref):
    """方向性 bp，正值＝對策略不利。"""
    x = (px / ref - 1) * 1e4; return x if side == 'BUY' else -x


# ---------- 4. 候選、全局一對一 ----------
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
        if len(cs) == 1 and used[cs[0]] == 1: res[li] = ('已配對', cs[0], '')
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
        out[k] = out.get(k, 0) + 1
    return out


# ---------- 5. 逐腿與損益橋接 ----------
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
                     手續費=float(o.fee), 手續費已驗證=bool(o.fee_ok), _oid=oid, _qty=float(o.qty), _vwap=float(o.vwap), _ref=ref, _rpnl=float(o.rpnl))
        rows.append(r)
    R = pd.DataFrame(rows)
    # 逐筆交易橋接
    B = []; flags = {}
    for sn, g in R.groupby('序號'):
        lr = local.reset_index(drop=True).iloc[int(sn) - 1]; e = g[g.腿 == '進場']; x = g[g.腿 == '出場']
        why = ''
        if len(x) == 0: why = '未平倉或無出場紀錄'
        elif not ((e.狀態 == '已配對').all() and (x.狀態 == '已配對').all()) or len(e) == 0: why = '有腿未配對'
        else:
            e = e.iloc[0]; x = x.iloc[0]
            if abs(e._qty - x._qty) > QSTEP / 2: why = '數量不一致'
            sbc = num(pd.Series([lr.get('entry_signal_bar_close')]))[0]
            if not why and np.isfinite(sbc) and abs(bp_dir('BUY', sbc, e._ref)) > TOL_BP: why = '收盤價不一致'
        if why:
            flags[why] = flags.get(why, 0) + 1; continue
        s = 1.0 if e.方向 == 'LONG' else -1.0; qq = e._qty
        act = s * qq * (x._vwap - e._vwap); cf = s * qq * (x._ref - e._ref) if np.isfinite(x._ref) else np.nan
        fee = e.手續費 + x.手續費; exn = act - fee; ln = float(num(pd.Series([lr.get('net_pnl_usd')]))[0])
        miss = ['funding 未取得'] + ([] if (e.手續費已驗證 and x.手續費已驗證) else ['手續費非 USDT 未換算']) + ([] if np.isfinite(cf) else ['出場參考價缺'])
        B.append(dict(序號=int(sn), 月份=x.月份, 方向=e.方向, 出場類型=x.出場類型, 實際毛損益=act, 收盤價反事實毛損益=cf, 價差美元=act - cf if np.isfinite(cf) else np.nan,
                      進場價差成本美元=e.價差成本美元, 出場價差成本美元=x.價差成本美元, 進場手續費=e.手續費, 出場手續費=x.手續費,
                      手續費=fee, 手續費已驗證=bool(e.手續費已驗證 and x.手續費已驗證), funding='未取得', 交易所淨_不含funding=exn, 本機淨=ln, 未拆分殘差=ln - exn,
                      realizedPnl核對差=float(x._rpnl - act), 缺失成本='；'.join(miss)))
    B = pd.DataFrame(B)
    pub = [c for c in R.columns if not c.startswith('_')]
    return R[pub], B, summarize(R, B, q, m, res, cand, legs, flags, bar_time)


def d(x):
    x = pd.Series(x, dtype=float).dropna()
    return dict(n=int(len(x)), 中位=float(x.median()) if len(x) else None, p90=float(x.quantile(.9)) if len(x) else None, 最小=float(x.min()) if len(x) else None, 最大=float(x.max()) if len(x) else None)


def summarize(R, B, q, m, res, cand, legs, flags, bar_time):
    ok = R[R.狀態 == '已配對']
    if '訂單類型' not in ok.columns: ok = ok.assign(訂單類型=pd.Series(dtype=object))
    mk = ok[ok.訂單類型 == 'MARKET']; sn = ok[ok.訂單類型 == 'STOP_MARKET']
    S = dict(清洗=q, 腿數=len(R), 已配對腿=len(ok), 未配對腿=int((R.狀態 != '已配對').sum()), 未配對比例=round(float((R.狀態 != '已配對').mean()), 4) if len(R) else None,
             未配對腿原因=R[R.狀態 != '已配對'].原因.value_counts().to_dict(), 未配對訂單=unmatched_orders(m, res, cand),
             市價腿={k: {'建單−T_ms': d(g.建單_T_ms), '末筆−T_ms': d(g.末筆_T_ms), '建單到末筆_ms（毫秒解析度）': d(g.建單到末筆_ms), '方向性bp': d(g.方向性bp), '價差成本美元': d(g.價差成本美元)}
                   for k, g in mk.groupby(['腿', '方向'])} if len(mk) else {},
             SafeNet腿=dict(n=len(sn), 掛單存續_ms=d(sn.掛單存續_ms), 對停損價bp=d(sn.方向性bp), 說明='掛單存續不是撮合延遲，不併入市價腿'),
             橋接=dict(交易筆=len(B), 排除=flags,
                     合計=dict(實際毛損益=float(B.實際毛損益.sum()), 收盤價反事實毛損益=float(B.收盤價反事實毛損益.sum()), 價差美元=float(B.價差美元.sum()),
                             手續費=float(B.手續費.sum()), 交易所淨_不含funding=float(B.交易所淨_不含funding.sum()), 本機淨=float(B.本機淨.sum()), 未拆分殘差=float(B.未拆分殘差.sum()),
                             realizedPnl核對差_最大絕對=float(B.realizedPnl核對差.abs().max())) if len(B) else {},
                     逐月={k: dict(筆=len(g), 價差美元=float(g.價差美元.sum()), 手續費=float(g.手續費.sum()), 交易所淨_不含funding=float(g.交易所淨_不含funding.sum()), 本機淨=float(g.本機淨.sum()))
                         for k, g in B.groupby('月份')} if len(B) else {},
                     最差價差事件=B.nsmallest(3, '價差美元')[['序號', '月份', '方向', '出場類型', '價差美元']].to_dict('records') if len(B) else [],
                     手續費未驗證筆=int((~B.手續費已驗證).sum()) if len(B) else 0),
             未取得=['funding（/fapi/v1/income 不在允許清單，未查詢）', '漏訊號與拒單（需 signal.log／alerts.log，本工具不讀）'],
             說明=dict(bar_time=bar_time, 價差正負='方向性 bp 正值＝對策略不利；逐腿價差成本美元正值＝成本；橋接的價差美元＝實際−反事實＝−(進場＋出場價差成本美元)', 重算='逐腿 方向性bp＝(實際名目/參考名目−1)×1e4（買）或其負值（賣）；價差成本美元＝參考名目×bp/1e4', 價格容差='1bp 是看過使用者回報（0.0bp）後訂的工程容差，不是事前依據',
                     毫秒='0 ms 只表示交易所時間戳在同一毫秒內', 樣本='已曝光 2026 樣本，不外推到 2021～2026'))
    return S


# ---------- 輸出目錄 ----------
def inside_git(path):
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')): return True
        n = os.path.dirname(p)
        if n == p: return False
        p = n


def no_symlink_components(path):
    p = os.path.abspath(path); parts = []
    while True:
        parts.append(p); n = os.path.dirname(p)
        if n == p: break
        p = n
    for x in parts:
        if os.path.islink(x): raise SystemExit(f'拒絕：路徑含符號連結（{x}）')


def prep_out(out):
    no_symlink_components(out)
    if inside_git(out): raise SystemExit(f'拒絕：輸出目錄位於 git 工作樹內（{out}）')
    if os.path.lexists(out): raise SystemExit(f'拒絕：輸出目錄已存在（{out}），不覆寫')
    os.makedirs(out, mode=0o700)
    if POSIX: os.chmod(out, 0o700)


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f: f.write(text)
    if POSIX: os.chmod(path, 0o600)


def run(a):
    try:
        R, B, S = analyze(pd.read_csv(a.fills), pd.read_csv(a.orders), pd.read_csv(a.local), pd.read_csv(a.klines), a.bar_time)
    except Stop as e:
        print(f'停止：{e}（未輸出統計）', file=sys.stderr); sys.exit(3)
    prep_out(a.out)
    write_private(os.path.join(a.out, 'legs_deidentified.csv'), R.to_csv(index=False))
    write_private(os.path.join(a.out, 'bridge_deidentified.csv'), B.to_csv(index=False))
    js = json.dumps(S, ensure_ascii=False, indent=1, default=str); write_private(os.path.join(a.out, 'summary.json'), js); print(js)


# ---------- 反例測試（合成資料） ----------
def selftest():
    import tempfile
    T0 = 1_762_000_000_000 // H * H; ts = lambda ms: pd.Timestamp(ms, unit='ms').strftime('%Y-%m-%d %H:%M:%S')
    F = []; O = []; nid = [0]

    def order(oid, create, side, ps, otype, fills, stop=0, fee_asset='USDT', rp=0.0):
        O.append(dict(orderId=oid, time=create, side=side, positionSide=ps, origType=otype, type='MARKET', stopPrice=stop, status='FILLED'))
        for t, p, qq in fills:
            nid[0] += 1; F.append(dict(time=t, id=nid[0], orderId=oid, side=side, positionSide=ps, price=p, qty=qq, commission=round(p * qq * 0.0005, 6), commissionAsset=fee_asset, realizedPnl=rp / len(fills)))
    # 交易 1：多，進場部分成交 2 筆、出場 1 筆，乾淨配對
    order(101, T0 + 10_800, 'BUY', 'LONG', 'MARKET', [(T0 + 10_800, 2000.0, 0.5), (T0 + 10_800, 2001.0, 0.5)])
    order(102, T0 + 5 * H + 10_700, 'SELL', 'LONG', 'MARKET', [(T0 + 5 * H + 10_700, 2020.0, 1.0)], rp=1.0 * (2020.0 - 2000.5))
    # 交易 2：空，SafeNet 預掛 4 小時前建單，觸發成交
    order(201, T0 + 7 * H + 10_000, 'SELL', 'SHORT', 'MARKET', [(T0 + 7 * H + 10_000, 2010.0, 1.0)])
    order(202, T0 + 7 * H + 10_500, 'BUY', 'SHORT', 'STOP_MARKET', [(T0 + 9 * H + 1_234_000, 2091.0, 1.0)], stop=2090.0)
    # 交易 3：同刻有兩張同向市價單（重試）→ 模糊
    order(301, T0 + 12 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 12 * H + 10_500, 2050.0, 1.0)])
    order(302, T0 + 12 * H + 11_000, 'BUY', 'LONG', 'MARKET', [(T0 + 12 * H + 11_000, 2050.0, 1.0)])
    # 交易 4：窗口內只有一張同向市價單，但價格與紀錄差 >1bp（手動單）→ 不配對
    order(401, T0 + 15 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 15 * H + 10_500, 2100.0, 1.0)])
    # 交易 5：進場訂單缺 origType；交易 6：成交有、訂單列缺（不得用首筆時間代替建單時間）
    order(501, T0 + 18 * H + 10_500, 'BUY', 'LONG', '', [(T0 + 18 * H + 10_500, 2200.0, 1.0)])
    nid[0] += 1; F.append(dict(time=T0 + 20 * H + 10_500, id=nid[0], orderId=601, side='BUY', positionSide='LONG', price=2300.0, qty=1.0, commission=1.0, commissionAsset='USDT', realizedPnl=0))
    # 交易 7：兩筆本機紀錄落在同一 T，只有一張單 → 該訂單同時是兩腿候選 → 都不配對
    order(701, T0 + 22 * H + 10_500, 'SELL', 'SHORT', 'MARKET', [(T0 + 22 * H + 10_500, 2400.0, 1.0)])
    # 交易 8：進出場數量不一致 → 不入橋接
    order(801, T0 + 24 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 24 * H + 10_500, 2500.0, 1.0)])
    order(802, T0 + 26 * H + 10_500, 'SELL', 'LONG', 'MARKET', [(T0 + 26 * H + 10_500, 2510.0, 0.9)])
    # 交易 9：同一 orderId 成交出現兩個 positionSide → 衝突
    order(901, T0 + 28 * H + 10_500, 'BUY', 'LONG', 'MARKET', [(T0 + 28 * H + 10_500, 2600.0, 1.0)])
    nid[0] += 1; F.append(dict(time=T0 + 28 * H + 10_600, id=nid[0], orderId=901, side='BUY', positionSide='SHORT', price=2600.0, qty=0.1, commission=0.1, commissionAsset='USDT', realizedPnl=0))
    # 重複成交列（同 tradeId）
    F.append(dict(F[0]))
    fills = pd.DataFrame(F); orders = pd.DataFrame(O)
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
    kl = pd.DataFrame(dict(open_time=om, close=cl))
    R, B, S = analyze(fills, orders, local, kl, 'open'); g = R.set_index(['序號', '腿'])
    e1 = g.loc[(1, '進場')]; assert e1.狀態 == '已配對' and e1.成交筆 == 2 and e1.建單_T_ms == 10_800 and abs(e1.方向性bp - 2.5) < 1e-9, '部分成交合併'
    x2 = g.loc[(2, '出場')]; assert x2.狀態 == '已配對' and x2.訂單類型 == 'STOP_MARKET' and pd.isna(x2.建單_T_ms) and x2.掛單存續_ms == 2 * H - 10_500 + 1_234_000, 'SafeNet 存續不入市價延遲'
    assert S['SafeNet腿']['n'] == 1 and sum(v['方向性bp']['n'] for v in S['市價腿'].values()) == 5, '市價腿統計不含 SafeNet'
    assert g.loc[(3, '進場')].原因.startswith('模糊：2'), '重試兩張 → 模糊'
    assert g.loc[(4, '進場')].原因 == '窗口內有同向單但價格與紀錄不一致', '只同方向同窗口不算候選'
    assert g.loc[(5, '進場')].狀態 == '未配對' and S['未配對訂單'].get('類型不明（origType 空白）') == 1, '缺 origType'
    assert g.loc[(6, '進場')].狀態 == '未配對' and S['未配對訂單'].get('缺訂單') == 1, '缺訂單不以首筆時間代替'
    assert g.loc[(7, '進場')].原因 == '模糊：該訂單同時是多條腿的候選' and g.loc[(8, '進場')].原因 == '模糊：該訂單同時是多條腿的候選', '全局一對一'
    assert S['橋接']['排除'].get('數量不一致') == 1, '數量不一致排除'
    assert g.loc[(10, '進場')].狀態 == '未配對' and S['未配對訂單'].get('同訂單多方向或多持倉方向') == 1, '多持倉方向衝突'
    assert S['清洗']['重複成交剔除'] == 1
    b1 = B.set_index('序號').loc[1]; assert abs(b1.實際毛損益 - 19.5) < 1e-9 and abs(b1.收盤價反事實毛損益 - 21.0) < 1e-9 and abs(b1.價差美元 + 1.5) < 1e-9, '橋接算術'
    assert abs(b1.realizedPnl核對差) < 1e-9 and abs(b1.交易所淨_不含funding - (19.5 - b1.手續費)) < 1e-9
    b2 = B.set_index('序號').loc[2]; assert abs(b2.價差美元 - (-1.0)) < 1e-9, 'SafeNet 以停損價為參考'
    # 去識別輸出可獨立重算：bp 由兩個名目重算、價差成本由參考名目×bp、橋接價差＝−(進場＋出場成本)、淨＝毛−費
    okr = R[R.狀態 == '已配對']; sgn = np.where(((okr.腿 == '進場') & (okr.方向 == 'LONG')) | ((okr.腿 == '出場') & (okr.方向 == 'SHORT')), 1.0, -1.0)
    assert np.allclose(sgn * (okr.實際名目美元 / okr.參考名目美元 - 1) * 1e4, okr.方向性bp) and np.allclose(okr.參考名目美元 * okr.方向性bp / 1e4, okr.價差成本美元), '逐腿重算'
    assert np.allclose(B.價差美元, -(B.進場價差成本美元 + B.出場價差成本美元)) and np.allclose(B.交易所淨_不含funding, B.實際毛損益 - B.進場手續費 - B.出場手續費), '橋接重算'
    assert (B.funding == '未取得').all() and B.缺失成本.str.contains('funding 未取得').all(), '缺失成本標示'
    assert set(B.序號) == {1, 2}
    # 手續費非 USDT → 標未驗證
    f2 = fills.copy(); f2.loc[f2.orderId == 102, 'commissionAsset'] = 'BNB'; _, B2, S2 = analyze(f2, orders, local, kl, 'open'); assert S2['橋接']['手續費未驗證筆'] == 1
    # K 線停止條件
    for bad, msg in ((kl.assign(open_time=kl.open_time + 60_000), '整點'), (pd.concat([kl, kl.iloc[[3]]]), '重複'), (kl[(kl.open_time + H) != T0 + 5 * H], 'T 不在'), (kl.drop(columns='open_time'), '缺')):
        try: analyze(fills, orders, local, bad, 'open'); raise AssertionError('應停止：' + msg)
        except Stop as e: assert msg in str(e), (msg, str(e))
    try: analyze(fills, orders, local, kl, 'x'); raise AssertionError('bar-time 必填')
    except Stop: pass
    # datetime 格式 K 線（pandas 3 解析度）與 open_time 結果一致
    kl2 = pd.DataFrame(dict(datetime=[(pd.Timestamp(int(v), unit='ms') + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') for v in kl.open_time], close=kl.close))
    R2, B2b, _ = analyze(fills, orders, local, kl2, 'open'); assert np.allclose(R2.方向性bp.fillna(-9e9), R.方向性bp.fillna(-9e9)) and np.allclose(B2b.價差美元, B.價差美元)
    # 輸出：不含識別碼、價格、數量；600；拒 git、拒既有、拒符號連結
    tmp = tempfile.mkdtemp(); gd = os.path.join(tmp, 'g'); os.makedirs(os.path.join(gd, '.git'))
    for bad in (os.path.join(gd, 'out'), tmp):
        try: prep_out(bad); raise AssertionError('應拒絕 ' + bad)
        except SystemExit: pass
    if POSIX:
        os.symlink(tmp, os.path.join(tmp, 'ln'))
        try: prep_out(os.path.join(tmp, 'ln', 'x')); raise AssertionError('應拒絕符號連結')
        except SystemExit: pass
    out = os.path.join(tmp, 'ok'); prep_out(out)
    write_private(os.path.join(out, 'legs.csv'), R.to_csv(index=False)); write_private(os.path.join(out, 'bridge.csv'), B.to_csv(index=False))
    txt = open(os.path.join(out, 'legs.csv'), encoding='utf-8').read() + open(os.path.join(out, 'bridge.csv'), encoding='utf-8').read()
    hdr = set(R.columns) | set(B.columns)
    for bad in ('orderId', 'tradeId', 'id', 'price', 'qty', 'vwap', 'stopPrice', 'avgPrice'): assert bad not in hdr, bad   # 不輸出識別碼、價格、數量欄
    assert '2001.0' not in txt, '個別成交價不得出現'   # 名目美元＝數量×價格會輸出（供重算）；合成資料數量為 1 時名目等於均價，屬測試資料巧合
    if POSIX: assert (os.stat(os.path.join(out, 'legs.csv')).st_mode & 0o077) == 0 and (os.stat(out).st_mode & 0o077) == 0
    try: write_private(os.path.join(out, 'legs.csv'), 'x'); raise AssertionError('應拒絕覆寫')
    except FileExistsError: pass
    assert not any(m in sys.modules for m in ('requests', 'urllib.request', 'binance', 'executor', 'strategy'))
    print('selftest ok（%s）：部分成交合併、SafeNet 掛單存續分開、重試模糊、同窗口價格不符不配對、缺 origType、缺訂單不以首筆代建單、'
          '全局一對一、數量不一致不入橋接、多持倉方向衝突、重複成交剔除、橋接算術與 realizedPnl 核對、去識別輸出可重算 bp 與美元、缺失成本標示、SafeNet 以停損價為參考、非 USDT 手續費標未驗證、'
          'K 線不整點／重複／缺必要 T／缺欄位即停止、bar-time 必填、datetime 與 open_time 一致、輸出無識別碼價格數量、600、拒 git／既有／符號連結、不覆寫、未載入網路模組'
          % ('POSIX' if POSIX else '非 POSIX：權限與符號連結項 SKIP'))
    print(json.dumps(dict(未配對腿原因=S['未配對腿原因'], 未配對訂單=S['未配對訂單'], 橋接排除=S['橋接']['排除']), ensure_ascii=False))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest': selftest(); return
    ap = argparse.ArgumentParser()
    for k in ('fills', 'orders', 'local', 'klines', 'out'): ap.add_argument('--' + k, required=True)
    ap.add_argument('--bar-time', required=True, choices=['open', 'close']); run(ap.parse_args())


if __name__ == '__main__':
    main()
