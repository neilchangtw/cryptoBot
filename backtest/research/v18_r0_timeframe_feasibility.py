"""
V18 R0: Multi-Timeframe Feasibility Analysis

Critical question: On which timeframe (if any) does non-breakout alpha
have enough MFE to overcome the fixed $4 fee?

For each timeframe (15m, 30m, 1h):
  1. Bar statistics (avg move, avg return, fee ratio)
  2. Breakout vs non-breakout bar split
  3. Non-breakout MFE/MAE analysis (forward-looking, N-bar hold)
  4. Fee viability: MFE - fee > 0?
  5. Best non-breakout directional signals and their MFE

Conclusion: which timeframe(s) are worth pursuing in R1+
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

NOTIONAL = 4000
FEE = 4.0
FEE_PCT = FEE / NOTIONAL * 100  # 0.1%


def load_tf(interval):
    path = DATA_DIR / f"ETHUSDT_{interval}_latest730d.csv"
    df = pd.read_csv(path, parse_dates=["datetime"])
    return df


def analyze_timeframe(df, label, warmup=100):
    """Comprehensive feasibility analysis for one timeframe."""
    n = len(df)
    sp = n // 2  # IS/OOS split

    d = df.copy()

    # Basic bar stats
    d['range_pct'] = (d['high'] - d['low']) / d['open'] * 100
    d['return_pct'] = (d['close'] - d['open']) / d['open'] * 100
    d['abs_return'] = d['return_pct'].abs()
    d['body'] = d['close'] - d['open']
    d['body_pct'] = d['body'] / d['open'] * 100

    # EMAs
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)
    d['ema20_dev'] = (d['close'] - d['ema20']) / d['ema20'] * 100

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # TBR
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_delta'] = d['tbr'] - d['tbr'].shift(1)  # TBR change rate

    # Volume
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio'] = gk.rolling(5).mean() / gk.rolling(20).mean()

    # Consecutive bars
    cu, cd = 0, 0
    consec_up = np.zeros(n)
    consec_dn = np.zeros(n)
    for i in range(n):
        if d.iloc[i].body > 0:
            cu += 1; cd = 0
        elif d.iloc[i].body < 0:
            cd += 1; cu = 0
        else:
            cu = cd = 0
        consec_up[i] = cu
        consec_dn[i] = cd
    d['consec_up'] = consec_up
    d['consec_dn'] = consec_dn

    # Returns for momentum
    d['ret_1'] = d['close'].pct_change(1) * 100
    d['ret_3'] = d['close'].pct_change(3) * 100
    d['ret_5'] = d['close'].pct_change(5) * 100

    # Breakout flags (15-bar for all timeframes — the standard)
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()
    d['is_brk_up'] = d['close'] > d['high_15']
    d['is_brk_dn'] = d['close'] < d['low_15']
    d['is_brk'] = d['is_brk_up'] | d['is_brk_dn']
    d['is_non_brk'] = ~d['is_brk']

    # Hour for session analysis
    d['hour'] = d['datetime'].dt.hour

    # Use IS period for analysis
    is_df = d.iloc[warmup:sp].copy()
    nbrk = is_df[is_df['is_non_brk']].copy()
    brk = is_df[is_df['is_brk']].copy()

    print(f"\n{'='*70}")
    print(f"  Timeframe: {label} | {n} bars | IS: {warmup}-{sp} ({sp-warmup} bars)")
    print(f"{'='*70}")

    # ── Section A: Basic Bar Statistics ──
    print(f"\n  A. Bar Statistics (IS period)")
    avg_range = is_df['range_pct'].mean()
    avg_return = is_df['abs_return'].mean()
    med_range = is_df['range_pct'].median()
    med_return = is_df['abs_return'].median()
    fee_over_range = FEE_PCT / avg_range * 100
    fee_over_return = FEE_PCT / avg_return * 100

    print(f"     avg |range|:  {avg_range:.4f}% (${avg_range/100*NOTIONAL:.2f})")
    print(f"     med |range|:  {med_range:.4f}% (${med_range/100*NOTIONAL:.2f})")
    print(f"     avg |return|: {avg_return:.4f}% (${avg_return/100*NOTIONAL:.2f})")
    print(f"     med |return|: {med_return:.4f}% (${med_return/100*NOTIONAL:.2f})")
    print(f"     fee ($4):     {FEE_PCT:.4f}% of notional")
    print(f"     fee/avg|range|:  {fee_over_range:.1f}%")
    print(f"     fee/avg|return|: {fee_over_return:.1f}%")

    # ── Section B: Breakout vs Non-Breakout Split ──
    brk_pct = len(brk) / len(is_df) * 100
    nbrk_pct = len(nbrk) / len(is_df) * 100
    print(f"\n  B. Breakout vs Non-Breakout (15-bar)")
    print(f"     Breakout bars: {len(brk)} ({brk_pct:.1f}%)")
    print(f"     Non-brk bars:  {len(nbrk)} ({nbrk_pct:.1f}%)")

    if len(brk) > 0:
        brk_avg_ret = brk['abs_return'].mean()
        nbrk_avg_ret = nbrk['abs_return'].mean()
        print(f"     Brk avg |return|:     {brk_avg_ret:.4f}%")
        print(f"     Non-brk avg |return|: {nbrk_avg_ret:.4f}%")

    # ── Section C: Non-Breakout MFE/MAE Analysis ──
    print(f"\n  C. Non-Breakout Forward MFE/MAE (IS, N-bar hold)")
    print(f"     (MFE = max favorable excursion, MAE = max adverse excursion)")
    print(f"     {'Hold':>6} | {'L MFE%':>8} {'L MAE%':>8} {'L net%':>8} {'L net$':>8} | {'S MFE%':>8} {'S MAE%':>8} {'S net%':>8} {'S net$':>8}")
    print(f"     {'-'*6}-+-{'-'*8}-{'-'*8}-{'-'*8}-{'-'*8}-+-{'-'*8}-{'-'*8}-{'-'*8}-{'-'*8}")

    best_hold = None
    best_net = -999

    for hold in [1, 2, 3, 4, 5, 6, 8, 10, 12]:
        l_mfes, l_maes = [], []
        s_mfes, s_maes = [], []

        for idx in nbrk.index:
            pos = d.index.get_loc(idx)
            if pos + hold >= n:
                continue
            entry = d.iloc[pos].close

            # Forward bars
            fwd = d.iloc[pos+1:pos+1+hold]
            if len(fwd) == 0:
                continue

            # L: MFE = max high pnl, MAE = max low loss
            l_mfe = (fwd['high'].max() - entry) / entry * 100
            l_mae = (entry - fwd['low'].min()) / entry * 100
            l_mfes.append(l_mfe)
            l_maes.append(l_mae)

            # S: MFE = max low pnl (price drops), MAE = max high loss
            s_mfe = (entry - fwd['low'].min()) / entry * 100
            s_mae = (fwd['high'].max() - entry) / entry * 100
            s_mfes.append(s_mfe)
            s_maes.append(s_mae)

        if l_mfes:
            l_mfe_avg = np.mean(l_mfes)
            l_mae_avg = np.mean(l_maes)
            l_net = l_mfe_avg - FEE_PCT  # simplified: MFE - fee
            l_net_d = l_net / 100 * NOTIONAL

            s_mfe_avg = np.mean(s_mfes)
            s_mae_avg = np.mean(s_maes)
            s_net = s_mfe_avg - FEE_PCT
            s_net_d = s_net / 100 * NOTIONAL

            print(f"     {hold:>4}b  | {l_mfe_avg:>7.4f}% {l_mae_avg:>7.4f}% {l_net:>+7.4f}% {l_net_d:>+7.2f} | "
                  f"{s_mfe_avg:>7.4f}% {s_mae_avg:>7.4f}% {s_net:>+7.4f}% {s_net_d:>+7.2f}")

            max_net = max(l_net, s_net)
            if max_net > best_net:
                best_net = max_net
                best_hold = hold

    print(f"\n     Best hold: {best_hold}b, best MFE-fee: {best_net:+.4f}% (${best_net/100*NOTIONAL:+.2f})")
    print(f"     {'VIABLE' if best_net > 0 else 'NOT VIABLE'}: MFE {'>' if best_net > 0 else '<='} fee")

    # ── Section D: Conditional Non-Breakout MFE ──
    # Check if certain conditions improve MFE for non-breakout bars
    print(f"\n  D. Conditional Non-Breakout MFE (best hold={best_hold}b)")
    print(f"     Testing which conditions boost MFE on non-breakout bars")
    print(f"     {'Condition':<35} | {'N':>5} | {'L MFE%':>8} {'S MFE%':>8} | {'L net$':>8} {'S net$':>8}")
    print(f"     {'-'*35}-+-{'-'*5}-+-{'-'*8}-{'-'*8}-+-{'-'*8}-{'-'*8}")

    hold = best_hold if best_hold else 3

    conditions = [
        ("All non-brk", nbrk.index),
        ("EMA trend up (L)", nbrk[nbrk['ema_trend'] == 1].index),
        ("EMA trend down (S)", nbrk[nbrk['ema_trend'] == -1].index),
        ("Body > 0 (bullish bar)", nbrk[nbrk['body'] > 0].index),
        ("Body < 0 (bearish bar)", nbrk[nbrk['body'] < 0].index),
        ("vol_ratio > 1.5", nbrk[nbrk['vol_ratio'] > 1.5].index if 'vol_ratio' in nbrk else []),
        ("vol_ratio > 2.0", nbrk[nbrk['vol_ratio'] > 2.0].index if 'vol_ratio' in nbrk else []),
        ("|return| > 0.3%", nbrk[nbrk['abs_return'] > 0.3].index),
        ("|return| > 0.5%", nbrk[nbrk['abs_return'] > 0.5].index),
        ("RSI < 30", nbrk[nbrk['rsi14'] < 30].index),
        ("RSI > 70", nbrk[nbrk['rsi14'] > 70].index),
        ("RSI 40-60 (neutral)", nbrk[(nbrk['rsi14'] >= 40) & (nbrk['rsi14'] <= 60)].index),
        ("TBR > 0.55 (buyer)", nbrk[nbrk['tbr'] > 0.55].index),
        ("TBR < 0.45 (seller)", nbrk[nbrk['tbr'] < 0.45].index),
        ("TBR delta > +0.05", nbrk[nbrk['tbr_delta'] > 0.05].index),
        ("TBR delta < -0.05", nbrk[nbrk['tbr_delta'] < -0.05].index),
        ("consec_up >= 3", nbrk[nbrk['consec_up'] >= 3].index),
        ("consec_dn >= 3", nbrk[nbrk['consec_dn'] >= 3].index),
        ("ema20_dev > +1%", nbrk[nbrk['ema20_dev'] > 1.0].index),
        ("ema20_dev < -1%", nbrk[nbrk['ema20_dev'] < -1.0].index),
        ("GK ratio < 0.6 (compress)", nbrk[nbrk['gk_ratio'] < 0.6].index),
        ("GK ratio > 1.5 (expand)", nbrk[nbrk['gk_ratio'] > 1.5].index),
        ("ret_1 > +0.3% (up bar)", nbrk[nbrk['ret_1'] > 0.3].index),
        ("ret_1 < -0.3% (down bar)", nbrk[nbrk['ret_1'] < -0.3].index),
    ]

    for cond_name, cond_idx in conditions:
        if len(cond_idx) < 20:
            print(f"     {cond_name:<35} | {len(cond_idx):>5} | {'(too few)':>17} | {'':>17}")
            continue

        l_mfes, s_mfes = [], []
        for idx in cond_idx:
            pos = d.index.get_loc(idx)
            if pos + hold >= n:
                continue
            entry = d.iloc[pos].close
            fwd = d.iloc[pos+1:pos+1+hold]
            if len(fwd) == 0:
                continue
            l_mfes.append((fwd['high'].max() - entry) / entry * 100)
            s_mfes.append((entry - fwd['low'].min()) / entry * 100)

        if l_mfes:
            l_mfe = np.mean(l_mfes)
            s_mfe = np.mean(s_mfes)
            l_net_d = (l_mfe - FEE_PCT) / 100 * NOTIONAL
            s_net_d = (s_mfe - FEE_PCT) / 100 * NOTIONAL
            print(f"     {cond_name:<35} | {len(cond_idx):>5} | {l_mfe:>7.4f}% {s_mfe:>7.4f}% | {l_net_d:>+7.2f} {s_net_d:>+7.2f}")

    # ── Section E: Session / Hour-of-Day Analysis ──
    print(f"\n  E. Hour-of-Day Analysis (non-breakout bars, {hold}b hold)")
    print(f"     {'Hour':>6} | {'N':>5} | {'Next bar WR%':>12} | {'L MFE%':>8} {'S MFE%':>8} | {'L net$':>8} {'S net$':>8}")
    print(f"     {'-'*6}-+-{'-'*5}-+-{'-'*12}-+-{'-'*8}-{'-'*8}-+-{'-'*8}-{'-'*8}")

    for hour in range(24):
        h_bars = nbrk[nbrk['hour'] == hour]
        if len(h_bars) < 20:
            continue

        # Next bar direction (WR of going long)
        next_returns = []
        l_mfes, s_mfes = [], []
        for idx in h_bars.index:
            pos = d.index.get_loc(idx)
            if pos + hold >= n:
                continue
            entry = d.iloc[pos].close
            next_close = d.iloc[pos+1].close
            next_returns.append(next_close > entry)

            fwd = d.iloc[pos+1:pos+1+hold]
            l_mfes.append((fwd['high'].max() - entry) / entry * 100)
            s_mfes.append((entry - fwd['low'].min()) / entry * 100)

        wr = np.mean(next_returns) * 100 if next_returns else 50
        l_mfe = np.mean(l_mfes) if l_mfes else 0
        s_mfe = np.mean(s_mfes) if s_mfes else 0
        l_net_d = (l_mfe - FEE_PCT) / 100 * NOTIONAL
        s_net_d = (s_mfe - FEE_PCT) / 100 * NOTIONAL

        marker = " *" if abs(wr - 50) > 3 else ""
        print(f"     {hour:>4}h  | {len(h_bars):>5} | {wr:>10.1f}%  | {l_mfe:>7.4f}% {s_mfe:>7.4f}% | {l_net_d:>+7.2f} {s_net_d:>+7.2f}{marker}")

    return best_net, best_hold


def main():
    print("=" * 70)
    print("V18 R0: Multi-Timeframe Feasibility Analysis")
    print("=" * 70)
    print(f"Account: $1,000 / $200 margin / 20x / $4,000 notional")
    print(f"Fee: $4/trade = {FEE_PCT:.4f}% of notional")
    print(f"Question: Does non-breakout MFE exceed $4 fee on any timeframe?")

    results = {}

    for tf, label in [("15m", "15m"), ("30m", "30m"), ("1h", "1h")]:
        path = DATA_DIR / f"ETHUSDT_{tf}_latest730d.csv"
        if not path.exists():
            print(f"\n  {label}: DATA NOT FOUND, skipping")
            continue
        df = load_tf(tf)
        best_net, best_hold = analyze_timeframe(df, label)
        results[label] = (best_net, best_hold)

    # ── Final Summary ──
    print("\n" + "=" * 70)
    print("  FINAL FEASIBILITY SUMMARY")
    print("=" * 70)

    print(f"\n  {'Timeframe':<10} | {'Best MFE-fee':>14} | {'Best hold':>10} | {'Verdict':<20}")
    print(f"  {'-'*10}-+-{'-'*14}-+-{'-'*10}-+-{'-'*20}")

    for tf in ["15m", "30m", "1h"]:
        if tf in results:
            net, hold = results[tf]
            net_d = net / 100 * NOTIONAL
            if net > 0.05:
                verdict = "WORTH EXPLORING"
            elif net > 0:
                verdict = "MARGINAL"
            else:
                verdict = "NOT VIABLE"
            print(f"  {tf:<10} | {net:>+12.4f}%  | {hold:>8}b  | {verdict}")

    print(f"""
  Interpretation:
  - 'WORTH EXPLORING': non-breakout bars have avg MFE > fee by meaningful margin
  - 'MARGINAL': MFE barely exceeds fee, need very high WR to be viable
  - 'NOT VIABLE': avg MFE <= fee, structurally impossible to profit

  Note: This is RANDOM entry MFE on non-breakout bars.
  A good signal should have BETTER MFE than random entry.
  So even 'MARGINAL' might work with the right signal.
  But 'NOT VIABLE' means no signal can overcome the fee.
""")


if __name__ == "__main__":
    main()
