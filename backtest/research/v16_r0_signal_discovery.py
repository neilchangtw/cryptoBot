"""
V16 R0: Non-GK Signal Discovery (IS-only)
Explores: TBR (taker buy ratio), Return Consistency, CTR (close-to-range), OBV
Goal: Find a completely different edge source from V14's GK compression breakout.

Hypothesis: Order flow (TBR) and bar microstructure contain directional information
independent of volatility regime (GK). These could serve as V14 backup signals.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Account
MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE  # $4,000
FEE = 4.0
WARMUP = 150
SN_SLIP = 0.25

# Exit params (V14 baseline — held constant, only entry varies)
L_SN, S_SN = 0.035, 0.04
L_TP, S_TP = 0.035, 0.02
L_MH, S_MH = 6, 10
L_EXT, S_EXT = 2, 2
L_CD, S_CD = 6, 8
L_CAP, S_CAP = 20, 20
L_ML, S_ML = -75, -150
DAILY_LOSS = -200
CONSEC_PAUSE, CONSEC_CD = 4, 24
BLOCK_H = {0, 1, 2, 12}
L_BLOCK_D, S_BLOCK_D = {5, 6}, {0, 5, 6}


# ══════════════════════════════════════════
#  Data Loading & Feature Engineering
# ══════════════════════════════════════════

def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    d = eth.copy()
    rng = (d['high'] - d['low']).clip(lower=1e-8)
    d['ret'] = d['close'].pct_change()
    d['atr14'] = rng.rolling(14).mean()

    # === TBR (Taker Buy Ratio) — order flow ===
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    for w in [3, 5, 10, 20]:
        d[f'tbr_ma{w}'] = d['tbr'].rolling(w).mean()
    d['tbr_std20'] = d['tbr'].rolling(20).std()

    # Shifted for signals (value known at start of bar)
    d['tbr_prev'] = d['tbr'].shift(1)
    d['tbr_ma5_prev'] = d['tbr_ma5'].shift(1)
    d['tbr_ma10_prev'] = d['tbr_ma10'].shift(1)
    d['tbr_ma20_prev'] = d['tbr_ma20'].shift(1)

    # TBR percentiles (shift(1) built in)
    d['tbr_pctile'] = d['tbr'].shift(1).rolling(100).rank(pct=True) * 100
    d['tbr_ma5_pctile'] = d['tbr_ma5'].shift(1).rolling(100).rank(pct=True) * 100
    d['tbr_std_pctile'] = d['tbr_std20'].shift(1).rolling(100).rank(pct=True) * 100

    # === CTR (Close-to-Range) — microstructure ===
    d['ctr'] = (d['close'] - d['low']) / rng
    d['ctr_ma5'] = d['ctr'].rolling(5).mean()
    d['ctr_ma10'] = d['ctr'].rolling(10).mean()
    d['ctr_prev'] = d['ctr'].shift(1)
    d['ctr_ma5_prev'] = d['ctr_ma5'].shift(1)
    d['ctr_ma10_prev'] = d['ctr_ma10'].shift(1)

    # === Body Ratio ===
    d['body_ratio'] = abs(d['close'] - d['open']) / rng
    d['body_ratio_prev'] = d['body_ratio'].shift(1)

    # === Return Consistency (fraction of bullish bars) ===
    bull = (d['close'] > d['open']).astype(float)
    for N in [5, 8, 10, 15]:
        d[f'rc{N}'] = bull.rolling(N).mean()
        d[f'rc{N}_prev'] = d[f'rc{N}'].shift(1)

    # === Momentum (simple) ===
    for N in [5, 10, 15, 20]:
        d[f'mom{N}'] = d['close'] / d['close'].shift(N) - 1
        d[f'mom{N}_prev'] = d[f'mom{N}'].shift(1)

    # === OBV ===
    obv_sign = np.sign(d['ret'].fillna(0))
    d['obv'] = (obv_sign * d['volume']).cumsum()
    d['obv_ma20'] = d['obv'].rolling(20).mean()
    d['obv_dev'] = d['obv'] - d['obv_ma20']
    d['obv_dev_prev'] = d['obv_dev'].shift(1)
    d['obv_dev_pctile'] = d['obv_dev'].shift(1).rolling(100).rank(pct=True) * 100

    # === Volume Ratio (vol vs MA) — proxy for activity regime ===
    d['vol_ma20'] = d['volume'].rolling(20).mean()
    d['vol_ratio'] = d['volume'] / d['vol_ma20'].clip(lower=1)
    d['vol_ratio_prev'] = d['vol_ratio'].shift(1)

    # === Breakout (same as V14 framework) ===
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()

    # === GK (for V14 baseline) ===
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
    d['gk_ratio_L'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile_L'] = d['gk_ratio_L'].shift(1).rolling(100).rank(pct=True) * 100
    d['gk_ratio_S'] = gk.rolling(10).mean() / gk.rolling(30).mean()
    d['gk_pctile_S'] = d['gk_ratio_S'].shift(1).rolling(100).rank(pct=True) * 100

    # === Forward returns (analysis ONLY) ===
    d['fwd1'] = d['close'].shift(-1) / d['close'] - 1
    d['fwd3'] = d['close'].shift(-3) / d['close'] - 1
    d['fwd5'] = d['close'].shift(-5) / d['close'] - 1

    return d


# ══════════════════════════════════════════
#  Analysis
# ══════════════════════════════════════════

def quintile_fwd(df, feat, fwd='fwd1'):
    """Quintile forward return analysis. Returns Q5-Q1 spread."""
    v = df[[feat, fwd]].dropna()
    if len(v) < 200:
        return None
    try:
        v['q'] = pd.qcut(v[feat], 5, labels=['Q1','Q2','Q3','Q4','Q5'], duplicates='drop')
    except ValueError:
        return None
    g = v.groupby('q', observed=True)[fwd].agg(['mean','count'])
    qs = []
    for q in ['Q1','Q2','Q3','Q4','Q5']:
        if q in g.index:
            qs.append(g.loc[q, 'mean'])
    spread = (qs[-1] - qs[0]) * 100 if len(qs) >= 2 else 0
    mono = all(qs[i] <= qs[i+1] for i in range(len(qs)-1))
    mono_dn = all(qs[i] >= qs[i+1] for i in range(len(qs)-1))
    return {'spread': spread, 'mono': 'UP' if mono else ('DN' if mono_dn else 'NO'),
            'q1': qs[0]*100, 'q5': qs[-1]*100, 'data': g}


# ══════════════════════════════════════════
#  Backtest Engine
# ══════════════════════════════════════════

def run_bt(df, start, end, entry_fn):
    """
    Backtest with V14 exit framework.
    entry_fn(row, i) -> str containing 'L' and/or 'S', or None
    """
    trades = []
    pos_l = pos_s = None
    last_exit_l = last_exit_s = -999

    daily_pnl = 0.0
    monthly_pnl_l = monthly_pnl_s = 0.0
    consec_losses = 0
    consec_pause_until = -999
    month_entries_l = month_entries_s = 0
    cur_day = cur_month = None

    for i in range(start, end):
        row = df.iloc[i]
        dt = row.datetime
        h, dow = dt.hour, dt.weekday()
        day_key, month_key = dt.date(), (dt.year, dt.month)

        if cur_day != day_key:
            daily_pnl = 0.0
            cur_day = day_key
        if cur_month != month_key:
            monthly_pnl_l = monthly_pnl_s = 0.0
            month_entries_l = month_entries_s = 0
            cur_month = month_key

        # --- Exit L ---
        if pos_l is not None:
            bh = i - pos_l['bar']
            ep = pos_l['ep']
            sn_p = ep * (1 - L_SN)
            if row.low <= sn_p:
                xp = sn_p * (1 - L_SN * SN_SLIP)
                net = (xp - ep) * NOTIONAL / ep - FEE
                trades.append(_mktrade(pos_l, 'L', 'SN', xp, net, i, dt))
                pos_l = None
            elif row.high >= ep * (1 + L_TP):
                xp = ep * (1 + L_TP)
                net = (xp - ep) * NOTIONAL / ep - FEE
                trades.append(_mktrade(pos_l, 'L', 'TP', xp, net, i, dt))
                pos_l = None
            else:
                mh_eff = L_MH + (L_EXT if pos_l.get('ext') else 0)
                if bh >= mh_eff:
                    r = 'MH-ext' if pos_l.get('ext') else 'MH'
                    net = (row.close - ep) * NOTIONAL / ep - FEE
                    trades.append(_mktrade(pos_l, 'L', r, row.close, net, i, dt))
                    pos_l = None
                elif bh == L_MH and not pos_l.get('ext'):
                    cpnl = (row.close - ep) / ep
                    if cpnl > 0:
                        pos_l['ext'] = True
                    else:
                        net = (row.close - ep) * NOTIONAL / ep - FEE
                        trades.append(_mktrade(pos_l, 'L', 'MH', row.close, net, i, dt))
                        pos_l = None
                elif pos_l.get('ext') and row.low <= ep:
                    net = -FEE  # BE
                    trades.append(_mktrade(pos_l, 'L', 'BE', ep, net, i, dt))
                    pos_l = None

            if pos_l is not None and trades and trades[-1].get('_just_closed'):
                pass  # already closed above

        # --- Exit S ---
        if pos_s is not None:
            bh = i - pos_s['bar']
            ep = pos_s['ep']
            sn_p = ep * (1 + S_SN)
            if row.high >= sn_p:
                xp = sn_p * (1 + S_SN * SN_SLIP)
                net = (ep - xp) * NOTIONAL / ep - FEE
                trades.append(_mktrade(pos_s, 'S', 'SN', xp, net, i, dt))
                pos_s = None
            elif row.low <= ep * (1 - S_TP):
                xp = ep * (1 - S_TP)
                net = (ep - xp) * NOTIONAL / ep - FEE
                trades.append(_mktrade(pos_s, 'S', 'TP', xp, net, i, dt))
                pos_s = None
            else:
                mh_eff = S_MH + (S_EXT if pos_s.get('ext') else 0)
                if bh >= mh_eff:
                    r = 'MH-ext' if pos_s.get('ext') else 'MH'
                    net = (ep - row.close) * NOTIONAL / ep - FEE
                    trades.append(_mktrade(pos_s, 'S', r, row.close, net, i, dt))
                    pos_s = None
                elif bh == S_MH and not pos_s.get('ext'):
                    cpnl = (ep - row.close) / ep
                    if cpnl > 0:
                        pos_s['ext'] = True
                    else:
                        net = (ep - row.close) * NOTIONAL / ep - FEE
                        trades.append(_mktrade(pos_s, 'S', 'MH', row.close, net, i, dt))
                        pos_s = None
                elif pos_s.get('ext') and row.high >= ep:
                    net = -FEE
                    trades.append(_mktrade(pos_s, 'S', 'BE', ep, net, i, dt))
                    pos_s = None

        # Update circuit breakers from last closed trade
        if trades:
            last = trades[-1]
            if last['xb'] == i:
                daily_pnl += last['p']
                if last['side'] == 'L':
                    monthly_pnl_l += last['p']
                    last_exit_l = i
                else:
                    monthly_pnl_s += last['p']
                    last_exit_s = i
                if last['p'] < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE:
                        consec_pause_until = i + CONSEC_CD
                else:
                    consec_losses = 0

        # --- Entry ---
        if i < consec_pause_until or daily_pnl <= DAILY_LOSS:
            continue

        sig = entry_fn(row, i)
        if not sig:
            continue

        if 'L' in sig and pos_l is None:
            if (h not in BLOCK_H and dow not in L_BLOCK_D
                and (i - last_exit_l) >= L_CD
                and monthly_pnl_l > L_ML and month_entries_l < L_CAP):
                pos_l = {'ep': row.open, 'bar': i}
                month_entries_l += 1

        if 'S' in sig and pos_s is None:
            if (h not in BLOCK_H and dow not in S_BLOCK_D
                and (i - last_exit_s) >= S_CD
                and monthly_pnl_s > S_ML and month_entries_s < S_CAP):
                pos_s = {'ep': row.open, 'bar': i}
                month_entries_s += 1

    return trades


def _mktrade(pos, side, reason, xp, net, xb, dt):
    return {
        'eb': pos['bar'], 'xb': xb, 'side': side,
        'ep': pos['ep'], 'xp': xp, 'p': net,
        'reason': reason, 'month': f"{dt.year}-{dt.month:02d}"
    }


def calc_metrics(trades, sp):
    """IS/OOS split metrics."""
    is_t = [t for t in trades if t['eb'] < sp]
    oos_t = [t for t in trades if t['eb'] >= sp]
    def _m(tlist, label=''):
        if not tlist:
            return {'pnl': 0, 't': 0, 'wr': 0, 'pf': 0, 'mdd': 0,
                    'pm': 0, 'pm_n': 0, 'worst': 0}
        pnl = sum(t['p'] for t in tlist)
        wins = sum(1 for t in tlist if t['p'] > 0)
        wr = wins / len(tlist) * 100
        w_pnl = sum(t['p'] for t in tlist if t['p'] > 0)
        l_pnl = abs(sum(t['p'] for t in tlist if t['p'] <= 0))
        pf = w_pnl / l_pnl if l_pnl > 0 else 99
        eq = peak = mdd = 0
        for t in tlist:
            eq += t['p']
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
        months = defaultdict(float)
        for t in tlist:
            months[t['month']] += t['p']
        pm = sum(1 for v in months.values() if v > 0)
        worst = min(months.values()) if months else 0
        return {'pnl': pnl, 't': len(tlist), 'wr': wr, 'pf': pf, 'mdd': mdd,
                'pm': pm, 'pm_n': len(months), 'worst': worst}
    return {'is': _m(is_t), 'oos': _m(oos_t)}


def fmt(m, label=''):
    """Format metrics one-liner."""
    return (f"{m['t']:3d}t ${m['pnl']:+6.0f} WR {m['wr']:4.0f}% "
            f"PF {m['pf']:4.1f} MDD ${m['mdd']:3.0f} PM {m['pm']}/{m['pm_n']}")


# ══════════════════════════════════════════
#  Entry Signal Generators
# ══════════════════════════════════════════

def entry_v14(row, i):
    """V14 baseline: GK compression + breakout."""
    sig = ''
    if not pd.isna(row.gk_pctile_L) and row.gk_pctile_L < 25:
        if row.close > row.high_15:
            sig += 'L'
    if not pd.isna(row.gk_pctile_S) and row.gk_pctile_S < 35:
        if row.close < row.low_15:
            sig += 'S'
    return sig or None


def make_tbr_momentum(hi_pct, lo_pct):
    """S1: TBR Momentum + Breakout — follow flow direction."""
    def entry(row, i):
        sig = ''
        p = row.tbr_ma5_pctile
        if pd.isna(p): return None
        if p > hi_pct and row.close > row.high_15:
            sig += 'L'
        if p < lo_pct and row.close < row.low_15:
            sig += 'S'
        return sig or None
    return entry


def make_tbr_reversal(lo_pct, hi_pct):
    """S2: TBR Flow Reversal + Breakout — fade exhausted flow."""
    def entry(row, i):
        sig = ''
        p = row.tbr_ma5_pctile
        if pd.isna(p): return None
        # Selling was dominant (low TBR) + price breaks UP → reversal long
        if p < lo_pct and row.close > row.high_15:
            sig += 'L'
        # Buying was dominant (high TBR) + price breaks DOWN → reversal short
        if p > hi_pct and row.close < row.low_15:
            sig += 'S'
        return sig or None
    return entry


def make_tbr_compression(std_pct):
    """S3: TBR Compression + Breakout (analogous to GK but on flow data)."""
    def entry(row, i):
        sig = ''
        sp = row.tbr_std_pctile
        if pd.isna(sp): return None
        if sp < std_pct:
            if row.close > row.high_15:
                sig += 'L'
            if row.close < row.low_15:
                sig += 'S'
        return sig or None
    return entry


def make_retcon(N, thresh):
    """S4: Return Consistency — bar direction persistence."""
    col = f'rc{N}_prev'
    def entry(row, i):
        sig = ''
        rc = getattr(row, col, np.nan)
        if pd.isna(rc): return None
        mom = getattr(row, f'mom{N}_prev', np.nan)
        if pd.isna(mom): return None
        if rc >= thresh and mom > 0:
            sig += 'L'
        if rc <= (1 - thresh) and mom < 0:
            sig += 'S'
        return sig or None
    return entry


def make_ctr_trend(thresh):
    """S5: CTR Trend + Breakout — bullish/bearish bar character."""
    def entry(row, i):
        sig = ''
        c = row.ctr_ma5_prev
        if pd.isna(c): return None
        if c > thresh and row.close > row.high_15:
            sig += 'L'
        if c < (1 - thresh) and row.close < row.low_15:
            sig += 'S'
        return sig or None
    return entry


def make_obv_trend(pct_hi, pct_lo):
    """S6: OBV trend + Breakout — volume-weighted momentum."""
    def entry(row, i):
        sig = ''
        p = row.obv_dev_pctile
        if pd.isna(p): return None
        if p > pct_hi and row.close > row.high_15:
            sig += 'L'
        if p < pct_lo and row.close < row.low_15:
            sig += 'S'
        return sig or None
    return entry


def entry_brk_only(row, i):
    """S0: Pure breakout (no filter) — measures raw breakout edge."""
    sig = ''
    if row.close > row.high_15:
        sig += 'L'
    if row.close < row.low_15:
        sig += 'S'
    return sig or None


# ══════════════════════════════════════════
#  Main
# ══════════════════════════════════════════

def main():
    print("=" * 70)
    print("V16 R0: Non-GK Signal Discovery (IS-only)")
    print("=" * 70)

    df = load_and_prepare()
    sp = len(df) // 2
    is_df = df.iloc[WARMUP:sp].copy()

    print(f"\nData: {len(df)} bars, IS: {WARMUP}-{sp} ({sp-WARMUP} bars), OOS: {sp}-{len(df)}")
    print(f"IS:  {df.iloc[WARMUP].datetime} ~ {df.iloc[sp-1].datetime}")
    print(f"OOS: {df.iloc[sp].datetime} ~ {df.iloc[-1].datetime}")

    # ═══════════════════════════════════════
    #  PART 1: Feature Statistics (IS)
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 1: Feature Statistics (IS)")
    print("=" * 70)

    stats_feats = [
        ('tbr', 'TBR (raw)'),
        ('tbr_ma5_prev', 'TBR MA5'),
        ('ctr_prev', 'CTR (raw)'),
        ('ctr_ma5_prev', 'CTR MA5'),
        ('body_ratio_prev', 'Body Ratio'),
        ('rc5_prev', 'RetCon 5'),
        ('rc10_prev', 'RetCon 10'),
        ('vol_ratio_prev', 'Volume Ratio'),
    ]
    print(f"  {'Feature':25s} {'mean':>8s} {'std':>8s} {'min':>8s} {'max':>8s} {'AC(1)':>8s}")
    for col, name in stats_feats:
        s = is_df[col].dropna()
        ac = s.autocorr(1) if len(s) > 10 else 0
        print(f"  {name:25s} {s.mean():8.4f} {s.std():8.4f} {s.min():8.4f} {s.max():8.4f} {ac:8.3f}")

    # ═══════════════════════════════════════
    #  PART 2: Quintile Analysis (IS)
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 2: Univariate Predictive Power (IS)")
    print("Feature quintile -> forward returns")
    print("=" * 70)

    qfeats = [
        ('tbr_prev', 'TBR raw'),
        ('tbr_ma5_prev', 'TBR MA5'),
        ('tbr_ma10_prev', 'TBR MA10'),
        ('ctr_prev', 'CTR raw'),
        ('ctr_ma5_prev', 'CTR MA5'),
        ('rc5_prev', 'RetCon 5'),
        ('rc10_prev', 'RetCon 10'),
        ('body_ratio_prev', 'Body Ratio'),
        ('vol_ratio_prev', 'Vol Ratio'),
        ('mom5_prev', 'Momentum 5'),
        ('mom10_prev', 'Momentum 10'),
    ]

    print(f"\n  {'Feature':18s} | {'1-bar spread':>12s} {'mono':>5s} | {'5-bar spread':>12s} {'mono':>5s} |")
    print(f"  {'-'*18}-+-{'-'*12}-{'-'*5}-+-{'-'*12}-{'-'*5}-+")

    promising = []
    for col, name in qfeats:
        r1 = quintile_fwd(is_df, col, 'fwd1')
        r5 = quintile_fwd(is_df, col, 'fwd5')
        if r1 is None or r5 is None:
            continue
        s1 = f"{r1['spread']:+.4f}%"
        s5 = f"{r5['spread']:+.4f}%"
        print(f"  {name:18s} | {s1:>12s} {r1['mono']:>5s} | {s5:>12s} {r5['mono']:>5s} |")
        if abs(r1['spread']) > 0.02 or abs(r5['spread']) > 0.10:
            promising.append((col, name, r1, r5))

    # Detail for promising features
    if promising:
        print(f"\n  Promising features (|spread| > 0.02% 1-bar or > 0.10% 5-bar):")
        for col, name, r1, r5 in promising:
            print(f"\n  {name} -> 1-bar fwd return by quintile:")
            for q in ['Q1','Q2','Q3','Q4','Q5']:
                if q in r1['data'].index:
                    m, n = r1['data'].loc[q, 'mean'], r1['data'].loc[q, 'count']
                    print(f"    {q}: mean={m*100:+.4f}%  n={int(n)}")
    else:
        print("\n  No features show strong individual predictive power.")

    # TBR distribution detail
    print(f"\n  --- TBR Distribution Detail (IS) ---")
    tbr_is = is_df['tbr'].dropna()
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        print(f"    P{pct:02d}: {tbr_is.quantile(pct/100):.4f}")
    print(f"    % of bars with TBR > 0.55: {(tbr_is > 0.55).mean()*100:.1f}%")
    print(f"    % of bars with TBR < 0.45: {(tbr_is < 0.45).mean()*100:.1f}%")
    print(f"    % of bars with TBR in [0.48, 0.52]: {((tbr_is >= 0.48) & (tbr_is <= 0.52)).mean()*100:.1f}%")

    # ═══════════════════════════════════════
    #  PART 3: Strategy Scan (IS only)
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 3: Strategy Scan (IS only, V14 exit framework)")
    print("=" * 70)

    results = []

    # V14 baseline
    m = calc_metrics(run_bt(df, WARMUP, sp, entry_v14), sp)
    print(f"\n  V14 baseline (IS):     {fmt(m['is'])}")
    v14_is = m['is']['pnl']

    # S0: Pure breakout
    m = calc_metrics(run_bt(df, WARMUP, sp, entry_brk_only), sp)
    print(f"  S0 Breakout only (IS): {fmt(m['is'])}")
    results.append(('S0_BrkOnly', m['is']))

    # S1: TBR Momentum + Breakout
    print(f"\n  --- S1: TBR Momentum (high TBR=bullish flow → L) ---")
    for hi, lo in [(60, 40), (65, 35), (70, 30), (75, 25)]:
        m = calc_metrics(run_bt(df, WARMUP, sp, make_tbr_momentum(hi, lo)), sp)
        label = f"S1_TBR_Mom_{hi}/{lo}"
        print(f"    {label:30s}: {fmt(m['is'])}")
        results.append((label, m['is']))

    # S2: TBR Reversal + Breakout
    print(f"\n  --- S2: TBR Reversal (low TBR → selling exhausted → L) ---")
    for lo, hi in [(25, 75), (30, 70), (35, 65), (40, 60)]:
        m = calc_metrics(run_bt(df, WARMUP, sp, make_tbr_reversal(lo, hi)), sp)
        label = f"S2_TBR_Rev_{lo}/{hi}"
        print(f"    {label:30s}: {fmt(m['is'])}")
        results.append((label, m['is']))

    # S3: TBR Compression + Breakout
    print(f"\n  --- S3: TBR Compression (stable flow → breakout) ---")
    for sp_thresh in [15, 20, 25, 30, 35, 40, 50]:
        m = calc_metrics(run_bt(df, WARMUP, sp, make_tbr_compression(sp_thresh)), sp)
        label = f"S3_TBR_Comp_{sp_thresh}"
        print(f"    {label:30s}: {fmt(m['is'])}")
        results.append((label, m['is']))

    # S4: Return Consistency
    print(f"\n  --- S4: Return Consistency (bar direction persistence) ---")
    for N in [5, 8, 10]:
        for thresh in [0.6, 0.7, 0.8]:
            m = calc_metrics(run_bt(df, WARMUP, sp, make_retcon(N, thresh)), sp)
            label = f"S4_RC{N}_{thresh}"
            print(f"    {label:30s}: {fmt(m['is'])}")
            results.append((label, m['is']))

    # S5: CTR Trend + Breakout
    print(f"\n  --- S5: CTR Trend (bar character momentum) ---")
    for thresh in [0.55, 0.60, 0.65, 0.70]:
        m = calc_metrics(run_bt(df, WARMUP, sp, make_ctr_trend(thresh)), sp)
        label = f"S5_CTR_{thresh}"
        print(f"    {label:30s}: {fmt(m['is'])}")
        results.append((label, m['is']))

    # S6: OBV Trend + Breakout
    print(f"\n  --- S6: OBV Trend (volume-weighted momentum) ---")
    for hi, lo in [(60, 40), (65, 35), (70, 30), (75, 25)]:
        m = calc_metrics(run_bt(df, WARMUP, sp, make_obv_trend(hi, lo)), sp)
        label = f"S6_OBV_{hi}/{lo}"
        print(f"    {label:30s}: {fmt(m['is'])}")
        results.append((label, m['is']))

    # ═══════════════════════════════════════
    #  PART 4: OOS Peek for Top 5 IS
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 4: IS Ranking + OOS Peek (top 5)")
    print("=" * 70)

    results.sort(key=lambda x: x[1]['pnl'], reverse=True)
    print(f"\n  {'#':>3s} {'Signal':35s} IS_PnL  IS_t IS_WR")
    for idx, (label, m) in enumerate(results[:15]):
        marker = " <<<" if m['pnl'] > v14_is else ""
        print(f"  {idx+1:3d} {label:35s} ${m['pnl']:+6.0f} {m['t']:4d} {m['wr']:4.0f}%{marker}")

    # OOS peek for top 5
    print(f"\n  --- OOS Peek (top 5 IS signals, full backtest) ---")
    top5_entries = {
        'S0_BrkOnly': entry_brk_only,
        'V14': entry_v14,
    }
    # Reconstruct entry functions for top 5
    for label, _ in results[:5]:
        if label.startswith('S1_TBR_Mom_'):
            parts = label.split('_')[-1].split('/')
            top5_entries[label] = make_tbr_momentum(int(parts[0]), int(parts[1]))
        elif label.startswith('S2_TBR_Rev_'):
            parts = label.split('_')[-1].split('/')
            top5_entries[label] = make_tbr_reversal(int(parts[0]), int(parts[1]))
        elif label.startswith('S3_TBR_Comp_'):
            t = int(label.split('_')[-1])
            top5_entries[label] = make_tbr_compression(t)
        elif label.startswith('S4_RC'):
            parts = label.replace('S4_RC', '').split('_')
            top5_entries[label] = make_retcon(int(parts[0]), float(parts[1]))
        elif label.startswith('S5_CTR_'):
            t = float(label.split('_')[-1])
            top5_entries[label] = make_ctr_trend(t)
        elif label.startswith('S6_OBV_'):
            parts = label.split('_')[-1].split('/')
            top5_entries[label] = make_obv_trend(int(parts[0]), int(parts[1]))

    # Run full dataset for top 5
    print(f"\n  {'Signal':35s} {'IS':>28s} | {'OOS':>28s}")
    for label, entry_fn in top5_entries.items():
        m = calc_metrics(run_bt(df, WARMUP, len(df), entry_fn), sp)
        print(f"  {label:35s} {fmt(m['is'])} | {fmt(m['oos'])}")

    # ═══════════════════════════════════════
    #  PART 5: Correlation with V14
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 5: Signal Overlap with V14")
    print("=" * 70)

    # Check if top signals fire on same bars as V14
    v14_trades = run_bt(df, WARMUP, len(df), entry_v14)
    v14_bars = set(t['eb'] for t in v14_trades)

    for label, _ in results[:3]:
        if label in top5_entries:
            other_trades = run_bt(df, WARMUP, len(df), top5_entries[label])
            other_bars = set(t['eb'] for t in other_trades)
            overlap = v14_bars & other_bars
            print(f"  {label:30s}: {len(other_bars)} trades, {len(overlap)} overlap with V14 "
                  f"({len(overlap)/max(len(v14_bars),1)*100:.0f}%)")

    # ═══════════════════════════════════════
    #  PART 6: Summary & Next Steps
    # ═══════════════════════════════════════
    print("\n" + "=" * 70)
    print("PART 6: Summary")
    print("=" * 70)

    best = results[0]
    print(f"\n  Best IS signal: {best[0]}")
    print(f"  IS PnL: ${best[1]['pnl']:+.0f} ({best[1]['t']} trades, WR {best[1]['wr']:.0f}%)")
    print(f"  V14 IS: ${v14_is:+.0f}")

    if best[1]['pnl'] > 0:
        print(f"\n  Recommendation: Proceed to R1 with {best[0]} for OOS validation")
    else:
        print(f"\n  All signals negative on IS. Need different approach in R1.")


if __name__ == "__main__":
    main()
