"""
V17 R2: Trend Following Without Breakout

R1 conclusion: ALL mean reversion signals failed IS (30 signals x 14 exits).
  Mean reversion MFE too small (0.1-0.4%) to overcome $4 fee + SafeNet.

R2 hypothesis: Maybe non-breakout alpha is TREND FOLLOWING, not mean reversion.
  Breakout works because it captures direction with conviction.
  Can we capture direction WITHOUT using N-bar new high/low?

Approaches:
  A. Range position (close within N-bar range, NOT at max/min)
  B. SMA/EMA slope + price position
  C. Multi-gate high-conviction filter
  D. Momentum (return percentile, NOT breakout)
  E. Trend acceleration (EMA divergence)

Breakout self-check:
  ✅ No close > N-bar max / min
  ✅ Range position uses relative position (0-1 scale), NOT max/min comparison
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0
WARMUP = 150
SN_SLIP = 0.25

L_SN, S_SN = 0.035, 0.04
L_CD, S_CD = 6, 8
L_CAP, S_CAP = 20, 20
DAILY_LOSS = -200
CONSEC_PAUSE, CONSEC_CD = 4, 24
BLOCK_H = {0, 1, 2, 12}
L_BLOCK_D, S_BLOCK_D = {5, 6}, {0, 5, 6}


def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    d = eth.copy()

    # EMAs
    d['ema10'] = d['close'].ewm(span=10).mean()
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()
    d['ema100'] = d['close'].ewm(span=100).mean()
    d['sma20'] = d['close'].rolling(20).mean()
    d['sma50'] = d['close'].rolling(50).mean()

    # EMA/SMA slopes
    d['ema20_slope'] = (d['ema20'] - d['ema20'].shift(5)) / d['ema20'].shift(5) * 100
    d['ema50_slope'] = (d['ema50'] - d['ema50'].shift(10)) / d['ema50'].shift(10) * 100
    d['sma20_slope'] = (d['sma20'] - d['sma20'].shift(5)) / d['sma20'].shift(5) * 100

    # EMA deviations
    d['ema20_dev'] = (d['close'] - d['ema20']) / d['ema20'] * 100
    d['ema50_dev'] = (d['close'] - d['ema50']) / d['ema50'] * 100

    # EMA trend
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)

    # Range position (NOT breakout — relative position within N-bar range)
    for n in [10, 15, 20, 30]:
        rmax = d['high'].shift(1).rolling(n).max()
        rmin = d['low'].shift(1).rolling(n).min()
        rng = (rmax - rmin).clip(lower=1e-8)
        d[f'rpos_{n}'] = (d['close'] - rmin) / rng  # 0 = at range bottom, 1 = at range top

    # Returns and momentum
    d['ret_1'] = d['close'].pct_change(1)
    d['ret_3'] = d['close'].pct_change(3)
    d['ret_5'] = d['close'].pct_change(5)
    d['ret_10'] = d['close'].pct_change(10)

    # Momentum percentile (rolling rank, NOT breakout)
    d['mom5_pctile'] = d['ret_5'].shift(1).rolling(100).rank(pct=True) * 100
    d['mom10_pctile'] = d['ret_10'].shift(1).rolling(100).rank(pct=True) * 100

    # Bar structure
    rng_bar = (d['high'] - d['low']).clip(lower=1e-8)
    d['bar_dir'] = np.sign(d['close'] - d['open'])
    d['body_pct'] = (d['close'] - d['open']) / d['open'] * 100

    # Volume
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_ma5'] = d['tbr'].rolling(5).mean()
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio'] = gk.rolling(5).mean() / gk.rolling(20).mean()

    # EMA convergence/divergence
    d['ema_conv'] = (d['ema20'] - d['ema50']) / d['ema50'] * 100  # like MACD %

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # Breakout flags (for exclusion verification)
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()
    d['is_brk_up'] = d['close'] > d['high_15']
    d['is_brk_dn'] = d['close'] < d['low_15']

    return d


def run_bt(df, start, end, entry_fn, tp_l=0.035, tp_s=0.02, mh_l=6, mh_s=10,
           ext_l=2, ext_s=2, ml_l=-75, ml_s=-150, fee=FEE):
    trades = []
    pos_l = pos_s = None
    last_exit_l = last_exit_s = -999
    daily_pnl = 0.0
    monthly_pnl_l = monthly_pnl_s = 0.0
    consec_losses = 0
    consec_pause_until = -999
    month_entries_l = month_entries_s = 0
    cur_day = cur_month = None

    def close_pos(pos, side, reason, xp, bar_i, dt):
        nonlocal daily_pnl, monthly_pnl_l, monthly_pnl_s
        nonlocal consec_losses, consec_pause_until
        nonlocal last_exit_l, last_exit_s
        ep = pos['ep']
        if side == 'L':
            net = (xp - ep) * NOTIONAL / ep - fee
        else:
            net = (ep - xp) * NOTIONAL / ep - fee
        daily_pnl += net
        if side == 'L':
            monthly_pnl_l += net
            last_exit_l = bar_i
        else:
            monthly_pnl_s += net
            last_exit_s = bar_i
        if net < 0:
            consec_losses += 1
            if consec_losses >= CONSEC_PAUSE:
                consec_pause_until = bar_i + CONSEC_CD
        else:
            consec_losses = 0
        trades.append({
            'eb': pos['bar'], 'xb': bar_i, 'side': side,
            'ep': ep, 'xp': xp, 'p': net, 'reason': reason,
            'month': f"{dt.year}-{dt.month:02d}"
        })

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

        if pos_l is not None:
            bh = i - pos_l['bar']
            ep = pos_l['ep']
            sn_p = ep * (1 - L_SN)
            if row.low <= sn_p:
                close_pos(pos_l, 'L', 'SN', sn_p * (1 - L_SN * SN_SLIP), i, dt)
                pos_l = None
            elif row.high >= ep * (1 + tp_l):
                close_pos(pos_l, 'L', 'TP', ep * (1 + tp_l), i, dt)
                pos_l = None
            elif pos_l.get('ext') and row.low <= ep:
                close_pos(pos_l, 'L', 'BE', ep, i, dt)
                pos_l = None
            elif bh >= mh_l + (ext_l if pos_l.get('ext') else 0):
                r = 'MH-ext' if pos_l.get('ext') else 'MH'
                close_pos(pos_l, 'L', r, row.close, i, dt)
                pos_l = None
            elif bh == mh_l and not pos_l.get('ext'):
                if (row.close - ep) / ep > 0:
                    pos_l['ext'] = True
                else:
                    close_pos(pos_l, 'L', 'MH', row.close, i, dt)
                    pos_l = None

        if pos_s is not None:
            bh = i - pos_s['bar']
            ep = pos_s['ep']
            sn_p = ep * (1 + S_SN)
            if row.high >= sn_p:
                close_pos(pos_s, 'S', 'SN', sn_p * (1 + S_SN * SN_SLIP), i, dt)
                pos_s = None
            elif row.low <= ep * (1 - tp_s):
                close_pos(pos_s, 'S', 'TP', ep * (1 - tp_s), i, dt)
                pos_s = None
            elif pos_s.get('ext') and row.high >= ep:
                close_pos(pos_s, 'S', 'BE', ep, i, dt)
                pos_s = None
            elif bh >= mh_s + (ext_s if pos_s.get('ext') else 0):
                r = 'MH-ext' if pos_s.get('ext') else 'MH'
                close_pos(pos_s, 'S', r, row.close, i, dt)
                pos_s = None
            elif bh == mh_s and not pos_s.get('ext'):
                if (ep - row.close) / ep > 0:
                    pos_s['ext'] = True
                else:
                    close_pos(pos_s, 'S', 'MH', row.close, i, dt)
                    pos_s = None

        if i < consec_pause_until or daily_pnl <= DAILY_LOSS:
            continue
        sig = entry_fn(row, i)
        if not sig:
            continue
        if 'L' in sig and pos_l is None:
            if (h not in BLOCK_H and dow not in L_BLOCK_D
                and (i - last_exit_l) >= L_CD
                and monthly_pnl_l > ml_l and month_entries_l < L_CAP):
                pos_l = {'ep': row.open, 'bar': i}
                month_entries_l += 1
        if 'S' in sig and pos_s is None:
            if (h not in BLOCK_H and dow not in S_BLOCK_D
                and (i - last_exit_s) >= S_CD
                and monthly_pnl_s > ml_s and month_entries_s < S_CAP):
                pos_s = {'ep': row.open, 'bar': i}
                month_entries_s += 1

    return trades


def _metrics(tlist):
    if not tlist:
        return {'pnl': 0, 't': 0, 'wr': 0, 'pf': 0, 'mdd': 0,
                'pm': 0, 'pm_n': 0, 'worst': 0, 'months': {}}
    pnl = sum(t['p'] for t in tlist)
    wins = sum(1 for t in tlist if t['p'] > 0)
    wr = wins / len(tlist) * 100
    w = sum(t['p'] for t in tlist if t['p'] > 0)
    l_sum = abs(sum(t['p'] for t in tlist if t['p'] <= 0))
    pf = w / l_sum if l_sum > 0 else 99
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
            'pm': pm, 'pm_n': len(months), 'worst': worst, 'months': dict(months)}


def split_metrics(trades, sp):
    is_t = [t for t in trades if t['eb'] < sp]
    oos_t = [t for t in trades if t['eb'] >= sp]
    return {'is': _metrics(is_t), 'oos': _metrics(oos_t)}


def fmt(m):
    return (f"{m['t']:3d}t ${m['pnl']:+7.0f} WR {m['wr']:4.0f}% "
            f"PF {m['pf']:4.1f} MDD ${m['mdd']:4.0f} PM {m['pm']}/{m['pm_n']}")


# ══════════════════════════════════════════
#  Entry Signals — Trend Following WITHOUT Breakout
# ══════════════════════════════════════════

# A: Range Position
def make_range_pos(n, l_thresh, s_thresh):
    """L: close in top l_thresh of N-bar range, S: close in bottom s_thresh"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if rp > l_thresh: sig += 'L'
        if rp < s_thresh: sig += 'S'
        return sig or None
    return entry

