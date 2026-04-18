"""
V17 R3: Range Position WITHOUT Breakout Bars

R2 key finding:
  - rpos signals (range position) have strong IS+OOS performance
  - BUT A8 (rpos15>0.90) has 93% overlap with breakout bars
  - A7 (rpos15>0.60) has only 28% overlap — possible independent alpha?

R3 critical question:
  If we EXCLUDE breakout bars from rpos signals, is there still edge?
  This separates "rpos AS breakout proxy" from "rpos AS independent alpha"

Breakout self-check:
  This round explicitly REMOVES breakout bars from entry.
  Any remaining edge is definitionally non-breakout.

Sections:
  1. Pure non-breakout rpos: entry only when NOT on breakout bar
  2. Breakout-only subset: entry only when ON breakout bar (control group)
  3. Residual analysis: does rpos add value WITHIN non-breakout bars?
  4. Cross-asset filter: BTC divergence + rpos (non-breakout)
  5. Volume-enhanced: rpos + volume confirmation (non-breakout)
  6. Time-of-day: rpos + session filter (non-breakout)
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

    # EMA trend
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)

    # EMA slope
    d['ema20_slope'] = (d['ema20'] - d['ema20'].shift(5)) / d['ema20'].shift(5) * 100

    # Range position for multiple windows
    for n in [10, 15, 20, 30]:
        rmax = d['high'].shift(1).rolling(n).max()
        rmin = d['low'].shift(1).rolling(n).min()
        rng = (rmax - rmin).clip(lower=1e-8)
        d[f'rpos_{n}'] = (d['close'] - rmin) / rng

    # Returns
    d['ret_1'] = d['close'].pct_change(1)
    d['ret_3'] = d['close'].pct_change(3)
    d['ret_5'] = d['close'].pct_change(5)

    # Volume / TBR
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio_l'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile_l'] = d['gk_ratio_l'].shift(1).rolling(100).rank(pct=True) * 100
    d['gk_ratio_s'] = gk.rolling(10).mean() / gk.rolling(30).mean()
    d['gk_pctile_s'] = d['gk_ratio_s'].shift(1).rolling(100).rank(pct=True) * 100

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # Breakout flags (for EXCLUSION)
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()
    d['is_brk_up'] = d['close'] > d['high_15']
    d['is_brk_dn'] = d['close'] < d['low_15']
    d['is_brk_any'] = d['is_brk_up'] | d['is_brk_dn']

    # BTC data for cross-asset signals
    btc_path = DATA_DIR / "BTCUSDT_1h_latest730d.csv"
    if btc_path.exists():
        btc = pd.read_csv(btc_path, parse_dates=["datetime"])
        btc = btc.rename(columns={c: f'btc_{c}' for c in btc.columns if c != 'datetime'})
        d = d.merge(btc[['datetime', 'btc_close']], on='datetime', how='left')
        d['btc_ret_5'] = d['btc_close'].pct_change(5)
        d['eth_ret_5'] = d['close'].pct_change(5)
        d['eth_btc_div'] = d['eth_ret_5'] - d['btc_ret_5']  # ETH outperformance
    else:
        d['btc_close'] = np.nan
        d['btc_ret_5'] = np.nan
        d['eth_btc_div'] = np.nan

    return d


def run_bt(df, start, end, entry_fn, tp_l=0.035, tp_s=0.02, mh_l=6, mh_s=10,
           ext_l=2, ext_s=2, ml_l=-75, ml_s=-150, fee=FEE,
           use_mfe=True, mfe_thresh=0.01, mfe_trail=0.008, mfe_min_bar=1,
           cond_mh_bar=2, cond_mh_loss=-0.01):
    """Full V14-equivalent backtest engine."""
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

    # Force close
    if pos_l is not None:
        close_pos(pos_l, 'L', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)
    if pos_s is not None:
        close_pos(pos_s, 'S', 'END', df.iloc[end-1].close, end-1, df.iloc[end-1].datetime)

    return trades


def summarize(trades, label=""):
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


def fmt(s, label=""):
    pm_str = f"{s['pm']}/{s['pm_total']}"
    return (f"{s['n']:3d}t ${s['pnl']:+7.0f} WR {s['wr']:5.0f}% "
            f"PF {s['pf']:4.1f} MDD ${s['mdd']:4.0f} PM {pm_str}")


# ══════════════════════════════════════════════════════════════════
#  Entry signal generators
# ══════════════════════════════════════════════════════════════════

def make_rpos_no_brk(n, l_thresh, s_thresh):
    """Range position, EXCLUDING breakout bars"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        # EXCLUDE breakout bars
        if rp > l_thresh and not row.is_brk_up: sig += 'L'
        if rp < s_thresh and not row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_rpos_brk_only(n, l_thresh, s_thresh):
    """Range position, ONLY on breakout bars (control group)"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if rp > l_thresh and row.is_brk_up: sig += 'L'
        if rp < s_thresh and row.is_brk_dn: sig += 'S'
        return sig or None
    return entry

def make_rpos_no_brk_vol(n, l_thresh, s_thresh, vol_min=1.2):
    """Range position + volume confirmation, EXCLUDING breakout bars"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if pd.isna(row.vol_ratio): return None
        if rp > l_thresh and not row.is_brk_up and row.vol_ratio > vol_min: sig += 'L'
        if rp < s_thresh and not row.is_brk_dn and row.vol_ratio > vol_min: sig += 'S'
        return sig or None
    return entry

