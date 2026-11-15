#!/usr/bin/env python3
"""唯讀成交時間提取工具（V85 初版，V86 審查後修訂）。供使用者審查後在 VPS 親自執行；研究 AI 不執行。

安全設計
- 只用 Python 標準函式庫；不匯入 binance_trade、executor、strategy、requests 或任何專案模組，不啟動交易流程。
- 網路目的地只有 https://fapi.binance.com；只允許下列 GET，其餘路徑直接拒絕：
    /fapi/v1/time         公開，取伺服器時間（不簽名）
    /fapi/v1/userTrades   簽名，逐筆成交（官方：只支援近 3 個月；startTime～endTime ≤ 7 天）
    /fapi/v1/allOrders    簽名，訂單（官方：查詢期間 < 7 天；建立超過 90 天的訂單查不到）
  程式內沒有 POST、PUT、DELETE，沒有子程序。
- 標的固定 ETHUSDT。
- 金鑰只從環境變數 BINANCE_API_KEY、BINANCE_API_SECRET 讀；不讀 .env，不寫檔，不印出。
  錯誤只印 HTTP 狀態碼與 Binance 錯誤碼，不印網址、簽章或回應全文。
- 不自動重試。每個時間窗口完成後寫檢查點；中斷後用 --resume 只補未完成的窗口。
- 單一窗口回傳達 1,000 筆上限時，把窗口切半重查（不使用 fromId）。
- 輸出目錄不得在任何 git 工作樹內；目錄權限 700、檔案 600；附 SHA256SUMS.txt。

子命令
  selftest                       離線自我測試（不連網）
  fetch --sample                 最近 7 天一個窗口（小樣本）
  fetch --start YYYY-MM-DD --end YYYY-MM-DD [--resume]
  check --trades 路徑/trades.csv [--run 輸出子目錄]   唯讀比對，只印去識別摘要
共用參數：--out 目錄（預設 ~/fill_audit_out）
"""
import argparse, csv, hashlib, hmac, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = 'https://fapi.binance.com'
SYMBOL = 'ETHUSDT'
ALLOWED = {'/fapi/v1/time': False, '/fapi/v1/userTrades': True, '/fapi/v1/allOrders': True}   # 路徑: 是否簽名
DAY = 24 * 3600 * 1000
WINDOW_MS = 7 * DAY - 1000          # 比 7 天少 1 秒
LIMIT = 1000
TRADE_FIELDS = ['time', 'id', 'orderId', 'side', 'positionSide', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', 'realizedPnl', 'maker']
ORDER_FIELDS = ['orderId', 'time', 'updateTime', 'side', 'positionSide', 'type', 'origType', 'status', 'reduceOnly', 'closePosition', 'avgPrice', 'executedQty', 'origQty', 'stopPrice', 'workingType']


class ApiError(Exception):
    """只帶遮蔽後的資訊。"""


class ReadOnlyClient:
    def __init__(self, key, secret, opener=None):
        self._key = key or ''; self._secret = (secret or '').encode()
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=20))
        self.offset_ms = 0
        self.calls = []                 # 只記路徑與參數名稱

    def _request(self, path, params):
        if path not in ALLOWED: raise PermissionError(f'路徑不在允許清單：{path}')
        params = dict(params or {})
        if ALLOWED[path]:
            if not self._key or not self._secret: raise ApiError('缺少環境變數 BINANCE_API_KEY／BINANCE_API_SECRET')
            params['timestamp'] = int(time.time() * 1000) + self.offset_ms; params['recvWindow'] = 10000
            q = urllib.parse.urlencode(params)
            q += '&signature=' + hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
        else:
            q = urllib.parse.urlencode(params)
        req = urllib.request.Request(BASE + path + ('?' + q if q else ''), method='GET')
        if ALLOWED[path]: req.add_header('X-MBX-APIKEY', self._key)
        if req.get_method() != 'GET': raise PermissionError('只允許 GET')
        self.calls.append((path, tuple(sorted(params))))
        return req

    def get(self, path, params=None):
        req = self._request(path, params)
        try:
            with self._open(req) as r: return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            code = None
            try: code = json.loads(e.read().decode()).get('code')
            except Exception: pass
            raise ApiError(f'{path} HTTP {e.code}，Binance 錯誤碼 {code}') from None
        except urllib.error.URLError as e:
            raise ApiError(f'{path} 連線失敗：{type(e.reason).__name__}') from None
        except (TimeoutError, OSError) as e:
            raise ApiError(f'{path} 連線失敗：{type(e).__name__}') from None

    def sync(self):
        srv = int(self.get('/fapi/v1/time')['serverTime']); self.offset_ms = srv - int(time.time() * 1000); return srv