# B: SMA/EMA slope + price position
def make_slope_entry(slope_thresh):
    """L: EMA20 slope > thresh AND close > EMA20, S: slope < -thresh AND close < EMA20"""
    def entry(row, i):
        sig = ''
        slope = row.ema20_slope
        if pd.isna(slope): return None
        if slope > slope_thresh and row.close > row.ema20: sig += 'L'
        if slope < -slope_thresh and row.close < row.ema20: sig += 'S'
        return sig or None
    return entry

# C: EMA convergence (ema20 pulling away from ema50)
def make_ema_conv(conv_thresh):
    """L: EMA convergence > thresh (EMA20 above EMA50 and widening), S: < -thresh"""
    def entry(row, i):
        sig = ''
        conv = row.ema_conv
        if pd.isna(conv): return None
        if conv > conv_thresh: sig += 'L'
        if conv < -conv_thresh: sig += 'S'
        return sig or None
    return entry

# D: Momentum percentile (NOT breakout — uses return rank, not price vs max)
def make_mom_pctile(n, l_pctile, s_pctile):
    """L: N-bar return pctile > l_pctile, S: < s_pctile"""
    col = f'mom{n}_pctile'
    def entry(row, i):
        sig = ''
        p = getattr(row, col, None)
        if p is None or pd.isna(p): return None
        if p > l_pctile: sig += 'L'
        if p < s_pctile: sig += 'S'
        return sig or None
    return entry