def make_rpos_no_brk_gk(n, l_thresh, s_thresh, gk_max=25):
    """Range position + GK compression, EXCLUDING breakout bars"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if rp > l_thresh and not row.is_brk_up and row.gk_pctile_l < gk_max: sig += 'L'
        if rp < s_thresh and not row.is_brk_dn and row.gk_pctile_s < gk_max: sig += 'S'  # using s gk for S
        return sig or None
    return entry

def make_rpos_no_brk_btc_div(n, l_thresh, s_thresh, div_thresh=0.01):
    """Range position + ETH outperforming BTC, EXCLUDING breakout bars"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        div = row.eth_btc_div
        if pd.isna(div): return None
        if rp > l_thresh and not row.is_brk_up and div > div_thresh: sig += 'L'
        if rp < s_thresh and not row.is_brk_dn and div < -div_thresh: sig += 'S'
        return sig or None
    return entry

def make_rpos_no_brk_trend(n, l_thresh, s_thresh):
    """Range position + EMA trend, EXCLUDING breakout bars"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        if rp > l_thresh and not row.is_brk_up and row.ema_trend == 1: sig += 'L'
        if rp < s_thresh and not row.is_brk_dn and row.ema_trend == -1: sig += 'S'
        return sig or None
    return entry

def make_rpos_no_brk_rsi(n, l_thresh, s_thresh, rsi_l_range=(40, 65), rsi_s_range=(35, 60)):
    """Range position + RSI in moderate zone (not overbought/oversold), EXCLUDING breakout"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        rsi = row.rsi14
        if pd.isna(rsi): return None
        if rp > l_thresh and not row.is_brk_up and rsi_l_range[0] <= rsi <= rsi_l_range[1]:
            sig += 'L'
        if rp < s_thresh and not row.is_brk_dn and rsi_s_range[0] <= rsi <= rsi_s_range[1]:
            sig += 'S'
        return sig or None
    return entry

