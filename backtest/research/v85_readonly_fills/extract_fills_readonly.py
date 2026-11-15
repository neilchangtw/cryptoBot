#!/usr/bin/env python3
"""唯讀成交時間提取工具（V85 初版；V86 第三版）。供使用者審查後在 VPS 親自執行；研究 AI 不執行。

安全設計
- 只用 Python 標準函式庫；不匯入 binance_trade、executor、strategy、requests 或任何專案模組，不啟動交易流程。
- 網路目的地只有 https://fapi.binance.com；只允許下列 GET，其餘路徑直接拒絕：
    /fapi/v1/time         公開，取伺服器時間（不簽名）
    /fapi/v1/userTrades   簽名，逐筆成交（官方：只支援近 3 個月；startTime～endTime ≤ 7 天）
    /fapi/v1/allOrders    簽名，訂單（官方：查詢期間 < 7 天；建立超過 90 天的訂單查不到）
  程式內沒有 POST、PUT、DELETE，沒有子程序。
- 任何 HTTP 重導向（3xx）一律拒絕，不發第二個請求，所以金鑰與簽章不會送到 Location 指向的網址。
- 標的固定 ETHUSDT。
- 金鑰只從環境變數 BINANCE_API_KEY、BINANCE_API_SECRET 讀；不讀 .env，不寫檔，不印出。
  錯誤只印 HTTP 狀態碼與 Binance 錯誤碼，不印網址、簽章或回應全文。
- 不自動重試。每個時間窗口完成後寫檢查點（含窗口、工具雜湊、內容雜湊）；中斷後用 --resume 只補未完成的窗口。
  續跑時工具版本不同、檢查點損壞、窗口不符或多出檔案，一律拒絕，不混用。
- 單一窗口回傳達 1,000 筆上限時，把窗口切半重查（不使用 fromId）。
- 輸出目錄：不得在 git 工作樹內；路徑任何一段是符號連結就拒絕；--run 只能是單一名稱（不得含 / 或 ..）；
  寫檔用 O_NOFOLLOW、O_EXCL。Linux／macOS 上目錄 700、檔案 600；附 SHA256SUMS.txt。

子命令
  selftest                       離線自我測試（不連網），逐項印出結果
  fetch --sample --run 名稱       最近 7 天一個窗口（小樣本）
  fetch --start YYYY-MM-DD --end YYYY-MM-DD --run 名稱 [--resume]
  check --run 名稱 --trades 路徑/trades.csv   唯讀比對，只印去識別摘要
共用參數：--out 目錄（預設 ~/fill_audit_out）
"""
import argparse, csv, hashlib, hmac, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