# E: Price cross above/below EMA
def make_ema_cross_price(ema_col):
    """L: close crosses above ema_col, S: crosses below"""
    def entry(row, i):
        # We need previous bar data — use deviation as proxy
        # Cross above = deviation just turned positive (dev > 0 and dev small)
        sig = ''
        dev = (row.close - getattr(row, ema_col)) / getattr(row, ema_col) * 100
        if pd.isna(dev): return None
        # "Just crossed" = deviation between 0 and 0.3%
        if 0 < dev < 0.3: sig += 'L'
        if -0.3 < dev < 0: sig += 'S'
        return sig or None
    return entry

# F: Multi-gate high-conviction
def make_multi_gate_l():
    """L: EMA20 > EMA50 + EMA20 slope > 0.1 + close > EMA20 + recent dip (ret_3 < 0)"""
    def entry(row, i):
        if pd.isna(row.ema20_slope) or pd.isna(row.ret_3): return None
        if (row.ema20 > row.ema50
            and row.ema20_slope > 0.1
            and row.close > row.ema20
            and row.ret_3 < 0):
            return 'L'
        return None
    return entry

def make_multi_gate_s():
    """S: EMA20 < EMA50 + EMA20 slope < -0.1 + close < EMA20 + recent bounce (ret_3 > 0)"""
    def entry(row, i):
        if pd.isna(row.ema20_slope) or pd.isna(row.ret_3): return None
        if (row.ema20 < row.ema50
            and row.ema20_slope < -0.1
            and row.close < row.ema20
            and row.ret_3 > 0):
            return 'S'
        return None
    return entry

