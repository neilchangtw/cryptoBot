"""
V21 R6 + R7 — Path A continued

R6: Monthly Range Breakout (event-anchored, monthly scale)
R7: Swing Pivot Break (5-bar fractal swing high/low, trade on break)

Both use a priori: hold=12, SL=3.5% (R6 also tries hold=48 for monthly scale),
maxTotal L=1/S=1, $4/trade, notional $4000
IS = first 50% bars, OOS = last 50%
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)
N = len(df); IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

NOTIONAL, FEE = 4000.0, 4.0

def simulate(work, sig_L, sig_S, hold_bars, safenet_pct):
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
                    trades.append({'side':'L','entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                                   'entry_price':ep,'exit_price':sl_px,
                                   'pnl':NOTIONAL*(sl_px-ep)/ep-FEE,'reason':'SL'})
                    pos = None; exited = True
            else:
                sl_px = ep*(1+SL)
                if bar_next['high'] >= sl_px:
                    trades.append({'side':'S','entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                                   'entry_price':ep,'exit_price':sl_px,
                                   'pnl':NOTIONAL*(ep-sl_px)/ep-FEE,'reason':'SL'})
                    pos = None; exited = True
            if not exited and (i+1) - pos['entry_bar'] >= hold_bars:
                ex_px = bar_next['close']
                pnl = NOTIONAL*(ex_px-ep)/ep-FEE if side=='L' else NOTIONAL*(ep-ex_px)/ep-FEE
                trades.append({'side':side,'entry_dt':pos['entry_dt'],'exit_dt':bar_next['datetime'],
                               'entry_price':ep,'exit_price':ex_px,'pnl':pnl,'reason':'MH'})
                pos = None
        if pos is None:
            row = work.iloc[i]
            if row.get(sig_L, False):
                pos = {'side':'L','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
            elif row.get(sig_S, False):
                pos = {'side':'S','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
    if pos is not None:
        bar = work.iloc[NB-1]; ep=pos['entry_price']; side=pos['side']
        ex_px = bar['close']
        pnl = NOTIONAL*(ex_px-ep)/ep-FEE if side=='L' else NOTIONAL*(ep-ex_px)/ep-FEE
        trades.append({'side':side,'entry_dt':pos['entry_dt'],'exit_dt':bar['datetime'],
                       'entry_price':ep,'exit_price':ex_px,'pnl':pnl,'reason':'EOD'})
    return pd.DataFrame(trades)

def report(tdf, label):
    if tdf.empty:
        print(f"\n=== {label} === NO TRADES"); return {}
    tot = tdf['pnl'].sum(); wr = (tdf['pnl']>0).mean()*100
    gw = tdf[tdf['pnl']>0]['pnl'].sum(); gl = abs(tdf[tdf['pnl']<0]['pnl'].sum())
    pf = gw/gl if gl>0 else np.inf
    tdf2 = tdf.copy()
    tdf2['month'] = pd.to_datetime(tdf2['exit_dt']).dt.to_period('M')
    monthly = tdf2.groupby('month')['pnl'].sum()
    eq = tdf['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
    L = tdf[tdf['side']=='L']; S = tdf[tdf['side']=='S']
    print(f"\n=== {label} ===")
    print(f"  {len(tdf)}t (L={len(L)}, S={len(S)}) / WR {wr:.1f}% / PF {pf:.2f}")
    print(f"  PnL ${tot:.0f} / MDD ${mdd:.0f} / {(monthly>0).sum()}/{len(monthly)} mo+")
    print(f"  Worst ${monthly.min():.0f} / Best ${monthly.max():.0f}")
    print(f"  L ${L['pnl'].sum():.0f} ({len(L)}t)  S ${S['pnl'].sum():.0f} ({len(S)}t)")
    return {'trades':len(tdf),'wr':wr,'pf':pf,'pnl':tot,'mdd':mdd}

# ============================================================
# R6: Monthly Range Breakout
# ============================================================
print("\n" + "="*60)
print("R6: Monthly Range Breakout")
print("="*60)

df6 = df.copy()
df6['ym'] = df6['datetime'].dt.to_period('M').astype(str)

mh = df6.groupby('ym').agg(mo_high=('high','max'), mo_low=('low','min')).reset_index()
mh['prev_mo_high'] = mh['mo_high'].shift(1)
mh['prev_mo_low']  = mh['mo_low'].shift(1)
df6 = df6.merge(mh[['ym','prev_mo_high','prev_mo_low']], on='ym', how='left')

df6['brk_L'] = df6['close'] > df6['prev_mo_high']
df6['brk_S'] = df6['close'] < df6['prev_mo_low']
df6['first_L'] = df6['brk_L'] & ~(
    df6.groupby('ym')['brk_L']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))
df6['first_S'] = df6['brk_S'] & ~(
    df6.groupby('ym')['brk_S']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))

# a priori: hold=48 (2 days, natural for monthly event scale)
print("\n--- R6 a priori: hold=48, SL=3.5% ---")
r6_is = simulate(df6.iloc[0:IS_END].reset_index(drop=True),'first_L','first_S', 48, 3.5)
report(r6_is, "R6 IS")
r6_oos = simulate(df6.iloc[IS_END:N].reset_index(drop=True),'first_L','first_S', 48, 3.5)
report(r6_oos, "R6 OOS")

print("\n--- R6 G4: hold × SL (IS) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [24, 48, 72, 120]:
    for sl in [2.5,3.0,3.5,4.0]:
        t = simulate(df6.iloc[0:IS_END].reset_index(drop=True),'first_L','first_S', h, sl)
        if not t.empty:
            tot=t['pnl'].sum(); wr=(t['pnl']>0).mean()*100
            gw=t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf=gw/gl if gl>0 else np.inf
            eq=t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

# ============================================================
# R7: Swing Pivot Break (5-bar fractal)
# ============================================================
print("\n" + "="*60)
print("R7: Swing Pivot Break (5-bar fractal)")
print("="*60)

df7 = df.copy()
# 5-bar fractal pivot: bar i is swing HIGH if high[i] > high[i-2], h[i-1], h[i+1], h[i+2]
# CONFIRMED at bar i+2 (requires 2 bars after)
hi = df7['high'].values; lo = df7['low'].values
NB = len(df7)
pivot_high = np.zeros(NB, dtype=bool)
pivot_low = np.zeros(NB, dtype=bool)
for i in range(2, NB-2):
    if hi[i] > hi[i-1] and hi[i] > hi[i-2] and hi[i] > hi[i+1] and hi[i] > hi[i+2]:
        pivot_high[i] = True
    if lo[i] < lo[i-1] and lo[i] < lo[i-2] and lo[i] < lo[i+1] and lo[i] < lo[i+2]:
        pivot_low[i] = True
df7['pivot_high'] = pivot_high
df7['pivot_low'] = pivot_low

# Last confirmed swing high price at bar i = the most recent pivot_high at bar j <= i-2 (confirmed)
# We track 'last_pivot_h' and 'last_pivot_l' available at each bar (confirmation lag = 2 bars)
last_ph = np.full(NB, np.nan)
last_pl = np.full(NB, np.nan)
cur_ph = np.nan; cur_pl = np.nan
for i in range(NB):
    # At bar i, pivots up to bar i-2 are confirmed
    conf_idx = i - 2
    if conf_idx >= 0:
        if pivot_high[conf_idx]:
            cur_ph = hi[conf_idx]
        if pivot_low[conf_idx]:
            cur_pl = lo[conf_idx]
    last_ph[i] = cur_ph
    last_pl[i] = cur_pl
df7['last_pivot_h'] = last_ph
df7['last_pivot_l'] = last_pl

# Signal at bar i: close > last confirmed pivot high (long) or close < last confirmed pivot low (short)
df7['brk_L'] = df7['close'] > df7['last_pivot_h']
df7['brk_S'] = df7['close'] < df7['last_pivot_l']
# Only first breakout after each new pivot is traded (reset signal when new pivot forms)
df7['first_L'] = df7['brk_L'] & ~df7['brk_L'].shift(1).fillna(False)
df7['first_S'] = df7['brk_S'] & ~df7['brk_S'].shift(1).fillna(False)

print("\n--- R7 a priori: hold=12, SL=3.5% ---")
r7_is = simulate(df7.iloc[0:IS_END].reset_index(drop=True),'first_L','first_S', 12, 3.5)
report(r7_is, "R7 IS")
r7_oos = simulate(df7.iloc[IS_END:N].reset_index(drop=True),'first_L','first_S', 12, 3.5)
report(r7_oos, "R7 OOS")

print("\n--- R7 G4: hold × SL (IS) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [6,12,24,48]:
    for sl in [2.5,3.0,3.5,4.0]:
        t = simulate(df7.iloc[0:IS_END].reset_index(drop=True),'first_L','first_S', h, sl)
        if not t.empty:
            tot=t['pnl'].sum(); wr=(t['pnl']>0).mean()*100
            gw=t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf=gw/gl if gl>0 else np.inf
            eq=t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

r6_is.to_csv("backtest/research/v21_r6_is.csv", index=False)
r6_oos.to_csv("backtest/research/v21_r6_oos.csv", index=False)
r7_is.to_csv("backtest/research/v21_r7_is.csv", index=False)
r7_oos.to_csv("backtest/research/v21_r7_oos.csv", index=False)
print("\nSaved.")
