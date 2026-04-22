"""
V23 Overlay Engine — V14 模擬 + overlay hooks
支援：
  block_bars:   (N,) bool 陣列，True 代表該 bar 禁止進場（Path R 用）
  size_scale:   (N,) float 陣列，每根 bar 的倉位縮放係數 (0.0~1.0)（Path V 用）
                預設 None = 全倉 $4000
其他 V14 規則 100% 沿用。
"""
import os
import numpy as np
import pandas as pd

NOTIONAL, FEE = 4000, 4
L_TP=0.035; L_SN=0.035; L_SLIP=0.25; L_MH=6; L_EXT=2
L_MFE_ACT=0.010; L_MFE_TR=0.008
L_CMH_BAR=2; L_CMH_TH=-0.010; L_CMH_MH=5
L_CD=6; L_CAP=20; L_BLK_H={0,1,2,12}; L_BLK_D={5,6}
S_TP=0.020; S_SN=0.040; S_SLIP=0.25; S_MH=10; S_EXT=2
S_CD=8; S_CAP=20; S_BLK_H={0,1,2,12}; S_BLK_D={0,5,6}
CB_DAILY=-200; CB_L_MONTH=-75; CB_S_MONTH=-150
CB_CONSEC=4; CB_CONSEC_CD=24


def rolling_pctile(vals, w):
    out = np.full(len(vals), np.nan)
    for i in range(w-1, len(vals)):
        win = vals[i-w+1:i+1]; v = win[~np.isnan(win)]
        if len(v) < 10: continue
        out[i] = np.sum(v <= vals[i]) / len(v) * 100
    return out


def compute_indicators(o, h, l, c):
    log_hl = np.log(h / np.maximum(l, 1e-10))
    log_co = np.log(np.maximum(c / np.maximum(o, 1e-10), 1e-10))
    gk = 0.5*log_hl**2 - (2*np.log(2)-1)*log_co**2
    gks = pd.Series(gk)
    rL = (gks.rolling(5).mean() / gks.rolling(20).mean().replace(0, np.nan)).values
    sL = np.roll(rL, 1); sL[0] = np.nan
    pL = rolling_pctile(sL, 100)
    rS = (gks.rolling(10).mean() / gks.rolling(30).mean().replace(0, np.nan)).values
    sS = np.roll(rS, 1); sS[0] = np.nan
    pS = rolling_pctile(sS, 100)
    sc = np.roll(c, 1); sc[0] = np.nan
    h15 = pd.Series(sc).rolling(15).max().values
    l15 = pd.Series(sc).rolling(15).min().values
    brk_up = c > h15
    brk_dn = c < l15
    return pL, pS, brk_up, brk_dn


