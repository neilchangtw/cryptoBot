"""Q20W30＋固定風險下單：逐事件執行器與模擬交易所（Hedge Mode、逐倉 5 倍）。離線驗證用，不連線。
事件順序（每根 1h 收盤）：交易所端先結算資金費與觸發止損（不由執行器發動），執行器再處理止損成交回報 → 出場決策 → 進場決策。決策來自參考帳本（訊號引擎），數量與檢查用 q20fr_core。"""
import json
import q20fr_core as QC

class SimExchange:
    """只依交易所規則記帳：positionSide 各一個部位、逐倉保證金、clientOrderId 去重、數量精度／最小名目檢查、可注入部分成交與拒單。"""
    def __init__(s, wallet, partial=None, reject_every=0, cancel_rest=False):
        s.wallet = wallet; s.pos = {'L': None, 'S': None}; s.stops = {}; s.seen = {}; s.fills = []; s.n_orders = 0; s.rejects = 0; s.partial = partial; s.reject_every = reject_every; s.cancel_rest = cancel_rest; s.violations = []; s.lev = {'L': None, 'S': None}; s.margin_type = None
    def set_leverage(s, side, lev, margin_type='ISOLATED'): s.lev[side] = lev; s.margin_type = margin_type
    def equity(s, mark): return s.wallet + sum((1 if k == 'L' else -1) * p['q'] * (mark - p['avg']) for k, p in s.pos.items() if p)
    def used(s): return sum(p['mg'] for p in s.pos.values() if p)
    def market(s, cid, side, dq, px, reduce=False, fee_ntl=None):
        """dq>0 開倉、<0 減倉。回傳成交回報清單（可能多筆部分成交）。相同 cid 重送時回傳先前結果，不重複成交。"""
        if cid in s.seen: return s.seen[cid]
        s.n_orders += 1
        if s.reject_every and s.n_orders % s.reject_every == 0: s.rejects += 1; return [dict(cid=cid, status='REJECTED', code=-1001)]   # 暫時性錯誤：不記入 seen，可用同 cid 重試
        q = abs(dq)
        if abs(q / QC.STEP - round(q / QC.STEP)) > 1e-6: s.violations.append((cid, '精度')); out = [dict(cid=cid, status='REJECTED', code=-1111)]; s.seen[cid] = out; return out
        if not reduce:
            if q * px < QC.MIN_NTL: s.violations.append((cid, '最小名目')); out = [dict(cid=cid, status='REJECTED', code=-4164)]; s.seen[cid] = out; return out
            if s.lev[side] != QC.LEV or s.margin_type != 'ISOLATED': s.violations.append((cid, '槓桿或保證金模式'))
            if s.equity(px) - s.used() < q * px / QC.LEV - 1e-9: out = [dict(cid=cid, status='REJECTED', code=-2019)]; s.seen[cid] = out; return out
            if s.pos[side] is not None: s.violations.append((cid, '同向已有部位'))
        else:
            if s.pos[side] is None or q > s.pos[side]['q'] + 1e-9: s.violations.append((cid, '減倉超量')); out = [dict(cid=cid, status='REJECTED', code=-2022)]; s.seen[cid] = out; return out
        parts = [q] if (reduce or not s.partial) else [QC.floor_qty(q * s.partial), None]
        if len(parts) == 2: parts[1] = round(q - parts[0], 6); parts = parts[:1] if s.cancel_rest else parts
        out = []; done = 0.0
        for k, pq in enumerate(parts):
            if pq <= 0: continue
            done += pq; s._apply(side, pq if not reduce else -pq, px, fee_ntl if fee_ntl is not None else pq * px)
            out.append(dict(cid=cid, status='FILLED' if abs(done - q) < 1e-9 else 'PARTIALLY_FILLED', side=side, qty=pq if not reduce else -pq, px=px, cum=done))
        if out and out[-1]['status'] == 'PARTIALLY_FILLED': out.append(dict(cid=cid, status='CANCELED', cum=done))
        s.seen[cid] = out; return out
    def _apply(s, side, dq, px, fee_ntl):
        sg = 1 if side == 'L' else -1; fee = QC.HC * fee_ntl; s.wallet -= fee; s.fills.append((side, dq, px, fee))
        if dq > 0:
            p = s.pos[side]
            if p is None: s.pos[side] = dict(q=dq, avg=px, mg=dq * px / QC.LEV)
            else: p['avg'] = (p['avg'] * p['q'] + px * dq) / (p['q'] + dq); p['q'] = round(p['q'] + dq, 6); p['mg'] = p['q'] * p['avg'] / QC.LEV
        else:
            p = s.pos[side]; s.wallet += sg * (-dq) * (px - p['avg']); p['q'] = round(p['q'] + dq, 6)
            if p['q'] <= 1e-9: s.pos[side] = None; s.stops.pop(side, None)   # closePosition 止損隨部位清空而失效
            else: p['mg'] = p['q'] * p['avg'] / QC.LEV
    def place_stop(s, cid, side, stop_px):
        if side in s.stops and s.stops[side]['cid'] != cid: s.violations.append((cid, '同向已有 closePosition 止損'))
        s.stops[side] = dict(cid=cid, px=stop_px)
    def trigger_stop(s, side, fill_px, fee_ntl):
        """止損觸發：closePosition＝平掉該方向全部數量。回傳成交回報。"""
        p = s.pos[side]; q = p['q']; cid = s.stops[side]['cid']; s._apply(side, -q, fill_px, fee_ntl); return dict(cid=cid, status='FILLED', side=side, qty=-q, px=fill_px, stop=True)
    def funding(s, rate, mark):
        for side, p in s.pos.items():
            if p: s.wallet -= (1 if side == 'L' else -1) * rate * p['q'] * mark
    def query(s, cid): return s.seen.get(cid)
    def snapshot(s): return dict(wallet=s.wallet, pos={k: (dict(v) if v else None) for k, v in s.pos.items()}, stops={k: dict(v) for k, v in s.stops.items()})