def ms_iso(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
def windows(start_ms, end_ms):
    s = start_ms
    while s <= end_ms:
        e = min(s + WINDOW_MS, end_ms); yield s, e; s = e + 1


def fetch_window(client, path, s, e, depth=0):
    """回傳 [s, e] 內 ETHUSDT 的資料；達上限就切半。"""
    rows = client.get(path, dict(symbol=SYMBOL, startTime=s, endTime=e, limit=LIMIT))
    if len(rows) >= LIMIT:
        if e - s < 60_000 or depth > 12: raise ApiError(f'{path} 在 1 分鐘窗口內仍達 {LIMIT} 筆，停止以免截斷')
        m = (s + e) // 2
        return fetch_window(client, path, s, m, depth + 1) + fetch_window(client, path, m + 1, e, depth + 1)
    return [r for r in rows if r.get('symbol', SYMBOL) == SYMBOL]


def inside_git(path):
    p = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(p, '.git')): return True
        q = os.path.dirname(p)
        if q == p: return False
        p = q


def prep_out(out):
    if inside_git(out): raise SystemExit(f'拒絕：輸出目錄位於 git 工作樹內（{out}）。請用專案外目錄，例如 ~/fill_audit_out')
    os.makedirs(out, mode=0o700, exist_ok=True); os.chmod(out, 0o700)


