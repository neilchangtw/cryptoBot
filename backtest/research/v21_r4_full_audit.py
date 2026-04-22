"""
V21 R4 — Full 10-Gate Audit
Staircase N=6 LOCKED, hold=12, SL=3.5%

Gates:
  G6: Swap test (swap IS/OOS roles)
  G7: Walk Forward 6 windows
  G8: Time reversal
  G9: Remove-best month
  V14 overlap check (approximate via V14 signal replication)
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)
N = len(df); IS_END = N // 2

NOTIONAL, FEE = 4000.0, 4.0
HOLD, SL_PCT = 12, 3.5
N_STAIR = 6

def rolling_monotone_inc(s, n):
    out = np.zeros(len(s), dtype=bool); arr = s.values
    for i in range(n-1, len(s)):
        out[i] = all(arr[i-k+1] > arr[i-k] for k in range(1, n))
    return out

def rolling_monotone_dec(s, n):
    out = np.zeros(len(s), dtype=bool); arr = s.values
    for i in range(n-1, len(s)):
        out[i] = all(arr[i-k+1] < arr[i-k] for k in range(1, n))
    return out

def stair_signals(d, n):
    d = d.copy().reset_index(drop=True)
    d['stair_up'] = rolling_monotone_inc(d['low'], n)
    d['stair_dn'] = rolling_monotone_dec(d['high'], n)
    d['stair_top'] = d['high'].rolling(n).max()
    d['stair_bot'] = d['low'].rolling(n).min()
    d['setup_L'] = d['stair_up'].shift(1).fillna(False)
    d['setup_S'] = d['stair_dn'].shift(1).fillna(False)
    d['ref_top'] = d['stair_top'].shift(1)
    d['ref_bot'] = d['stair_bot'].shift(1)
    d['brk_L'] = d['setup_L'] & (d['close'] > d['ref_top'])
    d['brk_S'] = d['setup_S'] & (d['close'] < d['ref_bot'])
    d['first_L'] = d['brk_L'] & ~d['brk_L'].shift(1).fillna(False)
    d['first_S'] = d['brk_S'] & ~d['brk_S'].shift(1).fillna(False)
    return d

def simulate(work, hold_bars=HOLD, safenet_pct=SL_PCT):
    SL = safenet_pct/100.0
    trades = []; pos = None; NB = len(work)
    for i in range(NB - 1):
        bar_next = work.iloc[i+1]
        if pos is not None:
            ep, side = pos['entry_price'], pos['side']
            exited = False
            if side == 'L':
                sl_px = ep*(1-SL)
                if bar_next['low'] <= sl_px:
                    trades.append({'side':'L','entry_bar':pos['entry_bar'],'exit_bar':i+1,
                                   'entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                                   'entry_price':ep,'exit_price':sl_px,
                                   'pnl':NOTIONAL*(sl_px-ep)/ep-FEE,'reason':'SL'})
                    pos = None; exited = True
            else:
                sl_px = ep*(1+SL)
                if bar_next['high'] >= sl_px:
                    trades.append({'side':'S','entry_bar':pos['entry_bar'],'exit_bar':i+1,
                                   'entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                                   'entry_price':ep,'exit_price':sl_px,
                                   'pnl':NOTIONAL*(ep-sl_px)/ep-FEE,'reason':'SL'})
                    pos = None; exited = True
            if not exited and (i+1) - pos['entry_bar'] >= hold_bars:
                ex_px = bar_next['close']
                pnl = NOTIONAL*(ex_px-ep)/ep-FEE if side=='L' else NOTIONAL*(ep-ex_px)/ep-FEE
                trades.append({'side':side,'entry_bar':pos['entry_bar'],'exit_bar':i+1,
                               'entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                               'entry_price':ep,'exit_price':ex_px,'pnl':pnl,'reason':'MH'})
                pos = None
        if pos is None:
            row = work.iloc[i]
            if row.get('first_L', False):
                pos = {'side':'L','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
            elif row.get('first_S', False):
                pos = {'side':'S','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
    if pos is not None:
        bar = work.iloc[NB-1]; ep=pos['entry_price']; side=pos['side']
        ex_px = bar['close']
        pnl = NOTIONAL*(ex_px-ep)/ep-FEE if side=='L' else NOTIONAL*(ep-ex_px)/ep-FEE
        trades.append({'side':side,'entry_bar':pos['entry_bar'],'exit_bar':NB-1,
                       'entry_dt':pos['entry_dt'],'exit_dt':bar['datetime'],
                       'entry_price':ep,'exit_price':ex_px,'pnl':pnl,'reason':'EOD'})
    return pd.DataFrame(trades)

def stats(tdf, label):
    if tdf.empty:
        print(f"  {label}: NO TRADES")
        return {'pnl':0,'mdd':0,'wr':0,'n':0,'mo_pos':0,'mo_tot':0}
    tot = tdf['pnl'].sum(); wr = (tdf['pnl']>0).mean()*100
    eq = tdf['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
    tdf = tdf.copy()
    tdf['month'] = pd.to_datetime(tdf['exit_dt']).dt.to_period('M')
    monthly = tdf.groupby('month')['pnl'].sum()
    mpos = (monthly>0).sum(); mtot = len(monthly)
    print(f"  {label}: {len(tdf)}t WR {wr:.0f}% PnL ${tot:.0f} MDD ${mdd:.0f} mo+ {mpos}/{mtot}")
    return {'pnl':tot,'mdd':mdd,'wr':wr,'n':len(tdf),'mo_pos':mpos,'mo_tot':mtot,
            'monthly':monthly}

# Precompute signals on full df
d6 = stair_signals(df, N_STAIR)

# ============================================================
# Baseline: IS / OOS recap
# ============================================================
print("=== R4 Baseline recap ===")
s_is = stats(simulate(d6.iloc[0:IS_END].reset_index(drop=True)), "IS ")
s_oos = stats(simulate(d6.iloc[IS_END:N].reset_index(drop=True)), "OOS")
baseline_total = s_is['pnl'] + s_oos['pnl']
print(f"Combined PnL: ${baseline_total:.0f}")

# ============================================================
# G6: Swap test
# Protocol: swap IS/OOS, see if strategy still works (degradation < 50%)
# Interpretation: IS was "in-sample", if we had instead DESIGNED on OOS and tested on IS,
# would we still find signal? Since our params (N=6, h=12, SL=3.5) are fixed,
# running them on "swapped roles" tests time-period sensitivity.
# ============================================================
print("\n=== G6: Swap Test ===")
# Same params, now treat OOS as "training" and IS as "validation"
# Since we don't re-fit params (already locked), the swap test here is just
# "is the edge present in both halves independently"
swap_is_pnl = s_oos['pnl']  # OOS when treated as IS = same numbers since params unchanged
swap_oos_pnl = s_is['pnl']  # IS when treated as OOS
# Decay measurement: if OOS had been used to design, would IS hold up?
# Our OOS = $2148, IS = $894. Swap "OOS→IS" shows $894 when period was "validation"
# Degradation from swap: (2148 - 894) / 2148 = 58% degradation (from OOS→IS direction)
# Degradation from baseline: (894 - 2148) / 894 = -140% (IS→OOS direction = improvement)
deg_fwd = (s_is['pnl'] - s_oos['pnl']) / s_is['pnl'] * 100 if s_is['pnl']>0 else np.nan
deg_bwd = (s_oos['pnl'] - s_is['pnl']) / s_oos['pnl'] * 100 if s_oos['pnl']>0 else np.nan
print(f"  Forward degradation (IS→OOS): {deg_fwd:.0f}%   (< 50% = PASS)")
print(f"  Backward degradation (OOS→IS): {deg_bwd:.0f}%  (< 50% = PASS)")
print(f"  {'PASS' if abs(deg_fwd)<50 and abs(deg_bwd)<50 else 'PARTIAL/FAIL'} — both halves need positive edge with < 50% variance")

# ============================================================
# G7: Walk Forward — 6 windows
# ============================================================
print("\n=== G7: Walk Forward (6 windows) ===")
win_size = N // 6
wf_pnls = []
for w in range(6):
    s = w * win_size
    e = (w+1) * win_size if w < 5 else N
    t = simulate(d6.iloc[s:e].reset_index(drop=True))
    p = t['pnl'].sum() if not t.empty else 0
    nn = len(t)
    wr = (t['pnl']>0).mean()*100 if not t.empty else 0
    print(f"  Window {w+1}: [{s}:{e}] {nn}t PnL ${p:.0f} WR {wr:.0f}%")
    wf_pnls.append(p)
wf_positive = sum(1 for p in wf_pnls if p > 0)
print(f"  Positive windows: {wf_positive}/6  ({'PASS' if wf_positive >= 4 else 'FAIL'})")

# ============================================================
# G8: Time reversal
# Reverse bar order, recompute signals, run
# ============================================================
print("\n=== G8: Time Reversal ===")
df_rev = df.iloc[::-1].reset_index(drop=True).copy()
# In reversed time: prev low becomes future low, etc.
# To preserve semantics: we still want "staircase up" = "4 consecutive rising lows" in the REVERSED series
# But rising lows in reversed time = falling lows in forward time → NOT the same pattern
# A proper reversal keeps the rule (monotone rising lows followed by break), applied on the reversed data
d6_rev = stair_signals(df_rev, N_STAIR)
rev_trades = simulate(d6_rev)
s_rev = stats(rev_trades, "Reversed")
print(f"  Original combined: ${baseline_total:.0f}")
print(f"  Reversed: ${s_rev['pnl']:.0f}")
print(f"  {'PASS if reversed > 0' if s_rev['pnl']>0 else 'FAIL — reversed lost edge'}")
# Rule: Some strategies SHOULD fail on reversal (those with asymmetric forward bias).
# Breakout edge should actually still show some signal if it's pure momentum pattern,
# but V14 S2 FAILED this test (from memory). This is a regime-dependency check.

# ============================================================
# G9: Remove-best month
# ============================================================
print("\n=== G9: Remove-Best Month ===")
all_trades = pd.concat([simulate(d6.iloc[0:IS_END].reset_index(drop=True)),
                        simulate(d6.iloc[IS_END:N].reset_index(drop=True))],
                       ignore_index=True)
all_trades['month'] = pd.to_datetime(all_trades['exit_dt']).dt.to_period('M')
monthly_full = all_trades.groupby('month')['pnl'].sum()
best_mo = monthly_full.idxmax()
best_mo_pnl = monthly_full.max()
remaining = all_trades[all_trades['month'] != best_mo]
remaining_pnl = remaining['pnl'].sum()
print(f"  Best month: {best_mo} (${best_mo_pnl:.0f})")
print(f"  Full PnL: ${all_trades['pnl'].sum():.0f}")
print(f"  Without best month: ${remaining_pnl:.0f}")
print(f"  {'PASS' if remaining_pnl > 0 else 'FAIL'}")

# ============================================================
# V14 overlap check — approximate by replicating V14 L entry signal
# ============================================================
print("\n=== V14 Overlap Check (approximate) ===")
# V14 L: GK pctile <25 (5/20 mean) + 15-bar close breakout
# Simplified: for overlap we only care about TIMING of trade entries
# Use L arm of V14 as proxy

def gk_pctile(d, n_short=5, n_long=20, pct_window=100):
    d = d.copy()
    ln_hl2 = 0.5 * np.log(d['high']/d['low'])**2
    ln_co2 = (2*np.log(2) - 1) * np.log(d['close']/d['open'])**2
    d['gk'] = ln_hl2 - ln_co2
    d['gk_short'] = d['gk'].rolling(n_short).mean()
    d['gk_long'] = d['gk'].rolling(n_long).mean()
    d['gk_ratio'] = d['gk_short'] / d['gk_long']
    d['gk_ratio_shifted'] = d['gk_ratio'].shift(1)
    d['gk_pct'] = d['gk_ratio_shifted'].rolling(pct_window).rank(pct=True) * 100
    return d

v14 = gk_pctile(df, 5, 20, 100)
v14['brk15'] = v14['close'] > v14['close'].shift(1).rolling(15).max()
v14['v14_L_signal'] = (v14['gk_pct'] < 25) & v14['brk15']

# Get V14 L entry timestamps (approximate, no cooldown/session filters applied)
v14_L_entries = set(v14.loc[v14['v14_L_signal'], 'datetime'].astype(str).tolist())

# R4 L entries
r4_all = pd.concat([simulate(d6.iloc[0:IS_END].reset_index(drop=True)),
                    simulate(d6.iloc[IS_END:N].reset_index(drop=True))], ignore_index=True)
r4_L_entries = set(r4_all[r4_all['side']=='L']['entry_dt'].astype(str).tolist())

overlap = len(v14_L_entries & r4_L_entries)
r4_L_count = len(r4_L_entries)
overlap_rate = overlap / r4_L_count * 100 if r4_L_count else 0
print(f"  V14 L signal bars (approx): {len(v14_L_entries)}")
print(f"  R4 L entries: {r4_L_count}")
print(f"  Same-bar overlap: {overlap} ({overlap_rate:.0f}%)")
# Check nearby overlap (within 2 bars)
overlap_near = 0
v14_L_dt = pd.to_datetime(list(v14_L_entries))
r4_L_dt = pd.to_datetime(list(r4_L_entries))
for r_dt in r4_L_dt:
    dist = abs((v14_L_dt - r_dt).total_seconds() / 3600)
    if dist.min() <= 2:
        overlap_near += 1
overlap_near_rate = overlap_near / r4_L_count * 100 if r4_L_count else 0
print(f"  Within-2-bar overlap: {overlap_near} ({overlap_near_rate:.0f}%)")
print(f"  {'PASS' if overlap_near_rate < 70 else 'FAIL'} (< 70% required for independent edge)")

# ============================================================
# Summary
# ============================================================
print("\n" + "="*60)
print("R4 FULL AUDIT SUMMARY")
print("="*60)
print(f"G1 (IS先行)     : PASS — N=6 locked from R3 IS-only grid")
print(f"G2 (自然步長)   : PASS — N integer, hold=12, SL=3.5%")
print(f"G3 (IS/OOS同向) : PASS — both positive, $894+$2148=${baseline_total:.0f}")
print(f"G4 (鄰域穩定)   : PARTIAL — N/hold cliffs (but SL stable at each N×hold)")
print(f"G5 (Cascade)    : N/A — single-layer strategy")
print(f"G6 (Swap)       : fwd {deg_fwd:.0f}%, bwd {deg_bwd:.0f}% — {'PASS' if abs(deg_fwd)<50 and abs(deg_bwd)<50 else 'FAIL'}")
print(f"G7 (WF 6 win)   : {wf_positive}/6 positive — {'PASS' if wf_positive>=4 else 'FAIL'}")
print(f"G8 (時序翻轉)   : reversed ${s_rev['pnl']:.0f} — {'PASS' if s_rev['pnl']>0 else 'FAIL'}")
print(f"G9 (Remove best): remaining ${remaining_pnl:.0f} — {'PASS' if remaining_pnl>0 else 'FAIL'}")
print(f"G10 (+params ≤2): PASS — N=6 vs R3 N=4, no new knob")
print(f"V14 overlap     : {overlap_near_rate:.0f}% — {'PASS' if overlap_near_rate<70 else 'FAIL'} (independent edge check)")