class Executor:
    """狀態只有：已處理到的事件時刻、每筆持倉的紀錄、已完成交易。可序列化成 JSON；重啟時與交易所端對帳。
    下單一律「先查後送」：同一決策的 clientOrderId 固定（E-/X-/S-＋參考編號），已存在就取回原回報，不重複成交。"""
    def __init__(s, ex, state=None, max_retry=3):
        s.ex = ex; s.max_retry = max_retry; st = state or {}; s.last_t = st.get('last_t'); s.open = st.get('open', {}); s.done = st.get('done', []); s.skips = st.get('skips', []); s.log = st.get('log', []); s.reconcile_issues = []
        for side in ('L', 'S'): ex.set_leverage(side, QC.LEV, 'ISOLATED')
    def dump(s): return json.dumps(dict(last_t=s.last_t, open=s.open, done=s.done, skips=s.skips, log=s.log))
    @classmethod
    def restore(cls, ex, blob, strict=True):
        e = cls(ex, json.loads(blob)); snap = ex.snapshot()
        if strict:
            for side in ('L', 'S'):   # 對帳：本地持倉紀錄須與交易所端一致，且有保護止損
                loc = next((v for v in e.open.values() if v['side'] == side), None); rem = snap['pos'][side]
                if (loc is None) != (rem is None) or (loc and abs(loc['q'] - rem['q']) > 1e-9): e.reconcile_issues.append((side, '部位不符', loc, rem))
                if loc and side not in snap['stops']: e.reconcile_issues.append((side, '缺保護止損'))
        return e
    def _send(s, cid, side, dq, px, reduce=False, fee_ntl=None):
        r = s.ex.query(cid)
        if r is not None: s.log.append(('found_existing', cid)); return r
        for k in range(s.max_retry):
            r = s.ex.market(cid, side, dq, px, reduce, fee_ntl)
            if r[0]['status'] != 'REJECTED' or r[0]['code'] != -1001: return r
            s.log.append(('retry', cid, k + 1))
        return r
    def on_bar(s, t, close, fund_rate, exits, entries, stop_reports=None, final=False):
        """t：ISO 字串。exits/entries：該根的決策（dict：ref, side, ep/xp, reason, snm）。stop_reports：交易所端已觸發的止損回報 {ref: 回報}。重複送達的事件直接略過。"""
        if s.last_t is not None and t <= s.last_t: s.log.append(('dup_bar', t)); return
        if fund_rate:
            for o in s.open.values(): o['fund'] += (1 if o['side'] == 'L' else -1) * fund_rate * o['q'] * close
        for d in exits:
            o = s.open.get(str(d['ref']))
            if o is None: continue
            if d['reason'] == 'SN': rep = [(stop_reports or {})[d['ref']]]
            else: rep = s._send(f"X-{d['ref']}", o['side'], -o['q'], d['xp'], reduce=True, fee_ntl=o['ntl'])
            s._on_exit(t, d, o, rep)
        for d in entries:
            cid = f"E-{d['ref']}"; wallet = s.ex.wallet; rep = s.ex.query(cid)
            if rep is None:
                q = QC.order_qty(wallet, d['side'], d['ep'], d.get('snm', 1.0)); ok, why, need = QC.check_order(q, d['ep'], s.ex.equity(close), s.ex.used())
                if not ok: s.skips.append(dict(t=t, ref=d['ref'], side=d['side'], 原因=why)); continue
                rep = s._send(cid, d['side'], q, d['ep'])
            else: s.log.append(('found_existing', cid))
            filled = sum(r['qty'] for r in rep if r.get('qty'))
            if filled <= 0: s.skips.append(dict(t=t, ref=d['ref'], side=d['side'], 原因=f"拒單 {rep[0].get('code')}")); continue
            s.ex.place_stop(f"S-{d['ref']}", d['side'], QC.stop_price(d['side'], d['ep'], d.get('snm', 1.0)))   # 保護單涵蓋實際持倉（closePosition）
            s.open[str(d['ref'])] = dict(ref=d['ref'], side=d['side'], q=round(filled, 6), ep=d['ep'], ntl=filled * d['ep'], t_in=t, nfill=sum(1 for r in rep if r.get('qty')), fund=0.0)
        if final:
            for k, o in list(s.open.items()): rep = s._send(f"X-{o['ref']}", o['side'], -o['q'], close, reduce=True, fee_ntl=o['ntl']); s._on_exit(t, dict(ref=o['ref'], xp=close, reason='END'), o, rep)
        s.last_t = t
    def _on_exit(s, t, d, o, rep):
        q = -sum(r['qty'] for r in rep if r.get('qty')); sg = 1 if o['side'] == 'L' else -1
        if abs(q - o['q']) > 1e-9: s.log.append(('exit_qty_mismatch', o['ref'], q, o['q']))
        gross = sg * o['q'] * (d['xp'] - o['ep']); cost = 2 * QC.HC * o['ntl']
        s.done.append(dict(ref=o['ref'], side=o['side'], t_in=o['t_in'], t_out=t, q=o['q'], ep=o['ep'], xp=d['xp'], reason=d['reason'], gross=gross, cost=cost, fund=o['fund'], net=gross - cost - o['fund'], nfill=o['nfill'])); del s.open[str(o['ref'])]
