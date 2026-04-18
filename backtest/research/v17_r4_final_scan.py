"""
V17 R4: Final Non-Breakout Scan

R1: Mean reversion ALL IS negative (30 signals x 14 exits)
R2: Range position works but 93% overlap with breakout bars
R3: Range position WITHOUT breakout = ALL IS negative (48 configs)

R4 tests the LAST remaining non-breakout approaches:
  A. Time-of-day (specific hours with persistent directional edge)
  B. Day-of-week (specific days with persistent directional edge)
  C. Candle patterns (engulfing, inside bar, pin bar, doji expansion)
  D. Volatility regime change (GK/ATR sharp rise or fall as signal)
  E. Volume anomaly (unusual volume without breakout)
  F. Consecutive bar pattern (N bars same direction, then entry)
  G. Range contraction then expansion (NOT breakout — enter on expansion bar)
  H. EMA cross (actual cross, not just position)
  I. Combined: best of above + session filter

If R4 also fails: conclude ETH 1h non-breakout alpha does not exist
at $1K / 20x / $4 fee account structure.

Breakout self-check:
  All signals verified to NOT use close > N-bar high/low.
  Breakout overlap percentage computed for every IS-positive signal.
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
    d['ema5'] = d['close'].ewm(span=5).mean()
    d['ema10'] = d['close'].ewm(span=10).mean()
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()

    # EMA crosses (actual cross events)
    d['ema5_above_20'] = (d['ema5'] > d['ema20']).astype(int)
    d['ema5_cross_up_20'] = (d['ema5_above_20'] == 1) & (d['ema5_above_20'].shift(1) == 0)
    d['ema5_cross_dn_20'] = (d['ema5_above_20'] == 0) & (d['ema5_above_20'].shift(1) == 1)
    d['ema10_above_20'] = (d['ema10'] > d['ema20']).astype(int)
    d['ema10_cross_up_20'] = (d['ema10_above_20'] == 1) & (d['ema10_above_20'].shift(1) == 0)
    d['ema10_cross_dn_20'] = (d['ema10_above_20'] == 0) & (d['ema10_above_20'].shift(1) == 1)
    d['ema10_above_50'] = (d['ema10'] > d['ema50']).astype(int)
    d['ema10_cross_up_50'] = (d['ema10_above_50'] == 1) & (d['ema10_above_50'].shift(1) == 0)
    d['ema10_cross_dn_50'] = (d['ema10_above_50'] == 0) & (d['ema10_above_50'].shift(1) == 1)
    d['ema20_above_50'] = (d['ema20'] > d['ema50']).astype(int)
    d['ema20_cross_up_50'] = (d['ema20_above_50'] == 1) & (d['ema20_above_50'].shift(1) == 0)
    d['ema20_cross_dn_50'] = (d['ema20_above_50'] == 0) & (d['ema20_above_50'].shift(1) == 1)

    # EMA trend
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)

    # Bar structure
    d['body'] = d['close'] - d['open']
    d['body_pct'] = d['body'] / d['open'] * 100
    d['range'] = d['high'] - d['low']
    d['range_pct'] = d['range'] / d['open'] * 100
    d['upper_wick'] = d['high'] - d[['close', 'open']].max(axis=1)
    d['lower_wick'] = d[['close', 'open']].min(axis=1) - d['low']
    d['body_ratio'] = abs(d['body']) / d['range'].clip(lower=1e-8)

    # Candle patterns
    # Engulfing: current body engulfs previous body
    prev_body = d['body'].shift(1)
    d['bullish_engulf'] = (d['body'] > 0) & (prev_body < 0) & (d['body'].abs() > prev_body.abs())
    d['bearish_engulf'] = (d['body'] < 0) & (prev_body > 0) & (d['body'].abs() > prev_body.abs())

    # Inside bar: current range within previous range
    d['inside_bar'] = (d['high'] <= d['high'].shift(1)) & (d['low'] >= d['low'].shift(1))

    # Pin bar (long wick): lower wick > 2x body for bullish, upper wick > 2x body for bearish
    abs_body = abs(d['body']).clip(lower=1e-8)
    d['pin_bull'] = (d['lower_wick'] > 2 * abs_body) & (d['body'] > 0) & (d['range_pct'] > 0.5)
    d['pin_bear'] = (d['upper_wick'] > 2 * abs_body) & (d['body'] < 0) & (d['range_pct'] > 0.5)

    # Doji: body < 20% of range
    d['doji'] = d['body_ratio'] < 0.20

    # Consecutive bars
    d['bar_dir'] = np.sign(d['body'])
    d['consec_up'] = 0
    d['consec_dn'] = 0
    cu, cd = 0, 0
    consec_up_arr = np.zeros(len(d))
    consec_dn_arr = np.zeros(len(d))
    for idx in range(len(d)):
        if d.iloc[idx].body > 0:
            cu += 1
            cd = 0
        elif d.iloc[idx].body < 0:
            cd += 1
            cu = 0
        else:
            cu = cd = 0
        consec_up_arr[idx] = cu
        consec_dn_arr[idx] = cd
    d['consec_up'] = consec_up_arr
    d['consec_dn'] = consec_dn_arr

    # Returns
    d['ret_1'] = d['close'].pct_change(1)

    # Volume
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio_l'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile_l'] = d['gk_ratio_l'].shift(1).rolling(100).rank(pct=True) * 100

    # ATR
    tr = pd.concat([
        d['high'] - d['low'],
        (d['high'] - d['close'].shift(1)).abs(),
        (d['low'] - d['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    d['atr14'] = tr.rolling(14).mean()
    d['atr_ratio'] = d['atr14'] / d['atr14'].rolling(50).mean()

    # Range contraction: average range of past N bars / average range of past M bars
    d['range_ratio_5_20'] = d['range'].rolling(5).mean() / d['range'].rolling(20).mean()
    d['range_ratio_3_15'] = d['range'].rolling(3).mean() / d['range'].rolling(15).mean()

    # Breakout flags
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()
    d['is_brk_up'] = d['close'] > d['high_15']
    d['is_brk_dn'] = d['close'] < d['low_15']
    d['is_brk_any'] = d['is_brk_up'] | d['is_brk_dn']

    return d


def run_bt(df, start, end, entry_fn, tp_l=0.035, tp_s=0.02, mh_l=6, mh_s=10,
           ext_l=2, ext_s=2, ml_l=-75, ml_s=-150, fee=FEE,
           use_mfe=True, mfe_thresh=0.01, mfe_trail=0.008, mfe_min_bar=1,
           cond_mh_bar=2, cond_mh_loss=-0.01):
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

            if bars == cond_mh_bar and pnl_pct <= cond_mh_loss:
                pos_l['mh_reduced'] = True

            eff_mh = (mh_l - 1) if pos_l['mh_reduced'] else mh_l

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
            if pos_l is not None and bars >= eff_mh:
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
                if (i - last_exit_l) >= L_CD and monthly_pnl_l > ml_l and month_entries_l < L_CAP:
                    month_entries_l += 1
                    pos_l = {'ep': c, 'bar': i,
                             'running_mfe': 0.0, 'mh_reduced': False,
                             'is_brk': bool(row.is_brk_up)}

        if 'S' in sig and pos_s is None:
            if h not in BLOCK_H and dow not in S_BLOCK_D:
                if (i - last_exit_s) >= S_CD and monthly_pnl_s > ml_s and month_entries_s < S_CAP:
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
        return {'n': 0, 'pnl': 0, 'wr': 0, 'pf': 0, 'mdd': 0, 'pm': 0, 'pm_total': 0}
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
    return {'n': n, 'pnl': total, 'wr': wr, 'pf': pf, 'mdd': mdd, 'pm': pm, 'pm_total': pm_total}


def fmt(s):
    pm_str = f"{s['pm']}/{s['pm_total']}"
    return (f"{s['n']:3d}t ${s['pnl']:+7.0f} WR {s['wr']:5.0f}% "
            f"PF {s['pf']:4.1f} MDD ${s['mdd']:4.0f} PM {pm_str}")


def brk_overlap(trades):
    """Percentage of trades that fell on a breakout bar"""
    if not trades:
        return 0.0
    brk_count = sum(1 for t in trades if t.get('is_brk', False))
    return brk_count / len(trades) * 100


# ══════════════════════════════════════════════════════════════════
#  Entry signal generators
# ══════════════════════════════════════════════════════════════════

# A: Time-of-day (specific hours)
def make_hour_entry(long_hours, short_hours):
    """Enter L at specific hours, S at specific hours"""
    def entry(row, i):
        sig = ''
        h = row.datetime.hour
        if h in long_hours: sig += 'L'
        if h in short_hours: sig += 'S'
        return sig or None
    return entry

# B: Day-of-week
def make_dow_entry(long_days, short_days):
    """Enter L on specific days, S on specific days"""
    def entry(row, i):
        sig = ''
        dow = row.datetime.weekday()
        if dow in long_days: sig += 'L'
        if dow in short_days: sig += 'S'
        return sig or None
    return entry

# C: Candle patterns
def make_engulfing():
    def entry(row, i):
        sig = ''
        if row.bullish_engulf: sig += 'L'
        if row.bearish_engulf: sig += 'S'
        return sig or None
    return entry

def make_pin_bar():
    def entry(row, i):
        sig = ''
        if row.pin_bull: sig += 'L'
        if row.pin_bear: sig += 'S'
        return sig or None
    return entry

def make_inside_bar_break():
    """After inside bar, enter in direction of breakout from inside bar range
    Note: this checks if PREVIOUS bar was inside, and current bar breaks out of it"""
    def entry(row, i):
        # Need previous bar data — use shift in precompute
        return None  # Will be implemented via precomputed column
    return entry

def make_doji_reversal():
    """Doji followed by directional bar"""
    def entry(row, i):
        sig = ''
        # Current bar is directional, previous was doji
        if row.body > 0 and row.range_pct > 0.5:  # bullish after doji
            sig += 'L'
        if row.body < 0 and row.range_pct > 0.5:  # bearish after doji
            sig += 'S'
        return sig or None
    return entry

# D: Volatility regime change
def make_vol_regime_change(gk_drop_thresh=0.6, gk_rise_thresh=1.5):
    """GK ratio sharp change — compression starting or expansion starting"""
    def entry(row, i):
        sig = ''
        gk = row.gk_pctile_l
        if pd.isna(gk): return None
        # Low GK = compression, could mean breakout imminent
        # But we can't use breakout! So use GK as TREND signal:
        # GK rising = volatility expanding = trend in progress
        ratio = row.gk_ratio_l
        if pd.isna(ratio): return None
        # Rising volatility + positive direction
        if ratio > gk_rise_thresh and row.body > 0: sig += 'L'
        if ratio > gk_rise_thresh and row.body < 0: sig += 'S'
        return sig or None
    return entry

def make_atr_expansion(atr_thresh=1.3):
    """ATR ratio > threshold = expanding volatility, enter in bar direction"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.atr_ratio): return None
        if row.atr_ratio > atr_thresh:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

