"""
V18 R1: 30m Non-Breakout Signal Scan

R0 showed 30m has fee/avg|return| = 29.7%, similar to 1h.
Non-breakout MFE > fee on all timeframes.
But V17 proved no DIRECTIONAL signal works on 1h non-breakout bars.

R1 tests: Can any directional signal profit on 30m non-breakout bars?

Test categories:
  A. Mean reversion (EMA dev, RSI, consecutive bars — V17 R1 failed on 1h)
  B. Trend continuation (EMA slope + direction — V17 R2 failed on 1h)
  C. Volume/TBR signals
  D. Candle patterns (engulfing, pin bar)
  E. Session effects (hour-of-day)
  F. Volatility regime (GK/ATR compression → direction)
  G. Combined multi-gate
  H. TBR change rate (delta) — new for shorter timeframe
  I. Intrabar flow (TBR * vol_ratio interaction)

Each signal explicitly EXCLUDES breakout bars.
Reports fee/avg_gross_profit ratio for every IS-positive strategy.

Breakout self-check:
  All signals verified: NO close > N-bar max/min, NO rpos > 0.85
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0
FEE_PCT = FEE / NOTIONAL * 100
WARMUP = 200  # 30m needs more warmup for rolling(100) on ratio

SN_SLIP = 0.25
L_SN, S_SN = 0.035, 0.04

# Cooldowns adjusted for 30m (2x bars per hour vs 1h)
L_CD, S_CD = 12, 16  # 6h / 8h equivalent
L_CAP, S_CAP = 40, 40  # 2x 1h cap (more bars)
DAILY_LOSS = -200
CONSEC_PAUSE, CONSEC_CD = 4, 48  # 24h equivalent on 30m
L_BLOCK_D, S_BLOCK_D = {5, 6}, {0, 5, 6}
BLOCK_H = {0, 1, 2, 12}  # Same hours blocked
ML_L, ML_S = -75, -150


def load_and_prepare():
    df = pd.read_csv(DATA_DIR / "ETHUSDT_30m_latest730d.csv", parse_dates=["datetime"])
    d = df.copy()

    # EMAs
    d['ema5'] = d['close'].ewm(span=5).mean()
    d['ema10'] = d['close'].ewm(span=10).mean()
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()
    d['ema100'] = d['close'].ewm(span=100).mean()

    # EMA trend and deviation
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)
    d['ema20_dev'] = (d['close'] - d['ema20']) / d['ema20'] * 100
    d['ema50_dev'] = (d['close'] - d['ema50']) / d['ema50'] * 100
    d['ema20_slope'] = (d['ema20'] - d['ema20'].shift(5)) / d['ema20'].shift(5) * 100

    # EMA cross events
    d['ema5_above_20'] = (d['ema5'] > d['ema20']).astype(int)
    d['ema5_cross_up'] = (d['ema5_above_20'] == 1) & (d['ema5_above_20'].shift(1) == 0)
    d['ema5_cross_dn'] = (d['ema5_above_20'] == 0) & (d['ema5_above_20'].shift(1) == 1)
    d['ema10_above_50'] = (d['ema10'] > d['ema50']).astype(int)
    d['ema10_cross_up'] = (d['ema10_above_50'] == 1) & (d['ema10_above_50'].shift(1) == 0)
    d['ema10_cross_dn'] = (d['ema10_above_50'] == 0) & (d['ema10_above_50'].shift(1) == 1)

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # Bar structure
    d['body'] = d['close'] - d['open']
    d['body_pct'] = d['body'] / d['open'] * 100
    d['range_bar'] = d['high'] - d['low']
    d['range_pct'] = d['range_bar'] / d['open'] * 100
    d['upper_wick'] = d['high'] - d[['close', 'open']].max(axis=1)
    d['lower_wick'] = d[['close', 'open']].min(axis=1) - d['low']
    d['body_ratio'] = abs(d['body']) / d['range_bar'].clip(lower=1e-8)

    # Candle patterns
    prev_body = d['body'].shift(1)
    d['bullish_engulf'] = (d['body'] > 0) & (prev_body < 0) & (d['body'].abs() > prev_body.abs())
    d['bearish_engulf'] = (d['body'] < 0) & (prev_body > 0) & (d['body'].abs() > prev_body.abs())

    abs_body = abs(d['body']).clip(lower=1e-8)
    d['pin_bull'] = (d['lower_wick'] > 2 * abs_body) & (d['body'] > 0) & (d['range_pct'] > 0.3)
    d['pin_bear'] = (d['upper_wick'] > 2 * abs_body) & (d['body'] < 0) & (d['range_pct'] > 0.3)

    # Consecutive bars
    cu, cd = 0, 0
    c_up = np.zeros(len(d))
    c_dn = np.zeros(len(d))
    for i in range(len(d)):
        if d.iloc[i].body > 0: cu += 1; cd = 0
        elif d.iloc[i].body < 0: cd += 1; cu = 0
        else: cu = cd = 0
        c_up[i] = cu; c_dn[i] = cd
    d['consec_up'] = c_up
    d['consec_dn'] = c_dn

    # Returns
    d['ret_1'] = d['close'].pct_change(1) * 100
    d['ret_3'] = d['close'].pct_change(3) * 100
    d['ret_5'] = d['close'].pct_change(5) * 100

    # Volume & TBR
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_ma5'] = d['tbr'].rolling(5).mean()
    d['tbr_delta'] = d['tbr'] - d['tbr'].shift(1)
    d['tbr_delta3'] = d['tbr'] - d['tbr'].shift(3)
    d['tbr_accel'] = d['tbr_delta'] - d['tbr_delta'].shift(1)  # 2nd derivative

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile'] = d['gk_ratio'].shift(1).rolling(100).rank(pct=True) * 100

    # ATR
    tr = pd.concat([
        d['high'] - d['low'],
        (d['high'] - d['close'].shift(1)).abs(),
        (d['low'] - d['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    d['atr14'] = tr.rolling(14).mean()
    d['atr_ratio'] = d['atr14'] / d['atr14'].rolling(50).mean()

    # Time
    d['hour'] = d['datetime'].dt.hour
    d['minute'] = d['datetime'].dt.minute
    d['dow'] = d['datetime'].dt.weekday

    # Breakout flags
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()
    d['is_brk_up'] = d['close'] > d['high_15']
    d['is_brk_dn'] = d['close'] < d['low_15']
    d['is_brk'] = d['is_brk_up'] | d['is_brk_dn']

    return d


def run_bt(df, start, end, entry_fn, tp_l=0.025, tp_s=0.015, mh_l=12, mh_s=20,
           ext_l=4, ext_s=4, fee=FEE,
           use_mfe=True, mfe_thresh=0.008, mfe_trail=0.006, mfe_min_bar=2):
    """Backtest engine adapted for 30m:
    - TP smaller (less time for move)
    - MH longer in bars (same real time: 12 bars = 6h for L, 20 bars = 10h for S)
    - Extension 4 bars (2h)
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

        # Gross profit before fee
        if side == 'L':
            gross = (xp - ep) * NOTIONAL / ep
        else:
            gross = (ep - xp) * NOTIONAL / ep

        trades.append({
            'eb': pos['bar'], 'xb': bar_i, 'side': side,
            'ep': ep, 'xp': xp, 'p': net, 'gross': gross,
            'reason': reason,
            'month': f"{dt.year}-{dt.month:02d}",
            'is_brk': pos.get('is_brk', False)
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

        o, hi, lo, c = row.open, row.high, row.low, row.close

        # === EXIT L ===
        if pos_l is not None:
            ep = pos_l['ep']
            bars = i - pos_l['bar']
            sn_price = ep * (1 - L_SN)
            sn_hit = lo <= sn_price
            pnl_pct = (c - ep) / ep
            hi_pnl_pct = (hi - ep) / ep
            pos_l['running_mfe'] = max(pos_l['running_mfe'], hi_pnl_pct)

            if sn_hit:
                slip_price = sn_price * (1 - SN_SLIP * L_SN)
                close_pos(pos_l, 'L', 'SN', slip_price, i, dt)
                pos_l = None
            elif hi_pnl_pct >= tp_l:
                close_pos(pos_l, 'L', 'TP', ep * (1 + tp_l), i, dt)
                pos_l = None
            elif use_mfe and pos_l['running_mfe'] >= mfe_thresh and bars >= mfe_min_bar:
                if (pos_l['running_mfe'] - pnl_pct) >= mfe_trail:
                    close_pos(pos_l, 'L', 'MFE', c, i, dt)
                    pos_l = None
            if pos_l is not None and bars >= mh_l:
                if pos_l.get('ext_bars', 0) < ext_l and pnl_pct > 0:
                    pos_l['ext_bars'] = pos_l.get('ext_bars', 0) + 1
                    if lo <= ep:
                        close_pos(pos_l, 'L', 'BE', ep, i, dt)
                        pos_l = None
                else:
                    close_pos(pos_l, 'L', 'MH', c, i, dt)
                    pos_l = None

        # === EXIT S ===
        if pos_s is not None:
            ep = pos_s['ep']
            bars = i - pos_s['bar']
            sn_price = ep * (1 + S_SN)
            sn_hit = hi >= sn_price
            pnl_pct = (ep - c) / ep
            lo_pnl_pct = (ep - lo) / ep

            if sn_hit:
                slip_price = sn_price * (1 + SN_SLIP * S_SN)
                close_pos(pos_s, 'S', 'SN', slip_price, i, dt)
                pos_s = None
            elif lo_pnl_pct >= tp_s:
                close_pos(pos_s, 'S', 'TP', ep * (1 - tp_s), i, dt)
                pos_s = None
            if pos_s is not None and bars >= mh_s:
                if pos_s.get('ext_bars', 0) < ext_s and pnl_pct > 0:
                    pos_s['ext_bars'] = pos_s.get('ext_bars', 0) + 1
                    if hi >= ep:
                        close_pos(pos_s, 'S', 'BE', ep, i, dt)
                        pos_s = None
                else:
                    close_pos(pos_s, 'S', 'MH', c, i, dt)
                    pos_s = None

        # === ENTRY ===
        if i < WARMUP:
            continue
        if daily_pnl <= DAILY_LOSS:
            continue
        if i < consec_pause_until:
            continue

        sig = entry_fn(row, i)
        if sig is None:
            continue

        if 'L' in sig and pos_l is None:
            if h not in BLOCK_H and dow not in L_BLOCK_D:
                if (i - last_exit_l) >= L_CD and monthly_pnl_l > ML_L and month_entries_l < L_CAP:
                    month_entries_l += 1
                    pos_l = {'ep': c, 'bar': i,
                             'running_mfe': 0.0, 'mh_reduced': False,
                             'is_brk': bool(row.is_brk_up)}

        if 'S' in sig and pos_s is None:
            if h not in BLOCK_H and dow not in S_BLOCK_D:
                if (i - last_exit_s) >= S_CD and monthly_pnl_s > ML_S and month_entries_s < S_CAP:
                    month_entries_s += 1
                    pos_s = {'ep': c, 'bar': i,
                             'is_brk': bool(row.is_brk_dn)}

    if pos_l is not None:
        close_pos(pos_l, 'L', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)
    if pos_s is not None:
        close_pos(pos_s, 'S', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)
    return trades


def summarize(trades):
    if not trades:
        return {'n': 0, 'pnl': 0, 'wr': 0, 'pf': 0, 'mdd': 0, 'pm': 0, 'pm_total': 0, 'fee_ratio': 0}
    pnls = [t['p'] for t in trades]
    n = len(pnls)
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / n * 100 if n else 0
    gross_p = sum(p for p in pnls if p > 0)
    gross_l = abs(sum(p for p in pnls if p < 0))
    pf = gross_p / gross_l if gross_l else 99.9
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    mdd = dd.max() if len(dd) else 0
    months = {}
    for t in trades:
        months[t['month']] = months.get(t['month'], 0) + t['p']
    pm = sum(1 for v in months.values() if v > 0)
    pm_total = len(months)

    # Fee ratio: fee / avg gross profit for winning trades
    winning_gross = [t['gross'] for t in trades if t['gross'] > 0]
    if winning_gross:
        avg_gross = np.mean(winning_gross)
        fee_ratio = FEE / avg_gross * 100 if avg_gross > 0 else 999
    else:
        fee_ratio = 999

    return {'n': n, 'pnl': total, 'wr': wr, 'pf': pf, 'mdd': mdd,
            'pm': pm, 'pm_total': pm_total, 'fee_ratio': fee_ratio}


def fmt(s):
    pm_str = f"{s['pm']}/{s['pm_total']}"
    return (f"{s['n']:3d}t ${s['pnl']:+7.0f} WR {s['wr']:5.0f}% "
            f"PF {s['pf']:4.1f} MDD ${s['mdd']:4.0f} PM {pm_str} fee%{s['fee_ratio']:3.0f}")


def brk_pct(trades):
    if not trades: return 0
    return sum(1 for t in trades if t.get('is_brk')) / len(trades) * 100


# ══════════════════════════════════════════════════════════════════
#  Entry signals — ALL exclude breakout bars
# ══════════════════════════════════════════════════════════════════

# A: Mean reversion
def make_ema_dev_mr(thresh=1.0):
    def entry(row, i):
        sig = ''
        dev = row.ema20_dev
        if pd.isna(dev): return None
        if dev < -thresh and not row.is_brk_dn: sig += 'L'
        if dev > thresh and not row.is_brk_up: sig += 'S'
        return sig or None
    return entry

def make_rsi_mr(lo=30, hi=70):
    def entry(row, i):
        sig = ''
        rsi = row.rsi14
        if pd.isna(rsi): return None
        if rsi < lo and not row.is_brk_dn: sig += 'L'
        if rsi > hi and not row.is_brk_up: sig += 'S'
        return sig or None
    return entry

def make_consec_mr(n=3):
    def entry(row, i):
        sig = ''
        if row.consec_dn >= n and not row.is_brk_dn: sig += 'L'
        if row.consec_up >= n and not row.is_brk_up: sig += 'S'
        return sig or None
    return entry

# B: Trend continuation (non-breakout)
def make_ema_slope_dir(thresh=0.1):
    def entry(row, i):
        sig = ''
        sl = row.ema20_slope
        if pd.isna(sl): return None
        if sl > thresh and row.close > row.ema20 and not row.is_brk_up: sig += 'L'
        if sl < -thresh and row.close < row.ema20 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_ema_trend_dir():
    """EMA trend + directional bar + NOT breakout"""
    def entry(row, i):
        sig = ''
        if row.ema_trend == 1 and row.body > 0 and not row.is_brk_up: sig += 'L'
        if row.ema_trend == -1 and row.body < 0 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_ema5_cross():
    def entry(row, i):
        sig = ''
        if row.ema5_cross_up and not row.is_brk_up: sig += 'L'
        if row.ema5_cross_dn and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_ema10x50_cross():
    def entry(row, i):
        sig = ''
        if row.ema10_cross_up and not row.is_brk_up: sig += 'L'
        if row.ema10_cross_dn and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# C: Volume/TBR
def make_vol_dir(thresh=1.5):
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio): return None
        if row.vol_ratio > thresh and not row.is_brk:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

def make_tbr_dir(buy_thresh=0.58, sell_thresh=0.42):
    def entry(row, i):
        sig = ''
        tbr = row.tbr
        if pd.isna(tbr): return None
        if tbr > buy_thresh and not row.is_brk_up: sig += 'L'
        if tbr < sell_thresh and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# D: Candle patterns
def make_engulfing():
    def entry(row, i):
        sig = ''
        if row.bullish_engulf and not row.is_brk_up: sig += 'L'
        if row.bearish_engulf and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_pin_bar():
    def entry(row, i):
        sig = ''
        if row.pin_bull and not row.is_brk_up: sig += 'L'
        if row.pin_bear and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# E: Session effects
def make_session(long_hours, short_hours):
    def entry(row, i):
        sig = ''
        h = row.hour
        if h in long_hours and not row.is_brk_up: sig += 'L'
        if h in short_hours and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# F: Volatility regime
def make_gk_compress_dir(gk_max=30):
    """GK compression + directional bar + NOT breakout"""
    def entry(row, i):
        sig = ''
        gk = row.gk_pctile
        if pd.isna(gk): return None
        if gk < gk_max and not row.is_brk:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

def make_atr_expand_dir(atr_min=1.3):
    def entry(row, i):
        sig = ''
        if pd.isna(row.atr_ratio): return None
        if row.atr_ratio > atr_min and not row.is_brk:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

# G: Combined multi-gate
def make_multi_trend_nobrk():
    """ema_trend + slope>0 + body direction + vol>1.0 + NOT breakout"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.ema20_slope) or pd.isna(row.vol_ratio): return None
        if (row.ema_trend == 1 and row.ema20_slope > 0 and
            row.body > 0 and row.vol_ratio > 1.0 and not row.is_brk_up):
            sig += 'L'
        if (row.ema_trend == -1 and row.ema20_slope < 0 and
            row.body < 0 and row.vol_ratio > 1.0 and not row.is_brk_dn):
            sig += 'S'
        return sig or None
    return entry

def make_mr_multi():
    """Mean reversion multi-gate: RSI extreme + consec bars + EMA dev + NOT breakout"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.rsi14) or pd.isna(row.ema20_dev): return None
        # L: oversold + consecutive down + below EMA
        l_score = 0
        if row.rsi14 < 35: l_score += 1
        if row.consec_dn >= 2: l_score += 1
        if row.ema20_dev < -0.5: l_score += 1
        if l_score >= 2 and not row.is_brk_dn: sig += 'L'

        s_score = 0
        if row.rsi14 > 65: s_score += 1
        if row.consec_up >= 2: s_score += 1
        if row.ema20_dev > 0.5: s_score += 1
        if s_score >= 2 and not row.is_brk_up: sig += 'S'
        return sig or None
    return entry

# H: TBR change rate
def make_tbr_delta_dir(delta_thresh=0.05):
    """TBR rising = buying pressure increasing → L. Falling → S."""
    def entry(row, i):
        sig = ''
        td = row.tbr_delta
        if pd.isna(td): return None
        if td > delta_thresh and not row.is_brk_up: sig += 'L'
        if td < -delta_thresh and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_tbr_delta3_dir(delta_thresh=0.03):
    """3-bar TBR change"""
    def entry(row, i):
        sig = ''
        td = row.tbr_delta3
        if pd.isna(td): return None
        if td > delta_thresh and not row.is_brk_up: sig += 'L'
        if td < -delta_thresh and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# I: Flow interaction
def make_flow_momentum(vol_min=1.3, tbr_thresh=0.55):
    """High volume + buyer dominance → L. High volume + seller → S."""
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio) or pd.isna(row.tbr): return None
        if row.vol_ratio > vol_min and not row.is_brk:
            if row.tbr > tbr_thresh: sig += 'L'
            if row.tbr < (1 - tbr_thresh): sig += 'S'
        return sig or None
    return entry


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V18 R1: 30m Non-Breakout Signal Scan")
    print("=" * 70)
    print(f"Data: {n} bars (30m), split at {sp}")
    print(f"IS: {WARMUP}-{sp}, OOS: {sp}-{n}")
    print(f"Fee: ${FEE} = {FEE_PCT:.4f}% | TP L={0.025*100:.1f}%/S={0.015*100:.1f}% | MH L=12/S=20 bars")

    # Breakout frequency
    is_df = df.iloc[WARMUP:sp]
    brk_freq = is_df['is_brk'].mean() * 100
    print(f"Breakout bar frequency (IS): {brk_freq:.1f}%")

    signals = [
        # A: Mean reversion
        ("A1 ema_dev>0.5% MR",     make_ema_dev_mr(0.5)),
        ("A2 ema_dev>1.0% MR",     make_ema_dev_mr(1.0)),
        ("A3 ema_dev>1.5% MR",     make_ema_dev_mr(1.5)),
        ("A4 RSI 30/70 MR",        make_rsi_mr(30, 70)),
        ("A5 RSI 35/65 MR",        make_rsi_mr(35, 65)),
        ("A6 RSI 25/75 MR",        make_rsi_mr(25, 75)),
        ("A7 consec>=3 MR",        make_consec_mr(3)),
        ("A8 consec>=4 MR",        make_consec_mr(4)),

        # B: Trend continuation
        ("B1 slope>0.1+dir",       make_ema_slope_dir(0.1)),
        ("B2 slope>0.2+dir",       make_ema_slope_dir(0.2)),
        ("B3 trend+dir bar",       make_ema_trend_dir()),
        ("B4 ema5x20 cross",       make_ema5_cross()),
        ("B5 ema10x50 cross",      make_ema10x50_cross()),

        # C: Volume/TBR
        ("C1 vol>1.5+dir nobrk",   make_vol_dir(1.5)),
        ("C2 vol>2.0+dir nobrk",   make_vol_dir(2.0)),
        ("C3 tbr>0.58/<0.42",      make_tbr_dir(0.58, 0.42)),
        ("C4 tbr>0.55/<0.45",      make_tbr_dir(0.55, 0.45)),

        # D: Candle patterns
        ("D1 engulfing nobrk",     make_engulfing()),
        ("D2 pin bar nobrk",       make_pin_bar()),

        # E: Session
        ("E1 L{8-11} S{20-23}",    make_session({8,9,10,11}, {20,21,22,23})),
        ("E2 L{3-7} S{15-19}",     make_session({3,4,5,6,7}, {15,16,17,18,19})),
        ("E3 L{5-9} S{17-21}",     make_session({5,6,7,8,9}, {17,18,19,20,21})),

        # F: Volatility regime
        ("F1 GK<30+dir nobrk",     make_gk_compress_dir(30)),
        ("F2 GK<20+dir nobrk",     make_gk_compress_dir(20)),
        ("F3 ATR>1.3+dir nobrk",   make_atr_expand_dir(1.3)),
        ("F4 ATR>1.5+dir nobrk",   make_atr_expand_dir(1.5)),

        # G: Combined
        ("G1 multi-trend nobrk",   make_multi_trend_nobrk()),
        ("G2 MR multi-gate",       make_mr_multi()),

        # H: TBR delta
        ("H1 tbr_delta>0.05",      make_tbr_delta_dir(0.05)),
        ("H2 tbr_delta>0.08",      make_tbr_delta_dir(0.08)),
        ("H3 tbr_delta3>0.03",     make_tbr_delta3_dir(0.03)),
        ("H4 tbr_delta3>0.05",     make_tbr_delta3_dir(0.05)),

        # I: Flow interaction
        ("I1 flow vol1.3+tbr.55",  make_flow_momentum(1.3, 0.55)),
        ("I2 flow vol1.5+tbr.55",  make_flow_momentum(1.5, 0.55)),
        ("I3 flow vol1.3+tbr.58",  make_flow_momentum(1.3, 0.58)),
    ]

    # ── Section 1: IS Scan ──
    print("\n" + "=" * 70)
    print("Section 1: IS Scan (all signals, 30m V14-adapted exits)")
    print("=" * 70)
    header = f"  {'Signal':<28} | {'IS':<55} | {'brk%':>5}"
    print(header)
    print("  " + "-" * 28 + "-+-" + "-" * 55 + "-+-" + "-" * 5)

    is_positive = []
    for name, entry_fn in signals:
        tr = run_bt(df, WARMUP, sp, entry_fn)
        s = summarize(tr)
        bo = brk_pct(tr)
        marker = " <<<" if s['pnl'] > 0 and s['n'] >= 15 else ""
        print(f"  {name:<28} | {fmt(s):<55} | {bo:4.0f}%{marker}")
        if s['pnl'] > 0 and s['n'] >= 15:
            is_positive.append((name, entry_fn, s, bo))

    # ── Section 2: OOS Validation ──
    print(f"\n{'='*70}")
    print(f"Section 2: OOS Validation ({len(is_positive)} IS-positive)")
    print("=" * 70)

    if not is_positive:
        print("  No IS-positive signals. ALL FAILED on 30m.")
    else:
        header2 = f"  {'Signal':<28} | {'IS':<55} | {'OOS':<55} | {'brk%':>5}"
        print(header2)
        print("  " + "-" * 28 + "-+-" + "-" * 55 + "-+-" + "-" * 55 + "-+-" + "-" * 5)

        for name, entry_fn, s_is, bo_is in is_positive:
            tr_oos = run_bt(df, sp, n, entry_fn)
            s_oos = summarize(tr_oos)
            bo_oos = brk_pct(tr_oos)
            marker = " <<<" if s_oos['pnl'] > 0 else ""
            print(f"  {name:<28} | {fmt(s_is):<55} | {fmt(s_oos):<55} | {bo_oos:4.0f}%{marker}")

    # ── Section 3: Conclusion ──
    print(f"\n{'='*70}")
    print("Section 3: 30m Conclusion")
    print("=" * 70)
    print(f"""
  Total signals tested: {len(signals)}
  IS-positive (>=15 trades): {len(is_positive)}
  IS-positive rate: {len(is_positive)/len(signals)*100:.0f}%

  Breakout self-check:
  [x] No close > N-bar max/min used
  [x] No rpos > 0.85 used
  [x] All signals explicitly check NOT is_brk

  Fee analysis:
  All IS-positive signals show fee% in output.
  >70% = structurally not viable
  50-70% = marginal
  <50% = viable space exists
""")


if __name__ == "__main__":
    main()
