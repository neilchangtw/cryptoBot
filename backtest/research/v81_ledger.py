"""V81 獨立事件帳本模擬器（不重用替換後的研究引擎；只共用指標層輸出與價格資料）。
語意：1 小時 K 在 T 收盤時決策，進場與市價出場在 T+k 分成交；常駐止損在持倉與等待期間都有效。
每筆交易保留完整事件：訊號時刻、委託意圖、成交、止損觸發、出場決策、出場成交、損益入帳時點。
可選帳戶掛鉤（資金受限自洽狀態機）：成交時刻可用保證金不足即整筆拒絕，被拒者不產生部位、損益、冷卻或熔斷。"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v14_export_trades as G   # 只讀參數常數

P_L = dict(sign=+1, gk=G.L_GK_TH, tp=G.L_TP, sn=G.L_SN, mh=G.L_MH, ext=G.L_EXT, cd=G.L_CD, cap=G.L_CAP, blk_h=set(G.L_BLK_H), blk_d=set(G.L_BLK_D), mcap=G.CB_L_MONTH, slipf=G.L_SN_SLIP,
           tp_reg={'DOWN': 0.040}, mh_reg={'MILD_UP': 7}, trail=(G.L_MFE_ACT, G.L_MFE_TR), cmh=(G.L_CMH_BAR, G.L_CMH_TH, G.L_CMH_MH))
P_S = dict(sign=-1, gk=G.S_GK_TH, tp=G.S_TP, sn=G.S_SN, mh=G.S_MH, ext=G.S_EXT, cd=G.S_CD, cap=G.S_CAP, blk_h=set(G.S_BLK_H), blk_d=set(G.S_BLK_D), mcap=G.CB_S_MONTH, slipf=G.S_SN_SLIP,
           tp_reg={}, mh_reg={'UP': 8}, trail=None, cmh=None)
NTL = 4000.0; COST = 9.0

def regime(s):
    if s is None or not np.isfinite(s): return 'NA'
    if s > 0.045: return 'UP'
    if abs(s) < 0.010: return 'SIDE'
    if s < -0.010: return 'DOWN'
    return 'MILD_UP'

class Minutes:
    def __init__(self, M):
        self.t0 = int(M['t0']); self.hi = M['high']; self.lo = M['low']; self.cl = M['close']
    def idx(self, t): return int((np.datetime64(t, 'ms').astype('int64') - self.t0) // 60000)
    def price(self, t, k): return float(self.cl[self.idx(t) + k - 1])
    def ext(self, a, b):
        """[a, b) 分鐘索引區間的最高與最低。"""
        return float(self.hi[a:b].max()), float(self.lo[a:b].min())

def run(df, ind, M, k=0, sides=('L', 'S'), defer=None, ok=None, acct=None):
    """回傳 (trades DataFrame, events list, cand list)。acct：dict(cap, lev, fund=Series, hc) 啟用資金受限狀態機。"""
    mm = Minutes(M); dtv = pd.DatetimeIndex(df.datetime.values); T = dtv - pd.Timedelta(hours=7); n = len(df)
    hr = dtv.hour.values; dw = dtv.dayofweek.values; mk = (dtv.year * 100 + dtv.month).values; dk = (dtv.year * 10000 + dtv.month * 100 + dtv.day).values
    H = df.high.values; Lo = df.low.values; Cl = df.close.values
    sig = dict(L=(ind['pctile_L'], ind['brk_up'], ind['regime_block_l']), S=(ind['pctile_S'], ind['brk_dn'], ind['regime_block_s'])); slope = ind['slope']
    PP = dict(L=P_L, S=P_S); defer = (k > 0) if defer is None else defer
    pos = {}; last_exit = dict(L=-999, S=-999); m_ent = dict(L=0, S=0); m_pnl = dict(L=0.0, S=0.0); d_pnl = 0.0; consec = 0; consec_end = -999; cm = cd_ = None
    trades = []; events = []; cand = []; rejects = []
    wal = acct['cap'] if acct else None   # 錢包＝初始＋已平倉淨額−未平倉已付成本與資金費
    fidx = acct['fund'].index.values if acct else None; fval = acct['fund'].values if acct else None

    def book_pnl(i, s, pnl):
        nonlocal d_pnl, consec, consec_end
        m_pnl[s] += pnl; d_pnl += pnl
        consec = consec + 1 if pnl < 0 else 0
        if consec >= G.CB_CONSEC: consec_end = i + G.CB_CONSEC_CD

    def unreal(p, price): return PP[p['side']]['sign'] * p['q'] * (price - p['ep'])

    def fund_paid(p, t_to):
        """帳戶口徑：(進場, t_to] 各次結算 × 數量 × 該整點收盤價 × 方向。"""
        a = np.searchsorted(fidx, np.datetime64(p['t_in']), side='right'); b = np.searchsorted(fidx, np.datetime64(t_to), side='right'); tot = 0.0
        for j in range(a, b):
            tot += PP[p['side']]['sign'] * fval[j] * p['q'] * mm.price(fidx[j], 0)
        return tot

    for i in range(G.WARMUP, n):
        if mk[i] != cm: cm = mk[i]; m_ent = dict(L=0, S=0); m_pnl = dict(L=0.0, S=0.0)
        if dk[i] != cd_: cd_ = dk[i]; d_pnl = 0.0
        pend = []; i0 = mm.idx(T[i]) if (k > 0 or acct) else None
        # ---- 既有部位：止損觸發與出場決策
        for s in ('L', 'S'):
            p = pos.get(s)
            if p is None: continue
            q = PP[s]; sg = q['sign']; ep = p['ep']; p['held'] += 1; bh = p['held']
            if bh == 1 and k > 0: hi, lo = mm.ext(i0 - 60 + k, i0)
            else: hi, lo = H[i], Lo[i]
            fav = (hi - ep) / ep if sg > 0 else (ep - lo) / ep; p['mfe'] = max(p['mfe'], fav)
            stop = ep * (1 - sg * q['sn']); adverse = lo if sg > 0 else hi; hit = adverse <= stop if sg > 0 else adverse >= stop
            xp = None; why = None; t_x = T[i]; inbar = False
            if hit:
                xp = stop - sg * abs(stop - adverse) * q['slipf']; why = 'SN'; inbar = True
            else:
                tp = q['tp_reg'].get(p['reg'], q['tp']); tgt = ep * (1 + sg * tp); cp = sg * (Cl[i] - ep) / ep; want = None
                if (hi >= tgt) if sg > 0 else (lo <= tgt): want = 'TP'
                elif q['trail'] and p['mfe'] >= q['trail'][0] and (p['mfe'] - cp) >= q['trail'][1]: want = 'MFE'
                else:
                    if q['cmh'] and bh == q['cmh'][0] and cp <= q['cmh'][1]: p['red'] = True
                    lim = q['cmh'][2] if (q['cmh'] and p['red']) else q['mh_reg'].get(p['reg'], q['mh'])
                    if not p['ext']:
                        if bh >= lim:
                            if cp > 0: p['ext'] = True; p['xb'] = 0
                            else: want = 'MH'
                    else:
                        p['xb'] += 1
                        if (lo <= ep) if sg > 0 else (hi >= ep): want = 'BE'
                        elif p['xb'] >= q['ext']: want = 'MHx'
                if want:
                    why = want; events.append((T[i], s, '出場決策', want, float(Cl[i])))
                    if k == 0: xp = float(Cl[i])
                    else:
                        whi, wlo = mm.ext(i0, i0 + k); wadv = wlo if sg > 0 else whi; t_x = T[i] + pd.Timedelta(minutes=k)
                        if (wadv <= stop) if sg > 0 else (wadv >= stop):
                            xp = stop - sg * abs(stop - wadv) * q['slipf']; why = 'SN'; p['dsn'] = True; events.append((t_x, s, '等待期間止損觸發', want, xp))
                        else: xp = mm.price(T[i], k)
            if xp is not None:
                gross = sg * NTL * (xp - ep) / ep; pnl = gross - COST
                tr = dict(side=s, t_sig=p['t_sig'], t_in=p['t_in'], t_dec=T[i], t_out=t_x, entry_price=ep, exit_price=xp, exit_reason=why, bars_held=bh, dsn=p.get('dsn', False), pnl_rc=pnl, entry_bar=p['bar'], exit_bar=i, regime=p['reg'])
                later = defer and not inbar
                tr['入帳'] = '本根進場判斷之後' if later else '本根進場判斷之前'
                events.append((t_x, s, '止損成交' if why == 'SN' else '市價出場成交', why, xp))
                if acct:
                    f = fund_paid(p, t_x); net = sg * p['q'] * (xp - ep) - 2 * acct['hc'] * p['q'] * ep - f; wal += sg * p['q'] * (xp - ep) - acct['hc'] * p['q'] * ep - (f - p['fpaid']); tr.update(q=p['q'], net_acct=net)
                trades.append(tr); del pos[s]; last_exit[s] = i
                if later: pend.append((s, pnl))
                else: book_pnl(i, s, pnl)
        # ---- 進場判斷（T 當下已知狀態）
        for s in sides:
            q = PP[s]; pc, brk, rb = sig[s]
            if not (np.isfinite(pc[i]) and pc[i] < q['gk'] and bool(brk[i])): continue
            gates = dict(持倉中=s in pos, 日虧=d_pnl <= G.CB_DAILY, 月虧=m_pnl[s] <= q['mcap'], 連虧冷卻=i < consec_end, 進場冷卻=i - last_exit[s] < q['cd'], 月上限=m_ent[s] >= q['cap'], 時段=int(hr[i]) in q['blk_h'], 星期=int(dw[i]) in q['blk_d'], 趨勢閘門=bool(rb[i]), 外加遮罩=bool(ok is not None and not ok[i]))
            go = not any(gates.values()); cand.append(dict(t=T[i], side=s, 進場=go, **gates))
            if not go: continue
            t_in = T[i] + pd.Timedelta(minutes=k); ep = float(Cl[i]) if k == 0 else mm.price(T[i], k); events.append((T[i], s, '進場訊號與委託意圖', '', float(Cl[i])))
            p = dict(side=s, bar=i, t_sig=T[i], t_in=t_in, ep=ep, held=0, mfe=0.0, red=False, ext=False, xb=0, reg=regime(slope[i]))
            if acct and T[i] < acct['start']: p.update(q=0.0, fpaid=0.0)   # 帳戶自評估起點才開始；之前的暖機交易不入帳
            elif acct:
                qty = NTL / ep; eq = wal + sum(unreal(o, ep if o['side'] == s else mm.price(T[i], k)) - (fund_paid(o, t_in) - o['fpaid']) for o in pos.values()); used = sum(o['q'] * o['ep'] / acct['lev'] for o in pos.values()); need = qty * ep / acct['lev']
                if eq - used < need:
                    rejects.append(dict(t_sig=T[i], t=t_in, side=s, 權益=round(eq, 2), 已占用=round(used, 2), 需要=round(need, 2))); events.append((t_in, s, '成交時保證金不足，拒絕', '', ep)); continue
                p.update(q=qty, fpaid=0.0); wal -= acct['hc'] * qty * ep
            pos[s] = p; m_ent[s] += 1; events.append((t_in, s, '進場成交', '', ep))
        for s, pnl in pend: book_pnl(i, s, pnl)
    for s, p in pos.items():
        trades.append(dict(side=s, t_sig=p['t_sig'], t_in=p['t_in'], t_dec=T[n - 1], t_out=T[n - 1], entry_price=p['ep'], exit_price=float(Cl[n - 1]), exit_reason='OPEN', bars_held=p['held'], dsn=False, pnl_rc=np.nan, entry_bar=p['bar'], exit_bar=n - 1, regime=p['reg']))
    t = pd.DataFrame(trades)
    return t, events, pd.DataFrame(cand), pd.DataFrame(rejects)