def make_pure_non_breakout_trend(n, l_range=(0.55, 0.85), s_range=(0.15, 0.45)):
    """Range position in MIDDLE zone (clearly NOT breakout) + trend confirmation"""
    col = f'rpos_{n}'
    def entry(row, i):
        sig = ''
        rp = getattr(row, col, None)
        if rp is None or pd.isna(rp): return None
        # L: above midpoint but NOT near top (not breakout)
        if l_range[0] <= rp <= l_range[1] and not row.is_brk_up and row.ema_trend == 1:
            sig += 'L'
        # S: below midpoint but NOT near bottom (not breakout)
        if s_range[0] >= rp >= (1 - l_range[1]) and not row.is_brk_dn and row.ema_trend == -1:
            sig += 'S'
        return sig or None
    return entry


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V17 R3: Range Position WITHOUT Breakout Bars")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")

    # Count breakout bar frequency
    is_df = df.iloc[WARMUP:sp]
    brk_up_pct = is_df['is_brk_up'].mean() * 100
    brk_dn_pct = is_df['is_brk_dn'].mean() * 100
    brk_any_pct = is_df['is_brk_any'].mean() * 100
    print(f"\nBreakout bar frequency (IS): up {brk_up_pct:.1f}%, down {brk_dn_pct:.1f}%, any {brk_any_pct:.1f}%")

    # ══════════════════════════════════════════
    #  Section 1: Non-breakout rpos vs breakout-only rpos vs pure rpos
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 1: rpos WITH vs WITHOUT breakout bars (IS)")
    print("  Comparing: full rpos / breakout-only rpos / non-breakout rpos")
    print("=" * 70)

    configs = [
        (15, 0.90, 0.10, "A8"),
        (15, 0.80, 0.20, "A2"),
        (15, 0.70, 0.30, "A1"),
        (15, 0.60, 0.40, "A7"),
        (20, 0.80, 0.20, "A4"),
        (20, 0.70, 0.30, "A3"),
    ]

    header = f"  {'Signal':<28} | {'Full (brk+non)':<42} | {'Breakout-only':<42} | {'Non-breakout only':<42}"
    print(header)
    print("  " + "-" * 28 + "-+-" + "-" * 42 + "-+-" + "-" * 42 + "-+-" + "-" * 42)

    for rn, lt, st, label in configs:
        # Full (same as R2)
        def make_full(n_, lt_, st_):
            col = f'rpos_{n_}'
            def e(row, i):
                sig = ''
                rp = getattr(row, col, None)
                if rp is None or pd.isna(rp): return None
                if rp > lt_: sig += 'L'
                if rp < st_: sig += 'S'
                return sig or None
            return e

        tr_full = run_bt(df, WARMUP, sp, make_full(rn, lt, st))
        tr_brk = run_bt(df, WARMUP, sp, make_rpos_brk_only(rn, lt, st))
        tr_nobrk = run_bt(df, WARMUP, sp, make_rpos_no_brk(rn, lt, st))

        s_full = summarize(tr_full)
        s_brk = summarize(tr_brk)
        s_nobrk = summarize(tr_nobrk)

        name = f"rpos{rn} >{lt:.2f}/<{st:.2f} ({label})"
        print(f"  {name:<28} | {fmt(s_full):<42} | {fmt(s_brk):<42} | {fmt(s_nobrk):<42}")

    # ══════════════════════════════════════════
    #  Section 2: OOS for non-breakout rpos (if IS > 0)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 2: Non-breakout rpos IS + OOS validation")
    print("=" * 70)

    header2 = f"  {'Signal':<35} | {'IS':<42} | {'OOS':<42}"
    print(header2)
    print("  " + "-" * 35 + "-+-" + "-" * 42 + "-+-" + "-" * 42)

    all_configs = [
        (15, 0.90, 0.10), (15, 0.85, 0.15), (15, 0.80, 0.20),
        (15, 0.75, 0.25), (15, 0.70, 0.30), (15, 0.65, 0.35),
        (15, 0.60, 0.40), (15, 0.55, 0.45),
        (20, 0.90, 0.10), (20, 0.80, 0.20), (20, 0.70, 0.30),
        (20, 0.60, 0.40),
        (10, 0.80, 0.20), (10, 0.70, 0.30),
        (30, 0.80, 0.20), (30, 0.70, 0.30),
    ]

    is_positive = []
    for rn, lt, st in all_configs:
        tr_is = run_bt(df, WARMUP, sp, make_rpos_no_brk(rn, lt, st))
        s_is = summarize(tr_is)
        name = f"nobrk rpos{rn} >{lt:.2f}/<{st:.2f}"

        if s_is['pnl'] > 0 and s_is['n'] >= 15:
            tr_oos = run_bt(df, sp, n, make_rpos_no_brk(rn, lt, st))
            s_oos = summarize(tr_oos)
            marker = " <<<" if s_oos['pnl'] > 0 else ""
            print(f"  {name:<35} | {fmt(s_is):<42} | {fmt(s_oos):<42}{marker}")
            if s_oos['pnl'] > 0:
                is_positive.append((name, rn, lt, st, s_is, s_oos))
        else:
            print(f"  {name:<35} | {fmt(s_is):<42} | {'(IS negative or <15t, skip OOS)':<42}")

    # ══════════════════════════════════════════
    #  Section 3: Enhanced non-breakout rpos (volume, GK, BTC, trend, RSI)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 3: Enhanced non-breakout rpos signals (IS + OOS)")
    print("=" * 70)

    enhanced_signals = []
    # Use the range configs that showed some promise in section 2
    for rn, lt, st in [(15, 0.70, 0.30), (15, 0.60, 0.40), (20, 0.70, 0.30), (20, 0.60, 0.40)]:
        enhanced_signals.extend([
            (f"nobrk rpos{rn}>{lt:.1f}+vol1.2", make_rpos_no_brk_vol(rn, lt, st, 1.2)),
            (f"nobrk rpos{rn}>{lt:.1f}+vol1.5", make_rpos_no_brk_vol(rn, lt, st, 1.5)),
            (f"nobrk rpos{rn}>{lt:.1f}+GK<25", make_rpos_no_brk_gk(rn, lt, st, 25)),
            (f"nobrk rpos{rn}>{lt:.1f}+GK<35", make_rpos_no_brk_gk(rn, lt, st, 35)),
            (f"nobrk rpos{rn}>{lt:.1f}+btcDiv", make_rpos_no_brk_btc_div(rn, lt, st, 0.01)),
            (f"nobrk rpos{rn}>{lt:.1f}+trend", make_rpos_no_brk_trend(rn, lt, st)),
            (f"nobrk rpos{rn}>{lt:.1f}+RSI", make_rpos_no_brk_rsi(rn, lt, st)),
        ])

    # Also test "pure middle zone" (clearly non-breakout)
    enhanced_signals.extend([
        ("midzone15 0.55-0.85+trend", make_pure_non_breakout_trend(15, (0.55, 0.85), (0.15, 0.45))),
        ("midzone15 0.60-0.80+trend", make_pure_non_breakout_trend(15, (0.60, 0.80), (0.20, 0.40))),
        ("midzone20 0.55-0.85+trend", make_pure_non_breakout_trend(20, (0.55, 0.85), (0.15, 0.45))),
        ("midzone20 0.60-0.80+trend", make_pure_non_breakout_trend(20, (0.60, 0.80), (0.20, 0.40))),
    ])

    header3 = f"  {'Signal':<35} | {'IS':<42} | {'OOS':<42}"
    print(header3)
    print("  " + "-" * 35 + "-+-" + "-" * 42 + "-+-" + "-" * 42)

    for name, entry_fn in enhanced_signals:
        tr_is = run_bt(df, WARMUP, sp, entry_fn)
        s_is = summarize(tr_is)
        if s_is['pnl'] > 0 and s_is['n'] >= 10:
            tr_oos = run_bt(df, sp, n, entry_fn)
            s_oos = summarize(tr_oos)
            marker = " <<<" if s_oos['pnl'] > 0 else ""
            print(f"  {name:<35} | {fmt(s_is):<42} | {fmt(s_oos):<42}{marker}")
        else:
            print(f"  {name:<35} | {fmt(s_is):<42} | {'(IS<=0 or <10t)':<42}")

    # ══════════════════════════════════════════
    #  Section 4: Trade quality deep dive (best non-breakout candidate)
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 4: Per-trade quality of best non-breakout candidate")
    print("=" * 70)

    # Use rpos15>0.60 no-brk as it had lowest overlap (28%) and still worked
    best_fn = make_rpos_no_brk(15, 0.60, 0.40)
    all_tr = run_bt(df, WARMUP, n, best_fn)
    s_all = summarize(all_tr)
    print(f"\n  Full period: {fmt(s_all)}")

    # Analyze by exit reason
    reasons = defaultdict(list)
    for t in all_tr:
        reasons[t['reason']].append(t['p'])

    print("\n  Exit reason breakdown:")
    for reason, pnls in sorted(reasons.items()):
        cnt = len(pnls)
        avg = np.mean(pnls)
        total = sum(pnls)
        wr = sum(1 for p in pnls if p > 0) / cnt * 100
        print(f"    {reason:<8}: {cnt:3d}t  avg ${avg:+6.1f}  total ${total:+7.0f}  WR {wr:.0f}%")

    # Monthly PnL
    monthly = defaultdict(float)
    for t in all_tr:
        monthly[t['month']] += t['p']
    print("\n  Monthly PnL:")
    for m in sorted(monthly.keys()):
        bar = '+' * int(max(0, monthly[m]) / 50) if monthly[m] > 0 else '-' * int(max(0, -monthly[m]) / 50)
        print(f"    {m}: ${monthly[m]:+7.0f} {bar}")

    pos_months = sum(1 for v in monthly.values() if v > 0)
    print(f"\n  Positive months: {pos_months}/{len(monthly)}")

    # ══════════════════════════════════════════
    #  Section 5: Exit optimization for best non-breakout candidate
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 5: Exit optimization for non-breakout rpos15>0.60")
    print("=" * 70)

    exit_configs = [
        # (label, tp_l, tp_s, mh_l, mh_s, use_mfe)
        ("V14 default",         0.035, 0.020, 6, 10, True),
        ("TP2.5/1.5 MH6/10",   0.025, 0.015, 6, 10, True),
        ("TP3.0/2.0 MH6/10",   0.030, 0.020, 6, 10, True),
        ("TP4.0/2.5 MH6/10",   0.040, 0.025, 6, 10, True),
        ("TP3.5/2.0 MH4/8",    0.035, 0.020, 4, 8, True),
        ("TP3.5/2.0 MH8/12",   0.035, 0.020, 8, 12, True),
        ("TP2.0/1.5 MH4/6",    0.020, 0.015, 4, 6, True),
        ("TP3.0/1.5 MH5/8",    0.030, 0.015, 5, 8, True),
        ("No MFE trail",        0.035, 0.020, 6, 10, False),
        ("TP1.5/1.0 MH3/5",    0.015, 0.010, 3, 5, True),
        ("TP2.0/1.0 MH4/6",    0.020, 0.010, 4, 6, True),
    ]

    best_fn = make_rpos_no_brk(15, 0.60, 0.40)

    header5 = f"  {'Exit config':<24} | {'IS':<42} | {'OOS':<42}"
    print(header5)
    print("  " + "-" * 24 + "-+-" + "-" * 42 + "-+-" + "-" * 42)

    for label, tpl, tps, mhl, mhs, umfe in exit_configs:
        tr_is = run_bt(df, WARMUP, sp, best_fn, tp_l=tpl, tp_s=tps, mh_l=mhl, mh_s=mhs, use_mfe=umfe)
        tr_oos = run_bt(df, sp, n, best_fn, tp_l=tpl, tp_s=tps, mh_l=mhl, mh_s=mhs, use_mfe=umfe)
        s_is = summarize(tr_is)
        s_oos = summarize(tr_oos)
        print(f"  {label:<24} | {fmt(s_is):<42} | {fmt(s_oos):<42}")

    # ══════════════════════════════════════════
    #  Section 6: Walk-Forward for best non-breakout candidate
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 6: Walk-Forward validation (6-fold)")
    print("=" * 70)

    fold_size = (n - WARMUP) // 6
    wf_results = []
    for fold in range(6):
        f_start = WARMUP + fold * fold_size
        f_end = f_start + fold_size if fold < 5 else n
        tr = run_bt(df, f_start, f_end, best_fn)
        s = summarize(tr)
        status = "PASS" if s['pnl'] > 0 else "FAIL"
        wf_results.append(status)
        period = f"{df.iloc[f_start].datetime.strftime('%Y-%m')} ~ {df.iloc[min(f_end-1, n-1)].datetime.strftime('%Y-%m')}"
        print(f"  Fold {fold+1}: {period} | {fmt(s)} | {status}")

    wf_pass = sum(1 for r in wf_results if r == "PASS")
    print(f"\n  Walk-Forward: {wf_pass}/6 PASS")

    # ══════════════════════════════════════════
    #  Section 7: Honest Assessment
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 7: Honest Assessment")
    print("=" * 70)

    print(f"""
  R3 answers: Does rpos have INDEPENDENT alpha beyond breakout?

  Key test: strip breakout bars from rpos signals and check residual edge.

  If non-breakout rpos IS is still positive:
    -> rpos captures SOME non-breakout trend-following alpha
    -> But need to verify it's not just a diluted breakout proxy
       (e.g., entering 1 bar before/after a breakout bar)

  If non-breakout rpos IS is zero or negative:
    -> rpos = pure breakout proxy with no independent edge
    -> Confirms V16 audit: 15-bar breakout IS the only ETH 1h alpha

  Conclusion based on results above:
  (see Section 1 and Section 2 for definitive answer)
""")


if __name__ == "__main__":
    main()
