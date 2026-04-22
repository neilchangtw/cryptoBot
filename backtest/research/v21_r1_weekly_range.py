"""
V21 R1 - Weekly Range Escape
假說：突破上週 H/L 後會延續 N bar 的趨勢動能（事件錨定，非 rolling）
方向：L+S
新增參數：2 (hold_bars, safenet_pct)

規格：
  Entry: 每週第一次 close 突破上週 H (long) 或跌破上週 L (short)
  Entry price: O[i+1]
  Hold: N bar (fixed)
  Exit: SafeNet hit OR hold N bar close
  maxTotal L=1, S=1, per-week reset
  Fee: $4/trade
"""
import pandas as pd
import numpy as np

# ---------- Load ----------
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.sort_values('datetime').reset_index(drop=True)

N = len(df)
IS_END = N // 2  # first 50%
print(f"Total bars: {N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")
print(f"IS range:  {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[IS_END-1]}")
print(f"OOS range: {df['datetime'].iloc[IS_END]} ~ {df['datetime'].iloc[N-1]}")

# ---------- Build weekly refs ----------
# ISO week: Monday start
df['year'] = df['datetime'].dt.isocalendar().year
df['week'] = df['datetime'].dt.isocalendar().week
df['week_id'] = df['year'] * 100 + df['week']

wk = df.groupby('week_id').agg(week_high=('high', 'max'),
                               week_low=('low', 'min')).reset_index()
wk['prev_week_high'] = wk['week_high'].shift(1)
wk['prev_week_low']  = wk['week_low'].shift(1)

df = df.merge(wk[['week_id', 'prev_week_high', 'prev_week_low']],
              on='week_id', how='left')

# Compute first-breakout flags on FULL df (avoid IS/OOS boundary-week bias)
df['brk_L'] = df['close'] > df['prev_week_high']
df['brk_S'] = df['close'] < df['prev_week_low']
df['first_L'] = df['brk_L'] & ~(
    df.groupby('week_id')['brk_L']
      .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))
df['first_S'] = df['brk_S'] & ~(
    df.groupby('week_id')['brk_S']
      .transform(lambda x: x.cummax().shift(1).fillna(False).astype(bool)))

