"""
V22 V14 Self-Audit — 回頭把 10-Gate 稽核套在 V14 自己身上
目標: G8 時序翻轉 + G9 移除最佳月 + G4 TP 鄰域 + G7 WF + G6 swap

V14 邏輯完整複製自 v14_april2026.py，但把模擬封裝成 run_v14(o,h,l,c,dt_parsed,start,end)，
以便能對 reversed OHLCV 重跑。
"""
import os, sys
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(SCRIPT_DIR, '..', '..', 'data', 'ETHUSDT_1h_latest730d.csv')

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


def run_v14(o, h, l, c, hours, dows, months_key, days_key, start, end,
            l_tp=L_TP, s_tp=S_TP, l_mh=L_MH, s_mh=S_MH):
    pL, pS, brk_up, brk_dn = compute_indicators(o, h, l, c)
    trades = []
    n = len(o)
    end = min(end, n)
    lp_active=False; lp_entry=0; lp_bar=0; lp_held=0
    lp_mfe=0; lp_mae=0; lp_reduced=False
    lp_ext=False; lp_ext_bars=0; lp_gk=0
    sp_active=False; sp_entry=0; sp_bar=0; sp_held=0
    sp_mfe=0; sp_mae=0
    sp_ext=False; sp_ext_bars=0; sp_gk=0
    l_last_exit=-999; s_last_exit=-999
    cur_month=-1; l_m_entries=0; s_m_entries=0
    l_m_pnl=0; s_m_pnl=0
    cur_day=-1; d_pnl=0
    consec=0; consec_end=-999

    for i in range(start, end):
        oi, hi, li, ci = o[i], h[i], l[i], c[i]
        hr, dw, mk, dk = hours[i], dows[i], months_key[i], days_key[i]
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
            elif hi>=ep*(1+l_tp):
                ex_p = ep*(1+l_tp); ex_r='TP'
            else:
                cpnl=(ci-ep)/ep
                if lp_mfe>=L_MFE_ACT and (lp_mfe-cpnl)>=L_MFE_TR and bh>=1:
                    ex_p=ci; ex_r='MFE'
                else:
                    if bh==L_CMH_BAR and cpnl<=L_CMH_TH:
                        lp_reduced=True
                    mh_eff = L_CMH_MH if lp_reduced else l_mh
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
                pnl=pnl_pct*NOTIONAL - FEE
                trades.append({'side':'L','entry_bar':lp_bar,'exit_bar':i,
                               'entry_mk':months_key[lp_bar],'pnl':pnl,'reason':ex_r,'bars_held':bh})
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
            elif li<=ep*(1-s_tp):
                ex_p = ep*(1-s_tp); ex_r='TP'
            else:
                cpnl=(ep-ci)/ep
                if not sp_ext:
                    if bh>=s_mh:
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
                pnl=pnl_pct*NOTIONAL - FEE
                trades.append({'side':'S','entry_bar':sp_bar,'exit_bar':i,
                               'entry_mk':months_key[sp_bar],'pnl':pnl,'reason':ex_r,'bars_held':bh})
                sp_active=False; s_last_exit=i
                s_m_pnl += pnl; d_pnl += pnl
                if pnl<0: consec+=1
                else: consec=0
                if consec>=CB_CONSEC: consec_end = i + CB_CONSEC_CD

        # Entry
        l_cb = (d_pnl<=CB_DAILY or l_m_pnl<=CB_L_MONTH or i<consec_end)
        s_cb = (d_pnl<=CB_DAILY or s_m_pnl<=CB_S_MONTH or i<consec_end)

        if (not lp_active and not l_cb and i-l_last_exit>=L_CD and l_m_entries<L_CAP
            and hr not in L_BLK_H and dw not in L_BLK_D
            and not np.isnan(pL[i]) and pL[i]<25 and brk_up[i]):
            lp_active=True; lp_entry=ci; lp_bar=i; lp_held=0
            lp_mfe=0; lp_mae=0; lp_reduced=False
            lp_ext=False; lp_ext_bars=0; lp_gk=pL[i]
            l_m_entries += 1
        if (not sp_active and not s_cb and i-s_last_exit>=S_CD and s_m_entries<S_CAP
            and hr not in S_BLK_H and dw not in S_BLK_D
            and not np.isnan(pS[i]) and pS[i]<35 and brk_dn[i]):
            sp_active=True; sp_entry=ci; sp_bar=i; sp_held=0
            sp_mfe=0; sp_mae=0
            sp_ext=False; sp_ext_bars=0; sp_gk=pS[i]
            s_m_entries += 1
    return trades


