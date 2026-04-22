"""
V21 R4 + R5 — Path A continued

R4: Staircase N=6 LOCKED (proper test of R3 N-sweep insight)
R5: Prior Day HL Breakout (event-anchored daily, uses yesterday's full-day range)

Both use hold=12, SL=3.5%, maxTotal L=1/S=1, $4/trade, notional $4000
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
        print(f"\n=== {label} ===  NO TRADES"); return {}
    tot = tdf['pnl'].sum(); wr = (tdf['pnl']>0).mean()*100
    gw = tdf[tdf['pnl']>0]['pnl'].sum(); gl = abs(tdf[tdf['pnl']<0]['pnl'].sum())
    pf = gw/gl if gl>0 else np.inf
    tdf['month'] = pd.to_datetime(tdf['exit_dt']).dt.to_period('M')
    monthly = tdf.groupby('month')['pnl'].sum()
    eq = tdf['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
    L = tdf[tdf['side']=='L']; S = tdf[tdf['side']=='S']
    print(f"\n=== {label} ===")
    print(f"  {len(tdf)}t (L={len(L)}, S={len(S)}) / WR {wr:.1f}% / PF {pf:.2f}")
    print(f"  PnL ${tot:.0f} / MDD ${mdd:.0f} / {(monthly>0).sum()}/{len(monthly)} mo+")
    print(f"  Worst ${monthly.min():.0f} / Best ${monthly.max():.0f}")
    print(f"  L ${L['pnl'].sum():.0f} ({len(L)}t, WR {(L['pnl']>0).mean()*100 if len(L) else 0:.0f}%)")
    print(f"  S ${S['pnl'].sum():.0f} ({len(S)}t, WR {(S['pnl']>0).mean()*100 if len(S) else 0:.0f}%)")
    return {'trades':len(tdf),'wr':wr,'pf':pf,'pnl':tot,'mdd':mdd,
            'mo_pos':(monthly>0).sum(),'mo_tot':len(monthly),
            'L_pnl':L['pnl'].sum(),'S_pnl':S['pnl'].sum()}

# ============================================================
# R4: Staircase N=6 LOCKED
# ============================================================
print("\n" + "="*60)
print("R4: Staircase N=6 LOCKED (retest R3 N-sweep insight)")
print("="*60)

def rolling_monotone_inc(s, n):
    out = np.zeros(len(s), dtype=bool); arr = s.values
    for i in range(n-1, len(s)):
        mono = all(arr[i-k+1] > arr[i-k] for k in range(1, n))
        out[i] = mono
    return out

def rolling_monotone_dec(s, n):
    out = np.zeros(len(s), dtype=bool); arr = s.values
    for i in range(n-1, len(s)):
        mono = all(arr[i-k+1] < arr[i-k] for k in range(1, n))
        out[i] = mono
    return out

def stair_signals(d, n):
    d = d.copy()
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

# Lock N=6
d6 = stair_signals(df, 6)
r4_is = simulate(d6.iloc[0:IS_END].reset_index(drop=True), 'first_L','first_S', 12, 3.5)
r4_is_stats = report(r4_is, "R4 IS (N=6 LOCKED, hold=12, SL=3.5%)")
r4_oos = simulate(d6.iloc[IS_END:N].reset_index(drop=True), 'first_L','first_S', 12, 3.5)
r4_oos_stats = report(r4_oos, "R4 OOS (N=6 LOCKED, hold=12, SL=3.5%)")

# G4 around N=6
print("\n--- R4 G4: N rigor (IS) ---")
print(f"{'N':>3} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for n_s in [5, 6, 7, 8]:
    dd = stair_signals(df, n_s)
    t = simulate(dd.iloc[0:IS_END].reset_index(drop=True), 'first_L','first_S', 12, 3.5)
    if not t.empty:
        tot=t['pnl'].sum(); wr=(t['pnl']>0).mean()*100
        gw=t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
        pf=gw/gl if gl>0 else np.inf
        eq=t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
        print(f"{n_s:>3} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

print("\n--- R4 G4: hold × SL at N=6 (IS) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [6,12,24,36]:
    for sl in [2.5,3.0,3.5,4.0]:
        t = simulate(d6.iloc[0:IS_END].reset_index(drop=True), 'first_L','first_S', h, sl)
        if not t.empty:
            tot=t['pnl'].sum(); wr=(t['pnl']>0).mean()*100
            gw=t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf=gw/gl if gl>0 else np.inf
            eq=t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

# ============================================================
# R5: Prior Day HL Breakout
# ============================================================
print("\n" + "="*60)
print("R5: Prior Day HL Breakout (yesterday's full-day H/L)")
print("="*60)

df5 = df.copy()
df5['date'] = df5['datetime'].dt.date

day_hl = df5.groupby('date').agg(day_high=('high','max'), day_low=('low','min')).reset_index()
day_hl['prev_day_high'] = day_hl['day_high'].shift(1)
day_hl['prev_day_low']  = day_hl['day_low'].shift(1)
df5 = df5.merge(day_hl[['date','prev_day_high','prev_day_low']], on='date', how='left')

df5['brk_L'] = df5['close'] > df5['prev_day_high']
df5['brk_S'] = df5['close'] < df5['prev_day_low']
df5['first_L'] = df5['brk_L'] & ~(
    df5.groupby('date')['brk_L']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))
df5['first_S'] = df5['brk_S'] & ~(
    df5.groupby('date')['brk_S']
       .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))

r5_is = simulate(df5.iloc[0:IS_END].reset_index(drop=True), 'first_L','first_S', 12, 3.5)
r5_is_stats = report(r5_is, "R5 IS (hold=12, SL=3.5%)")
r5_oos = simulate(df5.iloc[IS_END:N].reset_index(drop=True), 'first_L','first_S', 12, 3.5)
r5_oos_stats = report(r5_oos, "R5 OOS (hold=12, SL=3.5%)")

print("\n--- R5 G4: hold × SL (IS) ---")
print(f"{'hold':>5} {'SL%':>5} {'trades':>7} {'WR%':>6} {'PF':>5} {'PnL':>8} {'MDD':>7}")
for h in [6,12,24,36]:
    for sl in [2.5,3.0,3.5,4.0]:
        t = simulate(df5.iloc[0:IS_END].reset_index(drop=True), 'first_L','first_S', h, sl)
        if not t.empty:
            tot=t['pnl'].sum(); wr=(t['pnl']>0).mean()*100
            gw=t[t['pnl']>0]['pnl'].sum(); gl=abs(t[t['pnl']<0]['pnl'].sum())
            pf=gw/gl if gl>0 else np.inf
            eq=t['pnl'].cumsum(); mdd=(eq.cummax()-eq).max()
            print(f"{h:>5} {sl:>5} {len(t):>7} {wr:>6.1f} {pf:>5.2f} ${tot:>7.0f} ${mdd:>6.0f}")

r4_is.to_csv("backtest/research/v21_r4_is.csv", index=False)
r4_oos.to_csv("backtest/research/v21_r4_oos.csv", index=False)
r5_is.to_csv("backtest/research/v21_r5_is.csv", index=False)
r5_oos.to_csv("backtest/research/v21_r5_oos.csv", index=False)
print("\nSaved R4/R5 csvs")
