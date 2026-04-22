"""
V21 R2 + R3 — Path A Candidates

R2: Daily Opening Range Breakout (event-anchored daily)
R3: Multi-bar Staircase + break of range top

Both use: hold=12, SL=3.5%, maxTotal L=1/S=1, $4/trade, notional $4000
IS = first 50% bars, OOS = last 50%
"""
import pandas as pd
import numpy as np

# ---------- Load ----------
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)
N = len(df); IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

# ---------- Common sim ----------
NOTIONAL, FEE = 4000.0, 4.0

def simulate(work, signal_L_col, signal_S_col, hold_bars, safenet_pct):
    SL = safenet_pct/100.0
    trades = []
    pos = None
    NB = len(work)
    for i in range(NB - 1):
        bar_next = work.iloc[i+1]
        # Exit first
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
        # Entry
        if pos is None:
            row = work.iloc[i]
            if row.get(signal_L_col, False):
                pos = {'side':'L','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
            elif row.get(signal_S_col, False):
                pos = {'side':'S','entry_price':bar_next['open'],'entry_bar':i+1,'entry_dt':bar_next['datetime']}
    # Force close
    if pos is not None:
        bar = work.iloc[NB-1]; ep=pos['entry_price']; side=pos['side']
        ex_px = bar['close']
        pnl = NOTIONAL*(ex_px-ep)/ep-FEE if side=='L' else NOTIONAL*(ep-ex_px)/ep-FEE
        trades.append({'side':side,'entry_dt':pos['entry_dt'],'exit_dt':bar['datetime'],
                       'entry_price':ep,'exit_price':ex_px,'pnl':pnl,'reason':'EOD'})
    return pd.DataFrame(trades)

def report(tdf, label):
    if tdf.empty:
        print(f"\n=== {label} ===  NO TRADES")
        return {}
    tot = tdf['pnl'].sum()
    wr = (tdf['pnl']>0).mean()*100
    gw = tdf[tdf['pnl']>0]['pnl'].sum()
    gl = abs(tdf[tdf['pnl']<0]['pnl'].sum())
    pf = gw/gl if gl>0 else np.inf
    tdf['month'] = pd.to_datetime(tdf['exit_dt']).dt.to_period('M')
    monthly = tdf.groupby('month')['pnl'].sum()
    eq = tdf['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
    L = tdf[tdf['side']=='L']; S = tdf[tdf['side']=='S']
    print(f"\n=== {label} ===")
    print(f"  {len(tdf)}t (L={len(L)}, S={len(S)}) / WR {wr:.1f}% / PF {pf:.2f}")
    print(f"  PnL ${tot:.0f} / MDD ${mdd:.0f} / {(monthly>0).sum()}/{len(monthly)} mo+")
    print(f"  Worst mo ${monthly.min():.0f} / Best mo ${monthly.max():.0f}")
    print(f"  L ${L['pnl'].sum():.0f} ({len(L)}t, WR {(L['pnl']>0).mean()*100 if len(L) else 0:.0f}%)")
    print(f"  S ${S['pnl'].sum():.0f} ({len(S)}t, WR {(S['pnl']>0).mean()*100 if len(S) else 0:.0f}%)")
    return {'trades':len(tdf),'wr':wr,'pf':pf,'pnl':tot,'mdd':mdd,
            'mo_pos':(monthly>0).sum(),'mo_tot':len(monthly),
            'worst':monthly.min(),'best':monthly.max(),
            'L_pnl':L['pnl'].sum(),'S_pnl':S['pnl'].sum()}

# ============================================================
# R2: Daily Opening Range Breakout
# ============================================================
print("\n" + "="*60)
print("R2: Daily Opening Range Breakout")
print("="*60)

df2 = df.copy()
df2['date'] = df2['datetime'].dt.date
df2['hour'] = df2['datetime'].dt.hour

# Each day's opening range = first 4 bars (hour 0-3)
or_mask = df2['hour'] < 4
# Aggregate OR per day (use the 4 bars' high/low)
or_df = df2[or_mask].groupby('date').agg(or_h=('high','max'), or_l=('low','min')).reset_index()
df2 = df2.merge(or_df, on='date', how='left')

# Signal only valid when hour >= 4 (OR window has ended)
df2['brk_L'] = (df2['hour'] >= 4) & (df2['close'] > df2['or_h'])
df2['brk_S'] = (df2['hour'] >= 4) & (df2['close'] < df2['or_l'])

# First breakout per day
df2['first_L'] = df2['brk_L'] & ~(
    df2.groupby('date')['brk_L']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))
df2['first_S'] = df2['brk_S'] & ~(
    df2.groupby('date')['brk_S']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))

# IS
is_work = df2.iloc[0:IS_END].reset_index(drop=True)
r2_is = simulate(is_work, 'first_L', 'first_S', hold_bars=12, safenet_pct=3.5)
r2_is_stats = report(r2_is, "R2 IS (hold=12, SL=3.5%)")

# OOS
oos_work = df2.iloc[IS_END:N].reset_index(drop=True)
r2_oos = simulate(oos_work, 'first_L', 'first_S', hold_bars=12, safenet_pct=3.5)
r2_oos_stats = report(r2_oos, "R2 OOS (hold=12, SL=3.5%)")

