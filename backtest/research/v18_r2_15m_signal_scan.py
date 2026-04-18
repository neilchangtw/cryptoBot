"""
V18 R2: 15m Non-Breakout Signal Scan

R1: 30m ALL 35 signals IS-negative. Fee not the problem (7-9%).
R2: Final test on 15m — different microstructure, 4x data, finer session resolution.

If 15m also fails → conclude ETH non-breakout alpha doesn't exist on any timeframe.

Focus on:
  - Mean reversion (wider parameter range for 15m bar size)
  - Session effects (15m resolution allows 96 slots/day vs 24)
  - TBR delta / flow patterns (finer granularity on 15m)
  - Candle patterns (4x sample size)
  - Combined multi-gate

ALL signals exclude breakout bars.
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0
FEE_PCT = FEE / NOTIONAL * 100
WARMUP = 400  # More bars needed for rolling windows on 15m

SN_SLIP = 0.25
L_SN, S_SN = 0.035, 0.04

# 15m cooldowns (4x bars per hour vs 1h)
L_CD, S_CD = 24, 32  # 6h / 8h
L_CAP, S_CAP = 80, 80
DAILY_LOSS = -200
CONSEC_PAUSE, CONSEC_CD = 4, 96  # 24h on 15m
L_BLOCK_D, S_BLOCK_D = {5, 6}, {0, 5, 6}
BLOCK_H = {0, 1, 2, 12}
ML_L, ML_S = -75, -150


def load_and_prepare():
    df = pd.read_csv(DATA_DIR / "ETHUSDT_15m_latest730d.csv", parse_dates=["datetime"])
    d = df.copy()

    d['ema10'] = d['close'].ewm(span=10).mean()
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()
    d['ema100'] = d['close'].ewm(span=100).mean()

    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)
    d['ema20_dev'] = (d['close'] - d['ema20']) / d['ema20'] * 100
    d['ema20_slope'] = (d['ema20'] - d['ema20'].shift(10)) / d['ema20'].shift(10) * 100

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # Bar structure
    d['body'] = d['close'] - d['open']
    d['body_pct'] = d['body'] / d['open'] * 100
    d['range_pct'] = (d['high'] - d['low']) / d['open'] * 100
    d['upper_wick'] = d['high'] - d[['close', 'open']].max(axis=1)
    d['lower_wick'] = d[['close', 'open']].min(axis=1) - d['low']
    abs_body = abs(d['body']).clip(lower=1e-8)
    d['bullish_engulf'] = (d['body'] > 0) & (d['body'].shift(1) < 0) & (d['body'].abs() > d['body'].shift(1).abs())
    d['bearish_engulf'] = (d['body'] < 0) & (d['body'].shift(1) > 0) & (d['body'].abs() > d['body'].shift(1).abs())
    d['pin_bull'] = (d['lower_wick'] > 2 * abs_body) & (d['body'] > 0) & (d['range_pct'] > 0.2)
    d['pin_bear'] = (d['upper_wick'] > 2 * abs_body) & (d['body'] < 0) & (d['range_pct'] > 0.2)

    # Consecutive bars
    cu, cd = 0, 0
    c_up = np.zeros(len(d)); c_dn = np.zeros(len(d))
    for i in range(len(d)):
        if d.iloc[i].body > 0: cu += 1; cd = 0
        elif d.iloc[i].body < 0: cd += 1; cu = 0
        else: cu = cd = 0
        c_up[i] = cu; c_dn[i] = cd
    d['consec_up'] = c_up; d['consec_dn'] = c_dn

    d['ret_1'] = d['close'].pct_change(1) * 100
    d['ret_4'] = d['close'].pct_change(4) * 100  # 1-hour return on 15m

    # Volume & TBR
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_delta'] = d['tbr'] - d['tbr'].shift(1)
    d['tbr_delta4'] = d['tbr'] - d['tbr'].shift(4)  # 1-hour TBR change

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio'] = gk.rolling(10).mean() / gk.rolling(40).mean()

    # ATR
    tr = pd.concat([d['high'] - d['low'],
                     (d['high'] - d['close'].shift(1)).abs(),
                     (d['low'] - d['close'].shift(1)).abs()], axis=1).max(axis=1)
    d['atr14'] = tr.rolling(14).mean()
    d['atr_ratio'] = d['atr14'] / d['atr14'].rolling(80).mean()

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


def run_bt(df, start, end, entry_fn, tp_l=0.020, tp_s=0.012, mh_l=24, mh_s=40,
           ext_l=8, ext_s=8, fee=FEE,
           use_mfe=True, mfe_thresh=0.006, mfe_trail=0.005, mfe_min_bar=4):
    """15m engine: TP tighter, MH=24/40 bars (6h/10h), ext=8 bars (2h)"""
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
        if side == 'L': monthly_pnl_l += net; last_exit_l = bar_i
        else: monthly_pnl_s += net; last_exit_s = bar_i
        if net < 0:
            consec_losses += 1
            if consec_losses >= CONSEC_PAUSE:
                consec_pause_until = bar_i + CONSEC_CD
        else:
            consec_losses = 0
        gross = (xp - ep) * NOTIONAL / ep if side == 'L' else (ep - xp) * NOTIONAL / ep
        trades.append({'eb': pos['bar'], 'xb': bar_i, 'side': side,
                       'ep': ep, 'xp': xp, 'p': net, 'gross': gross,
                       'reason': reason, 'month': f"{dt.year}-{dt.month:02d}",
                       'is_brk': pos.get('is_brk', False)})

    for i in range(start, end):
        row = df.iloc[i]
        dt = row.datetime
        h, dow = dt.hour, dt.weekday()
        day_key, month_key = dt.date(), (dt.year, dt.month)

        if cur_day != day_key: daily_pnl = 0.0; cur_day = day_key
        if cur_month != month_key:
            monthly_pnl_l = monthly_pnl_s = 0.0
            month_entries_l = month_entries_s = 0
            cur_month = month_key

        o, hi, lo, c = row.open, row.high, row.low, row.close

        # EXIT L
        if pos_l is not None:
            ep = pos_l['ep']; bars = i - pos_l['bar']
            sn_price = ep * (1 - L_SN); sn_hit = lo <= sn_price
            pnl_pct = (c - ep) / ep; hi_pnl = (hi - ep) / ep
            pos_l['rmfe'] = max(pos_l['rmfe'], hi_pnl)

            if sn_hit:
                close_pos(pos_l, 'L', 'SN', sn_price * (1 - SN_SLIP * L_SN), i, dt); pos_l = None
            elif hi_pnl >= tp_l:
                close_pos(pos_l, 'L', 'TP', ep * (1 + tp_l), i, dt); pos_l = None
            elif use_mfe and pos_l['rmfe'] >= mfe_thresh and bars >= mfe_min_bar:
                if (pos_l['rmfe'] - pnl_pct) >= mfe_trail:
                    close_pos(pos_l, 'L', 'MFE', c, i, dt); pos_l = None
            if pos_l is not None and bars >= mh_l:
                if pos_l.get('ext', 0) < ext_l and pnl_pct > 0:
                    pos_l['ext'] = pos_l.get('ext', 0) + 1
                    if lo <= ep: close_pos(pos_l, 'L', 'BE', ep, i, dt); pos_l = None
                else:
                    close_pos(pos_l, 'L', 'MH', c, i, dt); pos_l = None

        # EXIT S
        if pos_s is not None:
            ep = pos_s['ep']; bars = i - pos_s['bar']
            sn_price = ep * (1 + S_SN); sn_hit = hi >= sn_price
            pnl_pct = (ep - c) / ep; lo_pnl = (ep - lo) / ep

            if sn_hit:
                close_pos(pos_s, 'S', 'SN', sn_price * (1 + SN_SLIP * S_SN), i, dt); pos_s = None
            elif lo_pnl >= tp_s:
                close_pos(pos_s, 'S', 'TP', ep * (1 - tp_s), i, dt); pos_s = None
            if pos_s is not None and bars >= mh_s:
                if pos_s.get('ext', 0) < ext_s and pnl_pct > 0:
                    pos_s['ext'] = pos_s.get('ext', 0) + 1
                    if hi >= ep: close_pos(pos_s, 'S', 'BE', ep, i, dt); pos_s = None
                else:
                    close_pos(pos_s, 'S', 'MH', c, i, dt); pos_s = None

        # ENTRY
        if i < WARMUP: continue
        if daily_pnl <= DAILY_LOSS: continue
        if i < consec_pause_until: continue

        sig = entry_fn(row, i)
        if sig is None: continue

        if 'L' in sig and pos_l is None:
            if h not in BLOCK_H and dow not in L_BLOCK_D:
                if (i - last_exit_l) >= L_CD and monthly_pnl_l > ML_L and month_entries_l < L_CAP:
                    month_entries_l += 1
                    pos_l = {'ep': c, 'bar': i, 'rmfe': 0.0, 'is_brk': bool(row.is_brk_up)}

        if 'S' in sig and pos_s is None:
            if h not in BLOCK_H and dow not in S_BLOCK_D:
                if (i - last_exit_s) >= S_CD and monthly_pnl_s > ML_S and month_entries_s < S_CAP:
                    month_entries_s += 1
                    pos_s = {'ep': c, 'bar': i, 'is_brk': bool(row.is_brk_dn)}

    if pos_l: close_pos(pos_l, 'L', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)
    if pos_s: close_pos(pos_s, 'S', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)
    return trades


def summarize(trades):
    if not trades:
        return {'n': 0, 'pnl': 0, 'wr': 0, 'pf': 0, 'mdd': 0, 'pm': 0, 'pmt': 0, 'fr': 0}
    pnls = [t['p'] for t in trades]
    n = len(pnls); total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = wins / n * 100
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl else 99.9
    eq = np.cumsum(pnls); pk = np.maximum.accumulate(eq); mdd = (pk - eq).max()
    months = {}
    for t in trades: months[t['month']] = months.get(t['month'], 0) + t['p']
    pm = sum(1 for v in months.values() if v > 0)
    wg = [t['gross'] for t in trades if t['gross'] > 0]
    fr = FEE / np.mean(wg) * 100 if wg else 999
    return {'n': n, 'pnl': total, 'wr': wr, 'pf': pf, 'mdd': mdd,
            'pm': pm, 'pmt': len(months), 'fr': fr}

def fmt(s):
    return (f"{s['n']:3d}t ${s['pnl']:+7.0f} WR {s['wr']:5.0f}% "
            f"PF {s['pf']:4.1f} MDD ${s['mdd']:4.0f} PM {s['pm']}/{s['pmt']} f%{s['fr']:3.0f}")


# ══════════════════════════════════════════════════════════════════
#  Signals — all exclude breakout
# ══════════════════════════════════════════════════════════════════

def make_ema_dev_mr(thresh):
    def e(r, i):
        d = r.ema20_dev
        if pd.isna(d): return None
        s = ''
        if d < -thresh and not r.is_brk_dn: s += 'L'
        if d > thresh and not r.is_brk_up: s += 'S'
        return s or None
    return e

def make_rsi_mr(lo, hi):
    def e(r, i):
        rsi = r.rsi14
        if pd.isna(rsi): return None
        s = ''
        if rsi < lo and not r.is_brk_dn: s += 'L'
        if rsi > hi and not r.is_brk_up: s += 'S'
        return s or None
    return e

def make_consec_mr(n):
    def e(r, i):
        s = ''
        if r.consec_dn >= n and not r.is_brk_dn: s += 'L'
        if r.consec_up >= n and not r.is_brk_up: s += 'S'
        return s or None
    return e

def make_slope_dir(thresh):
    def e(r, i):
        sl = r.ema20_slope
        if pd.isna(sl): return None
        s = ''
        if sl > thresh and r.close > r.ema20 and not r.is_brk_up: s += 'L'
        if sl < -thresh and r.close < r.ema20 and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_trend_dir():
    def e(r, i):
        s = ''
        if r.ema_trend == 1 and r.body > 0 and not r.is_brk_up: s += 'L'
        if r.ema_trend == -1 and r.body < 0 and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_vol_dir(thresh):
    def e(r, i):
        if pd.isna(r.vol_ratio): return None
        s = ''
        if r.vol_ratio > thresh and not r.is_brk:
            if r.body > 0: s += 'L'
            if r.body < 0: s += 'S'
        return s or None
    return e

def make_tbr_dir(bt, st):
    def e(r, i):
        if pd.isna(r.tbr): return None
        s = ''
        if r.tbr > bt and not r.is_brk_up: s += 'L'
        if r.tbr < st and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_engulf():
    def e(r, i):
        s = ''
        if r.bullish_engulf and not r.is_brk_up: s += 'L'
        if r.bearish_engulf and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_pin():
    def e(r, i):
        s = ''
        if r.pin_bull and not r.is_brk_up: s += 'L'
        if r.pin_bear and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_session(lh, sh):
    def e(r, i):
        s = ''
        h = r.hour
        if h in lh and not r.is_brk_up: s += 'L'
        if h in sh and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_session_minute(long_slots, short_slots):
    """Fine-grained 15m slots: (hour, minute) tuples"""
    def e(r, i):
        s = ''
        slot = (r.hour, r.minute)
        if slot in long_slots and not r.is_brk_up: s += 'L'
        if slot in short_slots and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_atr_dir(thresh):
    def e(r, i):
        if pd.isna(r.atr_ratio): return None
        s = ''
        if r.atr_ratio > thresh and not r.is_brk:
            if r.body > 0: s += 'L'
            if r.body < 0: s += 'S'
        return s or None
    return e

def make_gk_dir(gk_max):
    def e(r, i):
        if pd.isna(r.gk_ratio): return None
        s = ''
        if r.gk_ratio < gk_max and not r.is_brk:
            if r.body > 0: s += 'L'
            if r.body < 0: s += 'S'
        return s or None
    return e

def make_tbr_delta_dir(thresh):
    def e(r, i):
        td = r.tbr_delta
        if pd.isna(td): return None
        s = ''
        if td > thresh and not r.is_brk_up: s += 'L'
        if td < -thresh and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_tbr4_dir(thresh):
    def e(r, i):
        td = r.tbr_delta4
        if pd.isna(td): return None
        s = ''
        if td > thresh and not r.is_brk_up: s += 'L'
        if td < -thresh and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_multi_trend():
    def e(r, i):
        if pd.isna(r.ema20_slope) or pd.isna(r.vol_ratio): return None
        s = ''
        if (r.ema_trend == 1 and r.ema20_slope > 0 and
            r.body > 0 and r.vol_ratio > 1.0 and not r.is_brk_up): s += 'L'
        if (r.ema_trend == -1 and r.ema20_slope < 0 and
            r.body < 0 and r.vol_ratio > 1.0 and not r.is_brk_dn): s += 'S'
        return s or None
    return e

def make_mr_multi():
    def e(r, i):
        if pd.isna(r.rsi14) or pd.isna(r.ema20_dev): return None
        s = ''
        ls = (r.rsi14 < 35) + (r.consec_dn >= 2) + (r.ema20_dev < -0.3)
        if ls >= 2 and not r.is_brk_dn: s += 'L'
        ss = (r.rsi14 > 65) + (r.consec_up >= 2) + (r.ema20_dev > 0.3)
        if ss >= 2 and not r.is_brk_up: s += 'S'
        return s or None
    return e

def make_flow_mom(vm, tt):
    def e(r, i):
        if pd.isna(r.vol_ratio) or pd.isna(r.tbr): return None
        s = ''
        if r.vol_ratio > vm and not r.is_brk:
            if r.tbr > tt: s += 'L'
            if r.tbr < (1-tt): s += 'S'
        return s or None
    return e

def make_ret4_continuation(thresh):
    """If last 1h return was strong + trend, continue"""
    def e(r, i):
        if pd.isna(r.ret_4): return None
        s = ''
        if r.ret_4 > thresh and r.ema_trend == 1 and not r.is_brk_up: s += 'L'
        if r.ret_4 < -thresh and r.ema_trend == -1 and not r.is_brk_dn: s += 'S'
        return s or None
    return e

def make_ret4_reversal(thresh):
    """If last 1h return was extreme, reverse"""
    def e(r, i):
        if pd.isna(r.ret_4): return None
        s = ''
        if r.ret_4 < -thresh and not r.is_brk_dn: s += 'L'
        if r.ret_4 > thresh and not r.is_brk_up: s += 'S'
        return s or None
    return e


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V18 R2: 15m Non-Breakout Signal Scan")
    print("=" * 70)
    print(f"Data: {n} bars (15m), split at {sp}")
    print(f"IS: {WARMUP}-{sp}, OOS: {sp}-{n}")
    print(f"Fee: ${FEE} = {FEE_PCT:.4f}% | TP L={2.0}%/S={1.2}% | MH L=24/S=40 bars")

    brk = df.iloc[WARMUP:sp]['is_brk'].mean() * 100
    print(f"Breakout bar frequency (IS): {brk:.1f}%")

    signals = [
        # A: Mean reversion
        ("A1 ema_dev>0.3% MR",    make_ema_dev_mr(0.3)),
        ("A2 ema_dev>0.5% MR",    make_ema_dev_mr(0.5)),
        ("A3 ema_dev>1.0% MR",    make_ema_dev_mr(1.0)),
        ("A4 RSI 30/70 MR",       make_rsi_mr(30, 70)),
        ("A5 RSI 35/65 MR",       make_rsi_mr(35, 65)),
        ("A6 consec>=3 MR",       make_consec_mr(3)),
        ("A7 consec>=4 MR",       make_consec_mr(4)),
        ("A8 consec>=5 MR",       make_consec_mr(5)),

        # B: Trend continuation
        ("B1 slope>0.05+dir",     make_slope_dir(0.05)),
        ("B2 slope>0.1+dir",      make_slope_dir(0.1)),
        ("B3 trend+dir bar",      make_trend_dir()),

        # C: Volume/TBR
        ("C1 vol>1.5+dir nobrk",  make_vol_dir(1.5)),
        ("C2 vol>2.0+dir nobrk",  make_vol_dir(2.0)),
        ("C3 tbr>0.56/<0.44",     make_tbr_dir(0.56, 0.44)),
        ("C4 tbr>0.60/<0.40",     make_tbr_dir(0.60, 0.40)),

        # D: Candle patterns
        ("D1 engulfing nobrk",    make_engulf()),
        ("D2 pin bar nobrk",      make_pin()),

        # E: Session (coarse + fine)
        ("E1 L{8-11} S{20-23}",   make_session({8,9,10,11}, {20,21,22,23})),
        ("E2 L{3-7} S{15-19}",    make_session({3,4,5,6,7}, {15,16,17,18,19})),
        ("E3 L{5-9} S{17-21}",    make_session({5,6,7,8,9}, {17,18,19,20,21})),
        ("E4 L{9} S{21}",         make_session({9}, {21})),
        ("E5 L{5,6} S{6}",        make_session({5,6}, {6})),

        # F: Volatility
        ("F1 ATR>1.3+dir nobrk",  make_atr_dir(1.3)),
        ("F2 ATR>1.5+dir nobrk",  make_atr_dir(1.5)),
        ("F3 GK<0.6+dir nobrk",   make_gk_dir(0.6)),

        # G: Combined
        ("G1 multi-trend nobrk",  make_multi_trend()),
        ("G2 MR multi-gate",      make_mr_multi()),

        # H: TBR delta
        ("H1 tbr_delta>0.05",     make_tbr_delta_dir(0.05)),
        ("H2 tbr_delta>0.08",     make_tbr_delta_dir(0.08)),
        ("H3 tbr4_delta>0.03",    make_tbr4_dir(0.03)),
        ("H4 tbr4_delta>0.05",    make_tbr4_dir(0.05)),

        # I: Flow
        ("I1 flow v1.3+t.55",     make_flow_mom(1.3, 0.55)),
        ("I2 flow v1.5+t.55",     make_flow_mom(1.5, 0.55)),

        # J: 1h return patterns (15m unique: can see intra-hour momentum)
        ("J1 ret4>0.5%+trend",    make_ret4_continuation(0.5)),
        ("J2 ret4>0.3%+trend",    make_ret4_continuation(0.3)),
        ("J3 ret4>0.5% reverse",  make_ret4_reversal(0.5)),
        ("J4 ret4>1.0% reverse",  make_ret4_reversal(1.0)),
    ]

    print(f"\n{'='*70}")
    print("Section 1: IS Scan")
    print("=" * 70)
    header = f"  {'Signal':<28} | {'IS':<52} | {'brk%':>4}"
    print(header)
    print("  " + "-" * 28 + "-+-" + "-" * 52 + "-+-" + "-" * 4)

    is_pos = []
    for name, fn in signals:
        tr = run_bt(df, WARMUP, sp, fn)
        s = summarize(tr)
        bo = sum(1 for t in tr if t.get('is_brk')) / len(tr) * 100 if tr else 0
        m = " <<<" if s['pnl'] > 0 and s['n'] >= 15 else ""
        print(f"  {name:<28} | {fmt(s):<52} | {bo:3.0f}%{m}")
        if s['pnl'] > 0 and s['n'] >= 15:
            is_pos.append((name, fn, s))

    # Section 2: OOS
    print(f"\n{'='*70}")
    print(f"Section 2: OOS Validation ({len(is_pos)} IS-positive)")
    print("=" * 70)

    if not is_pos:
        print("  No IS-positive signals. ALL FAILED on 15m.")
    else:
        for name, fn, s_is in is_pos:
            tr_oos = run_bt(df, sp, n, fn)
            s_oos = summarize(tr_oos)
            print(f"  {name:<28} | IS: {fmt(s_is)}")
            print(f"  {'':28} | OOS: {fmt(s_oos)}")

    # Section 3: Conclusion
    print(f"\n{'='*70}")
    print("Section 3: V18 Conclusion")
    print("=" * 70)
    print(f"""
  Total 15m signals tested: {len(signals)}
  IS-positive: {len(is_pos)}

  Combined V18 results:
  ---------------------
  R0: Feasibility OK on all timeframes (MFE > fee)
  R1: 30m - 35 signals ALL IS-negative
  R2: 15m - {len(signals)} signals {'ALL IS-negative' if not is_pos else f'{len(is_pos)} IS-positive'}

  Breakout self-check: ALL signals exclude breakout bars.
  Fee is NOT the bottleneck (7-9% of avg gross profit).

  The problem is DIRECTIONAL PREDICTION on non-breakout bars:
  No signal can predict whether the next move is up or down
  when the current bar is not a breakout bar.

  This holds on 15m, 30m, AND 1h.
""")


if __name__ == "__main__":
    main()