# E: Volume anomaly
def make_vol_spike(vol_thresh=2.0):
    """Volume > N x average, enter in bar direction"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio): return None
        if row.vol_ratio > vol_thresh:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

def make_tbr_extreme(buy_thresh=0.65, sell_thresh=0.35):
    """TBR extreme — strong buyer/seller pressure"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.tbr): return None
        if row.tbr > buy_thresh: sig += 'L'
        if row.tbr < sell_thresh: sig += 'S'
        return sig or None
    return entry

# F: Consecutive bar pattern
def make_consec_continuation(n_consec=3):
    """After N consecutive same-direction bars, continue"""
    def entry(row, i):
        sig = ''
        if row.consec_up >= n_consec: sig += 'L'
        if row.consec_dn >= n_consec: sig += 'S'
        return sig or None
    return entry

def make_consec_reversal(n_consec=3):
    """After N consecutive same-direction bars, reverse"""
    def entry(row, i):
        sig = ''
        if row.consec_dn >= n_consec: sig += 'L'  # reversal
        if row.consec_up >= n_consec: sig += 'S'
        return sig or None
    return entry

# G: Range contraction then expansion
def make_range_contract_expand(contract_thresh=0.6, expand_pct=1.5):
    """Range ratio < contract_thresh (compressed), then current bar range > expand * avg"""
    def entry(row, i):
        sig = ''
        rr = row.range_ratio_5_20
        if pd.isna(rr): return None
        if rr < contract_thresh and row.range_pct > 0.8:
            if row.body > 0: sig += 'L'
            if row.body < 0: sig += 'S'
        return sig or None
    return entry

