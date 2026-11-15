#!/usr/bin/env python3
"""V85 唯讀成交時間提取工具（供使用者審查後在 VPS 親自執行；研究 AI 不執行）。

安全設計：
- 只用 Python 標準函式庫（urllib、hmac、hashlib），不匯入 binance_trade、executor、strategy 或任何專案模組，
  不會啟動交易流程。
- 只允許下列 GET 路徑，其餘一律拒絕：
    /fapi/v1/time         公開，取伺服器時間（不簽名）
    /fapi/v1/userTrades   簽名 GET，逐筆成交（官方：只支援近 3 個月；單次 startTime～endTime ≤ 7 天）
    /fapi/v1/allOrders    簽名 GET，訂單（官方：查詢期間 < 7 天；建立超過 90 天的訂單查不到）
  程式內沒有任何 POST、PUT、DELETE。
- 標的固定 ETHUSDT；主網位址固定 https://fapi.binance.com。
- 金鑰只從環境變數 BINANCE_API_KEY／BINANCE_API_SECRET 讀取（或 --env-file 唯讀解析這兩行），
  不寫入任何檔案、不印出；簽章與原始回應不寫入輸出。建議另建「只開讀取權限」的 API 金鑰。
- 輸出只有精簡欄位的 CSV，預設放在 ~/fill_audit_out/（專案目錄之外），留在使用者機器上。
- 不修改 .env、策略、服務、排程或實盤狀態。

用法：
  python3 extract_fills_readonly.py --selftest                 # 離線自我測試，不連網
  python3 extract_fills_readonly.py --sample                   # 最近 7 天一個窗口（小樣本核對）
  python3 extract_fills_readonly.py --start 2026-08-20 --end 2026-11-14   # 自動切成 ≤7 天窗口
可加 --env-file ~/cryptoBot/.env（只讀 BINANCE_API_KEY、BINANCE_API_SECRET 兩行）、--out 目錄。
"""
import argparse, csv, hashlib, hmac, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = 'https://fapi.binance.com'
SYMBOL = 'ETHUSDT'
ALLOWED = {'/fapi/v1/time': False, '/fapi/v1/userTrades': True, '/fapi/v1/allOrders': True}  # 路徑: 是否簽名
WINDOW_MS = 7 * 24 * 3600 * 1000 - 1000   # 比 7 天少 1 秒，避免邊界被拒
TRADE_FIELDS = ['time', 'id', 'orderId', 'side', 'positionSide', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', 'realizedPnl', 'maker']
ORDER_FIELDS = ['orderId', 'time', 'updateTime', 'side', 'positionSide', 'type', 'origType', 'status', 'reduceOnly', 'closePosition', 'avgPrice', 'executedQty', 'origQty', 'stopPrice', 'workingType']


class ReadOnlyClient:
    def __init__(self, key, secret, opener=None):
        self._key = key; self._secret = secret.encode() if secret else b''
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=20))
        self.calls = []   # 只記路徑與參數名稱，不記值、不記簽章

    def get(self, path, params=None):
        if path not in ALLOWED: raise PermissionError(f'路徑不在允許清單：{path}')
        params = dict(params or {})
        if ALLOWED[path]:
            if not self._key or not self._secret: raise RuntimeError('缺少 BINANCE_API_KEY／BINANCE_API_SECRET')
            params['timestamp'] = int(time.time() * 1000); params['recvWindow'] = 10000
            q = urllib.parse.urlencode(params)
            q += '&signature=' + hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
        else:
            q = urllib.parse.urlencode(params)
        req = urllib.request.Request(BASE + path + ('?' + q if q else ''), method='GET')
        if ALLOWED[path]: req.add_header('X-MBX-APIKEY', self._key)
        assert req.get_method() == 'GET'
        self.calls.append((path, sorted(k for k in params if k != 'signature')))
        with self._open(req) as r:
            return json.loads(r.read().decode())


def windows(start_ms, end_ms):
    s = start_ms
    while s < end_ms:
        e = min(s + WINDOW_MS, end_ms); yield s, e; s = e + 1


def fetch(client, start_ms, end_ms):
    trades, orders = [], []
    for s, e in windows(start_ms, end_ms):
        from_params = dict(symbol=SYMBOL, startTime=s, endTime=e, limit=1000)
        tr = client.get('/fapi/v1/userTrades', from_params)
        if len(tr) >= 1000: print(f'警告：{ms_iso(s)} 起的窗口成交達 1000 筆上限，可能截斷', file=sys.stderr)
        trades += [t for t in tr if t.get('symbol', SYMBOL) == SYMBOL]
        od = client.get('/fapi/v1/allOrders', dict(symbol=SYMBOL, startTime=s, endTime=e, limit=1000))
        if len(od) >= 1000: print(f'警告：{ms_iso(s)} 起的窗口訂單達 1000 筆上限，可能截斷', file=sys.stderr)
        orders += [o for o in od if o.get('symbol', SYMBOL) == SYMBOL]
        time.sleep(0.3)
    return trades, orders


