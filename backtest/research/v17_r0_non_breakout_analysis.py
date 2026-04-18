"""
V17 R0: Non-Breakout Bar Directional Analysis

Core question: Do non-breakout bars have directional bias?
If yes, which sub-groups have WR > 55%?

Available columns: open, high, low, close, volume, taker_buy_volume, datetime
NO trades/qv/tbqv columns.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def main():
    print("=" * 70)
    print("V17 R0: Non-Breakout Bar Directional Analysis")
    print("=" * 70)

    df = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    n = len(df)
    sp = n // 2  # IS/OOS split
    WARMUP = 150

    print(f"Data: {n} bars")
    print(f"IS:  {df.iloc[WARMUP].datetime} ~ {df.iloc[sp-1].datetime}")
    print(f"OOS: {df.iloc[sp].datetime} ~ {df.iloc[-1].datetime}")

    # ══════════════════════════════════════════
    #  1. Define breakout vs non-breakout
    # ══════════════════════════════════════════
    df['high_15'] = df['close'].shift(1).rolling(15).max()
    df['low_15'] = df['close'].shift(1).rolling(15).min()
    df['is_brk_up'] = df['close'] > df['high_15']
    df['is_brk_dn'] = df['close'] < df['low_15']
    df['is_brk'] = df['is_brk_up'] | df['is_brk_dn']

    # Next bar return (forward looking, for analysis only)
    df['next_ret'] = df['close'].shift(-1) / df['open'].shift(-1) - 1  # next bar's O->C
    df['next_ret_oc'] = df['open'].shift(-1).pct_change(-1)  # this close to next close
    # More useful: entry at next bar open, exit at next bar close
    df['fwd_ret'] = (df['close'].shift(-1) - df['open'].shift(-1)) / df['open'].shift(-1)

    # ══════════════════════════════════════════
    #  2. Compute features (all shifted to avoid lookahead)
    # ══════════════════════════════════════════

    # OHLC bar structure (current bar, known at bar close)
    rng = (df['high'] - df['low']).clip(lower=1e-8)
    df['body_ratio'] = abs(df['close'] - df['open']) / rng
    df['upper_wick'] = (df['high'] - df[['open', 'close']].max(axis=1)) / rng
    df['lower_wick'] = (df[['open', 'close']].min(axis=1) - df['low']) / rng
    df['ctr'] = (df['close'] - df['low']) / rng  # close-to-range: 1=closed at high, 0=at low
    df['bar_dir'] = np.sign(df['close'] - df['open'])  # +1 bull, -1 bear, 0 doji

    # Returns
    df['ret_1'] = df['close'].pct_change(1)
    df['ret_3'] = df['close'].pct_change(3)
    df['ret_5'] = df['close'].pct_change(5)

    # EMA
    df['ema20'] = df['close'].ewm(span=20).mean()
    df['ema50'] = df['close'].ewm(span=50).mean()
    df['ema_dev20'] = (df['close'] - df['ema20']) / df['ema20'] * 100  # % deviation
    df['ema_dev50'] = (df['close'] - df['ema50']) / df['ema50'] * 100
    df['ema_trend'] = np.where(df['ema20'] > df['ema50'], 1, -1)  # trend direction

    # Volatility
    ln_hl = np.log(df['high'] / df['low'])
    ln_co = np.log(df['close'] / df['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    df['gk_ratio'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    df['atr_pct'] = (df['high'] - df['low']) / df['close'] * 100  # current bar ATR%
    df['atr5'] = df['atr_pct'].rolling(5).mean()

    # Volume / TBR
    df['tbr'] = df['taker_buy_volume'] / df['volume'].clip(lower=1)
    df['tbr_ma5'] = df['tbr'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()

    # Time
    df['hour'] = df['datetime'].dt.hour
    df['dow'] = df['datetime'].dt.weekday

    # Consecutive direction
    df['consec_up'] = 0
    df['consec_dn'] = 0
    for i in range(1, len(df)):
        if df.iloc[i]['close'] > df.iloc[i]['open']:
            df.iloc[i, df.columns.get_loc('consec_up')] = df.iloc[i-1]['consec_up'] + 1
        if df.iloc[i]['close'] < df.iloc[i]['open']:
            df.iloc[i, df.columns.get_loc('consec_dn')] = df.iloc[i-1]['consec_dn'] + 1

    # RSI (14)
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # Shift features that use current bar (to make them "known at signal time")
    # At decision time (bar close), we know: current bar's OHLC, volume, TBR
    # We trade at NEXT bar's open
    # So features computed from current bar are fine (no shift needed for these)
    # But we need shift(1) for rolling percentiles to avoid using current bar in rank

    # ══════════════════════════════════════════
    #  3. Overall breakout vs non-breakout comparison
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("1. Breakout vs Non-Breakout Overview")
    print("=" * 70)

    is_df = df.iloc[WARMUP:sp].copy()
    is_brk = is_df[is_df['is_brk'] == True]
    is_nobrk = is_df[is_df['is_brk'] == False]

    print(f"\n  IS period: {len(is_df)} bars")
    print(f"  Breakout bars: {len(is_brk)} ({len(is_brk)/len(is_df)*100:.1f}%)")
    print(f"  Non-breakout bars: {len(is_nobrk)} ({len(is_nobrk)/len(is_df)*100:.1f}%)")

    for label, subset in [("Breakout UP", is_df[is_df['is_brk_up']]),
                           ("Breakout DN", is_df[is_df['is_brk_dn']]),
                           ("Non-breakout", is_nobrk)]:
        fwd = subset['fwd_ret'].dropna()
        if len(fwd) == 0:
            continue
        wr_up = (fwd > 0).mean() * 100
        avg = fwd.mean() * 100
        print(f"\n  {label} ({len(fwd)} bars):")
        print(f"    Next-bar WR up: {wr_up:.1f}%")
        print(f"    Next-bar avg return: {avg:+.3f}%")
        print(f"    Next-bar return std: {fwd.std()*100:.3f}%")

    # ══════════════════════════════════════════
    #  4. Non-breakout sub-group analysis (IS only)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("2. Non-Breakout Sub-Group Analysis (IS only)")
    print("=" * 70)

    nb = is_nobrk.dropna(subset=['fwd_ret']).copy()

    def quintile_analysis(name, col, ascending=True):
        """Quintile analysis: split col into 5 groups, show next-bar stats."""
        valid = nb.dropna(subset=[col])
        if len(valid) < 100:
            return
        valid = valid.copy()
        try:
            valid['q'] = pd.qcut(valid[col], 5, labels=False, duplicates='drop')
        except ValueError:
            return
        print(f"\n  --- {name} quintile analysis ---")
        print(f"  {'Q':>3s} {'Range':>20s} {'N':>5s} {'WR_up':>6s} {'Avg%':>7s} {'L_edge':>7s} {'S_edge':>7s}")
        q_results = []
        for q in sorted(valid['q'].unique()):
            qdata = valid[valid['q'] == q]
            fwd = qdata['fwd_ret']
            wr_up = (fwd > 0).mean() * 100
            avg = fwd.mean() * 100
            vmin = qdata[col].min()
            vmax = qdata[col].max()
            l_edge = wr_up - 50
            s_edge = 50 - wr_up
            q_results.append((q, vmin, vmax, len(qdata), wr_up, avg, l_edge, s_edge))
            rng_str = f"{vmin:.3f}~{vmax:.3f}"
            print(f"  {q:3.0f} {rng_str:>20s} {len(qdata):5d} {wr_up:5.1f}% {avg:+6.3f}% {l_edge:+6.1f}% {s_edge:+6.1f}%")

        # Spread
        if len(q_results) >= 2:
            spread = q_results[-1][4] - q_results[0][4]
            print(f"  Spread (Q4-Q0 WR): {spread:+.1f}pp")
            # Is any quintile > 55% or < 45%?
            best_l = max(q_results, key=lambda x: x[4])
            best_s = min(q_results, key=lambda x: x[4])
            if best_l[4] > 55:
                print(f"  >>> L candidate: Q{best_l[0]:.0f} WR {best_l[4]:.1f}% ({best_l[1]:.3f}~{best_l[2]:.3f})")
            if best_s[4] < 45:
                print(f"  >>> S candidate: Q{best_s[0]:.0f} WR {best_s[4]:.1f}% ({best_s[1]:.3f}~{best_s[2]:.3f})")

    # Run quintile analysis on all features
    features = [
        ("Bar direction (bull/bear)", "bar_dir"),
        ("Close-to-Range (CTR)", "ctr"),
        ("Body Ratio", "body_ratio"),
        ("Upper Wick", "upper_wick"),
        ("Lower Wick", "lower_wick"),
        ("1-bar Return", "ret_1"),
        ("3-bar Return", "ret_3"),
        ("5-bar Return", "ret_5"),
        ("EMA20 deviation %", "ema_dev20"),
        ("EMA50 deviation %", "ema_dev50"),
        ("GK Ratio (5/20)", "gk_ratio"),
        ("ATR % (current bar)", "atr_pct"),
        ("ATR 5-bar avg", "atr5"),
        ("TBR (taker buy ratio)", "tbr"),
        ("TBR MA5", "tbr_ma5"),
        ("Volume Ratio (vs 20-bar)", "vol_ratio"),
        ("RSI 14", "rsi14"),
        ("Consecutive Up Bars", "consec_up"),
        ("Consecutive Down Bars", "consec_dn"),
    ]

    for name, col in features:
        quintile_analysis(name, col)

    # ══════════════════════════════════════════
    #  5. Time-based analysis (non-breakout)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("3. Time-Based Analysis (Non-Breakout, IS)")
    print("=" * 70)

    # By hour
    print("\n  --- By Hour (UTC+8) ---")
    print(f"  {'Hour':>4s} {'N':>5s} {'WR_up':>6s} {'Avg%':>7s}")
    for h in range(24):
        hdata = nb[nb['hour'] == h]
        if len(hdata) < 20:
            continue
        fwd = hdata['fwd_ret'].dropna()
        wr = (fwd > 0).mean() * 100
        avg = fwd.mean() * 100
        marker = " ***" if wr > 57 or wr < 43 else ""
        print(f"  {h:4d} {len(fwd):5d} {wr:5.1f}% {avg:+6.3f}%{marker}")

    # By weekday
    print("\n  --- By Weekday ---")
    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    for d in range(7):
        ddata = nb[nb['dow'] == d]
        if len(ddata) < 20:
            continue
        fwd = ddata['fwd_ret'].dropna()
        wr = (fwd > 0).mean() * 100
        avg = fwd.mean() * 100
        marker = " ***" if wr > 55 or wr < 45 else ""
        print(f"  {dow_names[d]:>4s} {len(fwd):5d} {wr:5.1f}% {avg:+6.3f}%{marker}")

    # ══════════════════════════════════════════
    #  6. Combination filters (non-breakout, IS)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("4. Combination Filters (Non-Breakout, IS)")
    print("=" * 70)

    # EMA trend + various filters
    print("\n  --- EMA20 > EMA50 (uptrend) + feature filter ---")
    nb_up = nb[nb['ema_trend'] == 1].copy()
    nb_dn = nb[nb['ema_trend'] == -1].copy()

    fwd_up = nb_up['fwd_ret'].dropna()
    fwd_dn = nb_dn['fwd_ret'].dropna()
    print(f"  Uptrend bars: {len(fwd_up)}, WR up {(fwd_up > 0).mean()*100:.1f}%")
    print(f"  Downtrend bars: {len(fwd_dn)}, WR up {(fwd_dn > 0).mean()*100:.1f}%")

    # L candidates: uptrend + pullback (negative ret) + reversal signal
    combos = [
        # (name, filter_fn, side)
        ("Uptrend + ret_1 < 0 (pullback)", lambda d: d[(d['ema_trend']==1) & (d['ret_1']<0)], 'L'),
        ("Uptrend + ret_3 < 0 (pullback)", lambda d: d[(d['ema_trend']==1) & (d['ret_3']<0)], 'L'),
        ("Uptrend + CTR < 0.3 (closed low)", lambda d: d[(d['ema_trend']==1) & (d['ctr']<0.3)], 'L'),
        ("Uptrend + RSI < 40 (oversold in uptrend)", lambda d: d[(d['ema_trend']==1) & (d['rsi14']<40)], 'L'),
        ("Uptrend + lower_wick > 0.5 (hammer)", lambda d: d[(d['ema_trend']==1) & (d['lower_wick']>0.5)], 'L'),
        ("Uptrend + TBR < 0.45 (selling pressure)", lambda d: d[(d['ema_trend']==1) & (d['tbr']<0.45)], 'L'),
        ("Downtrend + ret_1 > 0 (bounce)", lambda d: d[(d['ema_trend']==-1) & (d['ret_1']>0)], 'S'),
        ("Downtrend + ret_3 > 0 (bounce)", lambda d: d[(d['ema_trend']==-1) & (d['ret_3']>0)], 'S'),
        ("Downtrend + CTR > 0.7 (closed high)", lambda d: d[(d['ema_trend']==-1) & (d['ctr']>0.7)], 'S'),
        ("Downtrend + RSI > 60 (overbought in downtrend)", lambda d: d[(d['ema_trend']==-1) & (d['rsi14']>60)], 'S'),
        ("Downtrend + upper_wick > 0.5 (shooting star)", lambda d: d[(d['ema_trend']==-1) & (d['upper_wick']>0.5)], 'S'),
        ("Downtrend + TBR > 0.55 (buying pressure)", lambda d: d[(d['ema_trend']==-1) & (d['tbr']>0.55)], 'S'),
        # Cross-based entries
        ("EMA20 cross above EMA50 (recent)", lambda d: d[(d['ema_trend']==1) & (d['ema_dev20']<0.5)], 'L'),
        ("EMA20 cross below EMA50 (recent)", lambda d: d[(d['ema_trend']==-1) & (d['ema_dev20']>-0.5)], 'S'),
        # Volume spikes
        ("Vol ratio > 2 + bull bar", lambda d: d[(d['vol_ratio']>2) & (d['bar_dir']==1)], 'L'),
        ("Vol ratio > 2 + bear bar", lambda d: d[(d['vol_ratio']>2) & (d['bar_dir']==-1)], 'S'),
        # Compression states
        ("GK ratio < 0.5 (compressed) + bull", lambda d: d[(d['gk_ratio']<0.5) & (d['bar_dir']==1)], 'L'),
        ("GK ratio < 0.5 (compressed) + bear", lambda d: d[(d['gk_ratio']<0.5) & (d['bar_dir']==-1)], 'S'),
        # Mean reversion candidates
        ("EMA20 dev < -2% (oversold)", lambda d: d[d['ema_dev20'] < -2], 'L'),
        ("EMA20 dev > +2% (overbought)", lambda d: d[d['ema_dev20'] > 2], 'S'),
        ("RSI < 30 (extreme oversold)", lambda d: d[d['rsi14'] < 30], 'L'),
        ("RSI > 70 (extreme overbought)", lambda d: d[d['rsi14'] > 70], 'S'),
        # Consecutive bars
        ("3+ consec down bars", lambda d: d[d['consec_dn'] >= 3], 'L'),
        ("3+ consec up bars", lambda d: d[d['consec_up'] >= 3], 'S'),
        # Wick patterns
        ("Big lower wick > 0.6 (buying pressure)", lambda d: d[d['lower_wick'] > 0.6], 'L'),
        ("Big upper wick > 0.6 (selling pressure)", lambda d: d[d['upper_wick'] > 0.6], 'S'),
        # Price relative to EMA
        ("Close < EMA20 < EMA50 (deep downtrend pullback L)", lambda d: d[(d['close']<d['ema20']) & (d['ema20']<d['ema50']) & (d['ret_1']>0)], 'L'),
        ("Close > EMA20 > EMA50 (uptrend continuation S fail)", lambda d: d[(d['close']>d['ema20']) & (d['ema20']>d['ema50']) & (d['ret_1']<0)], 'S'),
    ]

    print(f"\n  {'Combo':55s} {'Side':>4s} {'N':>5s} {'WR%':>5s} {'Avg%':>7s}")
    print(f"  {'-'*55} {'-'*4} {'-'*5} {'-'*5} {'-'*7}")

    promising = []
    for name, fn, side in combos:
        try:
            subset = fn(nb)
            fwd = subset['fwd_ret'].dropna()
            if len(fwd) < 30:
                continue
            if side == 'L':
                wr = (fwd > 0).mean() * 100
                edge = wr - 50
            else:  # S
                wr = (fwd < 0).mean() * 100
                edge = wr - 50
            avg = fwd.mean() * 100
            marker = " ***" if edge > 5 else ""
            print(f"  {name:55s} {side:>4s} {len(fwd):5d} {wr:4.1f}% {avg:+6.3f}%{marker}")
            if edge > 3:
                promising.append((name, side, len(fwd), wr, avg, edge))
        except Exception:
            pass

    # ══════════════════════════════════════════
    #  7. Multi-bar pattern analysis
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("5. Multi-Bar Pattern Analysis (Non-Breakout, IS)")
    print("=" * 70)

    # 2-bar and 3-bar patterns
    nb2 = nb.copy()
    nb2['prev_dir'] = nb2['bar_dir'].shift(1)
    nb2['prev2_dir'] = nb2['bar_dir'].shift(2)
    nb2['prev_ctr'] = nb2['ctr'].shift(1)
    nb2['prev_body'] = nb2['body_ratio'].shift(1)
    nb2['prev_atr'] = nb2['atr_pct'].shift(1)

    patterns = [
        # Reversal patterns (no breakout)
        ("Bear->Bull (2-bar reversal L)", lambda d: d[(d['prev_dir']==-1)&(d['bar_dir']==1)], 'L'),
        ("Bull->Bear (2-bar reversal S)", lambda d: d[(d['prev_dir']==1)&(d['bar_dir']==-1)], 'S'),
        ("Bear->Bear->Bull (3-bar reversal L)", lambda d: d[(d['prev2_dir']==-1)&(d['prev_dir']==-1)&(d['bar_dir']==1)], 'L'),
        ("Bull->Bull->Bear (3-bar reversal S)", lambda d: d[(d['prev2_dir']==1)&(d['prev_dir']==1)&(d['bar_dir']==-1)], 'S'),
        # Continuation
        ("Bull->Bull (continuation L)", lambda d: d[(d['prev_dir']==1)&(d['bar_dir']==1)], 'L'),
        ("Bear->Bear (continuation S)", lambda d: d[(d['prev_dir']==-1)&(d['bar_dir']==-1)], 'S'),
        # Engulfing-like (big body after small body)
        ("Small->Big bull (engulf L)", lambda d: d[(d['prev_body']<0.3)&(d['body_ratio']>0.7)&(d['bar_dir']==1)], 'L'),
        ("Small->Big bear (engulf S)", lambda d: d[(d['prev_body']<0.3)&(d['body_ratio']>0.7)&(d['bar_dir']==-1)], 'S'),
        # High vol after low vol
        ("Low ATR->High ATR bull", lambda d: d[(d['prev_atr']<d['atr_pct'].quantile(0.25))&(d['atr_pct']>d['atr_pct'].quantile(0.75))&(d['bar_dir']==1)], 'L'),
        ("Low ATR->High ATR bear", lambda d: d[(d['prev_atr']<d['atr_pct'].quantile(0.25))&(d['atr_pct']>d['atr_pct'].quantile(0.75))&(d['bar_dir']==-1)], 'S'),
    ]

    print(f"\n  {'Pattern':45s} {'Side':>4s} {'N':>5s} {'WR%':>5s} {'Avg%':>7s}")
    print(f"  {'-'*45} {'-'*4} {'-'*5} {'-'*5} {'-'*7}")

    for name, fn, side in patterns:
        try:
            subset = fn(nb2)
            fwd = subset['fwd_ret'].dropna()
            if len(fwd) < 30:
                continue
            if side == 'L':
                wr = (fwd > 0).mean() * 100
            else:
                wr = (fwd < 0).mean() * 100
            avg = fwd.mean() * 100
            edge = wr - 50
            marker = " ***" if edge > 5 else ""
            print(f"  {name:45s} {side:>4s} {len(fwd):5d} {wr:4.1f}% {avg:+6.3f}%{marker}")
            if edge > 3:
                promising.append((name, side, len(fwd), wr, avg, edge))
        except Exception:
            pass

    # ══════════════════════════════════════════
    #  8. EMA crossover analysis (explicit non-breakout)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("6. EMA Crossover Analysis (Non-Breakout, IS)")
    print("=" * 70)

    for fast, slow in [(5, 20), (8, 21), (10, 30), (12, 26), (20, 50)]:
        ema_f = df['close'].ewm(span=fast).mean()
        ema_s = df['close'].ewm(span=slow).mean()
        cross_up = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
        cross_dn = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))

        is_cross_up = cross_up.iloc[WARMUP:sp]
        is_cross_dn = cross_dn.iloc[WARMUP:sp]

        # Only non-breakout crosses
        is_nobrk_mask = ~df['is_brk'].iloc[WARMUP:sp]

        cu_nb = is_cross_up & is_nobrk_mask
        cd_nb = is_cross_dn & is_nobrk_mask

        fwd_is = df['fwd_ret'].iloc[WARMUP:sp]

        cu_fwd = fwd_is[cu_nb].dropna()
        cd_fwd = fwd_is[cd_nb].dropna()

        if len(cu_fwd) >= 10:
            wr_l = (cu_fwd > 0).mean() * 100
            print(f"  EMA({fast}/{slow}) cross UP (non-brk): {len(cu_fwd):3d} bars, L WR {wr_l:.1f}%, avg {cu_fwd.mean()*100:+.3f}%")
        if len(cd_fwd) >= 10:
            wr_s = (cd_fwd < 0).mean() * 100
            print(f"  EMA({fast}/{slow}) cross DN (non-brk): {len(cd_fwd):3d} bars, S WR {wr_s:.1f}%, avg {cd_fwd.mean()*100:+.3f}%")

    # ══════════════════════════════════════════
    #  9. MFE/MAE analysis for non-breakout bars
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("7. MFE/MAE for Non-Breakout Bars (IS, 6-bar hold)")
    print("=" * 70)

    # For each non-breakout bar, calculate MFE and MAE over next 6 bars
    HOLD = 6
    mfe_l_list = []
    mae_l_list = []
    mfe_s_list = []
    mae_s_list = []

    for idx in nb.index:
        if idx + HOLD >= len(df):
            continue
        entry = df.iloc[idx + 1]['open']  # enter at next bar open
        if pd.isna(entry) or entry <= 0:
            continue

        future = df.iloc[idx+1:idx+1+HOLD]
        # L: MFE = max high - entry, MAE = entry - min low
        mfe_l = (future['high'].max() - entry) / entry * 100
        mae_l = (entry - future['low'].min()) / entry * 100
        mfe_l_list.append(mfe_l)
        mae_l_list.append(mae_l)
        # S: MFE = entry - min low, MAE = max high - entry
        mfe_s_list.append(mae_l)  # S MFE = L MAE
        mae_s_list.append(mfe_l)  # S MAE = L MFE

    mfe_l_arr = np.array(mfe_l_list)
    mae_l_arr = np.array(mae_l_list)
    mfe_s_arr = np.array(mfe_s_list)
    mae_s_arr = np.array(mae_s_list)

    print(f"\n  Non-breakout bars ({len(mfe_l_arr)} entries, {HOLD}-bar hold):")
    print(f"    L: avg MFE {mfe_l_arr.mean():.3f}%, avg MAE {mae_l_arr.mean():.3f}%, MFE-MAE {(mfe_l_arr-mae_l_arr).mean():+.3f}%")
    print(f"    S: avg MFE {mfe_s_arr.mean():.3f}%, avg MAE {mae_s_arr.mean():.3f}%, MFE-MAE {(mfe_s_arr-mae_s_arr).mean():+.3f}%")

    # Now compare with breakout bars
    brk_up_idx = is_df[is_df['is_brk_up']].index
    mfe_brk_l = []
    mae_brk_l = []
    for idx in brk_up_idx:
        if idx + HOLD >= len(df):
            continue
        entry = df.iloc[idx + 1]['open']
        if pd.isna(entry) or entry <= 0:
            continue
        future = df.iloc[idx+1:idx+1+HOLD]
        mfe_brk_l.append((future['high'].max() - entry) / entry * 100)
        mae_brk_l.append((entry - future['low'].min()) / entry * 100)

    if mfe_brk_l:
        mfe_bl = np.array(mfe_brk_l)
        mae_bl = np.array(mae_brk_l)
        print(f"\n  Breakout UP bars ({len(mfe_bl)} entries, {HOLD}-bar hold):")
        print(f"    L: avg MFE {mfe_bl.mean():.3f}%, avg MAE {mae_bl.mean():.3f}%, MFE-MAE {(mfe_bl-mae_bl).mean():+.3f}%")

    # ══════════════════════════════════════════
    #  10. Summary of promising directions
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("8. Summary of Promising Directions")
    print("=" * 70)

    if promising:
        promising.sort(key=lambda x: -x[5])  # sort by edge
        print(f"\n  {'Direction':55s} {'Side':>4s} {'N':>5s} {'WR%':>5s} {'Edge':>5s}")
        print(f"  {'-'*55} {'-'*4} {'-'*5} {'-'*5} {'-'*5}")
        for name, side, n, wr, avg, edge in promising[:20]:
            print(f"  {name:55s} {side:>4s} {n:5d} {wr:4.1f}% {edge:+4.1f}")
    else:
        print("\n  No combinations found with edge > 3pp")
        print("  Non-breakout bars on ETH 1h may lack directional bias")

    # ══════════════════════════════════════════
    #  11. Deeper look: conditional MFE/MAE by feature
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("9. Conditional MFE Analysis (Non-Breakout, IS)")
    print("=" * 70)
    print("  Which non-breakout bar subsets have MFE >> MAE?")

    # Re-do MFE/MAE with feature values
    mfe_mae_data = []
    for idx in nb.index:
        if idx + HOLD >= len(df):
            continue
        entry = df.iloc[idx + 1]['open']
        if pd.isna(entry) or entry <= 0:
            continue
        future = df.iloc[idx+1:idx+1+HOLD]
        mfe_l = (future['high'].max() - entry) / entry * 100
        mae_l = (entry - future['low'].min()) / entry * 100
        row = df.iloc[idx]
        mfe_mae_data.append({
            'mfe_l': mfe_l, 'mae_l': mae_l,
            'mfe_advantage_l': mfe_l - mae_l,
            'ema_trend': row.get('ema_trend', 0),
            'ret_1': row.get('ret_1', 0),
            'ret_3': row.get('ret_3', 0),
            'ctr': row.get('ctr', 0.5),
            'tbr': row.get('tbr', 0.5),
            'vol_ratio': row.get('vol_ratio', 1),
            'rsi14': row.get('rsi14', 50),
            'bar_dir': row.get('bar_dir', 0),
            'gk_ratio': row.get('gk_ratio', 1),
            'atr_pct': row.get('atr_pct', 1),
            'lower_wick': row.get('lower_wick', 0),
            'upper_wick': row.get('upper_wick', 0),
            'ema_dev20': row.get('ema_dev20', 0),
        })

    mdf = pd.DataFrame(mfe_mae_data)

    # Find subsets where MFE >> MAE for L (or S)
    cond_checks = [
        ("Uptrend (EMA20>50)", mdf[mdf['ema_trend']==1], 'L'),
        ("Downtrend (EMA20<50)", mdf[mdf['ema_trend']==-1], 'S'),
        ("Bull bar in uptrend", mdf[(mdf['ema_trend']==1)&(mdf['bar_dir']==1)], 'L'),
        ("Bear bar in uptrend (pullback)", mdf[(mdf['ema_trend']==1)&(mdf['bar_dir']==-1)], 'L'),
        ("Bull bar in downtrend (bounce)", mdf[(mdf['ema_trend']==-1)&(mdf['bar_dir']==1)], 'S'),
        ("Bear bar in downtrend", mdf[(mdf['ema_trend']==-1)&(mdf['bar_dir']==-1)], 'S'),
        ("RSI < 35", mdf[mdf['rsi14']<35], 'L'),
        ("RSI > 65", mdf[mdf['rsi14']>65], 'S'),
        ("TBR < 0.47 (selling flow)", mdf[mdf['tbr']<0.47], 'L'),
        ("TBR > 0.53 (buying flow)", mdf[mdf['tbr']>0.53], 'S'),
        ("Big lower wick > 0.5", mdf[mdf['lower_wick']>0.5], 'L'),
        ("Big upper wick > 0.5", mdf[mdf['upper_wick']>0.5], 'S'),
        ("Vol ratio > 1.5 + bull", mdf[(mdf['vol_ratio']>1.5)&(mdf['bar_dir']==1)], 'L'),
        ("Vol ratio > 1.5 + bear", mdf[(mdf['vol_ratio']>1.5)&(mdf['bar_dir']==-1)], 'S'),
        ("EMA dev < -1.5%", mdf[mdf['ema_dev20']<-1.5], 'L'),
        ("EMA dev > +1.5%", mdf[mdf['ema_dev20']>1.5], 'S'),
        ("GK compressed < 0.6 + bull", mdf[(mdf['gk_ratio']<0.6)&(mdf['bar_dir']==1)], 'L'),
        ("GK compressed < 0.6 + bear", mdf[(mdf['gk_ratio']<0.6)&(mdf['bar_dir']==-1)], 'S'),
        ("3-bar ret < -2% (dip)", mdf[mdf['ret_3']<-0.02], 'L'),
        ("3-bar ret > +2% (rally)", mdf[mdf['ret_3']>0.02], 'S'),
    ]

    print(f"\n  {'Condition':40s} {'Side':>4s} {'N':>5s} {'MFE':>6s} {'MAE':>6s} {'Adv':>7s}")
    print(f"  {'-'*40} {'-'*4} {'-'*5} {'-'*6} {'-'*6} {'-'*7}")

    mfe_promising = []
    for name, subset, side in cond_checks:
        if len(subset) < 30:
            continue
        if side == 'L':
            mfe = subset['mfe_l'].mean()
            mae = subset['mae_l'].mean()
        else:
            mfe = subset['mae_l'].mean()  # S MFE = price drop
            mae = subset['mfe_l'].mean()  # S MAE = price rise
        adv = mfe - mae
        marker = " ***" if adv > 0.1 else ""
        print(f"  {name:40s} {side:>4s} {len(subset):5d} {mfe:5.3f}% {mae:5.3f}% {adv:+6.3f}%{marker}")
        if adv > 0.05:
            mfe_promising.append((name, side, len(subset), mfe, mae, adv))

    if mfe_promising:
        print(f"\n  MFE > MAE subsets:")
        for name, side, n, mfe, mae, adv in sorted(mfe_promising, key=lambda x: -x[5]):
            print(f"    {name} ({side}): MFE-MAE = {adv:+.3f}%, N={n}")

    print("\n" + "=" * 70)
    print("END OF R0 ANALYSIS")
    print("=" * 70)


if __name__ == '__main__':
    main()
