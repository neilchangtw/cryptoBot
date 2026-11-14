"""V70 共用有限帳戶逐小時事件重播引擎（1,500U、不補款）。現行腿（參考帳本交易）＋日線趨勢腿共用同一套部位、訂單與風控記帳。
事件順序（每個小時收盤時刻，事前固定）：① 既有部位市值計價與資金費 → ② 現行腿出場 → ③ 趨勢腿翻向或調整 → ④ 現行腿進場。
錢包餘額＝初始＋已實現損益（平均成本法）−手續費與執行成本−資金費（對應交易所 walletBalance）；權益＝錢包餘額＋未實現損益。
成本：每次成交 11.25bp × 成交名目（現行腿進出場皆以進場名目計，與既有基準一致）。"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57_metrics as M
CAP = 1500.0; W0 = pd.Timestamp('2021-01-01'); W1 = pd.Timestamp('2026-10-31 23:00'); MMR = 0.005; HC = 0.001125; STEP = 0.001; MIN_NTL = 20.0

def rq(x): return np.floor(x / STEP + 1e-9) * STEP

def replay(df, fund, base=None, base_mode=('fixed', 4000.0, 20.0), sleeve=None, cap=CAP, cross=False):
    """base：參考帳本交易（side,t_in,t_out,ep,xp,reason,snm）。base_mode：('fixed',名目,槓桿) 或 ('risk',f,槓桿)。
    sleeve：dict(sig=日線方向 Series（index＝UTC 00:00 決策時刻，值 ±1/0）, mode='hold'|'rebal', g, band, lev)。cross＝全倉（強平以帳戶層級判定）。"""
    px = C.close_utc(df); px = px[(px.index >= W0 - pd.Timedelta(hours=1)) & (px.index <= W1)]; idx = px.index; c = px.values; n = len(idx)
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); hl = pd.DataFrame(dict(h=df.high.values, l=df.low.values), index=ct).reindex(idx); hi = hl.h.values; lo = hl.l.values
    fr = fund.reindex(idx).fillna(0.0).values; ip = {t: i for i, t in enumerate(idx)}
    ent, ext = {}, {}
    if base is not None:
        for k, r in enumerate(base.itertuples()):
            if r.t_in < W0 or r.t_in not in ip: continue
            ent.setdefault(ip[r.t_in], []).append((k, r))
    act = {}
    if sleeve:
        for t, v in sleeve['sig'].items():
            te = t + pd.Timedelta(hours=1)
            if te in ip and te >= W0 and not np.isnan(v): act[ip[te]] = int(v)
    eq = cap; wal = cap; E = np.full(n, np.nan); EW = np.full(n, np.nan); MG = np.zeros(n); NT = np.zeros(n); WL = np.full(n, np.nan); UP = np.zeros(n)
    op = {}; done = []; skips = []; fills = []; liq_acct = 0; tid = 0; sl = None; E[0] = EW[0] = WL[0] = cap

    def close(p, i, xp, reason):
        nonlocal eq, wal
        adj = p['s'] * p['q'] * (xp - c[i]); p['gross'] += adj; eq += adj; cost = HC * (p['cn'] if p['leg'] == 'base' else p['q'] * xp); p['cost'] += cost; eq -= cost; wal += p['s'] * p['q'] * (xp - p['avg']) - cost
        fills.append((idx[i], p['id'], p['leg'], p['s'], -p['q'], xp)); p.update(t_out=idx[i], xp=xp, reason=reason, net=p['gross'] - p['cost'] - p['fund']); done.append(p); del op[p['id']]

    def opn(i, leg, s, q, lev, reason='', snm=1.0, price=None):
        nonlocal eq, wal, tid
        price = c[i] if price is None else price; ntl = q * price; cost = HC * ntl; tid += 1; adj = s * q * (c[i] - price)
        p = dict(id=tid, leg=leg, s=s, side='L' if s > 0 else 'S', q=q, avg=price, ep=price, t_in=idx[i], lev=lev, mg=ntl / lev, cn=ntl, ntl=ntl, maxntl=ntl, gross=adj, cost=cost, fund=0.0, liq=False, nfill=1, reason=reason, snm=snm)
        eq += adj - cost; wal -= cost; op[tid] = p; fills.append((idx[i], tid, leg, s, q, price)); return p

    for i in range(1, n):
        d = c[i] - c[i - 1]; adv = 0.0
        for p in op.values():
            g = p['s'] * p['q'] * d; p['gross'] += g; eq += g; adv += p['q'] * ((c[i] - lo[i]) if p['s'] > 0 else (hi[i] - c[i]))
            if fr[i] != 0.0: pay = p['s'] * fr[i] * p['q'] * c[i]; p['fund'] += pay; eq -= pay; wal -= pay
            if not cross:
                lp = p['avg'] * (1 - p['s'] / p['lev']) / (1 - p['s'] * MMR)
                if (lo[i] <= lp) if p['s'] > 0 else (hi[i] >= lp): p['liq'] = True
        ew = eq - adv
        if cross and op and ew <= MMR * sum(p['q'] * c[i] for p in op.values()): liq_acct += 1
        for p in [p for p in op.values() if p['leg'] == 'base' and p['x_i'] == i]: close(p, i, p['x_px'], p['x_reason'])
        if i in act:
            v = act[i]; cur = sl['s'] if sl is not None and sl['id'] in op else 0
            if v != cur:
                if cur != 0: close(sl, i, c[i], 'TREND'); sl = None
                if v != 0:
                    basis = wal if sleeve['mode'] == 'hold' else eq; q = rq(sleeve['g'] * basis / c[i])
                    if q * c[i] >= MIN_NTL:
                        used = sum(p['mg'] for p in op.values())
                        if eq - used >= q * c[i] / sleeve['lev']: sl = opn(i, 'trend', v, q, sleeve['lev'], 'TREND')
                        else: skips.append(dict(t=idx[i], leg='trend', side='L' if v > 0 else 'S', 原因='可用保證金不足', 權益=round(eq, 1), 已占用=round(used, 1), 需要=round(q * c[i] / sleeve['lev'], 1)))
            elif cur != 0 and sleeve['mode'] == 'rebal':
                tgt = sleeve['g'] * eq; now = sl['q'] * c[i]
                if tgt > 0 and abs(now / tgt - 1) > sleeve['band']:
                    dq = rq(tgt / c[i]) - sl['q']
                    if abs(dq) * c[i] >= MIN_NTL and sl['q'] + dq > 0:
                        cost = HC * abs(dq) * c[i]; sl['cost'] += cost; eq -= cost; wal -= cost; sl['nfill'] += 1
                        if dq > 0: sl['avg'] = (sl['avg'] * sl['q'] + c[i] * dq) / (sl['q'] + dq)
                        else: wal += sl['s'] * (-dq) * (c[i] - sl['avg'])
                        sl['q'] += dq; sl['mg'] = sl['q'] * sl['avg'] / sl['lev']; sl['maxntl'] = max(sl['maxntl'], sl['q'] * c[i]); fills.append((idx[i], sl['id'], 'trend', sl['s'], dq, c[i]))
        for k, r in ent.get(i, []):
            s = 1 if r.side == 'L' else -1
            if base_mode[0] == 'fixed': q = base_mode[1] * getattr(r, 'scale', 1.0) / r.ep
            else: q = rq(base_mode[1] * wal / ((0.035 if s > 0 else 0.040) * getattr(r, 'snm', 1.0)) / r.ep)
            ntl = q * r.ep; used = sum(p['mg'] for p in op.values()); need = ntl / base_mode[2]
            if ntl < MIN_NTL: skips.append(dict(t=idx[i], leg='base', side=r.side, 原因='名目低於最小下單額', 權益=round(eq, 1), 已占用=round(used, 1), 需要=round(need, 1))); continue
            if eq - used < need: skips.append(dict(t=idx[i], leg='base', side=r.side, 原因='可用保證金不足', 權益=round(eq, 1), 已占用=round(used, 1), 需要=round(need, 1))); continue
            p = opn(i, 'base', s, q, base_mode[2], snm=getattr(r, 'snm', 1.0), price=r.ep); xo = min(r.t_out, W1); p.update(x_i=ip[xo], x_px=r.xp if r.t_out <= W1 else c[ip[xo]], x_reason=r.reason if r.t_out <= W1 else 'END', ref=k, wallet=wal)
        if i == n - 1:
            for p in list(op.values()): close(p, i, c[i] if p['leg'] != 'base' else p['x_px'], p.get('x_reason', 'TREND') if p['leg'] == 'base' else 'TREND(期末結算)')
        E[i] = eq; EW[i] = min(ew, eq); MG[i] = sum(p['mg'] for p in op.values()); NT[i] = sum(p['q'] * c[i] for p in op.values()); WL[i] = wal; UP[i] = eq - wal
    tr = pd.DataFrame([{k: p.get(k) for k in ('id', 'leg', 'side', 't_in', 't_out', 'ep', 'xp', 'reason', 'ntl', 'maxntl', 'lev', 'gross', 'cost', 'fund', 'net', 'liq', 'nfill', 'snm', 'ref', 'wallet')} for p in done]).sort_values(['t_out', 't_in']).reset_index(drop=True)
    acc = pd.DataFrame(dict(equity=E, equity_intrahour=EW, wallet=WL, unrealized=UP, margin=MG, notional=NT), index=idx).iloc[1:]; acc['free'] = acc.equity - acc.margin
    return dict(tr=tr, acc=acc, skips=pd.DataFrame(skips), fills=pd.DataFrame(fills, columns=['t', 'id', 'leg', 's', 'dq', 'px']), liq_acct=liq_acct, cross=cross)

def merged_check(R, df):
    """Hedge Mode 同向合併驗證：把各腿成交併成 LONG／SHORT 兩個部位，以平均成本法計已實現＋期末未實現，須等於分腿帳本毛損益；減倉量不得超過合併部位。"""
    out = {}
    for s, nm in ((1, 'LONG'), (-1, 'SHORT')):
        f = R['fills'][R['fills'].s == s].sort_values('t', kind='stable'); q = 0.0; avg = 0.0; real = 0.0; viol = 0; mx = 0.0
        for r in f.itertuples():
            if r.dq > 0: avg = (avg * q + r.px * r.dq) / (q + r.dq); q += r.dq
            else:
                if -r.dq > q + 1e-9: viol += 1
                real += s * (-r.dq) * (r.px - avg); q += r.dq
            mx = max(mx, q)
        out[nm] = dict(合併已實現=round(real, 4), 分腿毛損益=round(R['tr'][R['tr'].side == ('L' if s > 0 else 'S')].gross.sum(), 4), 期末數量=round(q, 6), 減倉超量次數=viol, 同時多筆最大數量=round(mx, 3))
    return out

def dd3(e, cap=CAP):
    pk = np.maximum(e.cummax(), cap); dd = pk - e; j = dd.idxmax(); pct = dd / pk; k = pct.idxmax()
    return dict(MDD金額=round(dd.max()), MDD谷底=str(j)[:13], 最大百分比回撤=round(pct.max() * 100, 1), 百分比回撤高點=str(e[:k].idxmax())[:13], 百分比回撤谷底=str(k)[:13], MDD除以初始=round(dd.max() / cap * 100, 1))

def streaks(t):
    t = t.sort_values(['t_out', 't_in']); best_n = 0; worst = 0.0; n = 0; s = 0.0; segs = []
    for x in t.net.values:
        if x < 0: n += 1; s += x
        else:
            if n: segs.append((n, s))
            n = 0; s = 0.0
    if n: segs.append((n, s))
    return (max([a for a, _ in segs]) if segs else 0), (min([b for _, b in segs]) if segs else 0.0), segs

def wr_row(name, t, base_wr=None):
    if len(t) == 0: return dict(版本=name, 筆數=0)
    w = (t.net > 0).sum(); l = (t.net < 0).sum(); z = (t.net == 0).sum(); mx, worst, segs = streaks(t); wr = w / len(t) * 100
    d = dict(版本=name, 筆數=len(t), 勝=int(w), 敗=int(l), 平=int(z), 勝率=round(wr, 2), 較現行百分點=None if base_wr is None else round(wr - base_wr, 2), 平均贏單=round(t[t.net > 0].net.mean(), 1) if w else 0, 平均輸單=round(t[t.net < 0].net.mean(), 1) if l else 0,
             每筆平均淨利=round(t.net.mean(), 2), 最長連敗=int(mx), 最差連敗段=round(worst, 1), MH筆=int((t.reason == 'MH').sum()), MH淨=round(t[t.reason == 'MH'].net.sum()), SN筆=int((t.reason == 'SN').sum()), SN淨=round(t[t.reason == 'SN'].net.sum())); return d

def summary(name, R, df, cap=CAP):
    tr = R['tr']; acc = R['acc']; e = acc.equity; inc = e.diff(); inc.iloc[0] = e.iloc[0] - cap; ye = e.groupby(e.index.year).last(); yr = ye.diff(); yr.iloc[0] = ye.iloc[0] - cap
    inc_full = inc.copy(); c = tr.rename(columns={}).copy(); Rm, T = M.evaluate(inc_full, c[['side', 't_in', 't_out', 'net']].assign(ep=tr.ep, xp=tr.xp), name)
    d = dict(版本=name, 淨利=round(e.iloc[-1] - cap), 月均=round((e.iloc[-1] - cap) / 70, 1), **{f'y{int(k)}': round(v) for k, v in yr.items()}, **dd3(e, cap), 最低權益=round(e.min()), 最低權益時刻=str(e.idxmin())[:13], 小時內保守最低權益=round(acc.equity_intrahour.min()),
             最低可用資金=round(acc.free.min()), 最高保證金占用=round(acc.margin.max()), 最大名目=round(acc.notional.max()), 強平觸及_逐倉估算=int(tr.liq.sum()) if not R['cross'] else 0, 強平觸及_全倉帳戶=R['liq_acct'], 跳過=len(R['skips']),
             五年MDD=round(Rm['mdd_5y']), 最差滾動12月=round(Rm['r12_worst']), 最差連敗段_五年口徑=round(Rm['streak_worst_loss']), 最差90天=round(Rm['r90_worst']), 回復天數=Rm['mdd_recovery_days_5y'], 連敗4以上每百筆=Rm['streak4_per100'], 連敗6以上每百筆=Rm['streak6_per100'])
    d.update({k: v for k, v in wr_row(name, tr).items() if k != '版本'}); return d