def stats_from(trades, side=None):
    if side: trades = [t for t in trades if t['side']==side]
    if not trades: return {'n':0,'pnl':0,'wr':0,'pf':0}
    pnl = sum(t['pnl'] for t in trades)
    wins = [t['pnl'] for t in trades if t['pnl']>0]
    losses = [t['pnl'] for t in trades if t['pnl']<=0]
    wr = len(wins)/len(trades)*100
    pf = sum(wins)/abs(sum(losses)) if losses else 99
    return {'n':len(trades),'pnl':pnl,'wr':wr,'pf':pf}


# ============== MAIN ==============
df = pd.read_csv(DATA)
o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
dt = pd.to_datetime(df['datetime'])
hours = dt.dt.hour.values
dows = dt.dt.dayofweek.values
mks = (dt.dt.year*100 + dt.dt.month).values
dks = (dt.dt.year*10000 + dt.dt.month*100 + dt.dt.day).values
N = len(o)
IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

# Baseline IS / OOS
print("\n" + "="*80)
print("BASELINE (V14 original params)")
print("="*80)
tr_is = run_v14(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos = run_v14(o,h,l,c,hours,dows,mks,dks,IS_END,N)
for name, tr in [("IS", tr_is), ("OOS", tr_oos)]:
    sL = stats_from(tr,'L'); sS = stats_from(tr,'S'); sA = stats_from(tr)
    print(f"{name:3}  L:{sL['n']:3}t ${sL['pnl']:>+6.0f} WR{sL['wr']:4.1f}% PF{sL['pf']:4.2f}   "
          f"S:{sS['n']:3}t ${sS['pnl']:>+6.0f} WR{sS['wr']:4.1f}% PF{sS['pf']:4.2f}   "
          f"Total:{sA['n']:3}t ${sA['pnl']:>+6.0f}")

# ============== G8: TIME REVERSAL ==============
print("\n" + "="*80)
print("G8: TIME REVERSAL (reverse OHLCV, rerun V14)")
print("="*80)
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
# Reverse time-of-day / day-of-week / month/day keys aligned with reversed bars
hours_r = hours[::-1].copy()
dows_r = dows[::-1].copy()
mks_r = mks[::-1].copy()
dks_r = dks[::-1].copy()

tr_rev = run_v14(o_r,h_r,l_r,c_r,hours_r,dows_r,mks_r,dks_r,0,N)
sL=stats_from(tr_rev,'L'); sS=stats_from(tr_rev,'S'); sA=stats_from(tr_rev)
print(f"Reversed full period:")
print(f"  L:{sL['n']:3}t ${sL['pnl']:>+6.0f} WR{sL['wr']:4.1f}% PF{sL['pf']:4.2f}")
print(f"  S:{sS['n']:3}t ${sS['pnl']:>+6.0f} WR{sS['wr']:4.1f}% PF{sS['pf']:4.2f}")
print(f"  Total:{sA['n']:3}t ${sA['pnl']:>+6.0f}")
orig = stats_from(tr_is)['pnl'] + stats_from(tr_oos)['pnl']
print(f"\nOriginal combined: ${orig:+.0f}")
print(f"Reversed:          ${sA['pnl']:+.0f}")
print(f"G8 verdict: {'PASS (true alpha)' if sA['pnl']>0 else 'FAIL (regime-dependent)'}")

# ============== G9: REMOVE BEST MONTH ==============
print("\n" + "="*80)
print("G9: REMOVE BEST MONTH")
print("="*80)
all_tr = tr_is + tr_oos
for side_label in ['L', 'S', 'ALL']:
    tt = all_tr if side_label=='ALL' else [t for t in all_tr if t['side']==side_label]
    by_m = {}
    for t in tt:
        by_m[t['entry_mk']] = by_m.get(t['entry_mk'],0) + t['pnl']
    if not by_m: continue
    best_m = max(by_m, key=by_m.get)
    worst_m = min(by_m, key=by_m.get)
    total = sum(by_m.values())
    without_best = total - by_m[best_m]
    pos_m = sum(1 for v in by_m.values() if v>0)
    tot_m = len(by_m)
    print(f"  {side_label}: {tot_m} months, {pos_m} positive, best {best_m} ${by_m[best_m]:+.0f}, "
          f"worst {worst_m} ${by_m[worst_m]:+.0f}")
    print(f"      Total ${total:+.0f} -> without best ${without_best:+.0f}  "
          f"{'PASS' if without_best>0 else 'FAIL'}")

# ============== G6: SWAP (IS <-> OOS) ==============
print("\n" + "="*80)
print("G6: SWAP")
print("="*80)
is_pnl = stats_from(tr_is)['pnl']; oos_pnl = stats_from(tr_oos)['pnl']
fwd = (is_pnl - oos_pnl)/abs(is_pnl)*100 if is_pnl else 0
bwd = (oos_pnl - is_pnl)/abs(oos_pnl)*100 if oos_pnl else 0
print(f"  IS ${is_pnl:+.0f}   OOS ${oos_pnl:+.0f}")
print(f"  Forward (IS->OOS): {fwd:+.1f}%  {'PASS' if abs(fwd)<50 else 'FAIL'}")
print(f"  Backward (OOS->IS): {bwd:+.1f}%  {'PASS' if abs(bwd)<50 else 'FAIL'}")

# ============== G7: WALK FORWARD (6 windows) ==============
print("\n" + "="*80)
print("G7: WALK-FORWARD (6 windows)")
print("="*80)
wf_win = N // 6
pos_w = 0
for w in range(6):
    sb = w*wf_win; eb = (w+1)*wf_win if w<5 else N
    tt = run_v14(o,h,l,c,hours,dows,mks,dks,sb,eb)
    p = stats_from(tt)['pnl']; n = stats_from(tt)['n']
    if p>0: pos_w += 1
    print(f"  W{w+1} [{sb}:{eb}]  {n}t  ${p:+.0f}")
print(f"  {pos_w}/6 positive   {'PASS' if pos_w>=4 else 'FAIL'}")

# ============== G4: TP NEIGHBORHOOD ==============
print("\n" + "="*80)
print("G4: L TP x S TP neighborhood")
print("="*80)
print(f"{'L_TP':>6} {'S_TP':>6} {'IS $':>8} {'OOS $':>8}")
for ltp in [0.025, 0.030, 0.035, 0.040, 0.045]:
    for stp in [0.015, 0.020, 0.025, 0.030]:
        ti = run_v14(o,h,l,c,hours,dows,mks,dks,0,IS_END, l_tp=ltp, s_tp=stp)
        to = run_v14(o,h,l,c,hours,dows,mks,dks,IS_END,N, l_tp=ltp, s_tp=stp)
        tag = " <--" if ltp==L_TP and stp==S_TP else ""
        print(f"{ltp:>6.3f} {stp:>6.3f} ${stats_from(ti)['pnl']:>+7.0f} "
              f"${stats_from(to)['pnl']:>+7.0f}{tag}")

print("\n" + "="*80)
print("DONE")
print("="*80)
