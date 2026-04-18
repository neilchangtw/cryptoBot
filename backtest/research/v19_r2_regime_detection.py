"""
V19 R2: Statistical Structure & HMM Regime Detection
======================================================
Last resort: can HMM find hidden regimes where non-breakout ETH has directional alpha?

Part A: Autocorrelation structure (confirm/deny any exploitable serial dependency)
Part B: HMM regime detection (walk-forward, 2-4 states)
Part C: Regime-based signals with breakout exclusion, IS/OOS

If ACF is flat AND HMM regimes have symmetric returns after excluding breakout:
  → ETH alpha = breakout only. V19 conclusion.
"""

import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

from scipy import stats
try:
    from hmmlearn.hmm import GaussianHMM
    HAS_HMM = True
except ImportError:
    HAS_HMM = False
    print("WARNING: hmmlearn not installed. Skipping HMM analysis.")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')

NOTIONAL = 4000
FEE = 4
ACCOUNT = 1000


# =========================================================================
# Load Data
# =========================================================================

def load_eth():
    df = pd.read_csv(os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv'))
    df['datetime'] = pd.to_datetime(df['datetime'])
    df['ret'] = df['close'].pct_change()
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
    df['log_range'] = np.log(df['high'] / df['low'])
    df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()

    # Breakout flags (to exclude)
    df['high_15'] = df['close'].shift(1).rolling(15).max()
    df['low_15'] = df['close'].shift(1).rolling(15).min()
    df['is_brk_up'] = df['close'] > df['high_15']
    df['is_brk_dn'] = df['close'] < df['low_15']
    df['is_breakout'] = df['is_brk_up'] | df['is_brk_dn']

    # Forward returns
    for n in [1, 3, 6, 12, 24]:
        df[f'fwd_{n}'] = df['close'].shift(-n) / df['close'] - 1

    df = df.dropna(subset=['ret', 'log_range']).reset_index(drop=True)
    return df


# =========================================================================
# Part A: Autocorrelation Structure
# =========================================================================

def part_a(df):
    print("=" * 70)
    print("A. AUTOCORRELATION STRUCTURE")
    print("=" * 70)

    ret = df['ret'].dropna()

    # 1. Return autocorrelation at lags 1-48
    print("\n--- Return ACF (lags 1-48h) ---")
    print(f"{'Lag':>5} {'ACF':>8} {'t-stat':>8} {'Sig?':>5}")
    n = len(ret)
    se = 1 / np.sqrt(n)  # Standard error under null
    sig_count = 0

    for lag in [1, 2, 3, 4, 5, 6, 8, 12, 16, 24, 36, 48]:
        acf = ret.autocorr(lag)
        t = acf / se
        sig = '*' if abs(t) > 1.96 else ''
        if sig:
            sig_count += 1
        print(f"{lag:>5} {acf:>+.5f} {t:>+7.2f} {sig:>5}")

    print(f"\nSignificant lags: {sig_count}/12 (expected ~0.6 by chance at 5%)")

    # 2. Sign autocorrelation (direction persistence)
    print("\n--- Sign ACF (up/down persistence) ---")
    sign = np.sign(ret)
    print(f"{'Lag':>5} {'SignACF':>8} {'t-stat':>8} {'Sig?':>5}")
    for lag in [1, 2, 3, 4, 5, 6, 12, 24]:
        acf = sign.autocorr(lag)
        t = acf / se
        sig = '*' if abs(t) > 1.96 else ''
        print(f"{lag:>5} {acf:>+.5f} {t:>+7.2f} {sig:>5}")

    # 3. Variance ratio test (momentum vs mean-reversion)
    print("\n--- Variance Ratio Test (H0: random walk) ---")
    print("VR > 1 = momentum, VR < 1 = mean-reversion")
    for k in [2, 4, 6, 12, 24, 48]:
        var_1 = ret.var()
        ret_k = df['close'].pct_change(k).dropna()
        var_k = ret_k.var()
        vr = var_k / (k * var_1)
        # Z-test
        z = (vr - 1) * np.sqrt(n * k)  # Simplified
        z_approx = (vr - 1) / np.sqrt(2 * (2*k - 1) * (k-1) / (3 * k * n))
        sig = '*' if abs(z_approx) > 1.96 else ''
        print(f"  VR({k:>2}): {vr:.4f}  z={z_approx:+.2f} {sig}")

    # 4. Squared return ACF (volatility clustering)
    print("\n--- Squared Return ACF (volatility clustering) ---")
    ret_sq = ret ** 2
    for lag in [1, 2, 3, 6, 12, 24]:
        acf = ret_sq.autocorr(lag)
        t = acf / se
        sig = '*' if abs(t) > 1.96 else ''
        print(f"  Lag {lag:>2}: {acf:+.5f} (t={t:+.2f}) {sig}")

    # 5. Non-breakout return ACF
    print("\n--- Non-Breakout Return ACF ---")
    nb_ret = df[~df['is_breakout']]['ret'].dropna()
    se_nb = 1 / np.sqrt(len(nb_ret))
    print(f"Non-breakout bars: {len(nb_ret)} ({len(nb_ret)/len(df)*100:.1f}%)")
    for lag in [1, 2, 3, 6, 12, 24]:
        acf = nb_ret.autocorr(lag)
        t = acf / se_nb
        sig = '*' if abs(t) > 1.96 else ''
        print(f"  Lag {lag:>2}: {acf:+.5f} (t={t:+.2f}) {sig}")


# =========================================================================
# Part B: HMM Regime Detection (Walk-Forward)
# =========================================================================

def part_b(df):
    if not HAS_HMM:
        print("\n" + "=" * 70)
        print("B. HMM REGIME DETECTION — SKIPPED (hmmlearn not installed)")
        print("=" * 70)
        return None

    print("\n" + "=" * 70)
    print("B. HMM REGIME DETECTION (Walk-Forward)")
    print("=" * 70)

    # Features for HMM
    df['feat_ret'] = df['ret']
    df['feat_vol'] = df['log_range']
    df['feat_volr'] = df['vol_ratio']

    features = ['feat_ret', 'feat_vol', 'feat_volr']
    valid = df.dropna(subset=features).reset_index(drop=True)

    # Walk-forward HMM: train on past TRAIN_WINDOW bars, classify current bar
    TRAIN_WINDOW = 500  # ~21 days
    N_STATES = 3
    results = []

    print(f"\nConfig: {N_STATES} states, train window={TRAIN_WINDOW} bars")
    print(f"Features: return, log_range, volume_ratio")
    print(f"Total bars: {len(valid)}")
    print(f"Walk-forward starts at bar {TRAIN_WINDOW}")

    for i in range(TRAIN_WINDOW, len(valid)):
        if i % 2000 == 0:
            print(f"  Processing bar {i}/{len(valid)}...")

        train_data = valid[features].iloc[i-TRAIN_WINDOW:i].values

        # Normalize features
        mu = train_data.mean(axis=0)
        sigma = train_data.std(axis=0)
        sigma[sigma < 1e-10] = 1
        train_norm = (train_data - mu) / sigma

        try:
            model = GaussianHMM(
                n_components=N_STATES, covariance_type='diag',
                n_iter=50, random_state=42, verbose=False
            )
            model.fit(train_norm)

            # Classify current bar
            curr = valid[features].iloc[i:i+1].values
            curr_norm = (curr - mu) / sigma
            state = model.predict(curr_norm)[0]

            # Get state means (for return dimension)
            state_means = model.means_[:, 0] * sigma[0] + mu[0]

            results.append({
                'idx': i,
                'datetime': valid['datetime'].iloc[i],
                'state': state,
                'state_mean_ret': state_means[state],
                'ret': valid['ret'].iloc[i],
                'fwd_1': valid.get('fwd_1', pd.Series([np.nan]*len(valid))).iloc[i],
                'fwd_3': valid.get('fwd_3', pd.Series([np.nan]*len(valid))).iloc[i],
                'fwd_6': valid.get('fwd_6', pd.Series([np.nan]*len(valid))).iloc[i],
                'is_breakout': valid['is_breakout'].iloc[i],
            })
        except Exception:
            continue

    res_df = pd.DataFrame(results)
    print(f"\nClassified {len(res_df)} bars into {N_STATES} states")

    # Analyze states
    print(f"\n--- State Distribution ---")
    print(f"{'State':>6} {'N':>6} {'%':>6} {'Breakout%':>10} {'MeanRet':>10} {'Fwd1':>10} {'Fwd3':>10}")
    print("-" * 65)

    for s in range(N_STATES):
        sub = res_df[res_df['state'] == s]
        n = len(sub)
        pct = n / len(res_df) * 100
        brk_pct = sub['is_breakout'].sum() / n * 100 if n > 0 else 0
        mr = sub['ret'].mean() * 100 if n > 0 else 0
        f1 = sub['fwd_1'].mean() * 100 if n > 0 else 0
        f3 = sub['fwd_3'].mean() * 100 if n > 0 else 0
        print(f"{s:>6} {n:>6} {pct:>5.1f}% {brk_pct:>9.1f}% {mr:>+9.4f}% {f1:>+9.4f}% {f3:>+9.4f}%")

    # Non-breakout only
    print(f"\n--- NON-BREAKOUT ONLY ---")
    nb = res_df[~res_df['is_breakout']]
    print(f"Non-breakout bars: {len(nb)}")
    print(f"{'State':>6} {'N':>6} {'%':>6} {'MeanRet':>10} {'Fwd1':>10} {'Fwd3':>10} {'Fwd1 tstat':>12}")
    print("-" * 65)

    for s in range(N_STATES):
        sub = nb[nb['state'] == s]
        n = len(sub)
        if n < 10:
            continue
        pct = n / len(nb) * 100
        mr = sub['ret'].mean() * 100
        f1 = sub['fwd_1'].mean() * 100
        f3 = sub['fwd_3'].mean() * 100
        t, p = stats.ttest_1samp(sub['fwd_1'].dropna(), 0)
        sig = '*' if p < 0.05 else ''
        print(f"{s:>6} {n:>6} {pct:>5.1f}% {mr:>+9.4f}% {f1:>+9.4f}% {f3:>+9.4f}% {t:>+6.2f}(p={p:.3f}){sig}")

    return res_df


# =========================================================================
# Part C: Regime-Based Trading Signals (IS/OOS)
# =========================================================================

def part_c(df, hmm_results):
    print("\n" + "=" * 70)
    print("C. REGIME-BASED TRADING SIGNALS (IS/OOS)")
    print("=" * 70)

    # IS/OOS split
    mid = len(df) // 2
    is_end_dt = df['datetime'].iloc[mid]
    print(f"IS: before {is_end_dt}")
    print(f"OOS: from {is_end_dt}")

    # ---------------------------------------------------------------
    # Signal 1: Rolling momentum regime (no HMM needed)
    # ---------------------------------------------------------------
    print("\n--- Signal Group 1: Rolling Momentum Regime ---")
    print("Long if rolling_5bar_ret is in top 20%, Short if bottom 20%")
    print("(Excludes breakout bars)")

    df['roll_ret5'] = df['ret'].rolling(5).sum()
    df['roll_ret5_pct'] = df['roll_ret5'].rolling(100).apply(
        lambda x: stats.percentileofscore(x, x.iloc[-1]))

    for th_long, th_short in [(80, 20), (70, 30), (90, 10)]:
        for fwd_col, fwd_n in [('fwd_1', 1), ('fwd_3', 3), ('fwd_6', 6)]:
            trades_is, trades_oos = [], []
            for i, row in df.iterrows():
                if row['is_breakout'] or pd.isna(row['roll_ret5_pct']) or pd.isna(row.get(fwd_col)):
                    continue

                pnl = None
                if row['roll_ret5_pct'] >= th_long:  # Momentum long
                    pnl = row[fwd_col] * NOTIONAL - FEE
                elif row['roll_ret5_pct'] <= th_short:  # Momentum short
                    pnl = -row[fwd_col] * NOTIONAL - FEE

                if pnl is not None:
                    if i < mid:
                        trades_is.append(pnl)
                    else:
                        trades_oos.append(pnl)

            if not trades_is:
                continue
            is_pnl = sum(trades_is)
            oos_pnl = sum(trades_oos) if trades_oos else 0
            is_wr = sum(1 for t in trades_is if t > 0) / len(trades_is) * 100
            oos_wr = sum(1 for t in trades_oos if t > 0) / len(trades_oos) * 100 if trades_oos else 0

            marker = ''
            if is_pnl > 0 and oos_pnl > 0:
                marker = ' <-- BOTH+'

            print(f"  L>={th_long}/S<={th_short} hold{fwd_n}: "
                  f"IS {len(trades_is):>5}t ${is_pnl:>+7.0f} WR{is_wr:>5.1f}% | "
                  f"OOS {len(trades_oos):>4}t ${oos_pnl:>+7.0f} WR{oos_wr:>5.1f}%{marker}")

    # ---------------------------------------------------------------
    # Signal 2: Volatility regime (vol compression → long)
    # ---------------------------------------------------------------
    print("\n--- Signal Group 2: Volatility Regime ---")
    print("Long if vol compressing, Short if vol expanding (excl breakout)")

    df['vol_20'] = df['ret'].rolling(20).std()
    df['vol_5'] = df['ret'].rolling(5).std()
    df['vol_comp'] = df['vol_5'] / df['vol_20']
    df['vol_comp_pct'] = df['vol_comp'].rolling(100).apply(
        lambda x: stats.percentileofscore(x, x.iloc[-1]))

    for th_low, th_high in [(25, 75), (20, 80), (15, 85)]:
        for fwd_col, fwd_n in [('fwd_1', 1), ('fwd_6', 6)]:
            trades_is, trades_oos = [], []
            for i, row in df.iterrows():
                if row['is_breakout'] or pd.isna(row.get('vol_comp_pct')) or pd.isna(row.get(fwd_col)):
                    continue

                pnl = None
                if row['vol_comp_pct'] <= th_low:  # Vol compression → expect breakout (but we can't use breakout!)
                    # So we go long AND short equally? No — just long for simplicity
                    pnl = abs(row[fwd_col]) * NOTIONAL - FEE  # Take absolute return (straddle equivalent)

                if pnl is not None:
                    if i < mid:
                        trades_is.append(pnl)
                    else:
                        trades_oos.append(pnl)

            if not trades_is:
                continue
            is_pnl = sum(trades_is)
            oos_pnl = sum(trades_oos) if trades_oos else 0
            print(f"  VolComp<={th_low} |fwd{fwd_n}|: "
                  f"IS {len(trades_is):>5}t ${is_pnl:>+7.0f} | "
                  f"OOS {len(trades_oos):>4}t ${oos_pnl:>+7.0f}")

    # ---------------------------------------------------------------
    # Signal 3: HMM regime signals (if available)
    # ---------------------------------------------------------------
    if hmm_results is not None and len(hmm_results) > 0:
        print("\n--- Signal Group 3: HMM Regime ---")
        print("Trade based on HMM state (non-breakout only)")

        hmm_nb = hmm_results[~hmm_results['is_breakout']].copy()
        mid_hmm = len(hmm_nb) // 2

        # Find best and worst state by mean forward return
        state_means = {}
        for s in hmm_nb['state'].unique():
            sub = hmm_nb[hmm_nb['state'] == s]
            state_means[s] = sub['fwd_1'].mean() if len(sub) > 10 else 0

        best_state = max(state_means, key=state_means.get)
        worst_state = min(state_means, key=state_means.get)

        print(f"  Best state (highest fwd_1): {best_state} (mean={state_means[best_state]*100:+.4f}%)")
        print(f"  Worst state (lowest fwd_1): {worst_state} (mean={state_means[worst_state]*100:+.4f}%)")

        # Signal: Long in best state, Short in worst state
        for fwd_col, fwd_n in [('fwd_1', 1), ('fwd_3', 3)]:
            trades_is, trades_oos = [], []
            for i, (_, row) in enumerate(hmm_nb.iterrows()):
                fwd = row.get(fwd_col)
                if pd.isna(fwd):
                    continue

                pnl = None
                if row['state'] == best_state:
                    pnl = fwd * NOTIONAL - FEE
                elif row['state'] == worst_state:
                    pnl = -fwd * NOTIONAL - FEE

                if pnl is not None:
                    if i < mid_hmm:
                        trades_is.append(pnl)
                    else:
                        trades_oos.append(pnl)

            if not trades_is:
                continue
            is_pnl = sum(trades_is)
            oos_pnl = sum(trades_oos) if trades_oos else 0
            is_wr = sum(1 for t in trades_is if t > 0) / len(trades_is) * 100
            oos_wr = sum(1 for t in trades_oos if t > 0) / len(trades_oos) * 100 if trades_oos else 0

            marker = ''
            if is_pnl > 0 and oos_pnl > 0:
                marker = ' <-- BOTH+'

            print(f"  HMM best/worst hold{fwd_n}: "
                  f"IS {len(trades_is):>5}t ${is_pnl:>+7.0f} WR{is_wr:>5.1f}% | "
                  f"OOS {len(trades_oos):>4}t ${oos_pnl:>+7.0f} WR{oos_wr:>5.1f}%{marker}")

    # ---------------------------------------------------------------
    # Signal 4: Return sign persistence (consecutive bars)
    # ---------------------------------------------------------------
    print("\n--- Signal Group 4: Consecutive Bar Patterns ---")
    print("After N consecutive up/down bars (non-breakout), what happens?")

    df['sign'] = np.sign(df['ret'])
    df['consec'] = 0
    for i in range(1, len(df)):
        if df['sign'].iloc[i] == df['sign'].iloc[i-1] and df['sign'].iloc[i] != 0:
            df.loc[df.index[i], 'consec'] = df['consec'].iloc[i-1] + 1

    for n_consec in [3, 4, 5]:
        for fwd_col, fwd_n in [('fwd_1', 1), ('fwd_3', 3)]:
            # Momentum: follow the streak
            trades_is_mom, trades_oos_mom = [], []
            # Reversal: fade the streak
            trades_is_rev, trades_oos_rev = [], []

            for i, row in df.iterrows():
                if row['is_breakout'] or row['consec'] < n_consec or pd.isna(row.get(fwd_col)):
                    continue

                fwd = row[fwd_col]
                direction = row['sign']  # +1 for up streak, -1 for down streak

                # Momentum: continue in same direction
                mom_pnl = direction * fwd * NOTIONAL - FEE
                # Reversal: opposite direction
                rev_pnl = -direction * fwd * NOTIONAL - FEE

                if i < mid:
                    trades_is_mom.append(mom_pnl)
                    trades_is_rev.append(rev_pnl)
                else:
                    trades_oos_mom.append(mom_pnl)
                    trades_oos_rev.append(rev_pnl)

            if not trades_is_mom:
                continue

            is_mom = sum(trades_is_mom)
            oos_mom = sum(trades_oos_mom) if trades_oos_mom else 0
            is_rev = sum(trades_is_rev)
            oos_rev = sum(trades_oos_rev) if trades_oos_rev else 0

            print(f"  {n_consec}+ consec hold{fwd_n}: "
                  f"Mom IS${is_mom:>+6.0f}/OOS${oos_mom:>+6.0f} | "
                  f"Rev IS${is_rev:>+6.0f}/OOS${oos_rev:>+6.0f} | "
                  f"N_IS={len(trades_is_mom)} N_OOS={len(trades_oos_mom)}")

    # ---------------------------------------------------------------
    # Signal 5: Skewness regime
    # ---------------------------------------------------------------
    print("\n--- Signal Group 5: Return Skewness Regime ---")
    print("Long if recent returns are negatively skewed, Short if positively skewed")

    df['skew_20'] = df['ret'].rolling(20).skew()
    df['skew_20_pct'] = df['skew_20'].rolling(100).apply(
        lambda x: stats.percentileofscore(x, x.iloc[-1]))

    for th_neg, th_pos in [(25, 75), (20, 80)]:
        for fwd_col, fwd_n in [('fwd_1', 1), ('fwd_6', 6)]:
            trades_is, trades_oos = [], []
            for i, row in df.iterrows():
                if row['is_breakout'] or pd.isna(row.get('skew_20_pct')) or pd.isna(row.get(fwd_col)):
                    continue

                pnl = None
                if row['skew_20_pct'] <= th_neg:  # Neg skew → expect bounce
                    pnl = row[fwd_col] * NOTIONAL - FEE
                elif row['skew_20_pct'] >= th_pos:  # Pos skew → expect drop
                    pnl = -row[fwd_col] * NOTIONAL - FEE

                if pnl is not None:
                    if i < mid:
                        trades_is.append(pnl)
                    else:
                        trades_oos.append(pnl)

            if not trades_is:
                continue
            is_pnl = sum(trades_is)
            oos_pnl = sum(trades_oos) if trades_oos else 0
            is_wr = sum(1 for t in trades_is if t > 0) / len(trades_is) * 100
            oos_wr = sum(1 for t in trades_oos if t > 0) / len(trades_oos) * 100 if trades_oos else 0

            marker = ''
            if is_pnl > 0 and oos_pnl > 0:
                marker = ' <-- BOTH+'

            print(f"  Skew<={th_neg}L/>={th_pos}S hold{fwd_n}: "
                  f"IS {len(trades_is):>5}t ${is_pnl:>+7.0f} WR{is_wr:>5.1f}% | "
                  f"OOS {len(trades_oos):>4}t ${oos_pnl:>+7.0f} WR{oos_wr:>5.1f}%{marker}")


# =========================================================================
# Part D: Comprehensive Non-Breakout MFE Direction Test
# =========================================================================

def part_d(df):
    print("\n" + "=" * 70)
    print("D. NON-BREAKOUT MFE SYMMETRY TEST (Final Proof)")
    print("=" * 70)
    print("If L_MFE approx= S_MFE on non-breakout bars, no signal can work.")

    nb = df[~df['is_breakout']].copy()
    mid = len(nb) // 2
    is_nb = nb.iloc[:mid]
    oos_nb = nb.iloc[mid:]

    print(f"\nNon-breakout bars: {len(nb)} (IS: {len(is_nb)}, OOS: {len(oos_nb)})")

    for hold_n in [1, 3, 6, 12]:
        fwd = f'fwd_{hold_n}'
        if fwd not in nb.columns:
            continue

        for split_name, split_df in [('IS', is_nb), ('OOS', oos_nb)]:
            vals = split_df[fwd].dropna()
            l_mfe = vals[vals > 0].mean() * 100  # Average positive return
            s_mfe = (-vals[vals < 0]).mean() * 100  # Average |negative return|
            mean_ret = vals.mean() * 100
            up_pct = (vals > 0).sum() / len(vals) * 100

            # Is mean return significantly different from 0?
            t, p = stats.ttest_1samp(vals, 0)

            print(f"  {split_name} hold{hold_n:>2}: "
                  f"L_MFE={l_mfe:>+.4f}% S_MFE={s_mfe:>.4f}% "
                  f"ratio={l_mfe/s_mfe:.3f} "
                  f"mean={mean_ret:>+.4f}% up%={up_pct:.1f}% "
                  f"t={t:+.2f} p={p:.3f}")


# =========================================================================
# Main
# =========================================================================

if __name__ == '__main__':
    print("V19 R2: Statistical Structure & HMM Regime Detection")
    print("=" * 70)

    df = load_eth()
    print(f"Loaded {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"Breakout bars: {df['is_breakout'].sum()} ({df['is_breakout'].sum()/len(df)*100:.1f}%)")
    print(f"Non-breakout bars: {(~df['is_breakout']).sum()} ({(~df['is_breakout']).sum()/len(df)*100:.1f}%)")

    part_a(df)
    hmm_res = part_b(df)
    part_c(df, hmm_res)
    part_d(df)

    print("\n" + "=" * 70)
    print("V19 R2 CONCLUSION")
    print("=" * 70)
    print("""
Check:
  1. Any significant ACF at non-trivial lags?
  2. Any HMM state with non-breakout directional alpha?
  3. Any regime signal with IS+ AND OOS+?
  4. Is non-breakout MFE symmetric (L_MFE / S_MFE ~ 1.0)?

If all answers confirm no alpha → V19 FINAL CONCLUSION:
  ETH alpha from ANY available data source = 15-bar close breakout ONLY.
""")