def make_multi_gate_ls():
    """Combined L+S multi-gate"""
    fn_l = make_multi_gate_l()
    fn_s = make_multi_gate_s()
    def entry(row, i):
        sl = fn_l(row, i)
        ss = fn_s(row, i)
        sig = ''
        if sl: sig += sl
        if ss: sig += ss
        return sig or None
    return entry

# G: Range pos + trend filter
def make_range_trend(n, l_thresh, s_thresh):
    """L: rpos > l_thresh AND ema_trend=1, S: rpos < s_thresh AND ema_trend=-1"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if rp > l_thresh and row.ema_trend == 1: sig += 'L'
        if rp < s_thresh and row.ema_trend == -1: sig += 'S'
        return sig or None
    return entry

# H: Close above/below longer-term EMA (trend bias)
def make_ema_bias(fast, slow):
    """L: close > fast EMA AND fast > slow EMA, S: close < fast AND fast < slow"""
    def entry(row, i):
        sig = ''
        f_val = getattr(row, f'ema{fast}', None)
        s_val = getattr(row, f'ema{slow}', None)
        if f_val is None or s_val is None: return None
        if pd.isna(f_val) or pd.isna(s_val): return None
        if row.close > f_val and f_val > s_val: sig += 'L'
        if row.close < f_val and f_val < s_val: sig += 'S'
        return sig or None
    return entry

# I: Trend + pullback to EMA (buy the dip in uptrend)
def make_pullback_ema(dev_thresh):
    """L: uptrend + close just above EMA20 (0 to dev_thresh), S: downtrend + close just below"""
    def entry(row, i):
        sig = ''
        dev = row.ema20_dev
        if pd.isna(dev): return None
        if row.ema_trend == 1 and 0 < dev < dev_thresh: sig += 'L'
        if row.ema_trend == -1 and -dev_thresh < dev < 0: sig += 'S'
        return sig or None
    return entry


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V17 R2: Trend Following Without Breakout")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")

    # ══════════════════════════════════════════
    #  Section 1: IS scan with V14 exits
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 1: IS Scan — V14 Exits (TP 3.5%/2%, MH 6/10)")
    print("=" * 70)

    signals = [
        # A: Range position
        ("A1 rpos15 >0.70/<0.30", make_range_pos(15, 0.70, 0.30)),
        ("A2 rpos15 >0.80/<0.20", make_range_pos(15, 0.80, 0.20)),
        ("A3 rpos20 >0.70/<0.30", make_range_pos(20, 0.70, 0.30)),
        ("A4 rpos20 >0.80/<0.20", make_range_pos(20, 0.80, 0.20)),
        ("A5 rpos30 >0.70/<0.30", make_range_pos(30, 0.70, 0.30)),
        ("A6 rpos10 >0.70/<0.30", make_range_pos(10, 0.70, 0.30)),
        ("A7 rpos15 >0.60/<0.40", make_range_pos(15, 0.60, 0.40)),
        ("A8 rpos15 >0.90/<0.10", make_range_pos(15, 0.90, 0.10)),

        # B: Slope
        ("B1 slope >0.1", make_slope_entry(0.1)),
        ("B2 slope >0.2", make_slope_entry(0.2)),
        ("B3 slope >0.3", make_slope_entry(0.3)),
        ("B4 slope >0.5", make_slope_entry(0.5)),

        # C: EMA convergence
        ("C1 conv >0.5", make_ema_conv(0.5)),
        ("C2 conv >1.0", make_ema_conv(1.0)),
        ("C3 conv >1.5", make_ema_conv(1.5)),
        ("C4 conv >2.0", make_ema_conv(2.0)),

        # D: Momentum percentile
        ("D1 mom5 >70/<30", make_mom_pctile(5, 70, 30)),
        ("D2 mom5 >80/<20", make_mom_pctile(5, 80, 20)),
        ("D3 mom10 >70/<30", make_mom_pctile(10, 70, 30)),
        ("D4 mom10 >80/<20", make_mom_pctile(10, 80, 20)),
        ("D5 mom5 >60/<40", make_mom_pctile(5, 60, 40)),

        # E: Price cross EMA
        ("E1 price cross ema20", make_ema_cross_price('ema20')),
        ("E2 price cross ema50", make_ema_cross_price('ema50')),

        # F: Multi-gate
        ("F1 multi-gate L", make_multi_gate_l()),
        ("F2 multi-gate S", make_multi_gate_s()),
        ("F3 multi-gate L+S", make_multi_gate_ls()),

        # G: Range + trend
        ("G1 rpos15>0.70+trend", make_range_trend(15, 0.70, 0.30)),
        ("G2 rpos20>0.70+trend", make_range_trend(20, 0.70, 0.30)),
        ("G3 rpos15>0.80+trend", make_range_trend(15, 0.80, 0.20)),
        ("G4 rpos20>0.80+trend", make_range_trend(20, 0.80, 0.20)),

        # H: EMA bias
        ("H1 ema 10/50 bias", make_ema_bias(10, 50)),
        ("H2 ema 20/50 bias", make_ema_bias(20, 50)),
        ("H3 ema 20/100 bias", make_ema_bias(20, 100)),

        # I: Pullback to EMA in trend
        ("I1 pullback 0.5%", make_pullback_ema(0.5)),
        ("I2 pullback 1.0%", make_pullback_ema(1.0)),
        ("I3 pullback 0.3%", make_pullback_ema(0.3)),
    ]

    print(f"\n  {'Signal':30s} | {'IS':>45s}")
    print(f"  {'-'*30}-+-{'-'*45}")

    is_results = []
    for name, fn in signals:
        trades = run_bt(df, WARMUP, sp, fn)
        m = _metrics(trades)
        marker = " <<<" if m['pnl'] > 0 else ""
        print(f"  {name:30s} | {fmt(m)}{marker}")
        is_results.append((name, fn, m))

    # ══════════════════════════════════════════
    #  Section 2: OOS validation of any IS-positive
    # ══════════════════════════════════════════
    positive = [(name, fn, m) for name, fn, m in is_results if m['pnl'] > 0 and m['t'] >= 15]
    positive.sort(key=lambda x: -x[2]['pnl'])

    print(f"\n" + "=" * 70)
    print(f"Section 2: OOS Validation ({len(positive)} IS-positive)")
    print("=" * 70)

    if not positive:
        print("\n  NO positive IS strategies found.")
    else:
        print(f"\n  {'Signal':30s} | {'IS':>45s} | {'OOS':>45s}")
        print(f"  {'-'*30}-+-{'-'*45}-+-{'-'*45}")
        for name, fn, _ in positive:
            full = split_metrics(run_bt(df, WARMUP, n, fn), sp)
            oos_pos = "<<<" if full['oos']['pnl'] > 0 else ""
            print(f"  {name:30s} | {fmt(full['is'])} | {fmt(full['oos'])} {oos_pos}")

    # ══════════════════════════════════════════
    #  Section 3: Breakout overlap check
    # ══════════════════════════════════════════
    if positive:
        print(f"\n" + "=" * 70)
        print("Section 3: Breakout Overlap Check")
        print("  How many trades from IS-positive signals are on breakout bars?")
        print("=" * 70)

        for name, fn, _ in positive[:5]:
            trades = run_bt(df, WARMUP, n, fn)
            brk_count = 0
            for t in trades:
                bi = t['eb']
                if bi < len(df):
                    row = df.iloc[bi]
                    if row.is_brk_up or row.is_brk_dn:
                        brk_count += 1
            pct = brk_count / len(trades) * 100 if trades else 0
            print(f"  {name:30s}: {len(trades)} trades, {brk_count} on breakout bars ({pct:.0f}%)")

    # ══════════════════════════════════════════
    #  Section 4: Exit optimization for best
    # ══════════════════════════════════════════
    if positive:
        best_name, best_fn, _ = positive[0]
        print(f"\n" + "=" * 70)
        print(f"Section 4: Exit Optimization — {best_name}")
        print("=" * 70)

        exit_configs = [
            ("V14 default", 0.035, 0.02, 6, 10, 2, 2),
            ("TP2.5/1.5 MH6/10", 0.025, 0.015, 6, 10, 2, 2),
            ("TP3.0/2.0 MH6/10", 0.030, 0.020, 6, 10, 2, 2),
            ("TP4.0/2.5 MH6/10", 0.040, 0.025, 6, 10, 2, 2),
            ("TP3.5/2.0 MH4/8", 0.035, 0.020, 4, 8, 2, 2),
            ("TP3.5/2.0 MH8/12", 0.035, 0.020, 8, 12, 2, 2),
            ("TP3.0/1.5 MH5/8", 0.030, 0.015, 5, 8, 2, 2),
            ("TP2.0/1.5 MH4/6", 0.020, 0.015, 4, 6, 2, 2),
            ("TP3.5/2.0 MH6/10 no ext", 0.035, 0.020, 6, 10, 0, 0),
        ]

        print(f"\n  {'Exit':25s} | {'IS':>45s} | {'OOS':>45s}")
        print(f"  {'-'*25}-+-{'-'*45}-+-{'-'*45}")
        for xname, tp_l, tp_s, mh_l, mh_s, ext_l, ext_s in exit_configs:
            full = split_metrics(run_bt(df, WARMUP, n, best_fn,
                                        tp_l=tp_l, tp_s=tp_s, mh_l=mh_l, mh_s=mh_s,
                                        ext_l=ext_l, ext_s=ext_s), sp)
            print(f"  {xname:25s} | {fmt(full['is'])} | {fmt(full['oos'])}")

    # ══════════════════════════════════════════
    #  Section 5: Honest assessment
    # ══════════════════════════════════════════
    print(f"\n" + "=" * 70)
    print("Section 5: Honest Assessment")
    print("=" * 70)

    total_tested = len(signals)
    num_positive = len(positive)
    print(f"\n  Signals tested: {total_tested}")
    print(f"  IS-positive: {num_positive}")
    print(f"  IS-positive rate: {num_positive/total_tested*100:.0f}%")

    # Count breakout-equivalent strategies
    if positive:
        print(f"\n  IS-positive strategies may be capturing breakout bars indirectly.")
        print(f"  (Range position > 0.9 ≈ breakout, momentum pctile > 80 ≈ breakout)")
    else:
        print(f"\n  CONCLUSION: Non-breakout trend following also fails on IS.")
        print(f"  Combined with R1 (mean reversion FAILED):")
        print(f"  → Mean reversion: MFE too small (0.1-0.4%), $4 fee kills edge")
        print(f"  → Trend following without breakout: no directional conviction")
        print(f"  → ETH 1h non-breakout alpha may not exist at $1K/$4 fee structure")


if __name__ == '__main__':
    main()
