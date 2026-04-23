"""
V25 Engine — V14+R + regime-conditional exits

相對 v24_engine：
  - 新增 L_TP_BY_RG / L_MH_BY_RG / S_TP_BY_RG / S_MH_BY_RG dict 參數
    格式 {'UP':v, 'MILD_UP':v, 'DOWN':v, 'SIDE':v}，不傳則用 default
  - 每筆進場根據 entry bar slope 決定該筆的出場參數，持倉期間鎖定
  - 其他 V14+R 規則 100% 沿用

注意：regime 分類與 R gate 一致（TH_UP=4.5%, TH_SIDE=1.0%），L 在 UP 被 R gate block、
     S 在 SIDE 被 R gate block，所以 L 不會在 UP 進場、S 不會在 SIDE 進場，
     因此 L_*_BY_RG['UP'] 和 S_*_BY_RG['SIDE'] 即使設了也不會被用到。
"""
import os
import numpy as np
import pandas as pd

# Default (V14 baseline) exit params
L_TP_DEF=0.035; L_SN=0.035; L_SLIP=0.25; L_MH_DEF=6; L_EXT=2
L_MFE_ACT=0.010; L_MFE_TR=0.008
L_CMH_BAR=2; L_CMH_TH=-0.010; L_CMH_MH=5
L_CD=6; L_CAP=20; L_BLK_H={0,1,2,12}; L_BLK_D={5,6}
S_TP_DEF=0.020; S_SN=0.040; S_SLIP=0.25; S_MH_DEF=10; S_EXT=2
S_CD=8; S_CAP=20; S_BLK_H={0,1,2,12}; S_BLK_D={0,5,6}

TH_UP = 0.045
TH_SIDE = 0.010


def _rg(s):
    if np.isnan(s): return "NA"
    if s > TH_UP: return "UP"
    if abs(s) < TH_SIDE: return "SIDE"
    if s < -TH_SIDE: return "DOWN"
    return "MILD_UP"


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