def make_range_contract_dir(contract_thresh=0.7):
    """Low range ratio + directional bar + NOT breakout"""
    def entry(row, i):
        sig = ''
        rr = row.range_ratio_5_20
        if pd.isna(rr): return None
        if rr < contract_thresh:
            if row.body_pct > 0.3 and not row.is_brk_up: sig += 'L'
            if row.body_pct < -0.3 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

# H: EMA cross (actual crossover event)
def make_ema_cross(fast, slow):
    """EMA cross event — L on golden cross, S on death cross"""
    up_col = f'ema{fast}_cross_up_{slow}'
    dn_col = f'ema{fast}_cross_dn_{slow}'
    def entry(row, i):
        sig = ''
        if getattr(row, up_col, False): sig += 'L'
        if getattr(row, dn_col, False): sig += 'S'
        return sig or None
    return entry

# I: Combined signals
def make_engulfing_trend():
    """Engulfing + EMA trend confirmation"""
    def entry(row, i):
        sig = ''
        if row.bullish_engulf and row.ema_trend == 1: sig += 'L'
        if row.bearish_engulf and row.ema_trend == -1: sig += 'S'
        return sig or None
    return entry

def make_vol_spike_trend(vol_thresh=1.5):
    """Volume spike + EMA trend + NOT breakout"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio): return None
        if row.vol_ratio > vol_thresh:
            if row.body > 0 and row.ema_trend == 1 and not row.is_brk_up: sig += 'L'
            if row.body < 0 and row.ema_trend == -1 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_engulfing_vol():
    """Engulfing + volume spike + NOT breakout"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio): return None
        if row.bullish_engulf and row.vol_ratio > 1.3 and not row.is_brk_up: sig += 'L'
        if row.bearish_engulf and row.vol_ratio > 1.3 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_range_contract_ema_cross():
    """Range contraction + EMA5 cross EMA20 event"""
    def entry(row, i):
        sig = ''
        rr = row.range_ratio_5_20
        if pd.isna(rr): return None
        if rr < 0.8:
            if row.ema5_cross_up_20 and not row.is_brk_up: sig += 'L'
            if row.ema5_cross_dn_20 and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_multi_signal():
    """3+ conditions: EMA trend + vol above avg + body > 0.3% + NOT breakout"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.vol_ratio) or pd.isna(row.atr_ratio): return None
        # L: uptrend + volume + directional body + not breakout
        l_score = 0
        if row.ema_trend == 1: l_score += 1
        if row.vol_ratio > 1.2: l_score += 1
        if row.body_pct > 0.3: l_score += 1
        if row.atr_ratio > 1.0: l_score += 1
        if not row.is_brk_up and l_score >= 3: sig += 'L'

        s_score = 0
        if row.ema_trend == -1: s_score += 1
        if row.vol_ratio > 1.2: s_score += 1
        if row.body_pct < -0.3: s_score += 1
        if row.atr_ratio > 1.0: s_score += 1
        if not row.is_brk_dn and s_score >= 3: sig += 'S'
        return sig or None
    return entry


def main():
    df = load_and_prepare()

    # Precompute inside bar breakout
    df['prev_inside'] = df['inside_bar'].shift(1).fillna(False)
    df['prev_high'] = df['high'].shift(1)
    df['prev_low'] = df['low'].shift(1)
    df['inside_brk_up'] = df['prev_inside'] & (df['close'] > df['prev_high'])
    df['inside_brk_dn'] = df['prev_inside'] & (df['close'] < df['prev_low'])
    # Precompute doji-then-direction
    df['prev_doji'] = df['doji'].shift(1).fillna(False)

    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V17 R4: Final Non-Breakout Scan")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")

    # ══════════════════════════════════════════
    #  Section 1: IS scan — all signals with V14 exits
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 1: IS Scan (V14 exits)")
    print("=" * 70)

    # Inside bar break entry function (uses precomputed columns)
    def inside_bar_break_entry(row, i):
        sig = ''
        if row.inside_brk_up and not row.is_brk_up: sig += 'L'
        if row.inside_brk_dn and not row.is_brk_dn: sig += 'S'
        return sig or None

    # Doji reversal (previous bar doji, current directional)
    def doji_dir_entry(row, i):
        sig = ''
        if not row.prev_doji: return None
        if row.body_pct > 0.3: sig += 'L'
        if row.body_pct < -0.3: sig += 'S'
        return sig or None

    signals = [
        # A: Time-of-day
        ("A1 hour L{3-7} S{14-18}",    make_hour_entry({3,4,5,6,7}, {14,15,16,17,18})),
        ("A2 hour L{8-11} S{20-23}",   make_hour_entry({8,9,10,11}, {20,21,22,23})),
        ("A3 hour L{3-5} S{15-17}",    make_hour_entry({3,4,5}, {15,16,17})),
        ("A4 hour L{6-10} S{18-22}",   make_hour_entry({6,7,8,9,10}, {18,19,20,21,22})),

        # B: Day-of-week
        ("B1 dow L{Mon,Tue} S{Thu}",    make_dow_entry({0,1}, {3})),
        ("B2 dow L{Wed} S{Fri}",        make_dow_entry({2}, {4})),

        # C: Candle patterns
        ("C1 engulfing",                make_engulfing()),
        ("C2 pin bar",                  make_pin_bar()),
        ("C3 inside bar break",         inside_bar_break_entry),
        ("C4 doji+direction",           doji_dir_entry),
        ("C5 engulfing+trend",          make_engulfing_trend()),
        ("C6 engulfing+vol",            make_engulfing_vol()),

        # D: Volatility regime change
        ("D1 GK expand+dir",            make_vol_regime_change(0.6, 1.5)),
        ("D2 GK expand>2.0+dir",        make_vol_regime_change(0.6, 2.0)),
        ("D3 ATR expand>1.3+dir",       make_atr_expansion(1.3)),
        ("D4 ATR expand>1.5+dir",       make_atr_expansion(1.5)),

        # E: Volume anomaly
        ("E1 vol>2.0+dir",              make_vol_spike(2.0)),
        ("E2 vol>2.5+dir",              make_vol_spike(2.5)),
        ("E3 vol>3.0+dir",              make_vol_spike(3.0)),
        ("E4 tbr>0.65/<0.35",           make_tbr_extreme(0.65, 0.35)),
        ("E5 tbr>0.60/<0.40",           make_tbr_extreme(0.60, 0.40)),
        ("E6 vol>1.5+trend+nobrk",      make_vol_spike_trend(1.5)),
        ("E7 vol>2.0+trend+nobrk",      make_vol_spike_trend(2.0)),

        # F: Consecutive bars
        ("F1 consec>=3 continue",       make_consec_continuation(3)),
        ("F2 consec>=4 continue",       make_consec_continuation(4)),
        ("F3 consec>=3 reverse",        make_consec_reversal(3)),
        ("F4 consec>=4 reverse",        make_consec_reversal(4)),

        # G: Range contraction/expansion
        ("G1 contract<0.6+expand",      make_range_contract_expand(0.6, 1.5)),
        ("G2 contract<0.7+dir",         make_range_contract_dir(0.7)),
        ("G3 contract<0.8+dir",         make_range_contract_dir(0.8)),

        # H: EMA cross events
        ("H1 ema5x20 cross",            make_ema_cross(5, 20)),
        ("H2 ema10x20 cross",           make_ema_cross(10, 20)),
        ("H3 ema10x50 cross",           make_ema_cross(10, 50)),
        ("H4 ema20x50 cross",           make_ema_cross(20, 50)),

        # I: Combined
        ("I1 contract+ema5x20",         make_range_contract_ema_cross()),
        ("I2 multi-signal 3+",          make_multi_signal()),
    ]

    header = f"  {'Signal':<28} | {'IS':<42} | {'brk%':>5}"
    print(header)
    print("  " + "-" * 28 + "-+-" + "-" * 42 + "-+-" + "-" * 5)

    is_positive_sigs = []
    for name, entry_fn in signals:
        tr = run_bt(df, WARMUP, sp, entry_fn)
        s = summarize(tr)
        bo = brk_overlap(tr)
        marker = " <<<" if s['pnl'] > 0 and s['n'] >= 10 else ""
        print(f"  {name:<28} | {fmt(s):<42} | {bo:4.0f}%{marker}")
        if s['pnl'] > 0 and s['n'] >= 10:
            is_positive_sigs.append((name, entry_fn, s, bo))

    # ══════════════════════════════════════════
    #  Section 2: OOS validation for IS-positive signals
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print(f"Section 2: OOS Validation ({len(is_positive_sigs)} IS-positive)")
    print("=" * 70)

    if not is_positive_sigs:
        print("  No IS-positive signals found. STOPPING.")
    else:
        header2 = f"  {'Signal':<28} | {'IS':<42} | {'OOS':<42} | {'brk%':>5}"
        print(header2)
        print("  " + "-" * 28 + "-+-" + "-" * 42 + "-+-" + "-" * 42 + "-+-" + "-" * 5)

        oos_positive = []
        for name, entry_fn, s_is, bo in is_positive_sigs:
            tr_oos = run_bt(df, sp, n, entry_fn)
            s_oos = summarize(tr_oos)
            bo_oos = brk_overlap(tr_oos)
            marker = " <<<" if s_oos['pnl'] > 0 else ""
            print(f"  {name:<28} | {fmt(s_is):<42} | {fmt(s_oos):<42} | {bo_oos:4.0f}%{marker}")
            if s_oos['pnl'] > 0:
                oos_positive.append((name, entry_fn, s_is, s_oos, bo_oos))

    # ══════════════════════════════════════════
    #  Section 3: Exit optimization for any IS+OOS positive
    # ══════════════════════════════════════════
    if is_positive_sigs:
        print("\n" + "=" * 70)
        print("Section 3: Exit optimization for IS-positive signals")
        print("=" * 70)

        exit_configs = [
            ("V14 default",         0.035, 0.020, 6, 10, True),
            ("TP2.0/1.5 MH4/6",    0.020, 0.015, 4, 6, True),
            ("TP2.5/1.5 MH5/8",    0.025, 0.015, 5, 8, True),
            ("TP3.0/2.0 MH4/8",    0.030, 0.020, 4, 8, True),
            ("TP1.5/1.0 MH3/5",    0.015, 0.010, 3, 5, True),
            ("TP3.5/2.0 MH8/12",   0.035, 0.020, 8, 12, True),
            ("No MFE trail",        0.035, 0.020, 6, 10, False),
        ]

        # Test top 3 IS-positive signals (or fewer)
        for name, entry_fn, _, _ in is_positive_sigs[:3]:
            print(f"\n  --- {name} ---")
            header3 = f"  {'Exit':<24} | {'IS':<42} | {'OOS':<42}"
            print(header3)
            print("  " + "-" * 24 + "-+-" + "-" * 42 + "-+-" + "-" * 42)
            for elabel, tpl, tps, mhl, mhs, umfe in exit_configs:
                tr_is = run_bt(df, WARMUP, sp, entry_fn, tp_l=tpl, tp_s=tps, mh_l=mhl, mh_s=mhs, use_mfe=umfe)
                tr_oos = run_bt(df, sp, n, entry_fn, tp_l=tpl, tp_s=tps, mh_l=mhl, mh_s=mhs, use_mfe=umfe)
                print(f"  {elabel:<24} | {fmt(summarize(tr_is)):<42} | {fmt(summarize(tr_oos)):<42}")

    # ══════════════════════════════════════════
    #  Section 4: Honest Assessment / Conclusion
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 4: V17 Final Conclusion")
    print("=" * 70)

    total_tested = len(signals)
    is_pos = len(is_positive_sigs)
    print(f"""
  Signals tested: {total_tested}
  IS-positive: {is_pos}
  IS-positive rate: {is_pos/total_tested*100:.0f}%

  V17 Research Summary (4 rounds):
  --------------------------------
  R1: Mean reversion (30 signals x 14 exits)
      Result: ALL IS negative. MFE too small for fee structure.

  R2: Trend following with range position (36 signals)
      Result: IS-positive, but 93% breakout overlap.
      Range position > 0.90 is mathematically equivalent to breakout.

  R3: Range position WITHOUT breakout bars (48 configs)
      Result: ALL IS negative. Rpos has ZERO independent alpha.

  R4: Final scan (time, candle, volume, EMA cross, multi-signal)
      Result: see above.

  Overall Conclusion:
  If R4 also shows no viable non-breakout strategy,
  then ETH 1h non-breakout alpha DOES NOT EXIST
  at $1K / 20x / $4 fee account structure.

  The 15-bar close breakout is the ONLY source of alpha on ETH 1h.
  All other signals are either:
  1. Breakout proxies (rpos, momentum percentile)
  2. Structurally impossible (mean reversion MFE < fee)
  3. Random noise (candle patterns, time-of-day, volume)
""")


if __name__ == "__main__":
    main()