def ms_iso(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def write(out, name, rows, fields):
    os.makedirs(out, exist_ok=True); p = os.path.join(out, name)
    seen = set(); keep = []
    for r in rows:
        k = (r.get('id'), r.get('orderId'), r.get('time'))
        if k in seen: continue
        seen.add(k); keep.append(r)
    keep.sort(key=lambda r: (int(r.get('time', 0)), int(r.get('id', r.get('orderId', 0)))))
    with open(p, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(fields + [c + '_utc' for c in fields if c in ('time', 'updateTime')])
        for r in keep: w.writerow([r.get(c, '') for c in fields] + [ms_iso(int(r[c])) for c in fields if c in ('time', 'updateTime') and r.get(c) not in (None, '')])
    h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    return p, len(keep), h


def load_env_file(path):
    vals = {}
    with open(path, encoding='utf-8') as f:   # 唯讀
        for line in f:
            line = line.strip()
            if line.startswith(('BINANCE_API_KEY=', 'BINANCE_API_SECRET=')):
                k, v = line.split('=', 1); vals[k] = v.strip().strip('"').strip("'")
    return vals


def selftest():
    """離線測試：假的 opener，確認只發 GET、路徑在允許清單、窗口 ≤7 天、輸出不含金鑰與簽章。"""
    import tempfile
    seen = []
    class Resp:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake(req):
        assert req.get_method() == 'GET'; seen.append(req.full_url)
        u = urllib.parse.urlparse(req.full_url); q = urllib.parse.parse_qs(u.query)
        if u.path == '/fapi/v1/userTrades':
            s = int(q['startTime'][0]); e = int(q['endTime'][0]); assert e - s <= 7 * 24 * 3600 * 1000
            return Resp(json.dumps([dict(symbol='ETHUSDT', id=s // 1000, orderId=7, side='BUY', positionSide='LONG', price='2500.1', qty='1.6', quoteQty='4000.16', commission='1.6', commissionAsset='USDT', realizedPnl='0', maker=False, buyer=True, time=s + 10_123)]).encode())
        if u.path == '/fapi/v1/allOrders':
            s = int(q['startTime'][0])
            return Resp(json.dumps([dict(symbol='ETHUSDT', orderId=7, clientOrderId='x', time=s + 10_050, updateTime=s + 10_130, side='BUY', positionSide='LONG', type='MARKET', origType='MARKET', status='FILLED', reduceOnly=False, closePosition=False, avgPrice='2500.1', executedQty='1.6', origQty='1.6', stopPrice='0', workingType='CONTRACT_PRICE')]).encode())
        raise AssertionError(u.path)
    c = ReadOnlyClient('TESTKEY_ABC', 'TESTSECRET_XYZ', opener=fake)
    try: c.get('/fapi/v1/order', {}); raise AssertionError('應拒絕')
    except PermissionError: pass
    end = 1_763_000_000_000; start = end - 20 * 24 * 3600 * 1000
    tr, od = fetch(c, start, end)
    assert len(list(windows(start, end))) == 3 and len(tr) == 3 and len(od) == 3
    d = tempfile.mkdtemp(); p1, n1, _ = write(d, 'trades.csv', tr, TRADE_FIELDS); p2, n2, _ = write(d, 'orders.csv', od, ORDER_FIELDS)
    blob = open(p1).read() + open(p2).read()
    assert 'TESTKEY' not in blob and 'TESTSECRET' not in blob and 'signature' not in blob
    assert all('signature=' in u for u in seen)   # 有簽名
    assert all(not u.startswith(BASE + '/fapi/v1/order?') for u in seen)
    print('selftest ok：只發 GET、路徑在允許清單、窗口 ≤7 天、輸出不含金鑰或簽章', n1, n2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--selftest', action='store_true'); ap.add_argument('--sample', action='store_true', help='最近 7 天一個窗口')
    ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--env-file'); ap.add_argument('--out', default=os.path.expanduser('~/fill_audit_out'))
    a = ap.parse_args()
    if a.selftest: selftest(); return
    key = os.environ.get('BINANCE_API_KEY', ''); sec = os.environ.get('BINANCE_API_SECRET', '')
    if a.env_file: v = load_env_file(a.env_file); key = v.get('BINANCE_API_KEY', key); sec = v.get('BINANCE_API_SECRET', sec)
    c = ReadOnlyClient(key, sec)
    srv = c.get('/fapi/v1/time')['serverTime']; drift = srv - int(time.time() * 1000)
    if a.sample: end = srv; start = end - WINDOW_MS
    else:
        if not (a.start and a.end): ap.error('需要 --sample 或 --start/--end')
        start = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
        end = min(int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000) + 86_399_999, srv)
        if srv - start > 90 * 24 * 3600 * 1000: print('提醒：userTrades 官方只支援近 3 個月；allOrders 查不到建立超過 90 天的訂單。更早的部分不會回傳。', file=sys.stderr)
    tr, od = fetch(c, start, end)
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    p1, n1, h1 = write(a.out, f'ethusdt_trades_{tag}.csv', tr, TRADE_FIELDS); p2, n2, h2 = write(a.out, f'ethusdt_orders_{tag}.csv', od, ORDER_FIELDS)
    print(f'區間 {ms_iso(start)} ～ {ms_iso(end)}；本機與伺服器時差 {drift} ms')
    print(f'成交 {n1} 筆 → {p1}  sha256 {h1}')
    print(f'訂單 {n2} 筆 → {p2}  sha256 {h2}')
    print('呼叫紀錄（只含路徑與參數名稱）：', sorted(set((p, tuple(k)) for p, k in c.calls)))


if __name__ == '__main__':
    main()