BASE = 'https://fapi.binance.com'
HOST = 'fapi.binance.com'
SYMBOL = 'ETHUSDT'
ALLOWED = {'/fapi/v1/time': False, '/fapi/v1/userTrades': True, '/fapi/v1/allOrders': True}   # 路徑: 是否簽名
DAY = 24 * 3600 * 1000
WINDOW_MS = 7 * DAY - 1000          # 比 7 天少 1 秒
LIMIT = 1000
SCHEMA = 3
POSIX = os.name == 'posix'
O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
RUN_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$')
TRADE_FIELDS = ['time', 'id', 'orderId', 'side', 'positionSide', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', 'realizedPnl', 'maker']
ORDER_FIELDS = ['orderId', 'time', 'updateTime', 'side', 'positionSide', 'type', 'origType', 'status', 'reduceOnly', 'closePosition', 'avgPrice', 'executedQty', 'origQty', 'stopPrice', 'workingType']


class ApiError(Exception):
    """只帶遮蔽後的資訊。"""


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """收到 3xx 時直接丟錯，不建立、不發出第二個請求。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ApiError(f'收到 HTTP {code} 重導向，已拒絕，未跟隨')


def make_opener(*extra_handlers):
    """正式執行只用 RefuseRedirect 取代預設的重導向處理；extra_handlers 只給自我測試注入假傳輸層。"""
    return urllib.request.build_opener(RefuseRedirect, *extra_handlers)


def tool_sha256():
    with open(os.path.abspath(__file__), 'rb') as f: return hashlib.sha256(f.read()).hexdigest()


class ReadOnlyClient:
    def __init__(self, key, secret, opener=None):
        self._key = key or ''; self._secret = (secret or '').encode()
        op = opener or make_opener()
        self._open = lambda req: op.open(req, timeout=20)
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
        if urllib.parse.urlparse(req.full_url).netloc != HOST: raise PermissionError('網域不符')
        if ALLOWED[path]: req.add_header('X-MBX-APIKEY', self._key)
        if req.get_method() != 'GET': raise PermissionError('只允許 GET')
        self.calls.append((path, tuple(sorted(params))))
        return req

    def get(self, path, params=None):
        req = self._request(path, params)
        try:
            with self._open(req) as r:
                if urllib.parse.urlparse(r.geturl()).netloc != HOST: raise ApiError('回應網域不符，已拒絕')
                return json.loads(r.read().decode())
        except ApiError:
            raise
        except urllib.error.HTTPError as e:
            code = None
            try: code = json.loads(e.read().decode()).get('code')
            except Exception: pass
            raise ApiError(f'{path} HTTP {e.code}，Binance 錯誤碼 {code}') from None
        except urllib.error.URLError as e:
            raise ApiError(f'{path} 連線失敗：{type(e.reason).__name__}') from None
        except (TimeoutError, OSError, ValueError) as e:
            raise ApiError(f'{path} 連線或解析失敗：{type(e).__name__}') from None

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
    if not isinstance(rows, list): raise ApiError(f'{path} 回應格式不是清單')
    if len(rows) >= LIMIT:
        if e - s < 60_000 or depth > 12: raise ApiError(f'{path} 在 1 分鐘窗口內仍達 {LIMIT} 筆，停止以免截斷')
        m = (s + e) // 2
        return fetch_window(client, path, s, m, depth + 1) + fetch_window(client, path, m + 1, e, depth + 1)
    return [r for r in rows if r.get('symbol', SYMBOL) == SYMBOL]


# ---------------- 輸出路徑安全 ----------------
def inside_git(path):
    p = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(p, '.git')): return True
        q = os.path.dirname(p)
        if q == p: return False
        p = q


def no_symlink_components(path):
    """路徑任一段是符號連結就拒絕（含尚未建立部分之前已存在的各段）。"""
    p = os.path.abspath(path); parts = []
    while True:
        parts.append(p); q = os.path.dirname(p)
        if q == p: break
        p = q
    for x in parts:
        if os.path.islink(x): raise SystemExit(f'拒絕：路徑含符號連結（{x}）。請改用實際路徑')


def prep_out(out):
    no_symlink_components(out)
    if inside_git(out): raise SystemExit(f'拒絕：輸出目錄位於 git 工作樹內（{out}）。請用專案外目錄，例如 ~/fill_audit_out')
    os.makedirs(out, mode=0o700, exist_ok=True)
    if POSIX: os.chmod(out, 0o700)
    no_symlink_components(out)


def run_dir(out, run):
    if not run or not RUN_RE.match(run) or run in ('.', '..') or '..' in run:
        raise SystemExit('拒絕：--run 只能是 1～64 個英數字、底線、點或連字號，不得含路徑分隔或 ..')
    p = os.path.join(os.path.abspath(out), run)
    if os.path.dirname(p) != os.path.abspath(out): raise SystemExit('拒絕：--run 路徑穿越')
    no_symlink_components(p)
    return p


def write_private(path, text):
    tmp = path + '.tmp'
    if os.path.lexists(tmp): os.unlink(tmp)                 # 舊暫存或被放置的連結只移除連結本身
    if os.path.islink(path): raise SystemExit(f'拒絕：目標是符號連結（{path}）')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f: f.write(text)
    os.replace(tmp, path)
    if POSIX: os.chmod(path, 0o600)


def read_regular(path):
    if os.path.islink(path): raise SystemExit(f'拒絕：檔案是符號連結（{path}）')
    fd = os.open(path, os.O_RDONLY | O_NOFOLLOW)
    with os.fdopen(fd, 'r', encoding='utf-8') as f: return f.read()


def keep(r, fields): return {k: r.get(k, '') for k in fields}


# ---------------- 抓取、檢查點、續跑 ----------------
def ck_payload_hash(trades, orders):
    return hashlib.sha256(json.dumps(dict(trades=trades, orders=orders), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_checkpoint(p, s, e, tsha):
    try: d = json.loads(read_regular(p))
    except (ValueError, OSError): raise SystemExit(f'拒絕續跑：檢查點損壞（{os.path.basename(p)}）。刪除此檔後再 --resume 會重查該窗口')
    ok = (isinstance(d, dict) and d.get('schema') == SCHEMA and d.get('s') == s and d.get('e') == e and d.get('tool_sha256') == tsha
          and isinstance(d.get('trades'), list) and isinstance(d.get('orders'), list) and d.get('sha256') == ck_payload_hash(d['trades'], d['orders']))
    if not ok: raise SystemExit(f'拒絕續跑：檢查點內容、窗口或工具版本不符（{os.path.basename(p)}）。刪除此檔後再 --resume 會重查該窗口')
    return d


def run_fetch(client, out, start_ms, end_ms, resume, run_name):
    prep_out(out); run = run_dir(out, run_name); tsha = tool_sha256()
    os.makedirs(run, mode=0o700, exist_ok=True); ck = os.path.join(run, 'checkpoints'); os.makedirs(ck, mode=0o700, exist_ok=True)
    if POSIX: os.chmod(run, 0o700); os.chmod(ck, 0o700)
    no_symlink_components(ck)
    meta_p = os.path.join(run, 'run.json')
    if os.path.lexists(meta_p):
        if not resume: raise SystemExit(f'{run} 已存在；要續跑請加 --resume，或改用新的 --run 名稱')
        try: meta = json.loads(read_regular(meta_p))
        except ValueError: raise SystemExit('拒絕續跑：run.json 損壞')
        if meta.get('schema') != SCHEMA or meta.get('tool_sha256') != tsha:
            raise SystemExit('拒絕續跑：這個 --run 是用不同版本的工具建立的，請改用新的 --run 名稱重新抓取')
        start_ms, end_ms = meta['start_ms'], meta['end_ms']
    else:
        if resume: raise SystemExit(f'{run} 沒有 run.json，無法續跑')
        meta = dict(schema=SCHEMA, start_ms=start_ms, end_ms=end_ms, start=ms_iso(start_ms), end=ms_iso(end_ms), symbol=SYMBOL, tool_sha256=tsha)
        write_private(meta_p, json.dumps(meta, ensure_ascii=False, indent=1))
    expect = {f'{s}_{e}.json': (s, e) for s, e in windows(start_ms, end_ms)}
    extra = [f for f in os.listdir(ck) if f not in expect and not f.endswith('.tmp')]
    if extra: raise SystemExit(f'拒絕續跑：檢查點目錄有不屬於此範圍的檔案（{len(extra)} 個）')
    done = skipped = 0
    for name, (s, e) in expect.items():
        p = os.path.join(ck, name)
        if os.path.lexists(p): load_checkpoint(p, s, e, tsha); skipped += 1; continue
        tr = [keep(r, TRADE_FIELDS) for r in fetch_window(client, '/fapi/v1/userTrades', s, e)]
        od = [keep(r, ORDER_FIELDS) for r in fetch_window(client, '/fapi/v1/allOrders', s, e)]
        write_private(p, json.dumps(dict(schema=SCHEMA, s=s, e=e, tool_sha256=tsha, trades=tr, orders=od, sha256=ck_payload_hash(tr, od)), ensure_ascii=False))
        done += 1; time.sleep(0.3)
    return finalize(run, expect, tsha), done, skipped


def finalize(run, expect, tsha):
    ck = os.path.join(run, 'checkpoints'); T, O = {}, {}
    for name, (s, e) in expect.items():
        d = load_checkpoint(os.path.join(ck, name), s, e, tsha)
        for r in d['trades']: T[str(r['id'])] = r
        for r in d['orders']: O[str(r['orderId'])] = r
    res = {}
    for name, rows, fields, key in (('trades.csv', T.values(), TRADE_FIELDS, 'id'), ('orders.csv', O.values(), ORDER_FIELDS, 'orderId')):
        rows = sorted(rows, key=lambda r: (int(r['time'] or 0), int(r[key] or 0)))
        lines = [','.join(fields + ['time_utc'])] + [','.join(str(r[c]) for c in fields) + ',' + ms_iso(int(r['time'])) for r in rows]
        p = os.path.join(run, name); write_private(p, '\n'.join(lines) + '\n'); res[name] = (len(rows), hashlib.sha256('\n'.join(lines).encode() + b'\n').hexdigest())
    write_private(os.path.join(run, 'SHA256SUMS.txt'), ''.join(f'{h}  {n}\n' for n, (_, h) in res.items()))
    return res


# ---------------- check：唯讀比對，只印去識別摘要 ----------------
def _f(x):
    try: return float(x)
    except Exception: return None


def run_check(run, trades_csv):
    tr = list(csv.DictReader(read_regular(os.path.join(run, 'trades.csv')).splitlines()))
    od = {r['orderId']: r for r in csv.DictReader(read_regular(os.path.join(run, 'orders.csv')).splitlines())}
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
    with open(trades_csv, encoding='utf-8') as f: local = list(csv.DictReader(f))      # 唯讀
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
    import io, tempfile, contextlib, email.message, urllib.response
    results = []
    def item(name, fn):
        try:
            r = fn(); results.append((name, 'PASS' if r is None else r))
        except Exception as ex:
            results.append((name, f'FAIL：{type(ex).__name__} {ex}'))
    before = set(sys.modules)
    KEY, SEC = 'TESTKEY_ABC123', 'TESTSECRET_XYZ789'
    seen = []
    class Resp:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def geturl(self): return BASE + '/'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    state = dict(dense=False, fail_at=None, n=0)
    def fake(req):
        assert req.get_method() == 'GET'; u = urllib.parse.urlparse(req.full_url); assert u.netloc == HOST; seen.append(req.full_url); state['n'] += 1
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
    class FakeClient(ReadOnlyClient):
        def __init__(self): super().__init__(KEY, SEC, opener=urllib.request.build_opener()); self._open = fake
    root = tempfile.mkdtemp(prefix='fill_audit_test_'); out = os.path.join(root, 'out')
    buf_o, buf_e = io.StringIO(), io.StringIO()
    end = int(time.time() * 1000); start = end - 20 * DAY
    with contextlib.redirect_stdout(buf_o), contextlib.redirect_stderr(buf_e):
        def t_modules():
            for bad in ('binance', 'binance_trade', 'executor', 'strategy', 'requests', 'main_eth'): assert bad not in sys.modules, bad
        item('01 未載入專案或下單模組', t_modules)
        c = FakeClient(); c.sync()
        def t_allow():
            for p in ('/fapi/v1/order', '/fapi/v1/algoOrder', '/fapi/v1/trade/asyn', '/fapi/v1/order/asyn', '/api/meta'):
                try: c.get(p, {}); raise AssertionError('應拒絕 ' + p)
                except PermissionError: pass
        item('02 允許清單外路徑拒絕（order、algoOrder、兩種歷史匯出、檢視器）', t_allow)

        def t_redirect():
            # 用真正的 urllib opener 鏈：RefuseRedirect + 假傳輸層。假傳輸層記錄每一個實際「送出」的請求。
            sent = []
            class FakeTransport(urllib.request.BaseHandler):
                handler_order = 100                       # 比內建 HTTPSHandler 先處理，因此不會真的連網
                def __init__(self, code): self.code = code
                def https_open(self, req):
                    sent.append((urllib.parse.urlparse(req.full_url).netloc, req.full_url, dict(req.header_items())))
                    hdr = email.message.Message(); hdr['Location'] = 'https://evil.example.net/steal'
                    r = urllib.response.addinfourl(io.BytesIO(b'{}'), hdr, req.full_url, self.code); r.msg = 'Redirect'
                    return r
            for code in (301, 302, 303, 307, 308):
                sent.clear()
                rc = ReadOnlyClient(KEY, SEC, opener=make_opener(FakeTransport(code)))
                try: rc.get('/fapi/v1/userTrades', dict(symbol=SYMBOL, startTime=1, endTime=2, limit=1)); raise AssertionError('應拒絕重導向')
                except ApiError as ex: assert '重導向' in str(ex) and 'evil' not in str(ex) and 'signature' not in str(ex)
                assert len(sent) == 1, f'{code} 發出 {len(sent)} 個請求'
                assert all(h == HOST for h, _, _ in sent), '請求送到其他網域'
                assert not any('evil' in u for _, u, _ in sent)
        item('03 拒絕重導向 301/302/303/307/308：只送出 1 個請求、跨網域 Location 沒有收到任何請求（金鑰、簽章皆未送出）', t_redirect)

        def t_redirect_control():
            # 對照組：Python 預設 opener 會跟隨 307，並把金鑰標頭與簽章帶到 Location 網域。證明 03 的測試真的測得出洩漏。
            sent = []
            class T(urllib.request.BaseHandler):
                handler_order = 100
                def https_open(self, req):
                    sent.append((urllib.parse.urlparse(req.full_url).netloc, req.get_header('X-mbx-apikey') is not None, 'signature=' in req.full_url))
                    h = email.message.Message()
                    if len(sent) == 1: h['Location'] = 'https://evil.example.net/steal?' + urllib.parse.urlparse(req.full_url).query; code = 307
                    else: code = 200
                    r = urllib.response.addinfourl(io.BytesIO(b'[]'), h, req.full_url, code); r.msg = 'x'; return r
            rc = ReadOnlyClient(KEY, SEC, opener=urllib.request.build_opener(T()))
            try: rc.get('/fapi/v1/userTrades', dict(symbol=SYMBOL, startTime=1, endTime=2, limit=1))
            except ApiError: pass
            assert ('evil.example.net', True, True) in sent, '對照組應該洩漏，測試才有鑑別力'
        item('03b 對照組：預設 opener 會把金鑰與簽章送到跨網域 Location（證明 03 有鑑別力；正式路徑不用預設 opener）', t_redirect_control)

        def t_resp_host():
            class R(Resp):
                def geturl(self): return 'https://evil.example.net/x'
            rc = FakeClient(); rc._open = lambda req: R(b'[]')
            try: rc.get('/fapi/v1/time'); raise AssertionError('應拒絕')
            except ApiError: pass
        item('04 回應網址不是 fapi.binance.com 時拒絕', t_resp_host)

        def t_fetch():
            res, done, skip = run_fetch(c, out, start, end, False, 'r1'); assert done == 3 and skip == 0
            for s, e in windows(start, end): pass
            assert all(len(urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get('signature', [])) == 1 for u in seen if '/fapi/v1/time' not in u)
        item('05 正常抓取：窗口 ≤7 天、時間戳用伺服器校正、簽名請求都帶 signature', t_fetch)

        def t_resume():
            n0 = state['n']; _, done2, skip2 = run_fetch(c, out, start, end, True, 'r1'); assert done2 == 0 and skip2 == 3 and state['n'] == n0
        item('06 續跑：已完成窗口不重查', t_resume)

        def t_error_mask():
            state['fail_at'] = state['n'] + 3
            try: run_fetch(c, out, start, end, False, 'r2'); raise AssertionError('應失敗')
            except ApiError as e: msg = str(e); assert 'signature' not in msg and HOST not in msg and '-1021' in msg
            state['fail_at'] = None; _, done3, skip3 = run_fetch(c, out, start, end, True, 'r2'); assert skip3 == 1 and done3 == 2
        item('07 中途錯誤：訊息遮蔽（無網域、無簽章、有錯誤碼），續跑只補未完成窗口', t_error_mask)

        def t_split():
            state['dense'] = True
            try: t = fetch_window(c, '/fapi/v1/userTrades', start, start + 6 * DAY); assert 0 < len(t) < LIMIT * 4
            finally: state['dense'] = False
        item('08 單窗口達 1,000 筆時切半重查', t_split)

        def t_version():
            rj = os.path.join(out, 'r1', 'run.json'); meta = json.loads(read_regular(rj)); meta['tool_sha256'] = '0' * 64
            write_private(rj, json.dumps(meta))
            try: run_fetch(c, out, start, end, True, 'r1'); raise AssertionError('應拒絕')
            except SystemExit as ex: assert '不同版本' in str(ex)
        item('09 續跑時工具版本不同：拒絕，不混用', t_version)

        def t_corrupt():
            ck = os.path.join(out, 'r2', 'checkpoints'); f0 = sorted(os.listdir(ck))[0]; p = os.path.join(ck, f0)
            good = read_regular(p)
            for bad in ('{"schema": 3, "trades": [', good.replace('"orders": [', '"orders": [{"orderId": 999},', 1), good.replace(f'"s": {f0.split("_")[0]}', '"s": 1', 1)):
                write_private(p, bad)
                try: run_fetch(c, out, start, end, True, 'r2'); raise AssertionError('應拒絕')
                except SystemExit as ex: assert '檢查點' in str(ex)
            write_private(p, good)
            write_private(os.path.join(ck, '1_2.json'), good)
            try: run_fetch(c, out, start, end, True, 'r2'); raise AssertionError('應拒絕')
            except SystemExit as ex: assert '不屬於此範圍' in str(ex)
            os.unlink(os.path.join(ck, '1_2.json')); run_fetch(c, out, start, end, True, 'r2')
        item('10 檢查點損壞、內容被改、窗口不符、多出檔案：拒絕續跑；還原後可續跑', t_corrupt)

        def t_traversal():
            for bad in ('../x', '..', '.', 'a/b', '/tmp/x', '', 'a\\..\\b', '..hidden'):
                try: run_dir(out, bad); raise AssertionError('應拒絕 ' + repr(bad))
                except SystemExit: pass
        item('11 --run 路徑穿越拒絕（../、..、.、a/b、絕對路徑、空字串、反斜線）', t_traversal)

        def t_git():
            gdir = os.path.join(root, 'g'); os.makedirs(os.path.join(gdir, '.git'))
            try: prep_out(os.path.join(gdir, 'sub')); raise AssertionError('應拒絕')
            except SystemExit: pass
        item('12 輸出目錄位於 git 工作樹內：拒絕', t_git)

        def t_symlink():
            if not hasattr(os, 'symlink'): return 'SKIP：此平台沒有 os.symlink'
            gdir = os.path.join(root, 'g'); link = os.path.join(root, 'link_to_git'); link2 = os.path.join(root, 'link_plain')
            try: os.symlink(gdir, link); os.symlink(os.path.join(root, 'plain'), link2)
            except (OSError, NotImplementedError) as ex: return f'SKIP：此平台不允許建立符號連結（{type(ex).__name__}），未驗證'
            for bad in (os.path.join(link, 'out'), link2):
                try: prep_out(bad); raise AssertionError('應拒絕 ' + bad)
                except SystemExit: pass
            ck = os.path.join(out, 'r1', 'checkpoints'); os.makedirs(os.path.join(root, 'plain'), exist_ok=True)
            os.symlink(os.path.join(root, 'plain'), os.path.join(out, 'r_link'))
            try: run_fetch(c, out, start, end, False, 'r_link'); raise AssertionError('應拒絕')
            except SystemExit: pass
            target = os.path.join(root, 'victim.txt'); open(target, 'w').write('original')
            dst = os.path.join(out, 'r1', 'trap.csv'); os.symlink(target, dst)
            try: write_private(dst, 'x'); raise AssertionError('應拒絕')
            except SystemExit: pass
            os.symlink(target, dst + '2.tmp'); write_private(dst + '2', 'y')
            assert open(target).read() == 'original', '符號連結目標被改寫'
            os.unlink(dst); os.unlink(os.path.join(out, 'r_link'))
        item('13 符號連結繞過：輸出路徑任一段是連結、--run 是連結、目標檔或暫存檔是連結，都不會寫到連結目標', t_symlink)

        def t_check():
            lt = os.path.join(root, 'local_trades.csv'); t1 = datetime.fromtimestamp((start + 10_050) / 1000, tz=timezone.utc).replace(microsecond=0, second=0)
            with open(lt, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f); w.writerow(['entry_time_utc', 'entry_price', 'exit_time_utc', 'exit_price', 'direction', 'sub_strategy']); w.writerow([t1.strftime('%Y-%m-%d %H:%M:%S'), '2500.0', '', '', 'LONG', 'L'])
            S = run_check(os.path.join(out, 'r1'), lt); assert S['配對到的進出場腿'] == 1 and S['time看起來是毫秒']
        item('14 check 子命令：配對 1 腿、時間為 13 位數', t_check)

        def t_sums():
            d = os.path.join(out, 'r1')
            for line in read_regular(os.path.join(d, 'SHA256SUMS.txt')).splitlines():
                h, n = line.split('  ')
                with open(os.path.join(d, n), 'rb') as f: assert hashlib.sha256(f.read()).hexdigest() == h, n
        item('15 SHA256SUMS.txt 與輸出檔一致', t_sums)

    def t_perm():
        if not POSIX: return f'SKIP：平台 {os.name}，POSIX 權限位元不適用（NTFS 由 ACL 控制）；此項在 Linux 上必須為 PASS'
        bad = []
        for r_, dirs, files in os.walk(out):
            for d in dirs:
                if (os.stat(os.path.join(r_, d)).st_mode & 0o077): bad.append(d)
            for f in files:
                if (os.lstat(os.path.join(r_, f)).st_mode & 0o077): bad.append(f)
        if (os.stat(out).st_mode & 0o077): bad.append('out')
        assert not bad, bad
    item('16 Linux：輸出目錄 700、檔案 600（群組與其他人無任何權限）', t_perm)

    def t_secret():
        blob = buf_o.getvalue() + buf_e.getvalue()
        for r_, _, files in os.walk(out):
            for f in files:
                p = os.path.join(r_, f)
                if not os.path.islink(p): blob += open(p, encoding='utf-8').read()
        assert KEY not in blob and SEC not in blob and 'signature' not in blob
    item('17 終端輸出與所有輸出檔不含金鑰、密鑰或 signature', t_secret)

    def t_new_modules():
        new = set(sys.modules) - before
        assert not any(m.split('.')[0] in ('binance', 'binance_trade', 'executor', 'strategy', 'requests') for m in new)
    item('18 測試全程未載入專案或下單模組', t_new_modules)

    print(f'平台：{sys.platform}（os.name={os.name}），Python {sys.version.split()[0]}，工具 sha256 {tool_sha256()}')
    for n, r in results: print(f'{r[:4] if r.startswith(("PASS", "FAIL", "SKIP")) else r}  {n}' + ('' if r == 'PASS' else f'\n      {r}'))
    fails = [n for n, r in results if r.startswith('FAIL')]; skips = [n for n, r in results if r.startswith('SKIP')]
    print(f'結果：{len(results) - len(fails) - len(skips)} PASS、{len(skips)} SKIP、{len(fails)} FAIL')
    if POSIX and skips: print('注意：Linux 上不應有 SKIP；有 SKIP 請勿執行真實查詢')
    if fails: sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('selftest')
    f = sub.add_parser('fetch'); f.add_argument('--sample', action='store_true'); f.add_argument('--start'); f.add_argument('--end'); f.add_argument('--resume', action='store_true'); f.add_argument('--run', required=True); f.add_argument('--out', default=os.path.expanduser('~/fill_audit_out'))
    k = sub.add_parser('check'); k.add_argument('--trades', required=True); k.add_argument('--run', required=True); k.add_argument('--out', default=os.path.expanduser('~/fill_audit_out'))
    a = ap.parse_args()
    if a.cmd == 'selftest': selftest(); return
    if a.cmd == 'check':
        no_symlink_components(a.out); run_check(run_dir(a.out, a.run), a.trades); return
    c = ReadOnlyClient(os.environ.get('BINANCE_API_KEY'), os.environ.get('BINANCE_API_SECRET'))
    try:
        srv = c.sync()
        if a.sample: end = srv; start = end - WINDOW_MS
        elif a.resume and not (a.start or a.end): start = end = None          # 續跑沿用 run.json 的起訖
        else:
            if not (a.start and a.end): ap.error('需要 --sample，或 --start 與 --end')
            start = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
            end = min(int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY - 1, srv)
            if srv - start > 90 * DAY: print('提醒：userTrades 只支援近 3 個月、allOrders 查不到建立超過 90 天的訂單；更早的部分不會回傳', file=sys.stderr)
        res, done, skipped = run_fetch(c, a.out, start, end, a.resume, a.run)
    except ApiError as e:
        print(f'失敗：{e}。已完成的窗口保留在檢查點，可用同一個 --run 加 --resume 續跑。', file=sys.stderr); sys.exit(2)
    print(f'輸出：{run_dir(a.out, a.run)}（窗口 新查 {done}、跳過 {skipped}；本機與伺服器時差 {c.offset_ms} ms）')
    for n, (cnt, h) in res.items(): print(f'  {n}：{cnt} 筆  sha256 {h}')
    print('呼叫紀錄（只含路徑與參數名稱）：', sorted(set(c.calls)))


if __name__ == '__main__':
    main()
