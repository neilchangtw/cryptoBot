"""
V17 R1: Mean Reversion Strategy Scan (Non-Breakout)

R0 finding: ETH 1h non-breakout alpha is mean reversion.
  Best S signals: EMA overext, consec bars, CTR reversal
  Best L signals: big down bar, consec down bars

This round: backtest top candidates with TP+MH exit (IS first).

WHY "without breakout" changes V12's conclusion:
  V12 tested EMA overext AS FILTER on breakout → contradicts mean reversion.
  R0 shows pure mean reversion S has MFE advantage +0.387% (EMA dev > 1.5%).
  Non-breakout bars have structural S MFE-MAE = +0.128% (bearish follow-through).

Breakout self-check:
  ✅ No close > N-bar max
  ✅ No close < N-bar min
  ✅ No price breakout variants
  Entry is based on: EMA deviation / consecutive bars / CTR / RSI

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

# Exit params (start with V14 framework, adjust later)
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
    rng = (d['high'] - d['low']).clip(lower=1e-8)

    # Bar structure
    d['ctr'] = (d['close'] - d['low']) / rng
    d['bar_dir'] = np.sign(d['close'] - d['open'])

    # EMA
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema50'] = d['close'].ewm(span=50).mean()
    d['ema_dev20'] = (d['close'] - d['ema20']) / d['ema20'] * 100
    d['ema_trend'] = np.where(d['ema20'] > d['ema50'], 1, -1)

    # Returns
    d['ret_1'] = d['close'].pct_change(1)
    d['ret_3'] = d['close'].pct_change(3)

    # Consecutive bars
    consec_up = np.zeros(len(d))
    consec_dn = np.zeros(len(d))
    for i in range(1, len(d)):
        if d.iloc[i]['close'] > d.iloc[i]['open']:
            consec_up[i] = consec_up[i-1] + 1
        if d.iloc[i]['close'] < d.iloc[i]['open']:
            consec_dn[i] = consec_dn[i-1] + 1
    d['consec_up'] = consec_up
    d['consec_dn'] = consec_dn

    # RSI
    delta = d['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    d['rsi14'] = 100 - 100 / (1 + gain / loss.clip(lower=1e-8))

    # TBR
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_ma5'] = d['tbr'].rolling(5).mean()

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio'] = gk.rolling(5).mean() / gk.rolling(20).mean()

    # Volume
    d['vol_ratio'] = d['volume'] / d['volume'].rolling(20).mean()

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

        # Exit L
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

        # Exit S
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

        # Entry
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
#  Entry signals (ALL non-breakout)
# ══════════════════════════════════════════

# S1: EMA Overextension Mean Reversion
def make_ema_overext(l_thresh, s_thresh):
    """L: EMA dev < -l_thresh%, S: EMA dev > +s_thresh%"""
    def entry(row, i):
        sig = ''
        dev = row.ema_dev20
        if pd.isna(dev): return None
        if dev < -l_thresh: sig += 'L'
        if dev > s_thresh: sig += 'S'
        return sig or None
    return entry

# S2: Consecutive Bar Reversal
def make_consec_rev(l_bars, s_bars):
    """L: consec_dn >= l_bars, S: consec_up >= s_bars"""
    def entry(row, i):
        sig = ''
        if row.consec_dn >= l_bars: sig += 'L'
        if row.consec_up >= s_bars: sig += 'S'
        return sig or None
    return entry

# S3: CTR Reversal in Trend
def make_ctr_trend(l_ctr, s_ctr):
    """L: uptrend + CTR < l_ctr, S: downtrend + CTR > s_ctr"""
    def entry(row, i):
        sig = ''
        if pd.isna(row.ctr) or pd.isna(row.ema_trend): return None
        if row.ema_trend == 1 and row.ctr < l_ctr: sig += 'L'
        if row.ema_trend == -1 and row.ctr > s_ctr: sig += 'S'
        return sig or None
    return entry

# S4: RSI Mean Reversion
def make_rsi_rev(l_rsi, s_rsi):
    """L: RSI < l_rsi, S: RSI > s_rsi"""
    def entry(row, i):
        sig = ''
        rsi = row.rsi14
        if pd.isna(rsi): return None
        if rsi < l_rsi: sig += 'L'
        if rsi > s_rsi: sig += 'S'
        return sig or None
    return entry

# S5: Big Return Reversal
def make_ret_rev(l_ret, s_ret):
    """L: ret_1 < -l_ret, S: ret_1 > +s_ret"""
    def entry(row, i):
        sig = ''
        r = row.ret_1
        if pd.isna(r): return None
        if r < -l_ret: sig += 'L'
        if r > s_ret: sig += 'S'
        return sig or None
    return entry

# S6: Combined — EMA overext + CTR confirmation
def make_ema_ctr(ema_l, ema_s, ctr_l, ctr_s):
    """L: EMA dev < -ema_l AND CTR < ctr_l, S: EMA dev > ema_s AND CTR > ctr_s"""
    def entry(row, i):
        sig = ''
        dev = row.ema_dev20
        ctr = row.ctr
        if pd.isna(dev) or pd.isna(ctr): return None
        if dev < -ema_l and ctr < ctr_l: sig += 'L'
        if dev > ema_s and ctr > ctr_s: sig += 'S'
        return sig or None
    return entry

# S7: TBR + Consec Bars (flow + pattern)
def make_tbr_consec(tbr_l, tbr_s, con_l, con_s):
    """L: TBR_MA5 < tbr_l + consec_dn >= con_l, S: TBR_MA5 > tbr_s + consec_up >= con_s"""
    def entry(row, i):
        sig = ''
        tbr = row.tbr_ma5
        if pd.isna(tbr): return None
        if tbr < tbr_l and row.consec_dn >= con_l: sig += 'L'
        if tbr > tbr_s and row.consec_up >= con_s: sig += 'S'
        return sig or None
    return entry

# S8: S-only EMA Overextension (L is weak in R0, focus on S)
def make_s_only_ema(s_thresh):
    def entry(row, i):
        dev = row.ema_dev20
        if pd.isna(dev): return None
        if dev > s_thresh: return 'S'
        return None
    return entry


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V17 R1: Mean Reversion Strategy Scan (Non-Breakout)")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")
    print(f"IS:  {df.iloc[WARMUP].datetime} ~ {df.iloc[sp-1].datetime}")
    print(f"OOS: {df.iloc[sp].datetime} ~ {df.iloc[-1].datetime}")

    # ══════════════════════════════════════════
    #  Section 1: IS-only signal scan
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 1: IS-Only Signal Scan (V14 exits: TP 3.5%/2%, MH 6/10)")
    print("=" * 70)

    signals = [
        # S1: EMA overextension
        ("S1a EMA overext 1.5/1.5", make_ema_overext(1.5, 1.5)),
        ("S1b EMA overext 2.0/2.0", make_ema_overext(2.0, 2.0)),
        ("S1c EMA overext 2.5/2.5", make_ema_overext(2.5, 2.5)),
        ("S1d EMA overext 1.0/1.0", make_ema_overext(1.0, 1.0)),
        ("S1e EMA overext 3.0/3.0", make_ema_overext(3.0, 3.0)),

        # S2: Consecutive bar reversal
        ("S2a consec 2/2", make_consec_rev(2, 2)),
        ("S2b consec 3/3", make_consec_rev(3, 3)),
        ("S2c consec 4/4", make_consec_rev(4, 4)),
        ("S2d consec 2/3", make_consec_rev(2, 3)),
        ("S2e consec 3/2", make_consec_rev(3, 2)),

        # S3: CTR reversal in trend
        ("S3a CTR 0.30/0.70", make_ctr_trend(0.30, 0.70)),
        ("S3b CTR 0.25/0.75", make_ctr_trend(0.25, 0.75)),
        ("S3c CTR 0.20/0.80", make_ctr_trend(0.20, 0.80)),
        ("S3d CTR 0.35/0.65", make_ctr_trend(0.35, 0.65)),

        # S4: RSI mean reversion
        ("S4a RSI 30/70", make_rsi_rev(30, 70)),
        ("S4b RSI 35/65", make_rsi_rev(35, 65)),
        ("S4c RSI 25/75", make_rsi_rev(25, 75)),
        ("S4d RSI 40/60", make_rsi_rev(40, 60)),

        # S5: Big return reversal
        ("S5a ret rev 0.5%/0.5%", make_ret_rev(0.005, 0.005)),
        ("S5b ret rev 1.0%/1.0%", make_ret_rev(0.010, 0.010)),
        ("S5c ret rev 1.5%/1.5%", make_ret_rev(0.015, 0.015)),
        ("S5d ret rev 0.3%/0.3%", make_ret_rev(0.003, 0.003)),

        # S6: EMA + CTR combined
        ("S6a EMA1.5+CTR.30/.70", make_ema_ctr(1.5, 1.5, 0.30, 0.70)),
        ("S6b EMA2.0+CTR.25/.75", make_ema_ctr(2.0, 2.0, 0.25, 0.75)),
        ("S6c EMA1.0+CTR.35/.65", make_ema_ctr(1.0, 1.0, 0.35, 0.65)),

        # S7: TBR + Consecutive
        ("S7a TBR.47/.53+con2/2", make_tbr_consec(0.47, 0.53, 2, 2)),
        ("S7b TBR.47/.53+con3/3", make_tbr_consec(0.47, 0.53, 3, 3)),

        # S8: S-only EMA
        ("S8a S-only EMA 1.5", make_s_only_ema(1.5)),
        ("S8b S-only EMA 2.0", make_s_only_ema(2.0)),
        ("S8c S-only EMA 1.0", make_s_only_ema(1.0)),
    ]

    print(f"\n  {'Signal':30s} | {'IS':>45s}")
    print(f"  {'-'*30}-+-{'-'*45}")

    is_results = []
    for name, fn in signals:
        trades = run_bt(df, WARMUP, sp, fn)
        m = _metrics(trades)
        print(f"  {name:30s} | {fmt(m)}")
        is_results.append((name, fn, m))

    # ══════════════════════════════════════════
    #  Section 2: Top IS candidates → OOS validation
    # ══════════════════════════════════════════
    print("\n" + "=" * 70)
    print("Section 2: Top IS Candidates -> OOS Validation")
    print("=" * 70)

    # Filter: IS PnL > $0 and IS trades >= 20
    positive = [(name, fn, m) for name, fn, m in is_results if m['pnl'] > 0 and m['t'] >= 20]
    positive.sort(key=lambda x: -x[2]['pnl'])

    if not positive:
        print("\n  NO positive IS strategies found with >= 20 trades.")
        print("  ETH 1h non-breakout mean reversion may not work with V14 exits.")
    else:
        print(f"\n  Top {min(10, len(positive))} IS-positive candidates:")
        print(f"\n  {'Signal':30s} | {'IS':>45s} | {'OOS':>45s}")
        print(f"  {'-'*30}-+-{'-'*45}-+-{'-'*45}")

        for name, fn, m_is in positive[:10]:
            full = split_metrics(run_bt(df, WARMUP, n, fn), sp)
            print(f"  {name:30s} | {fmt(full['is'])} | {fmt(full['oos'])}")

    # ══════════════════════════════════════════
    #  Section 3: Exit optimization for best entry signal
    # ══════════════════════════════════════════
    if positive:
        best_name, best_fn, _ = positive[0]
        print(f"\n" + "=" * 70)
        print(f"Section 3: Exit Optimization for Best Entry ({best_name})")
        print("=" * 70)

        # TP scan
        print(f"\n  --- TP Scan (MH=6/10 fixed) ---")
        print(f"  {'TP_L':>5s} {'TP_S':>5s} | {'IS':>45s} | {'OOS':>45s}")
        for tp_l_pct in [0.015, 0.020, 0.025, 0.030, 0.035, 0.040, 0.050]:
            for tp_s_pct in [0.010, 0.015, 0.020, 0.025, 0.030]:
                m = split_metrics(run_bt(df, WARMUP, n, best_fn,
                                         tp_l=tp_l_pct, tp_s=tp_s_pct), sp)
                if m['is']['pnl'] > 0:
                    print(f"  {tp_l_pct*100:4.1f}% {tp_s_pct*100:4.1f}% | {fmt(m['is'])} | {fmt(m['oos'])}")

        # MH scan
        print(f"\n  --- MH Scan (TP=3.5%/2.0% fixed) ---")
        print(f"  {'MH_L':>4s} {'MH_S':>4s} | {'IS':>45s} | {'OOS':>45s}")
        for mh_l in [3, 4, 5, 6, 8, 10]:
            for mh_s in [4, 6, 8, 10, 12]:
                m = split_metrics(run_bt(df, WARMUP, n, best_fn,
                                         mh_l=mh_l, mh_s=mh_s), sp)
                if m['is']['pnl'] > 0:
                    print(f"  {mh_l:4d} {mh_s:4d} | {fmt(m['is'])} | {fmt(m['oos'])}")

    # ══════════════════════════════════════════
    #  Section 4: Alternative exit frameworks
    # ══════════════════════════════════════════
    print(f"\n" + "=" * 70)
    print("Section 4: Alternative Exit - Shorter Hold + Tighter TP")
    print("  (Mean reversion should have shorter holds and tighter targets)")
    print("=" * 70)

    # Mean reversion typically needs: short hold, tight TP, wider SL
    if positive:
        best_name, best_fn, _ = positive[0]
    else:
        # Fall back to a reasonable signal
        best_name = "S2b consec 3/3"
        best_fn = make_consec_rev(3, 3)

    # Try mean reversion-style exits
    mr_exits = [
        # (name, tp_l, tp_s, mh_l, mh_s, ext_l, ext_s)
        ("TP1.0/1.0 MH3/3", 0.010, 0.010, 3, 3, 0, 0),
        ("TP1.5/1.0 MH3/3", 0.015, 0.010, 3, 3, 0, 0),
        ("TP1.5/1.5 MH4/4", 0.015, 0.015, 4, 4, 0, 0),
        ("TP2.0/1.5 MH4/4", 0.020, 0.015, 4, 4, 0, 0),
        ("TP2.0/1.5 MH3/3", 0.020, 0.015, 3, 3, 0, 0),
        ("TP1.0/1.0 MH2/2", 0.010, 0.010, 2, 2, 0, 0),
        ("TP1.5/1.0 MH2/2", 0.015, 0.010, 2, 2, 0, 0),
        ("TP2.0/2.0 MH3/3", 0.020, 0.020, 3, 3, 0, 0),
        ("TP1.0/0.5 MH3/3", 0.010, 0.005, 3, 3, 0, 0),
        ("TP0.5/0.5 MH2/2", 0.005, 0.005, 2, 2, 0, 0),
        ("TP1.0/1.0 MH4/4", 0.010, 0.010, 4, 4, 0, 0),
        ("TP1.5/1.5 MH6/6", 0.015, 0.015, 6, 6, 0, 0),
        # Also test V14-style with ext
        ("TP2.0/1.5 MH4/4 ext2", 0.020, 0.015, 4, 4, 2, 2),
        ("TP1.5/1.0 MH3/3 ext2", 0.015, 0.010, 3, 3, 2, 2),
    ]

    # Test all entry signals with MR exits
    top_entries = [(name, fn) for name, fn, m in is_results if m['t'] >= 15]

    print(f"\n  Testing {len(top_entries)} entries x {len(mr_exits)} MR exits on IS:")
    print(f"\n  {'Entry+Exit':50s} | {'IS':>45s}")
    print(f"  {'-'*50}-+-{'-'*45}")

    best_combos = []
    for ename, efn in top_entries:
        for xname, tp_l, tp_s, mh_l, mh_s, ext_l, ext_s in mr_exits:
            trades = run_bt(df, WARMUP, sp, efn,
                           tp_l=tp_l, tp_s=tp_s, mh_l=mh_l, mh_s=mh_s,
                           ext_l=ext_l, ext_s=ext_s)
            m = _metrics(trades)
            if m['pnl'] > 100 and m['t'] >= 15:
                combo_name = f"{ename} + {xname}"
                best_combos.append((combo_name, efn, tp_l, tp_s, mh_l, mh_s, ext_l, ext_s, m))

    # Sort by IS PnL and show top 20
    best_combos.sort(key=lambda x: -x[8]['pnl'])
    for combo_name, _, _, _, _, _, _, _, m in best_combos[:20]:
        print(f"  {combo_name:50s} | {fmt(m)}")

    # ══════════════════════════════════════════
    #  Section 5: OOS validation of best IS combos
    # ══════════════════════════════════════════
    if best_combos:
        print(f"\n" + "=" * 70)
        print("Section 5: OOS Validation of Top IS Combos")
        print("=" * 70)

        print(f"\n  {'Combo':50s} | {'IS':>45s} | {'OOS':>45s}")
        print(f"  {'-'*50}-+-{'-'*45}-+-{'-'*45}")

        for combo_name, efn, tp_l, tp_s, mh_l, mh_s, ext_l, ext_s, _ in best_combos[:15]:
            full = split_metrics(run_bt(df, WARMUP, n, efn,
                                        tp_l=tp_l, tp_s=tp_s, mh_l=mh_l, mh_s=mh_s,
                                        ext_l=ext_l, ext_s=ext_s), sp)
            oos_pos = "<<<" if full['oos']['pnl'] > 0 else ""
            print(f"  {combo_name:50s} | {fmt(full['is'])} | {fmt(full['oos'])} {oos_pos}")


if __name__ == '__main__':
    main()
