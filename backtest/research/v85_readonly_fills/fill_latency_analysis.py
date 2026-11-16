#!/usr/bin/env python3
"""成交時間分析（V85 初版，V86 依使用者審查修訂）。在研究環境、收到提取輸出後才執行；不連網、不讀 .env。
本版只以合成資料自我測試，未用任何實盤資料執行。

方法（事前固定，不因結果更改）
1. 清洗逐筆成交：依 tradeId（id）去重；若有 symbol 欄，只留 ETHUSDT；price 或 qty 不大於 0 的剔除。各項剔除數都報出。
2. 訂單分類只看 origType（缺時用 type）：MARKET＝市價單；STOP_MARKET、TAKE_PROFIT_MARKET＝觸發單；其他（LIMIT 等）＝其他類型，不進延遲統計，只計數。
3. 訊號時間 T 不從訂單時間推定，而取自本機交易紀錄（trades.csv）的進出場時間，再依 --bar-time 換成 K 線收盤：
   open＝紀錄時間＋1 小時；close＝紀錄時間。--bar-time 必須由 extract_fills_readonly.py check 的小樣本結果決定，不得預設。
4. 配對（每一腿）：同 positionSide、同 side。
   - 一般進出場：市價單，訂單建立在 [T, T+30 分]。
   - 出場類型含 SafeNet：觸發單，第一筆成交在 [T−1 小時, T+1 小時]。
   - 0 個候選＝缺資料；超過 1 個＝多重候選，兩者都不配對、不猜。重試或延遲超過 30 分的訂單因此不會被配到錯的訊號，會出現在「未配對訂單」。
   - 沒配到任何腿的訂單（手動交易、限價單、重試）依類型計數。
5. 延遲分段（毫秒）：
   (a) K 線收盤到程式取得資料、(b) 程式醒來到送單：沒有本機時間戳，標「未驗證」，只報 (a)+(b) 合計＝T→訂單建立。
   (c) 交易所：訂單建立→第一筆成交、訂單建立→最後成交。另報 T→最後成交。觸發單不報 (a)(b)。
6. 方向性價差（bp）：市價單對 T 收盤價 P_T；觸發單對 stopPrice。買單 (均價÷參考−1)×1e4，賣單取負號；正值對策略不利。
   K 線檔先驗證：收盤時刻都落在整點、不重複；P_T 缺值另計。
7. 分組報表：進場／出場 × 多／空；出場類型；月份（UTC）；波動（T 時 ATR14% 在樣本內三分位）；另列 T→最後成交與價差各最大、最小 5 腿。
8. 55 筆實盤是已看過的 2026 樣本，只代表其期間與配置，不是樣本外證據；F0／F2 不是成交價上下界。

輸出：只能寫到專案外、尚不存在的目錄（拒絕 git 工作樹與覆寫）；目錄 700、檔案 600。
逐腿檔只含序號、腿別、方向、出場類型、月份、延遲與 bp，不含 orderId、tradeId、價格或數量。

用法：
  python3 -I fill_latency_analysis.py --fills trades.csv --orders orders.csv --local 本機trades.csv \
      --klines ETHUSDT_1h.csv --bar-time open|close --out ~/fill_analysis_out/run1
  python3 -I fill_latency_analysis.py --selftest
K 線檔需有 open_time（毫秒）或 datetime（UTC+8 開盤，專案快取格式），以及 high、low、close。
"""
import argparse, json, os, sys
import numpy as np, pandas as pd

H = 3600_000
TRIG = {'STOP_MARKET', 'TAKE_PROFIT_MARKET'}