# ---------- Backtest ----------
def backtest(df, hold_bars, safenet_pct, label, start, end, verbose=True):
    """
    df: full dataframe with prev_week_high/low + first_L/first_S precomputed
    hold_bars: exit after N bar close
    safenet_pct: 3.5 => 3.5%
    range: [start, end) index
    """
    NOTIONAL = 4000.0
    FEE = 4.0
    SL = safenet_pct / 100.0
    work = df.iloc[start:end].copy().reset_index(drop=True)

    trades = []
    pos = None  # {side, entry_price, entry_bar, safenet_price}
    NB = len(work)

    for i in range(NB - 1):
        o_next = work.loc[i+1, 'open']
        # Signal at bar i  -> entry at bar i+1 open
        # Skip if already in a position OR can't open due to week/data issues
        row = work.iloc[i]

        # ---- Check exit of existing position FIRST (at bar i+1 OHLC) ----
        if pos is not None:
            bar = work.iloc[i+1]
            exited = False
            ep = pos['entry_price']
            side = pos['side']
            if side == 'L':
                # SafeNet hit if low <= entry*(1-SL)
                sl_px = ep * (1 - SL)
                if bar['low'] <= sl_px:
                    trades.append({
                        'side': 'L', 'entry_bar': pos['entry_bar'], 'exit_bar': i+1,
                        'entry_price': ep, 'exit_price': sl_px,
                        'pnl': NOTIONAL * (sl_px - ep) / ep - FEE,
                        'reason': 'SL',
                        'entry_dt': pos['entry_dt'], 'exit_dt': bar['datetime']
                    })
                    pos = None; exited = True
            else:  # 'S'
                sl_px = ep * (1 + SL)
                if bar['high'] >= sl_px:
                    trades.append({
                        'side': 'S', 'entry_bar': pos['entry_bar'], 'exit_bar': i+1,
                        'entry_price': ep, 'exit_price': sl_px,
                        'pnl': NOTIONAL * (ep - sl_px) / ep - FEE,
                        'reason': 'SL',
                        'entry_dt': pos['entry_dt'], 'exit_dt': bar['datetime']
                    })
                    pos = None; exited = True

            # MaxHold: bars held = (i+1) - entry_bar
            if not exited and (i+1) - pos['entry_bar'] >= hold_bars:
                ex_px = bar['close']
                if pos['side'] == 'L':
                    pnl = NOTIONAL * (ex_px - ep) / ep - FEE
                else:
                    pnl = NOTIONAL * (ep - ex_px) / ep - FEE
                trades.append({
                    'side': pos['side'], 'entry_bar': pos['entry_bar'], 'exit_bar': i+1,
                    'entry_price': ep, 'exit_price': ex_px,
                    'pnl': pnl, 'reason': 'MH',
                    'entry_dt': pos['entry_dt'], 'exit_dt': bar['datetime']
                })
                pos = None

        # ---- Check entry at bar i+1 open (signal from bar i) ----
        if pos is None and pd.notna(row['prev_week_high']) and pd.notna(row['prev_week_low']):
            if row['first_L']:
                pos = {'side': 'L', 'entry_price': o_next, 'entry_bar': i+1,
                       'entry_dt': work.iloc[i+1]['datetime']}
            elif row['first_S']:
                pos = {'side': 'S', 'entry_price': o_next, 'entry_bar': i+1,
                       'entry_dt': work.iloc[i+1]['datetime']}

    # Force close remaining position at last bar
    if pos is not None:
        bar = work.iloc[NB-1]
        ex_px = bar['close']
        ep = pos['entry_price']
        if pos['side'] == 'L':
            pnl = NOTIONAL * (ex_px - ep) / ep - FEE
        else:
            pnl = NOTIONAL * (ep - ex_px) / ep - FEE
        trades.append({
            'side': pos['side'], 'entry_bar': pos['entry_bar'], 'exit_bar': NB-1,
            'entry_price': ep, 'exit_price': ex_px, 'pnl': pnl, 'reason': 'EOD',
            'entry_dt': pos['entry_dt'], 'exit_dt': bar['datetime']
        })

    tdf = pd.DataFrame(trades)
    if tdf.empty:
        if verbose:
            print(f"\n=== {label} ===  NO TRADES")
        return tdf, {}

    # Stats
    tot = tdf['pnl'].sum()
    wins = (tdf['pnl'] > 0).sum()
    losses = (tdf['pnl'] < 0).sum()
    wr = wins / len(tdf) * 100 if len(tdf) else 0
    gross_win = tdf[tdf['pnl']>0]['pnl'].sum()
    gross_loss = abs(tdf[tdf['pnl']<0]['pnl'].sum())
    pf = gross_win / gross_loss if gross_loss > 0 else np.inf

    # Monthly
    tdf['month'] = pd.to_datetime(tdf['exit_dt']).dt.to_period('M')
    monthly = tdf.groupby('month')['pnl'].sum()
    pos_months = (monthly > 0).sum()
    tot_months = len(monthly)

    # MDD on equity curve
    eq = tdf['pnl'].cumsum()
    peak = eq.cummax()
    dd = (peak - eq)
    mdd = dd.max()

    # By side
    L = tdf[tdf['side']=='L']
    S = tdf[tdf['side']=='S']

    stats = {
        'trades': len(tdf), 'L': len(L), 'S': len(S),
        'wr': wr, 'pf': pf, 'pnl': tot, 'mdd': mdd,
        'pos_months': pos_months, 'tot_months': tot_months,
        'worst_month': monthly.min(), 'best_month': monthly.max(),
        'L_pnl': L['pnl'].sum(), 'S_pnl': S['pnl'].sum(),
    }

    if verbose:
        print(f"\n=== {label} (hold={hold_bars}, SL={safenet_pct}%) ===")
        print(f"  Trades: {len(tdf)}  (L={len(L)}, S={len(S)})")
        print(f"  WR: {wr:.1f}%  PF: {pf:.2f}")
        print(f"  Total PnL: ${tot:.0f}  MDD: ${mdd:.0f}")
        print(f"  Monthly positive: {pos_months}/{tot_months}")
        print(f"  Worst month: ${monthly.min():.0f}  Best month: ${monthly.max():.0f}")
        print(f"  L PnL: ${L['pnl'].sum():.0f} ({len(L)}t, WR {(L['pnl']>0).mean()*100:.0f}%)")
        print(f"  S PnL: ${S['pnl'].sum():.0f} ({len(S)}t, WR {(S['pnl']>0).mean()*100:.0f}%)")
        print(f"  Exit reasons: {tdf['reason'].value_counts().to_dict()}")
    return tdf, stats

# ---------- Stage 2: IS with a priori params (hold=24, SL=3.5%) ----------
is_trades, is_stats = backtest(df, 24, 3.5, 'IS (baseline hold=24, SL=3.5%)', 0, IS_END)

# ---------- Stage 3: OOS ----------
oos_trades, oos_stats = backtest(df, 24, 3.5, 'OOS (baseline hold=24, SL=3.5%)', IS_END, N)

# ---------- G4: parameter neighborhood stability (IS only, don't touch OOS) ----------
print("\n\n=== G4: Parameter Neighborhood (IS only) ===")
print(f"{'hold':>5}  {'SL%':>5}  {'trades':>7}  {'WR%':>6}  {'PF':>5}  {'PnL':>8}  {'MDD':>7}")
grid_results = []
for hold in [12, 24, 48, 72]:
    for sl in [2.5, 3.0, 3.5, 4.0]:
        _, s = backtest(df, hold, sl, '', 0, IS_END, verbose=False)
        if s:
            print(f"{hold:>5}  {sl:>5}  {s['trades']:>7}  {s['wr']:>6.1f}  {s['pf']:>5.2f}  ${s['pnl']:>7.0f}  ${s['mdd']:>6.0f}")
            grid_results.append({'hold': hold, 'sl': sl, **s})

# Save
is_trades.to_csv("backtest/research/v21_r1_is_trades.csv", index=False)
oos_trades.to_csv("backtest/research/v21_r1_oos_trades.csv", index=False)
pd.DataFrame(grid_results).to_csv("backtest/research/v21_r1_grid.csv", index=False)
print("\nSaved: v21_r1_is_trades.csv, v21_r1_oos_trades.csv, v21_r1_grid.csv")