def write_private(path, text):
    fd = os.open(path + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f: f.write(text)
    os.replace(path + '.tmp', path); os.chmod(path, 0o600)


def keep(r, fields): return {k: r.get(k, '') for k in fields}


def run_fetch(client, out, start_ms, end_ms, resume, run_name):
    prep_out(out); run = os.path.join(out, run_name); os.makedirs(run, mode=0o700, exist_ok=True); ck = os.path.join(run, 'checkpoints'); os.makedirs(ck, mode=0o700, exist_ok=True)
    meta_p = os.path.join(run, 'run.json')
    if os.path.exists(meta_p):
        meta = json.load(open(meta_p, encoding='utf-8'))
        if not resume: raise SystemExit(f'{run} 已存在；要續跑請加 --resume，或改用新的 --run 名稱')
        start_ms, end_ms = meta['start_ms'], meta['end_ms']
    else:
        meta = dict(start_ms=start_ms, end_ms=end_ms, start=ms_iso(start_ms), end=ms_iso(end_ms), symbol=SYMBOL, tool_sha256=hashlib.sha256(open(os.path.abspath(__file__), 'rb').read()).hexdigest())
        write_private(meta_p, json.dumps(meta, ensure_ascii=False, indent=1))
    done = skipped = 0
    for s, e in windows(start_ms, end_ms):
        p = os.path.join(ck, f'{s}_{e}.json')
        if os.path.exists(p): skipped += 1; continue
        tr = fetch_window(client, '/fapi/v1/userTrades', s, e); od = fetch_window(client, '/fapi/v1/allOrders', s, e)
        write_private(p, json.dumps(dict(trades=[keep(r, TRADE_FIELDS) for r in tr], orders=[keep(r, ORDER_FIELDS) for r in od]), ensure_ascii=False))
        done += 1; time.sleep(0.3)
    return finalize(run), done, skipped


def finalize(run):
    ck = os.path.join(run, 'checkpoints'); T, O = {}, {}
    for f in sorted(os.listdir(ck)):
        if not f.endswith('.json'): continue
        d = json.load(open(os.path.join(ck, f), encoding='utf-8'))
        for r in d['trades']: T[r['id']] = r
        for r in d['orders']: O[r['orderId']] = r
    res = {}
    for name, rows, fields, key in (('trades.csv', T.values(), TRADE_FIELDS, 'id'), ('orders.csv', O.values(), ORDER_FIELDS, 'orderId')):
        rows = sorted(rows, key=lambda r: (int(r['time'] or 0), int(r[key] or 0)))
        lines = [','.join(fields + ['time_utc'])] + [','.join(str(r[c]) for c in fields) + ',' + ms_iso(int(r['time'])) for r in rows]
        p = os.path.join(run, name); write_private(p, '\n'.join(lines) + '\n'); res[name] = (len(rows), hashlib.sha256(open(p, 'rb').read()).hexdigest())
    write_private(os.path.join(run, 'SHA256SUMS.txt'), ''.join(f'{h}  {n}\n' for n, (_, h) in res.items()))
    return res


# ---------------- check：唯讀比對，只印去識別摘要 ----------------
def _f(x):
    try: return float(x)
    except Exception: return None


def run_check(run, trades_csv):
    tr = list(csv.DictReader(open(os.path.join(run, 'trades.csv'), encoding='utf-8')))
    od = {r['orderId']: r for r in csv.DictReader(open(os.path.join(run, 'orders.csv'), encoding='utf-8'))}
    S = dict(成交筆數=len(tr), 訂單筆數=len(od))
    S['欄位齊全'] = {c: all(r.get(c, '') != '' for r in tr) for c in ('id', 'orderId', 'time', 'side', 'positionSide', 'price', 'qty', 'commission')}
    S['time位數'] = sorted({len(r['time']) for r in tr}); S['time看起來是毫秒'] = S['time位數'] == [13] if tr else None
    S['成交都有對應訂單'] = all(r['orderId'] in od for r in tr) if tr else None
    g = {}
    for r in tr:
        x = g.setdefault(r['orderId'], dict(q=0.0, pq=0.0, fee=0.0, first=None, last=None, n=0, side=r['side'], ps=r['positionSide']))
        q = float(r['qty']); x['q'] += q; x['pq'] += q * float(r['price']); x['fee'] += float(r['commission']); x['n'] += 1
        t = int(r['time']); x['first'] = t if x['first'] is None else min(x['first'], t); x['last'] = t if x['last'] is None else max(x['last'], t)
    S['部分成交訂單數'] = sum(1 for x in g.values() if x['n'] > 1)
    local = list(csv.DictReader(open(trades_csv, encoding='utf-8')))      # 唯讀
    def parse(s):
        try: return datetime.fromisoformat(str(s).replace('Z', '')).replace(tzinfo=timezone.utc)
        except Exception: return None
    rows = []
    for L in local:
        for leg, tcol, pcol in (('進場', 'entry_time_utc', 'entry_price'), ('出場', 'exit_time_utc', 'exit_price')):
            t0 = parse(L.get(tcol)); px = _f(L.get(pcol))
            if t0 is None or px is None: continue
            ps = 'LONG' if str(L.get('direction', '')).upper() in ('LONG', 'L', 'BUY') or str(L.get('sub_strategy', '')).upper().startswith('L') else 'SHORT'
            side = ('BUY' if ps == 'LONG' else 'SELL') if leg == '進場' else ('SELL' if ps == 'LONG' else 'BUY')
            a = int(t0.timestamp() * 1000)
            cand = [(oid, x) for oid, x in g.items() if x['ps'] == ps and x['side'] == side and a - 3600_000 <= int(od.get(oid, {}).get('time') or x['first']) <= a + 2 * 3600_000]
            if not cand: continue
            if len(cand) > 1: rows.append(dict(leg=leg, 多重候選=len(cand))); continue
            oid, x = cand[0]; o = od.get(oid, {}); ct = int(o.get('time') or x['first']); vw = x['pq'] / x['q']
            rows.append(dict(leg=leg, 訂單建立減紀錄時間_分=round((ct - a) / 60000, 2), 建立到最後成交_ms=x['last'] - ct, 紀錄時間到最後成交_ms=x['last'] - a,
                             成交筆=x['n'], 紀錄價對交易所均價_bp=round((px / vw - 1) * 1e4, 2), 類型=o.get('origType') or o.get('type')))
    S['本機交易紀錄筆數'] = len(local); S['配對到的進出場腿'] = sum(1 for r in rows if '多重候選' not in r); S['多重候選腿'] = sum(1 for r in rows if '多重候選' in r)
    S['逐腿摘要（不含價格、數量、識別碼）'] = rows
    S['說明'] = '「訂單建立減紀錄時間」用來判斷 trades.csv 的時間欄是 K 線開盤還是收盤；紀錄價對交易所均價應接近 0bp。本摘要可以貼給研究端。'
    print(json.dumps(S, ensure_ascii=False, indent=1))
    return S


# ---------------- 自我測試（不連網） ----------------
def selftest():
    import io, tempfile, contextlib
    before = set(sys.modules)
    for bad in ('binance', 'binance_trade', 'executor', 'strategy', 'requests', 'main_eth'):
        assert bad not in sys.modules, bad
    KEY, SEC = 'TESTKEY_ABC123', 'TESTSECRET_XYZ789'
    seen = []
    class Resp:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    state = dict(dense=False, fail_at=None, n=0)
    def fake(req):
        assert req.get_method() == 'GET'; u = urllib.parse.urlparse(req.full_url); assert u.netloc == 'fapi.binance.com'; seen.append(req.full_url); state['n'] += 1
        if state['fail_at'] and state['n'] == state['fail_at']:
            raise urllib.error.HTTPError(req.full_url, 400, 'Bad Request', {}, io.BytesIO(b'{"code":-1021,"msg":"Timestamp outside recvWindow"}'))
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/fapi/v1/time': return Resp(json.dumps(dict(serverTime=int(time.time() * 1000) + 5000)).encode())
        s = int(q['startTime'][0]); e = int(q['endTime'][0]); assert e - s <= 7 * DAY
        assert abs(int(q['timestamp'][0]) - (int(time.time() * 1000) + 5000)) < 2000, '時間戳未用伺服器校正'
        n = LIMIT if (state['dense'] and e - s > 3 * DAY) else 1
        if u.path == '/fapi/v1/userTrades':
            return Resp(json.dumps([dict(symbol='ETHUSDT', id=s // 1000 + i, orderId=s // 1000, side='BUY', positionSide='LONG', price='2500.0', qty='1.6', quoteQty='4000', commission='1.6', commissionAsset='USDT', realizedPnl='0', maker=False, buyer=True, time=s + 10_123 + i) for i in range(n)]).encode())
        if u.path == '/fapi/v1/allOrders':
            return Resp(json.dumps([dict(symbol='ETHUSDT', orderId=s // 1000, clientOrderId='c', time=s + 10_050, updateTime=s + 10_130, side='BUY', positionSide='LONG', type='MARKET', origType='MARKET', status='FILLED', reduceOnly=False, closePosition=False, avgPrice='2500.0', executedQty='1.6', origQty='1.6', stopPrice='0', workingType='CONTRACT_PRICE')]).encode())
        raise AssertionError(u.path)
    out = tempfile.mkdtemp(prefix='fill_audit_test_'); buf_o, buf_e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_o), contextlib.redirect_stderr(buf_e):
        c = ReadOnlyClient(KEY, SEC, opener=fake); c.sync()
        # 1 允許清單外拒絕
        for p in ('/fapi/v1/order', '/fapi/v1/algoOrder', '/fapi/v1/trade/asyn', '/api/meta'):
            try: c.get(p, {}); raise AssertionError('應拒絕 ' + p)
            except PermissionError: pass
        # 2 正常抓取、窗口 ≤7 天
        end = int(time.time() * 1000); start = end - 20 * DAY
        res, done, skip = run_fetch(c, out, start, end, False, 'r1'); assert done == 3 and skip == 0
        # 3 續跑不重查
        n0 = state['n']; res2, done2, skip2 = run_fetch(c, out, start, end, True, 'r1'); assert done2 == 0 and skip2 == 3 and state['n'] == n0
        # 4 中途錯誤：遮蔽、可續跑
        state['fail_at'] = state['n'] + 3
        try: run_fetch(c, out, start, end, False, 'r2'); raise AssertionError('應失敗')
        except ApiError as e: msg = str(e); assert 'signature' not in msg and 'fapi.binance.com' not in msg and '-1021' in msg
        state['fail_at'] = None; n1 = state['n']; res3, done3, skip3 = run_fetch(c, out, start, end, True, 'r2'); assert skip3 == 1 and done3 == 2
        # 5 達上限切半
        state['dense'] = True; t, o = fetch_window(c, '/fapi/v1/userTrades', start, start + 6 * DAY), None; state['dense'] = False; assert len(t) < LIMIT * 4
        # 6 git 工作樹內拒絕
        gdir = tempfile.mkdtemp(); os.makedirs(os.path.join(gdir, '.git'))
        try: prep_out(os.path.join(gdir, 'sub')); raise AssertionError('應拒絕')
        except SystemExit: pass
        # 7 check 子命令
        lt = os.path.join(out, 'local_trades.csv'); t1 = datetime.fromtimestamp((start + 10_050) / 1000, tz=timezone.utc).replace(microsecond=0, second=0)
        with open(lt, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['entry_time_utc', 'entry_price', 'exit_time_utc', 'exit_price', 'direction', 'sub_strategy']); w.writerow([t1.strftime('%Y-%m-%d %H:%M:%S'), '2500.0', '', '', 'LONG', 'L'])
        S = run_check(os.path.join(out, 'r1'), lt); assert S['配對到的進出場腿'] == 1 and S['time看起來是毫秒']
    blob = buf_o.getvalue() + buf_e.getvalue()
    for root, _, files in os.walk(out):
        for f in files:
            if f != 'local_trades.csv': blob += open(os.path.join(root, f), encoding='utf-8').read()
            assert (os.stat(os.path.join(root, f)).st_mode & 0o077) == 0 or f == 'local_trades.csv', f
    assert KEY not in blob and SEC not in blob and 'signature' not in blob
    assert all('signature=' in u for u in seen if '/fapi/v1/time' not in u)
    new = set(sys.modules) - before
    assert not any(m.split('.')[0] in ('binance', 'binance_trade', 'executor', 'strategy', 'requests') for m in new)
    print('selftest ok：允許清單外拒絕（含 order、algoOrder、歷史匯出、檢視器路徑）、只發 GET、只連 fapi.binance.com、窗口 ≤7 天、時間戳用伺服器校正、'
          '錯誤訊息已遮蔽、續跑不重查、達上限切半、git 工作樹內拒絕、檔案權限 600、輸出與終端不含金鑰或簽章、未載入專案或下單模組')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('selftest')
    f = sub.add_parser('fetch'); f.add_argument('--sample', action='store_true'); f.add_argument('--start'); f.add_argument('--end'); f.add_argument('--resume', action='store_true'); f.add_argument('--run'); f.add_argument('--out', default=os.path.expanduser('~/fill_audit_out'))
    k = sub.add_parser('check'); k.add_argument('--trades', required=True); k.add_argument('--run'); k.add_argument('--out', default=os.path.expanduser('~/fill_audit_out'))
    a = ap.parse_args()
    if a.cmd == 'selftest': selftest(); return
    if a.cmd == 'check':
        run = os.path.join(a.out, a.run) if a.run else max((os.path.join(a.out, d) for d in os.listdir(a.out) if os.path.isdir(os.path.join(a.out, d))), key=os.path.getmtime)
        run_check(run, a.trades); return
    c = ReadOnlyClient(os.environ.get('BINANCE_API_KEY'), os.environ.get('BINANCE_API_SECRET'))
    try:
        srv = c.sync()
        if a.sample: end = srv; start = end - WINDOW_MS
        else:
            if not (a.start and a.end): ap.error('需要 --sample，或 --start 與 --end')
            start = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
            end = min(int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY - 1, srv)
            if srv - start > 90 * DAY: print('提醒：userTrades 只支援近 3 個月、allOrders 查不到建立超過 90 天的訂單；更早的部分不會回傳', file=sys.stderr)
        run_name = a.run or ('sample_' if a.sample else 'range_') + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        res, done, skipped = run_fetch(c, a.out, start, end, a.resume, run_name)
    except ApiError as e:
        print(f'失敗：{e}。已完成的窗口保留在檢查點，可用同一個 --run 加 --resume 續跑。', file=sys.stderr); sys.exit(2)
    print(f'輸出：{os.path.join(a.out, run_name)}（窗口 新查 {done}、跳過 {skipped}；本機與伺服器時差 {c.offset_ms} ms）')
    for n, (cnt, h) in res.items(): print(f'  {n}：{cnt} 筆  sha256 {h}')
    print('呼叫紀錄（只含路徑與參數名稱）：', sorted(set(c.calls)))


if __name__ == '__main__':
    main()