# G4 grid on IS
print("\n--- R2 G4 Neighborhood (IS only) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [6, 12, 24, 36]:
    for sl in [2.5, 3.0, 3.5, 4.0]:
        t = simulate(is_work, 'first_L', 'first_S', h, sl)
        if not t.empty:
            tot = t['pnl'].sum()
            wr = (t['pnl']>0).mean()*100
            gw = t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf = gw/gl if gl>0 else np.inf
            eq = t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

# ============================================================
# R3: Multi-bar Staircase (N=4)
# ============================================================
print("\n" + "="*60)
print("R3: Multi-bar Staircase (N=4)")
print("="*60)

df3 = df.copy()
N_STAIR = 4

# Bull staircase: last 4 bars each have low > previous low
# We look at bars (i-3, i-2, i-1, i) — 4 bars monotone low increasing
def rolling_monotone_inc(s, n):
    """True at index i if s[i-n+1..i] is strictly monotone increasing"""
    out = np.zeros(len(s), dtype=bool)
    arr = s.values
    for i in range(n-1, len(s)):
        mono = True
        for k in range(1, n):
            if arr[i-k+1] <= arr[i-k]:
                mono = False; break
        out[i] = mono
    return out

def rolling_monotone_dec(s, n):
    out = np.zeros(len(s), dtype=bool)
    arr = s.values
    for i in range(n-1, len(s)):
        mono = True
        for k in range(1, n):
            if arr[i-k+1] >= arr[i-k]:
                mono = False; break
        out[i] = mono
    return out

df3['stair_up'] = rolling_monotone_inc(df3['low'], N_STAIR)  # monotone rising lows over 4 bars
df3['stair_dn'] = rolling_monotone_dec(df3['high'], N_STAIR) # monotone falling highs over 4 bars

# After staircase forms at bar i, trigger is next bar's close > max(high over staircase bars)
# i.e., at bar i+1 or later, check close > range_high
# We track: stair_top_at_i = max(high, i-3, ..., i); stair_bot_at_i = min(low, i-3,...,i)
df3['stair_top'] = df3['high'].rolling(N_STAIR).max()
df3['stair_bot'] = df3['low'].rolling(N_STAIR).min()

# Signal at bar i: stair_up at bar (i-1) AND close[i] > stair_top[i-1]
# Use .shift(1) so data used is known at bar i's close
df3['setup_L'] = df3['stair_up'].shift(1).fillna(False)
df3['setup_S'] = df3['stair_dn'].shift(1).fillna(False)
df3['ref_top'] = df3['stair_top'].shift(1)
df3['ref_bot'] = df3['stair_bot'].shift(1)
df3['brk_L'] = df3['setup_L'] & (df3['close'] > df3['ref_top'])
df3['brk_S'] = df3['setup_S'] & (df3['close'] < df3['ref_bot'])

# Cooldown: avoid re-entering the same setup pattern — enforce minimum gap
# Use "first breakout per staircase": approximate via "the condition just turned True from False"
df3['first_L'] = df3['brk_L'] & ~df3['brk_L'].shift(1).fillna(False)
df3['first_S'] = df3['brk_S'] & ~df3['brk_S'].shift(1).fillna(False)

# IS
is_work = df3.iloc[0:IS_END].reset_index(drop=True)
r3_is = simulate(is_work, 'first_L', 'first_S', hold_bars=12, safenet_pct=3.5)
r3_is_stats = report(r3_is, "R3 IS (N=4, hold=12, SL=3.5%)")

# OOS
oos_work = df3.iloc[IS_END:N].reset_index(drop=True)
r3_oos = simulate(oos_work, 'first_L', 'first_S', hold_bars=12, safenet_pct=3.5)
r3_oos_stats = report(r3_oos, "R3 OOS (N=4, hold=12, SL=3.5%)")

# G4 grid on IS (hold × SL; keep N=4 fixed since that's the pattern param)
print("\n--- R3 G4 Neighborhood (IS only, N=4 fixed) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [6, 12, 24, 36]:
    for sl in [2.5, 3.0, 3.5, 4.0]:
        t = simulate(is_work, 'first_L', 'first_S', h, sl)
        if not t.empty:
            tot = t['pnl'].sum()
            wr = (t['pnl']>0).mean()*100
            gw = t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf = gw/gl if gl>0 else np.inf
            eq = t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

# Also scan N for staircase length (structural parameter)
print("\n--- R3 Staircase Length N (IS, hold=12, SL=3.5%) ---")
print(f"{'N':>3} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for n_s in [3, 4, 5, 6]:
    d = df.copy()
    d['stair_up'] = rolling_monotone_inc(d['low'], n_s)
    d['stair_dn'] = rolling_monotone_dec(d['high'], n_s)
    d['stair_top'] = d['high'].rolling(n_s).max()
    d['stair_bot'] = d['low'].rolling(n_s).min()
    d['setup_L'] = d['stair_up'].shift(1).fillna(False)
    d['setup_S'] = d['stair_dn'].shift(1).fillna(False)
    d['ref_top'] = d['stair_top'].shift(1)
    d['ref_bot'] = d['stair_bot'].shift(1)
    d['brk_L'] = d['setup_L'] & (d['close'] > d['ref_top'])
    d['brk_S'] = d['setup_S'] & (d['close'] < d['ref_bot'])
    d['first_L'] = d['brk_L'] & ~d['brk_L'].shift(1).fillna(False)
    d['first_S'] = d['brk_S'] & ~d['brk_S'].shift(1).fillna(False)
    w = d.iloc[0:IS_END].reset_index(drop=True)
    t = simulate(w, 'first_L', 'first_S', 12, 3.5)
    if not t.empty:
        tot = t['pnl'].sum(); wr = (t['pnl']>0).mean()*100
        gw = t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
        pf = gw/gl if gl>0 else np.inf
        eq = t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
        print(f"{n_s:>3} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

r2_is.to_csv("backtest/research/v21_r2_is.csv", index=False)
r2_oos.to_csv("backtest/research/v21_r2_oos.csv", index=False)
r3_is.to_csv("backtest/research/v21_r3_is.csv", index=False)
r3_oos.to_csv("backtest/research/v21_r3_oos.csv", index=False)
print("\nSaved: v21_r2/r3 IS/OOS csvs")