def run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                    block_bars_L=None, block_bars_S=None, size_scale=None):
    """
    block_bars_L / block_bars_S: (N,) bool, True=該 bar 該方向禁止進場
    size_scale: (N,) float in [0,1], 進場時倉位縮放（影響 notional & fee 同比例）
    """
    pL, pS, brk_up, brk_dn = compute_indicators(o, h, l, c)
    trades = []
    n = len(o); end = min(end, n)
    if block_bars_L is None: block_bars_L = np.zeros(n, dtype=bool)
    if block_bars_S is None: block_bars_S = np.zeros(n, dtype=bool)
    if size_scale is None: size_scale = np.ones(n, dtype=float)

    lp_active=False; lp_entry=0; lp_bar=0; lp_held=0
    lp_mfe=0; lp_mae=0; lp_reduced=False
    lp_ext=False; lp_ext_bars=0; lp_gk=0; lp_scale=1.0
    sp_active=False; sp_entry=0; sp_bar=0; sp_held=0
    sp_mfe=0; sp_mae=0
    sp_ext=False; sp_ext_bars=0; sp_gk=0; sp_scale=1.0
    l_last_exit=-999; s_last_exit=-999
    cur_month=-1; l_m_entries=0; s_m_entries=0
    l_m_pnl=0; s_m_pnl=0
    cur_day=-1; d_pnl=0
    consec=0; consec_end=-999

    for i in range(start, end):
        oi, hi, li, ci = o[i], h[i], l[i], c[i]
        hr, dw, mk, dk = hours[i], dows[i], mks[i], dks[i]
        if mk != cur_month:
            cur_month=mk; l_m_entries=0; s_m_entries=0; l_m_pnl=0; s_m_pnl=0
        if dk != cur_day:
            cur_day=dk; d_pnl=0

        # L exit
        if lp_active:
            lp_held += 1; ep=lp_entry; bh=lp_held
            bmfe=(hi-ep)/ep; bmae=(li-ep)/ep
            if bmfe>lp_mfe: lp_mfe=bmfe
            if bmae<lp_mae: lp_mae=bmae
            ex_p=0; ex_r=''
            sn_lv = ep*(1-L_SN)
            if li<=sn_lv:
                ex_p = ep*(1 - L_SN*(1+L_SLIP)); ex_r='SN'
            elif hi>=ep*(1+L_TP):
                ex_p = ep*(1+L_TP); ex_r='TP'
            else:
                cpnl=(ci-ep)/ep
                if lp_mfe>=L_MFE_ACT and (lp_mfe-cpnl)>=L_MFE_TR and bh>=1:
                    ex_p=ci; ex_r='MFE'
                else:
                    if bh==L_CMH_BAR and cpnl<=L_CMH_TH:
                        lp_reduced=True
                    mh_eff = L_CMH_MH if lp_reduced else L_MH
                    if not lp_ext:
                        if bh>=mh_eff:
                            if cpnl>0:
                                lp_ext=True; lp_ext_bars=0
                            else:
                                ex_p=ci; ex_r='MH'
                    else:
                        lp_ext_bars+=1
                        if li<=ep:
                            ex_p=ep; ex_r='BE'
                        elif lp_ext_bars>=L_EXT:
                            ex_p=ci; ex_r='MHx'
            if ex_p>0:
                pnl_pct=(ex_p-ep)/ep
                pnl = pnl_pct*NOTIONAL*lp_scale - FEE*lp_scale
                trades.append({'side':'L','entry_bar':lp_bar,'exit_bar':i,
                               'entry_mk':mks[lp_bar],'pnl':pnl,'reason':ex_r,
                               'bars_held':bh,'scale':lp_scale})
                lp_active=False; l_last_exit=i
                l_m_pnl += pnl; d_pnl += pnl
                if pnl<0: consec+=1
                else: consec=0
                if consec>=CB_CONSEC: consec_end = i + CB_CONSEC_CD

        # S exit
        if sp_active:
            sp_held += 1; ep=sp_entry; bh=sp_held
            bmfe=(ep-li)/ep; bmae=(ep-hi)/ep
            if bmfe>sp_mfe: sp_mfe=bmfe
            if bmae<sp_mae: sp_mae=bmae
            ex_p=0; ex_r=''
            sn_lv = ep*(1+S_SN)
            if hi>=sn_lv:
                ex_p = ep*(1 + S_SN*(1+S_SLIP)); ex_r='SN'
            elif li<=ep*(1-S_TP):
                ex_p = ep*(1-S_TP); ex_r='TP'
            else:
                cpnl=(ep-ci)/ep
                if not sp_ext:
                    if bh>=S_MH:
                        if cpnl>0:
                            sp_ext=True; sp_ext_bars=0
                        else:
                            ex_p=ci; ex_r='MH'
                else:
                    sp_ext_bars+=1
                    if hi>=ep:
                        ex_p=ep; ex_r='BE'
                    elif sp_ext_bars>=S_EXT:
                        ex_p=ci; ex_r='MHx'
            if ex_p>0:
                pnl_pct=(ep-ex_p)/ep
                pnl = pnl_pct*NOTIONAL*sp_scale - FEE*sp_scale
                trades.append({'side':'S','entry_bar':sp_bar,'exit_bar':i,
                               'entry_mk':mks[sp_bar],'pnl':pnl,'reason':ex_r,
                               'bars_held':bh,'scale':sp_scale})
                sp_active=False; s_last_exit=i
                s_m_pnl += pnl; d_pnl += pnl
                if pnl<0: consec+=1
                else: consec=0
                if consec>=CB_CONSEC: consec_end = i + CB_CONSEC_CD

        # Entry with overlay gating
        l_cb = (d_pnl<=CB_DAILY or l_m_pnl<=CB_L_MONTH or i<consec_end)
        s_cb = (d_pnl<=CB_DAILY or s_m_pnl<=CB_S_MONTH or i<consec_end)

        if (not lp_active and not l_cb and i-l_last_exit>=L_CD and l_m_entries<L_CAP
            and hr not in L_BLK_H and dw not in L_BLK_D
            and not np.isnan(pL[i]) and pL[i]<25 and brk_up[i]
            and not block_bars_L[i] and size_scale[i] > 0):
            lp_active=True; lp_entry=ci; lp_bar=i; lp_held=0
            lp_mfe=0; lp_mae=0; lp_reduced=False
            lp_ext=False; lp_ext_bars=0; lp_gk=pL[i]
            lp_scale = size_scale[i]
            l_m_entries += 1
        if (not sp_active and not s_cb and i-s_last_exit>=S_CD and s_m_entries<S_CAP
            and hr not in S_BLK_H and dw not in S_BLK_D
            and not np.isnan(pS[i]) and pS[i]<35 and brk_dn[i]
            and not block_bars_S[i] and size_scale[i] > 0):
            sp_active=True; sp_entry=ci; sp_bar=i; sp_held=0
            sp_mfe=0; sp_mae=0
            sp_ext=False; sp_ext_bars=0; sp_gk=pS[i]
            sp_scale = size_scale[i]
            s_m_entries += 1
    return trades