def inside_git(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')): return True
        q = os.path.dirname(p)
        if q == p: return False
        p = q


def prep_out(out):
    if inside_git(out): raise SystemExit(f'拒絕：輸出目錄位於 git 工作樹內（{out}）')
    if os.path.exists(out): raise SystemExit(f'拒絕：輸出目錄已存在（{out}），不覆寫；請換新名稱')
    os.makedirs(out, mode=0o700); os.chmod(out, 0o700)


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f: f.write(text)


def clean_fills(t):
    q = {}
    t = t.copy(); n0 = len(t)
    t = t.drop_duplicates(subset=['id']); q['重複成交剔除'] = n0 - len(t)
    if 'symbol' in t.columns:
        n = len(t); t = t[t.symbol == 'ETHUSDT']; q['非ETHUSDT剔除'] = n - len(t)
    else: q['非ETHUSDT剔除'] = '提取時已過濾（無 symbol 欄）'
    for c in ('price', 'qty', 'commission'): t[c] = pd.to_numeric(t[c], errors='coerce')
    t['time'] = pd.to_numeric(t['time'], errors='coerce')
    n = len(t); t = t[(t.price > 0) & (t.qty > 0) & t.time.notna()]; q['零或缺價量時間剔除'] = n - len(t)
    t['time'] = t.time.astype('int64'); q['有效成交'] = len(t)
    return t, q


def order_table(t, o):
    g = t.groupby('orderId').agg(qty=('qty', 'sum'), pq=('price', lambda s: 0), fee=('commission', 'sum'), n_fills=('id', 'size'),
                                 first_ms=('time', 'min'), last_ms=('time', 'max'), side=('side', 'first'), positionSide=('positionSide', 'first'),
                                 n_side=('side', 'nunique'), n_ps=('positionSide', 'nunique'))
    g['vwap'] = (t.assign(pq=t.price * t.qty).groupby('orderId').pq.sum()) / g.qty
    g = g.drop(columns='pq').reset_index()
    o = o.drop_duplicates(subset=['orderId']).copy()
    o['kind_src'] = o['origType'].where(o['origType'].notna() & (o['origType'].astype(str) != ''), o['type'])
    o = o[['orderId', 'time', 'kind_src', 'stopPrice']].rename(columns={'time': 'create_ms'})
    m = g.merge(o, on='orderId', how='left')
    m['kind'] = np.where(m.kind_src.isna(), '缺訂單資料', np.where(m.kind_src == 'MARKET', '市價單', np.where(m.kind_src.isin(TRIG), '觸發單', '其他類型')))
    m['create_ms'] = pd.to_numeric(m.create_ms, errors='coerce'); m['stopPrice'] = pd.to_numeric(m.stopPrice, errors='coerce')
    return m


EPOCH = pd.Timestamp('1970-01-01')


def to_ms(x):
    """UTC 無時區時間 → 毫秒整數；用差值除以 1ms，與 datetime64 的 ns/us/s 解析度無關。"""
    return (x - EPOCH) // pd.Timedelta(milliseconds=1)


def load_klines(kl):
    kl = kl.copy()
    if 'open_time' in kl.columns: kl['open_ms'] = pd.to_numeric(kl.open_time).astype('int64')
    else: kl['open_ms'] = to_ms(pd.to_datetime(kl['datetime']) - pd.Timedelta(hours=8))   # 不依賴 pandas 時間解析度（pandas 3 預設 us）
    kl['close_ms'] = kl.open_ms + H
    chk = dict(K線筆數=len(kl), 收盤都在整點=bool((kl.close_ms % H == 0).all()), 收盤時刻重複=int(kl.close_ms.duplicated().sum()))
    kl = kl.drop_duplicates('close_ms').sort_values('close_ms').set_index('close_ms')
    pc = kl.close.shift(1)
    tr = np.maximum(kl.high - kl.low, np.maximum((kl.high - pc).abs(), (kl.low - pc).abs()))
    kl['atr_pct'] = tr.rolling(14).mean() / kl.close * 100
    return kl, chk


def legs_from_local(local, bar_time):
    L = []
    for i, r in local.reset_index(drop=True).iterrows():
        ps = 'LONG' if str(r.get('direction', '')).upper() in ('LONG', 'L', 'BUY') else 'SHORT'
        for leg, tc in (('進場', 'entry_time_utc'), ('出場', 'exit_time_utc')):
            v = r.get(tc)
            if pd.isna(v) or str(v) == '': continue
            rec = int(to_ms(pd.Timestamp(str(v).replace('Z', ''))))
            T = rec + (H if bar_time == 'open' else 0)
            side = ('BUY' if ps == 'LONG' else 'SELL') if leg == '進場' else ('SELL' if ps == 'LONG' else 'BUY')
            et = str(r.get('exit_type', '')) if leg == '出場' else ''
            L.append(dict(序號=i + 1, 腿=leg, 方向=ps, side=side, 出場類型=et or ('—' if leg == '進場' else ''), T_ms=T))
    return pd.DataFrame(L)


def analyze(fills, orders, local, kl_raw, bar_time):
    assert bar_time in ('open', 'close'), '--bar-time 必須是 open 或 close'
    t, q = clean_fills(fills); m = order_table(t, orders); kl, kchk = load_klines(kl_raw)
    q['同訂單多方向或多持倉方向'] = int(((m.n_side > 1) | (m.n_ps > 1)).sum())
    legs = legs_from_local(local, bar_time); used = set(); rows = []
    for _, g in legs.iterrows():
        sn = 'SAFENET' in g.出場類型.upper()
        c = m[(m.positionSide == g.方向) & (m.side == g.side)]
        if sn: c = c[(c.kind == '觸發單') & (c.first_ms >= g.T_ms - H) & (c.first_ms <= g.T_ms + H)]
        else: c = c[(c.kind == '市價單') & (c.create_ms >= g.T_ms) & (c.create_ms <= g.T_ms + 30 * 60_000)]
        base = dict(序號=g.序號, 腿=g.腿, 方向=g.方向, 出場類型=g.出場類型, 月份=pd.Timestamp(g.T_ms, unit='ms').strftime('%Y-%m'))
        if len(c) != 1: rows.append({**base, '狀態': '缺資料' if len(c) == 0 else '多重候選'}); continue
        x = c.iloc[0]; used.add(x.orderId); sg = 1.0 if x.side == 'BUY' else -1.0
        ref = x.stopPrice if sn else kl.close.get(g.T_ms, np.nan)
        rows.append({**base, '狀態': '已配對', '訂單類型': x.kind, '成交筆': int(x.n_fills),
                     'ab_T到建立_ms_未拆分': np.nan if sn else x.create_ms - g.T_ms,
                     'c_建立到第一筆_ms': x.first_ms - x.create_ms, 'c_建立到最後_ms': x.last_ms - x.create_ms,
                     'T到最後成交_ms': x.last_ms - g.T_ms, '價差參考': '停損價' if sn else 'T收盤',
                     '方向性價差_bp': sg * (x.vwap / ref - 1) * 1e4 if ref and ref > 0 else np.nan,
                     'atr_pct': kl.atr_pct.get(g.T_ms, np.nan)})
    R = pd.DataFrame(rows)
    um = m[~m.orderId.isin(used)]
    def d(x):
        x = pd.to_numeric(x, errors='coerce').dropna()
        return dict(n=int(len(x)), 中位=float(x.median()) if len(x) else None, p90=float(x.quantile(.9)) if len(x) else None, 最小=float(x.min()) if len(x) else None, 最大=float(x.max()) if len(x) else None)
    ok = R[R.get('狀態', pd.Series(dtype=str)) == '已配對'].copy() if len(R) else R
    if len(ok) and ok.atr_pct.notna().sum() >= 3: ok['波動三分位'] = pd.qcut(ok.atr_pct, 3, labels=['低', '中', '高'])
    else: ok['波動三分位'] = '樣本不足'
    met = ['ab_T到建立_ms_未拆分', 'c_建立到最後_ms', 'T到最後成交_ms', '方向性價差_bp']
    grp = lambda keys: {' / '.join(map(str, k if isinstance(k, tuple) else (k,))): {mm: d(v[mm]) for mm in met} for k, v in ok.groupby(keys, observed=True)} if len(ok) else {}
    ext = lambda col: (lambda z: dict(最大5=z.nlargest(5, col)[['序號', '腿', '方向', col]].to_dict('records'), 最小5=z.nsmallest(5, col)[['序號', '腿', '方向', col]].to_dict('records')) if len(z) else {})(ok[ok[col].notna()] if len(ok) else ok)
    S = dict(清洗=q, K線檢查=kchk, 訂單分類={k: int(v) for k, v in m.kind.value_counts().items()},
             配對狀態={k: int(v) for k, v in R['狀態'].value_counts().items()} if len(R) else {},
             未配對訂單依類型={k: int(v) for k, v in um.kind.value_counts().items()},
             P_T缺值=int(ok['方向性價差_bp'].isna().sum()) if len(ok) else 0,
             進出場x多空=grp(['腿', '方向']), 出場類型=grp('出場類型'), 月份=grp('月份'), 波動三分位=grp('波動三分位'),
             極端_T到最後成交=ext('T到最後成交_ms'), 極端_價差=ext('方向性價差_bp'),
             延遲分段說明='(a) K 線收盤到取得資料、(b) 醒來到送單：未驗證（無本機時間戳），只報合計 ab；(c) 交易所建立到成交：已由交易所時間戳計算',
             限制='55 筆實盤是已看過的 2026 樣本，只代表其期間與配置，不是樣本外證據', bar_time=bar_time)
    return R, S


def run(a):
    prep_out(a.out)
    R, S = analyze(pd.read_csv(a.fills), pd.read_csv(a.orders), pd.read_csv(a.local), pd.read_csv(a.klines), a.bar_time)
    keep = [c for c in R.columns if c != 'atr_pct']
    write_private(os.path.join(a.out, 'legs_deidentified.csv'), R[keep].to_csv(index=False))
    write_private(os.path.join(a.out, 'summary.json'), json.dumps(S, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(S, ensure_ascii=False, indent=1, default=str))


def selftest():
    import tempfile
    T0 = 1_762_000_000_000 // H * H            # 某個整點（K 線收盤）
    fills = pd.DataFrame([
        dict(time=T0 + 10_400, id=1, orderId=11, side='BUY', positionSide='LONG', price=2000.0, qty=1.0, commission=0.8),
        dict(time=T0 + 10_600, id=2, orderId=11, side='BUY', positionSide='LONG', price=2002.0, qty=1.0, commission=0.8),
        dict(time=T0 + 10_600, id=2, orderId=11, side='BUY', positionSide='LONG', price=2002.0, qty=1.0, commission=0.8),   # 重複
        dict(time=T0 + 10_700, id=9, orderId=11, side='BUY', positionSide='LONG', price=2002.0, qty=0.0, commission=0.0),   # 零數量
        dict(time=T0 + 5 * H + 9_900, id=3, orderId=12, side='SELL', positionSide='LONG', price=2010.0, qty=2.0, commission=1.6),
        dict(time=T0 + 9 * H + 400_000, id=4, orderId=13, side='BUY', positionSide='SHORT', price=2050.0, qty=1.0, commission=0.8),  # SafeNet
        dict(time=T0 + 2 * H + 500, id=5, orderId=14, side='SELL', positionSide='SHORT', price=2030.0, qty=1.0, commission=0.8),
        dict(time=T0 + 20 * H + 100, id=6, orderId=15, side='BUY', positionSide='LONG', price=2100.0, qty=1.0, commission=0.8),   # 限價
        dict(time=T0 + 22 * H + 75 * 60_000, id=7, orderId=16, side='BUY', positionSide='LONG', price=2100.0, qty=1.0, commission=0.8),  # 晚 75 分
    ])
    orders = pd.DataFrame([
        dict(orderId=11, time=T0 + 10_300, type='MARKET', origType='MARKET', stopPrice=0),
        dict(orderId=12, time=T0 + 5 * H + 9_800, type='MARKET', origType='MARKET', stopPrice=0),
        dict(orderId=13, time=T0 + 2 * H + 600, type='MARKET', origType='STOP_MARKET', stopPrice=2048.0),
        dict(orderId=14, time=T0 + 2 * H + 300, type='MARKET', origType='MARKET', stopPrice=0),
        dict(orderId=15, time=T0 + 20 * H, type='LIMIT', origType='LIMIT', stopPrice=0),
        dict(orderId=16, time=T0 + 22 * H + 75 * 60_000 - 50, type='MARKET', origType='MARKET', stopPrice=0),
    ])
    ts = lambda ms: pd.Timestamp(ms, unit='ms').strftime('%Y-%m-%d %H:%M:%S')
    # 本機紀錄以 K 線開盤記時（--bar-time open）
    local = pd.DataFrame([
        dict(entry_time_utc=ts(T0 - H), exit_time_utc=ts(T0 + 4 * H), direction='LONG', exit_type='MaxHold'),
        dict(entry_time_utc=ts(T0 + H), exit_time_utc=ts(T0 + 8 * H), direction='SHORT', exit_type='SafeNet'),
        dict(entry_time_utc=ts(T0 + 21 * H), exit_time_utc='', direction='LONG', exit_type=''),        # 重試晚 75 分 → 不得配對
    ])
    n = 40; om = T0 - 10 * H + np.arange(n) * H
    kl = pd.DataFrame(dict(open_time=om, high=2015.0, low=1995.0, close=2000.0)); kl.loc[kl.open_time == T0 + 4 * H, 'close'] = 2012.0
    R, S = analyze(fills, orders, local, kl, 'open')
    g = R.set_index(['序號', '腿'])
    e1 = g.loc[(1, '進場')]; assert e1.狀態 == '已配對' and e1['ab_T到建立_ms_未拆分'] == 10_300 and e1['c_建立到最後_ms'] == 300 and abs(e1['方向性價差_bp'] - 5.0) < 1e-6
    x1 = g.loc[(1, '出場')]; assert abs(x1['方向性價差_bp'] - (-(2010 / 2012 - 1) * 1e4)) < 1e-6
    s2 = g.loc[(2, '出場')]; assert s2.訂單類型 == '觸發單' and s2.價差參考 == '停損價' and pd.isna(s2['ab_T到建立_ms_未拆分'])
    assert g.loc[(3, '進場')].狀態 == '缺資料'
    assert S['清洗']['重複成交剔除'] == 1 and S['清洗']['零或缺價量時間剔除'] == 1
    assert S['訂單分類']['其他類型'] == 1 and S['未配對訂單依類型'].get('其他類型') == 1 and S['未配對訂單依類型'].get('市價單') == 1
    assert S['K線檢查']['收盤都在整點']
    # 專案快取格式：datetime（UTC+8 開盤字串），結果須與 open_time 版完全一致（V86 第 4 輪：pandas 3 解析度回歸）
    kl2 = pd.DataFrame(dict(datetime=[(pd.Timestamp(int(v), unit='ms') + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') for v in kl.open_time], high=kl.high, low=kl.low, close=kl.close))
    R2, S2 = analyze(fills, orders, local, kl2, 'open')
    assert S2['K線檢查']['收盤都在整點'] and S2['P_T缺值'] == S['P_T缺值']
    assert np.allclose(R2['方向性價差_bp'].fillna(-9e9).values, R['方向性價差_bp'].fillna(-9e9).values)
    assert all(pd.notna(r['方向性價差_bp']) for r in S['極端_價差'].get('最大5', []))
    for bad in ('a', ''):
        try: analyze(fills, orders, local, kl, bad); raise SystemExit('應拒絕 bar_time')
        except AssertionError: pass
    tmp = tempfile.mkdtemp(); gd = os.path.join(tmp, 'g'); os.makedirs(os.path.join(gd, '.git'))
    for bad in (os.path.join(gd, 'out'), tmp):
        try: prep_out(bad); raise AssertionError('應拒絕 ' + bad)
        except SystemExit: pass
    out = os.path.join(tmp, 'ok'); prep_out(out)
    write_private(os.path.join(out, 'legs_deidentified.csv'), R[[c for c in R.columns if c != 'atr_pct']].to_csv(index=False))
    txt = open(os.path.join(out, 'legs_deidentified.csv'), encoding='utf-8').read()
    assert 'orderId' not in txt and '2002' not in txt and (os.stat(os.path.join(out, 'legs_deidentified.csv')).st_mode & 0o077) == 0
    try: write_private(os.path.join(out, 'legs_deidentified.csv'), 'x'); raise AssertionError('應拒絕覆寫')
    except FileExistsError: pass
    print('selftest ok：重複與零數量剔除、限價單歸其他類型不入統計、訊號時間取自本機紀錄、晚 75 分的重試不配對、SafeNet 對停損價、'
          'bar-time 必填、datetime 格式 K 線與 open_time 一致、拒絕 git 目錄與既有目錄、不覆寫、逐腿檔不含 orderId 與價格、檔案 600')
    print(json.dumps(dict(配對狀態=S['配對狀態'], 未配對訂單依類型=S['未配對訂單依類型'], 清洗=S['清洗']), ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest': selftest(); sys.exit()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ('fills', 'orders', 'local', 'klines', 'out'): ap.add_argument('--' + k, required=True)
    ap.add_argument('--bar-time', required=True, choices=['open', 'close'])
    run(ap.parse_args())