def run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, start, end,
            block_bars_L=None, block_bars_S=None,
            L_TP_BY_RG=None, L_MH_BY_RG=None,
            S_TP_BY_RG=None, S_MH_BY_RG=None,
            notional=4000, fee=4,
            cb_daily=-200, cb_l_month=-75, cb_s_month=-150,
            cb_consec=4, cb_consec_cd=24):
    """
    slope_use: per-bar SMA200(100-bar) slope, lagged 1 bar (與 R gate 一致)
    L_TP_BY_RG / L_MH_BY_RG / S_TP_BY_RG / S_MH_BY_RG: {rg: value} dict
      若為 None 或該 regime 不在 dict 中，使用 default
    """
    def get_tp_l(rg):
        if L_TP_BY_RG and rg in L_TP_BY_RG: return L_TP_BY_RG[rg]
        return L_TP_DEF
    def get_mh_l(rg):
        if L_MH_BY_RG and rg in L_MH_BY_RG: return L_MH_BY_RG[rg]
        return L_MH_DEF
    def get_tp_s(rg):
        if S_TP_BY_RG and rg in S_TP_BY_RG: return S_TP_BY_RG[rg]
        return S_TP_DEF
    def get_mh_s(rg):
        if S_MH_BY_RG and rg in S_MH_BY_RG: return S_MH_BY_RG[rg]
        return S_MH_DEF

    pL, pS, brk_up, brk_dn = compute_indicators(o, h, l, c)
    trades = []
    n = len(o); end = min(end, n)
    if block_bars_L is None: block_bars_L = np.zeros(n, dtype=bool)
    if block_bars_S is None: block_bars_S = np.zeros(n, dtype=bool)

    lp_active=False; lp_entry=0; lp_bar=0; lp_held=0
    lp_mfe=0; lp_mae=0; lp_reduced=False
    lp_ext=False; lp_ext_bars=0; lp_gk=0; lp_rg=""
    lp_tp=L_TP_DEF; lp_mh=L_MH_DEF
    sp_active=False; sp_entry=0; sp_bar=0; sp_held=0
    sp_mfe=0; sp_mae=0
    sp_ext=False; sp_ext_bars=0; sp_gk=0; sp_rg=""
    sp_tp=S_TP_DEF; sp_mh=S_MH_DEF
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
            elif hi>=ep*(1+lp_tp):
                ex_p = ep*(1+lp_tp); ex_r='TP'
            else:
                cpnl=(ci-ep)/ep
                if lp_mfe>=L_MFE_ACT and (lp_mfe-cpnl)>=L_MFE_TR and bh>=1:
                    ex_p=ci; ex_r='MFE'
                else:
                    if bh==L_CMH_BAR and cpnl<=L_CMH_TH:
                        lp_reduced=True
                    mh_eff = L_CMH_MH if lp_reduced else lp_mh
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
                pnl = pnl_pct*notional - fee
                trades.append({'side':'L','entry_bar':lp_bar,'exit_bar':i,
                               'entry_mk':mks[lp_bar],'pnl':pnl,'reason':ex_r,
                               'bars_held':bh,'regime':lp_rg,
                               'tp':lp_tp,'mh':lp_mh})
                lp_active=False; l_last_exit=i
                l_m_pnl += pnl; d_pnl += pnl
                if pnl<0: consec+=1
                else: consec=0
                if consec>=cb_consec: consec_end = i + cb_consec_cd

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
            elif li<=ep*(1-sp_tp):
                ex_p = ep*(1-sp_tp); ex_r='TP'
            else:
                cpnl=(ep-ci)/ep
                if not sp_ext:
                    if bh>=sp_mh:
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
                pnl = pnl_pct*notional - fee
                trades.append({'side':'S','entry_bar':sp_bar,'exit_bar':i,
                               'entry_mk':mks[sp_bar],'pnl':pnl,'reason':ex_r,
                               'bars_held':bh,'regime':sp_rg,
                               'tp':sp_tp,'mh':sp_mh})
                sp_active=False; s_last_exit=i
                s_m_pnl += pnl; d_pnl += pnl
                if pnl<0: consec+=1
                else: consec=0
                if consec>=cb_consec: consec_end = i + cb_consec_cd

        # Entry with overlay gating + regime binding
        l_cb = (d_pnl<=cb_daily or l_m_pnl<=cb_l_month or i<consec_end)
        s_cb = (d_pnl<=cb_daily or s_m_pnl<=cb_s_month or i<consec_end)

        if (not lp_active and not l_cb and i-l_last_exit>=L_CD and l_m_entries<L_CAP
            and hr not in L_BLK_H and dw not in L_BLK_D
            and not np.isnan(pL[i]) and pL[i]<25 and brk_up[i]
            and not block_bars_L[i]):
            lp_active=True; lp_entry=ci; lp_bar=i; lp_held=0
            lp_mfe=0; lp_mae=0; lp_reduced=False
            lp_ext=False; lp_ext_bars=0; lp_gk=pL[i]
            lp_rg = _rg(slope_use[i])
            lp_tp = get_tp_l(lp_rg); lp_mh = get_mh_l(lp_rg)
            l_m_entries += 1
        if (not sp_active and not s_cb and i-s_last_exit>=S_CD and s_m_entries<S_CAP
            and hr not in S_BLK_H and dw not in S_BLK_D
            and not np.isnan(pS[i]) and pS[i]<35 and brk_dn[i]
            and not block_bars_S[i]):
            sp_active=True; sp_entry=ci; sp_bar=i; sp_held=0
            sp_mfe=0; sp_mae=0
            sp_ext=False; sp_ext_bars=0; sp_gk=pS[i]
            sp_rg = _rg(slope_use[i])
            sp_tp = get_tp_s(sp_rg); sp_mh = get_mh_s(sp_rg)
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
    trs_sorted = sorted(trades, key=lambda x: x['exit_bar'])
    eq = np.cumsum([t['pnl'] for t in trs_sorted])
    peak = np.maximum.accumulate(eq); dd = eq - peak
    mdd = -dd.min() if len(dd) else 0
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


def build_slope(c):
    n = len(c)
    sma200 = pd.Series(c).rolling(200).mean().values
    slope = np.full(n, np.nan)
    for i in range(300, n):
        if not np.isnan(sma200[i]) and sma200[i-100] > 0:
            slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
    slope_use = np.roll(slope, 1); slope_use[0] = np.nan
    return slope_use


def build_r_gate(slope_use):
    block_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
    block_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))
    return block_L, block_S