def stats_from(trades, side=None):
    if side: trades = [t for t in trades if t['side']==side]
    if not trades: return {'n':0,'pnl':0,'wr':0,'pf':0,'mdd':0,'sharpe':0}
    pnl = sum(t['pnl'] for t in trades)
    wins = [t['pnl'] for t in trades if t['pnl']>0]
    losses = [t['pnl'] for t in trades if t['pnl']<=0]
    wr = len(wins)/len(trades)*100
    pf = sum(wins)/abs(sum(losses)) if losses else 99
    # Equity-based MDD
    trs_sorted = sorted(trades, key=lambda x: x['exit_bar'])
    eq = np.cumsum([t['pnl'] for t in trs_sorted])
    peak = np.maximum.accumulate(eq); dd = eq - peak
    mdd = -dd.min() if len(dd) else 0
    # Sharpe proxy: PnL per trade / std of PnL per trade
    arr = np.array([t['pnl'] for t in trades])
    sharpe = arr.mean()/arr.std()*np.sqrt(len(arr)) if arr.std()>0 else 0
    return {'n':len(trades),'pnl':pnl,'wr':wr,'pf':pf,'mdd':mdd,'sharpe':sharpe}


def load_data():
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA = os.path.join(SCRIPT_DIR, '..', '..', 'data', 'ETHUSDT_1h_latest730d.csv')
    df = pd.read_csv(DATA)
    o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
    dt = pd.to_datetime(df['datetime'])
    hours = dt.dt.hour.values; dows = dt.dt.dayofweek.values
    mks = (dt.dt.year*100 + dt.dt.month).values
    dks = (dt.dt.year*10000 + dt.dt.month*100 + dt.dt.day).values
    return df, o, h, l, c, hours, dows, mks, dks


def worst_rolling_dd(trades, n_bars, window_bars, top_n=3):
    """找最壞 non-overlapping N-day drawdown windows"""
    equity = np.zeros(n_bars)
    trades_by_exit = sorted(trades, key=lambda x: x['exit_bar'])
    ptr = 0; cum = 0
    for i in range(n_bars):
        while ptr < len(trades_by_exit) and trades_by_exit[ptr]['exit_bar'] <= i:
            cum += trades_by_exit[ptr]['pnl']
            ptr += 1
        equity[i] = cum
    drawdowns = []
    for i in range(window_bars, n_bars):
        win_eq = equity[i-window_bars:i+1]
        win_peak = np.maximum.accumulate(win_eq)
        win_dd = (win_eq - win_peak).min()
        drawdowns.append((i, win_dd))
    drawdowns.sort(key=lambda x: x[1])
    picks = []
    for end_bar, dd in drawdowns:
        conflict = any(abs(end_bar - ep) < window_bars for ep, _ in picks)
        if not conflict:
            picks.append((end_bar, dd))
        if len(picks) >= top_n: break
    return picks


def monthly_pnl(trades):
    by_mo = {}
    for t in trades:
        by_mo[t['entry_mk']] = by_mo.get(t['entry_mk'],0) + t['pnl']
    return by_mo
