"""
V19 R1: Cross-Market & Sentiment Feasibility Study
====================================================
Question: Do SPX/DXY/VIX/US10Y/FGI predict ETH returns?

Analyses:
  A. Data overview & alignment
  B. Daily correlation matrix (lag 0-5 days)
  C. Conditional returns by macro quintile
  D. FGI regime analysis
  E. Multi-variable regime (Risk-On vs Risk-Off)
  F. Best signal prototype with IS/OOS check
"""

import pandas as pd
import numpy as np
import os
from scipy import stats

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')


# =========================================================================
# Load & Align Data
# =========================================================================

def load_all():
    # --- ETH 1h → daily ---
    eth = pd.read_csv(os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv'))
    eth['datetime'] = pd.to_datetime(eth['datetime'])
    eth['date'] = eth['datetime'].dt.date
    eth_daily = eth.groupby('date').agg(
        eth_open=('open', 'first'),
        eth_high=('high', 'max'),
        eth_low=('low', 'min'),
        eth_close=('close', 'last'),
        eth_vol=('volume', 'sum'),
    ).reset_index()
    eth_daily['date'] = pd.to_datetime(eth_daily['date'])
    eth_daily = eth_daily.sort_values('date').reset_index(drop=True)
    eth_daily['eth_ret'] = eth_daily['eth_close'].pct_change()

    # Forward returns (what we want to predict)
    for n in [1, 2, 3, 5]:
        eth_daily[f'eth_fwd_{n}d'] = (
            eth_daily['eth_close'].shift(-n) / eth_daily['eth_close'] - 1
        )

    # --- Macro daily ---
    macro = eth_daily[['date']].copy()

    for name, filename in [('SPX', 'SPX_daily.csv'), ('DXY', 'DXY_daily.csv'),
                           ('VIX', 'VIX_daily.csv'), ('US10Y', 'US10Y_daily.csv')]:
        df = pd.read_csv(os.path.join(DATA_DIR, filename))
        df['date'] = pd.to_datetime(df['date'])
        # Use Close column
        df = df[['date', 'Close']].rename(columns={'Close': f'{name}'})
        df = df.sort_values('date')

        # Reindex to full calendar dates, forward-fill weekends/holidays
        full_dates = pd.DataFrame({'date': pd.date_range(df['date'].min(), df['date'].max())})
        df = full_dates.merge(df, on='date', how='left')
        df[name] = df[name].ffill()

        # Daily return
        df[f'{name}_ret'] = df[name].pct_change()

        # Shift by 1 day to avoid look-ahead:
        # SPX close on Monday → available Tue morning → predict Tue ETH
        df[f'{name}_ret_lag1'] = df[f'{name}_ret'].shift(1)
        df[f'{name}_lag1'] = df[name].shift(1)

        macro = macro.merge(df[['date', name, f'{name}_ret', f'{name}_ret_lag1', f'{name}_lag1']],
                            on='date', how='left')

    # Forward-fill any remaining NaN (start of series)
    for col in macro.columns:
        if col != 'date':
            macro[col] = macro[col].ffill()

    # --- FGI daily ---
    fgi = pd.read_csv(os.path.join(DATA_DIR, 'FearGreed_daily.csv'))
    fgi['date'] = pd.to_datetime(fgi['date'])
    fgi = fgi[['date', 'value']].rename(columns={'value': 'FGI'})
    fgi = fgi.sort_values('date')

    # FGI shift 1 day (published at end of day → use next day)
    fgi['FGI_lag1'] = fgi['FGI'].shift(1)
    fgi['FGI_delta'] = fgi['FGI'].diff()
    fgi['FGI_delta_lag1'] = fgi['FGI_delta'].shift(1)

    macro = macro.merge(fgi[['date', 'FGI', 'FGI_lag1', 'FGI_delta_lag1']], on='date', how='left')
    macro['FGI_lag1'] = macro['FGI_lag1'].ffill()
    macro['FGI_delta_lag1'] = macro['FGI_delta_lag1'].ffill()

    # --- Merge macro into ETH daily ---
    merged = eth_daily.merge(macro.drop(columns=['date'], errors='ignore'),
                             left_index=True, right_index=True, how='left')
    # Actually merge on date
    merged = eth_daily.merge(macro, on='date', how='left')
    merged = merged.dropna(subset=['eth_ret']).reset_index(drop=True)

    return merged


# =========================================================================
# A: Data Overview
# =========================================================================

def section_a(df):
    print("=" * 70)
    print("A. DATA OVERVIEW")
    print("=" * 70)
    print(f"\nDate range: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print(f"Total days: {len(df)}")

    cols_check = ['SPX_ret_lag1', 'DXY_ret_lag1', 'VIX_ret_lag1', 'US10Y_ret_lag1',
                  'FGI_lag1', 'eth_ret', 'eth_fwd_1d']
    for col in cols_check:
        valid = df[col].notna().sum()
        print(f"  {col}: {valid} valid ({valid/len(df)*100:.0f}%)")

    # IS/OOS split point
    mid = len(df) // 2
    print(f"\nIS: {df['date'].iloc[0].date()} ~ {df['date'].iloc[mid-1].date()} ({mid} days)")
    print(f"OOS: {df['date'].iloc[mid].date()} ~ {df['date'].iloc[-1].date()} ({len(df)-mid} days)")

    # Basic ETH stats
    print(f"\nETH daily return: mean={df['eth_ret'].mean()*100:.3f}%, "
          f"std={df['eth_ret'].std()*100:.2f}%, "
          f"median={df['eth_ret'].median()*100:.3f}%")
    return mid


# =========================================================================
# B: Correlation Matrix (Multiple Lags)
# =========================================================================

def section_b(df):
    print("\n" + "=" * 70)
    print("B. CORRELATION: Macro (lag-1) vs ETH Forward Returns")
    print("=" * 70)
    print("(All macro variables use previous day's value to avoid look-ahead)")

    predictors = ['SPX_ret_lag1', 'DXY_ret_lag1', 'VIX_ret_lag1', 'US10Y_ret_lag1',
                  'FGI_lag1', 'FGI_delta_lag1']
    targets = ['eth_fwd_1d', 'eth_fwd_2d', 'eth_fwd_3d', 'eth_fwd_5d']

    print(f"\n{'Predictor':<20}", end='')
    for t in targets:
        print(f" {t:>12}", end='')
    print()
    print("-" * 72)

    for pred in predictors:
        print(f"{pred:<20}", end='')
        for tgt in targets:
            valid = df[[pred, tgt]].dropna()
            if len(valid) < 30:
                print(f" {'N/A':>12}", end='')
                continue
            r, p = stats.pearsonr(valid[pred], valid[tgt])
            star = '*' if p < 0.05 else ' '
            print(f" {r:>+.4f}{star}(p{p:.2f})", end='')
        print()

    # Also check lag 2-5
    print(f"\n--- Extended Lag Analysis (SPX_ret vs eth_fwd_1d) ---")
    for lag in range(0, 6):
        col = f'SPX_ret_lag{lag}'
        if lag == 0:
            df['_spx_lag0'] = df['SPX_ret']
            col = '_spx_lag0'
        elif lag > 1:
            df[col] = df['SPX_ret'].shift(lag)

        valid = df[[col, 'eth_fwd_1d']].dropna()
        if len(valid) < 30:
            continue
        r, p = stats.pearsonr(valid[col], valid['eth_fwd_1d'])
        star = '*' if p < 0.05 else ''
        print(f"  SPX lag-{lag}: r={r:+.4f}, p={p:.3f} {star}")


# =========================================================================
# C: Conditional Returns by Quintile
# =========================================================================

def section_c(df):
    print("\n" + "=" * 70)
    print("C. CONDITIONAL RETURNS: ETH 1d fwd return by macro quintile")
    print("=" * 70)

    predictors = {
        'SPX_ret_lag1': 'SPX prev-day return',
        'DXY_ret_lag1': 'DXY prev-day return',
        'VIX_ret_lag1': 'VIX prev-day change',
        'US10Y_ret_lag1': 'US10Y prev-day change',
        'FGI_lag1': 'Fear & Greed Index',
    }

    for col, label in predictors.items():
        valid = df[[col, 'eth_fwd_1d']].dropna()
        if len(valid) < 100:
            print(f"\n{label}: insufficient data ({len(valid)} rows)")
            continue

        # Quintiles
        try:
            valid['q'] = pd.qcut(valid[col], 5, labels=['Q1(low)', 'Q2', 'Q3', 'Q4', 'Q5(high)'],
                                 duplicates='drop')
        except ValueError:
            valid['q'] = pd.cut(valid[col], 5, labels=['Q1(low)', 'Q2', 'Q3', 'Q4', 'Q5(high)'],
                                duplicates='drop')

        print(f"\n--- {label} ({col}) ---")
        print(f"{'Quintile':<12} {'N':>5} {'Mean ETH ret':>12} {'Median':>10} {'StdDev':>10} {'t-stat':>8} {'p-val':>8}")

        for q_name in ['Q1(low)', 'Q2', 'Q3', 'Q4', 'Q5(high)']:
            subset = valid[valid['q'] == q_name]['eth_fwd_1d']
            if len(subset) < 5:
                continue
            mean_r = subset.mean() * 100
            med_r = subset.median() * 100
            std_r = subset.std() * 100
            t, p = stats.ttest_1samp(subset, 0)
            print(f"{q_name:<12} {len(subset):>5} {mean_r:>+11.3f}% {med_r:>+9.3f}% {std_r:>9.2f}% {t:>+7.2f} {p:>7.3f}")

        # Q1 vs Q5 difference
        q1 = valid[valid['q'] == 'Q1(low)']['eth_fwd_1d']
        q5 = valid[valid['q'] == 'Q5(high)']['eth_fwd_1d']
        if len(q1) > 5 and len(q5) > 5:
            t_diff, p_diff = stats.ttest_ind(q5, q1)
            print(f"  Q5-Q1 spread: {(q5.mean()-q1.mean())*100:+.3f}%, t={t_diff:+.2f}, p={p_diff:.3f}")


# =========================================================================
# D: FGI Regime Analysis
# =========================================================================

def section_d(df):
    print("\n" + "=" * 70)
    print("D. FGI REGIME ANALYSIS")
    print("=" * 70)

    valid = df[['FGI_lag1', 'eth_fwd_1d', 'eth_fwd_3d', 'eth_fwd_5d']].dropna()

    bins = [0, 20, 35, 50, 65, 80, 100]
    labels = ['ExtFear(0-20)', 'Fear(20-35)', 'Neutral-(35-50)',
              'Neutral+(50-65)', 'Greed(65-80)', 'ExtGreed(80-100)']
    valid['regime'] = pd.cut(valid['FGI_lag1'], bins=bins, labels=labels, include_lowest=True)

    print(f"\n{'Regime':<18} {'N':>5}  {'fwd_1d':>10} {'fwd_3d':>10} {'fwd_5d':>10}")
    print("-" * 60)

    for regime in labels:
        sub = valid[valid['regime'] == regime]
        if len(sub) < 5:
            print(f"{regime:<18} {len(sub):>5}  {'--':>10} {'--':>10} {'--':>10}")
            continue
        m1 = sub['eth_fwd_1d'].mean() * 100
        m3 = sub['eth_fwd_3d'].mean() * 100
        m5 = sub['eth_fwd_5d'].mean() * 100
        print(f"{regime:<18} {len(sub):>5}  {m1:>+9.3f}% {m3:>+9.3f}% {m5:>+9.3f}%")

    # Extreme fear vs extreme greed
    fear = valid[valid['FGI_lag1'] <= 20]['eth_fwd_1d']
    greed = valid[valid['FGI_lag1'] >= 80]['eth_fwd_1d']
    print(f"\nExtreme Fear (<=20): N={len(fear)}, mean={fear.mean()*100:+.3f}%")
    print(f"Extreme Greed (>=80): N={len(greed)}, mean={greed.mean()*100:+.3f}%")
    if len(fear) > 5 and len(greed) > 5:
        t, p = stats.ttest_ind(fear, greed)
        print(f"Difference: t={t:+.2f}, p={p:.3f}")


# =========================================================================
# E: Multi-Variable Regime
# =========================================================================

def section_e(df):
    print("\n" + "=" * 70)
    print("E. MULTI-VARIABLE REGIME (Risk-On vs Risk-Off)")
    print("=" * 70)

    valid = df[['SPX_ret_lag1', 'DXY_ret_lag1', 'VIX_ret_lag1',
                'eth_fwd_1d', 'eth_fwd_3d', 'eth_fwd_5d']].dropna()

    # Risk-On: SPX up, DXY down, VIX down
    risk_on = valid[
        (valid['SPX_ret_lag1'] > 0) &
        (valid['DXY_ret_lag1'] < 0) &
        (valid['VIX_ret_lag1'] < 0)
    ]
    # Risk-Off: SPX down, DXY up, VIX up
    risk_off = valid[
        (valid['SPX_ret_lag1'] < 0) &
        (valid['DXY_ret_lag1'] > 0) &
        (valid['VIX_ret_lag1'] > 0)
    ]
    # Mixed
    mixed = valid[~valid.index.isin(risk_on.index) & ~valid.index.isin(risk_off.index)]

    print(f"\n{'Regime':<15} {'N':>5} {'%':>6}  {'fwd_1d':>10} {'fwd_3d':>10} {'fwd_5d':>10}")
    print("-" * 60)

    for name, sub in [('Risk-On', risk_on), ('Risk-Off', risk_off), ('Mixed', mixed)]:
        if len(sub) < 5:
            print(f"{name:<15} {len(sub):>5} {len(sub)/len(valid)*100:>5.1f}%  {'--':>10} {'--':>10} {'--':>10}")
            continue
        m1 = sub['eth_fwd_1d'].mean() * 100
        m3 = sub['eth_fwd_3d'].mean() * 100
        m5 = sub['eth_fwd_5d'].mean() * 100
        print(f"{name:<15} {len(sub):>5} {len(sub)/len(valid)*100:>5.1f}%  {m1:>+9.3f}% {m3:>+9.3f}% {m5:>+9.3f}%")

    if len(risk_on) > 5 and len(risk_off) > 5:
        t, p = stats.ttest_ind(risk_on['eth_fwd_1d'], risk_off['eth_fwd_1d'])
        print(f"\nRisk-On vs Risk-Off (fwd_1d): t={t:+.2f}, p={p:.3f}")

    # === Stronger filters ===
    print("\n--- Stronger Filters ---")

    # SPX big move
    spx_big_up = valid[valid['SPX_ret_lag1'] > valid['SPX_ret_lag1'].quantile(0.80)]
    spx_big_dn = valid[valid['SPX_ret_lag1'] < valid['SPX_ret_lag1'].quantile(0.20)]
    print(f"\nSPX big up (top 20%): N={len(spx_big_up)}, ETH fwd_1d={spx_big_up['eth_fwd_1d'].mean()*100:+.3f}%")
    print(f"SPX big dn (bot 20%): N={len(spx_big_dn)}, ETH fwd_1d={spx_big_dn['eth_fwd_1d'].mean()*100:+.3f}%")

    # VIX spike
    vix_spike = valid[valid['VIX_ret_lag1'] > valid['VIX_ret_lag1'].quantile(0.90)]
    vix_calm = valid[valid['VIX_ret_lag1'] < valid['VIX_ret_lag1'].quantile(0.10)]
    print(f"\nVIX spike (top 10%): N={len(vix_spike)}, ETH fwd_1d={vix_spike['eth_fwd_1d'].mean()*100:+.3f}%")
    print(f"VIX calm  (bot 10%): N={len(vix_calm)}, ETH fwd_1d={vix_calm['eth_fwd_1d'].mean()*100:+.3f}%")

    # DXY big move
    dxy_big_up = valid[valid['DXY_ret_lag1'] > valid['DXY_ret_lag1'].quantile(0.80)]
    dxy_big_dn = valid[valid['DXY_ret_lag1'] < valid['DXY_ret_lag1'].quantile(0.20)]
    print(f"\nDXY big up (top 20%): N={len(dxy_big_up)}, ETH fwd_1d={dxy_big_up['eth_fwd_1d'].mean()*100:+.3f}%")
    print(f"DXY big dn (bot 20%): N={len(dxy_big_dn)}, ETH fwd_1d={dxy_big_dn['eth_fwd_1d'].mean()*100:+.3f}%")


# =========================================================================
# F: Signal Prototypes with IS/OOS
# =========================================================================

def section_f(df):
    print("\n" + "=" * 70)
    print("F. SIGNAL PROTOTYPES (IS/OOS Check)")
    print("=" * 70)
    print("Account: $1K / 20x / $4K notional / $4 fee per trade")
    print("Entry at next day open, exit at end of day (1-day hold)")

    mid = len(df) // 2
    is_df = df.iloc[:mid].copy()
    oos_df = df.iloc[mid:].copy()

    NOTIONAL = 4000
    FEE = 4

    signals = []

    # --- Signal 1: FGI contrarian ---
    # Long when Extreme Fear, Short when Extreme Greed
    def fgi_contrarian(d, fear_th=25, greed_th=75):
        if pd.isna(d.get('FGI_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['FGI_lag1'] <= fear_th:
            return ('L', d['eth_fwd_1d'])
        elif d['FGI_lag1'] >= greed_th:
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('FGI Contrarian (F<=25 L, F>=75 S)', fgi_contrarian))

    # --- Signal 2: SPX momentum (follow SPX) ---
    def spx_follow(d, th=0.005):
        if pd.isna(d.get('SPX_ret_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['SPX_ret_lag1'] > th:
            return ('L', d['eth_fwd_1d'])
        elif d['SPX_ret_lag1'] < -th:
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('SPX Follow (>+0.5% L, <-0.5% S)', spx_follow))

    # --- Signal 3: DXY inverse ---
    def dxy_inverse(d, th=0.003):
        if pd.isna(d.get('DXY_ret_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['DXY_ret_lag1'] < -th:
            return ('L', d['eth_fwd_1d'])
        elif d['DXY_ret_lag1'] > th:
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('DXY Inverse (DXY dn L, DXY up S)', dxy_inverse))

    # --- Signal 4: VIX mean reversion ---
    def vix_meanrev(d, th=0.05):
        if pd.isna(d.get('VIX_ret_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['VIX_ret_lag1'] > th:  # VIX spike → buy the fear
            return ('L', d['eth_fwd_1d'])
        elif d['VIX_ret_lag1'] < -th:  # VIX drop → sell the complacency
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('VIX MeanRev (spike L, calm S)', vix_meanrev))

    # --- Signal 5: Risk-On/Off composite ---
    def risk_composite(d):
        if pd.isna(d.get('SPX_ret_lag1')) or pd.isna(d.get('DXY_ret_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        score = 0
        if d['SPX_ret_lag1'] > 0:
            score += 1
        else:
            score -= 1
        if d['DXY_ret_lag1'] < 0:
            score += 1
        else:
            score -= 1
        if not pd.isna(d.get('VIX_ret_lag1')):
            if d['VIX_ret_lag1'] < 0:
                score += 1
            else:
                score -= 1

        if score >= 2:
            return ('L', d['eth_fwd_1d'])
        elif score <= -2:
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('Risk Composite (2+ bullish L, 2+ bearish S)', risk_composite))

    # --- Signal 6: FGI momentum (follow trend) ---
    def fgi_momentum(d, th=5):
        if pd.isna(d.get('FGI_delta_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['FGI_delta_lag1'] > th:
            return ('L', d['eth_fwd_1d'])
        elif d['FGI_delta_lag1'] < -th:
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('FGI Momentum (delta>5 L, delta<-5 S)', fgi_momentum))

    # --- Signal 7: VIX level regime ---
    def vix_level(d):
        if pd.isna(d.get('VIX_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['VIX_lag1'] > 30:  # High VIX = fear, potential bounce
            return ('L', d['eth_fwd_1d'])
        elif d['VIX_lag1'] < 15:  # Low VIX = complacency, potential drop
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('VIX Level (>30 L, <15 S)', vix_level))

    # --- Signal 8: US10Y regime ---
    def us10y_move(d, th=0.02):
        if pd.isna(d.get('US10Y_ret_lag1')) or pd.isna(d.get('eth_fwd_1d')):
            return None
        if d['US10Y_ret_lag1'] < -th:  # Yields falling = risk-on
            return ('L', d['eth_fwd_1d'])
        elif d['US10Y_ret_lag1'] > th:  # Yields rising = risk-off
            return ('S', -d['eth_fwd_1d'])
        return None

    signals.append(('US10Y Inverse (yield dn L, yield up S)', us10y_move))

    # --- Evaluate all signals ---
    print(f"\n{'Signal':<45} {'Split':>5} {'N':>5} {'Wins':>5} {'WR':>6} {'AvgRet':>8} {'PnL$':>8}")
    print("-" * 90)

    for sig_name, sig_func in signals:
        for split_name, split_df in [('IS', is_df), ('OOS', oos_df)]:
            trades = []
            for _, row in split_df.iterrows():
                result = sig_func(row.to_dict())
                if result:
                    direction, ret = result
                    pnl = ret * NOTIONAL - FEE
                    trades.append({'dir': direction, 'ret': ret, 'pnl': pnl})

            if not trades:
                print(f"{sig_name:<45} {split_name:>5} {'--':>5}")
                continue

            tdf = pd.DataFrame(trades)
            n = len(tdf)
            wins = (tdf['pnl'] > 0).sum()
            wr = wins / n * 100
            avg_ret = tdf['ret'].mean() * 100
            total_pnl = tdf['pnl'].sum()

            marker = ''
            if split_name == 'IS' and total_pnl > 0:
                marker = ' <-- IS+'
            if split_name == 'OOS' and total_pnl > 0:
                marker = ' <-- OOS+'

            print(f"{sig_name if split_name=='IS' else '':45} {split_name:>5} {n:>5} {wins:>5} "
                  f"{wr:>5.1f}% {avg_ret:>+7.3f}% {total_pnl:>+7.0f}{marker}")


# =========================================================================
# G: Multi-Day Hold (2d, 3d, 5d) Signal Check
# =========================================================================

def section_g(df):
    print("\n" + "=" * 70)
    print("G. MULTI-DAY HOLD: Best signals at 2d/3d/5d horizon")
    print("=" * 70)
    print("(Same signals but held for 2, 3, or 5 days instead of 1)")

    mid = len(df) // 2
    is_df = df.iloc[:mid].copy()
    oos_df = df.iloc[mid:].copy()

    NOTIONAL = 4000
    FEE = 4

    # Only test the most promising-looking signals
    def make_signal(col, op, th, hold_col):
        def fn(d):
            val = d.get(col)
            fwd = d.get(hold_col)
            if pd.isna(val) or pd.isna(fwd):
                return None
            if op == '>' and val > th:
                return ('L', fwd)
            elif op == '<' and val < th:
                return ('L', fwd)
            elif op == '>S' and val > th:
                return ('S', -fwd)
            elif op == '<S' and val < th:
                return ('S', -fwd)
            return None
        return fn

    configs = []
    for hold_n in [2, 3, 5]:
        hold_col = f'eth_fwd_{hold_n}d'
        configs.extend([
            (f'FGI<=25 L (hold {hold_n}d)', make_signal('FGI_lag1', '<', 25, hold_col), hold_n),
            (f'FGI>=75 S (hold {hold_n}d)', make_signal('FGI_lag1', '>S', 75, hold_col), hold_n),
            (f'VIX>30 L (hold {hold_n}d)', make_signal('VIX_lag1', '>', 30, hold_col), hold_n),
            (f'SPX>+1% L (hold {hold_n}d)', make_signal('SPX_ret_lag1', '>', 0.01, hold_col), hold_n),
            (f'SPX<-1% S (hold {hold_n}d)', make_signal('SPX_ret_lag1', '<S', -0.01, hold_col), hold_n),
            (f'DXY<-0.5% L (hold {hold_n}d)', make_signal('DXY_ret_lag1', '<', -0.005, hold_col), hold_n),
        ])

    print(f"\n{'Signal':<35} {'Split':>5} {'N':>5} {'WR':>6} {'AvgRet':>8} {'PnL$':>8}")
    print("-" * 75)

    for sig_name, sig_func, hold_n in configs:
        for split_name, split_df in [('IS', is_df), ('OOS', oos_df)]:
            trades = []
            for _, row in split_df.iterrows():
                result = sig_func(row.to_dict())
                if result:
                    direction, ret = result
                    pnl = ret * NOTIONAL - FEE
                    trades.append({'ret': ret, 'pnl': pnl})

            if not trades:
                continue

            tdf = pd.DataFrame(trades)
            n = len(tdf)
            wr = (tdf['pnl'] > 0).sum() / n * 100
            avg_ret = tdf['ret'].mean() * 100
            total_pnl = tdf['pnl'].sum()

            marker = ''
            if total_pnl > 0:
                marker = ' +'

            label = sig_name if split_name == 'IS' else ''
            print(f"{label:<35} {split_name:>5} {n:>5} {wr:>5.1f}% {avg_ret:>+7.3f}% {total_pnl:>+7.0f}{marker}")


# =========================================================================
# Main
# =========================================================================

if __name__ == '__main__':
    print("V19 R1: Cross-Market & Sentiment Feasibility Study")
    print("=" * 70)

    df = load_all()
    mid = section_a(df)
    section_b(df)
    section_c(df)
    section_d(df)
    section_e(df)
    section_f(df)
    section_g(df)

    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    print("Check: any signal with IS+ AND OOS+ AND WR > 50%?")
    print("If no → macro/sentiment data has no tradable edge on ETH daily returns.")
    print("If yes → proceed to R2 for 1h-level strategy building.")
