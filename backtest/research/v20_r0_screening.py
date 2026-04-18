"""
V20 R0: V14 Multi-Asset Screening
===================================
Run V14 with LOCKED parameters on all candidate assets.
Compare against ETH baseline. Output screening table.
"""

import pandas as pd
import numpy as np
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')

# =========================================================================
# V14 Parameters (LOCKED — do NOT change)
# =========================================================================

NOTIONAL = 4000
FEE = 4
WARMUP = 150

# L strategy
L_GK_S, L_GK_L = 5, 20
L_GK_TH = 25
L_BRK = 15
L_TP = 0.035
L_SN = 0.035
L_SN_SLIP = 0.25
L_MH = 6
L_EXT = 2
L_MFE_ACT = 0.010
L_MFE_TR = 0.008
L_CMH_BAR = 2
L_CMH_TH = -0.010
L_CMH_MH = 5
L_CD = 6
L_CAP = 20
L_BLK_H = {0, 1, 2, 12}
L_BLK_D = {5, 6}

# S strategy
S_GK_S, S_GK_L = 10, 30
S_GK_TH = 35
S_BRK = 15
S_TP = 0.020
S_SN = 0.040
S_SN_SLIP = 0.25
S_MH = 10
S_EXT = 2
S_CD = 8
S_CAP = 20
S_BLK_H = {0, 1, 2, 12}
S_BLK_D = {0, 5, 6}

# Circuit breakers
CB_DAILY = -200
CB_L_MONTH = -75
CB_S_MONTH = -150
CB_CONSEC = 4
CB_CONSEC_CD = 24

SYMBOLS = [
    'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT',
    'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'LTCUSDT', 'BCHUSDT',
]


# =========================================================================
# Fast rolling percentile
# =========================================================================

def rolling_pctile(vals, window):
    """Rolling percentile rank (kind='weak'). Input: numpy array."""
    out = np.full(len(vals), np.nan)
    for i in range(window - 1, len(vals)):
        w = vals[i - window + 1: i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < 10:
            continue
        out[i] = np.sum(valid <= vals[i]) / len(valid) * 100
    return out


# =========================================================================
# Compute Indicators
# =========================================================================

def compute_indicators(df):
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values

    # GK volatility
    log_hl = np.log(h / np.maximum(l, 1e-10))
    co_ratio = c / np.maximum(o, 1e-10)
    co_ratio = np.maximum(co_ratio, 1e-10)
    log_co = np.log(co_ratio)
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2

    gk_s = pd.Series(gk)
    close_s = pd.Series(c)

    # L: ratio 5/20 + pctile
    gk_mean_5 = gk_s.rolling(L_GK_S, min_periods=L_GK_S).mean()
    gk_mean_20 = gk_s.rolling(L_GK_L, min_periods=L_GK_L).mean()
    ratio_L = (gk_mean_5 / gk_mean_20.replace(0, np.nan)).values
    shifted_L = np.roll(ratio_L, 1)
    shifted_L[0] = np.nan
    pctile_L = rolling_pctile(shifted_L, 100)

    # S: ratio 10/30 + pctile
    gk_mean_10 = gk_s.rolling(S_GK_S, min_periods=S_GK_S).mean()
    gk_mean_30 = gk_s.rolling(S_GK_L, min_periods=S_GK_L).mean()
    ratio_S = (gk_mean_10 / gk_mean_30.replace(0, np.nan)).values
    shifted_S = np.roll(ratio_S, 1)
    shifted_S[0] = np.nan
    pctile_S = rolling_pctile(shifted_S, 100)

    # Breakout
    shifted_close = np.roll(c, 1)
    shifted_close[0] = np.nan
    high_15 = pd.Series(shifted_close).rolling(L_BRK, min_periods=L_BRK).max().values
    low_15 = pd.Series(shifted_close).rolling(S_BRK, min_periods=S_BRK).min().values
    brk_up = c > high_15
    brk_dn = c < low_15

    # Datetime features
    dt = pd.to_datetime(df['datetime'])
    hours = dt.dt.hour.values
    dows = dt.dt.dayofweek.values
    months = (dt.dt.year * 100 + dt.dt.month).values
    days = (dt.dt.year * 10000 + dt.dt.month * 100 + dt.dt.day).values

    return {
        'o': o, 'h': h, 'l': l, 'c': c,
        'pctile_L': pctile_L, 'pctile_S': pctile_S,
        'brk_up': brk_up, 'brk_dn': brk_dn,
        'hours': hours, 'dows': dows, 'months': months, 'days': days,
    }


# =========================================================================
# V14 Backtest Engine
# =========================================================================

def simulate_v14(ind, symbol):
    """Run V14 L+S simulation. Returns list of trade dicts."""
    o, h, l, c = ind['o'], ind['h'], ind['l'], ind['c']
    pL, pS = ind['pctile_L'], ind['pctile_S']
    brk_up, brk_dn = ind['brk_up'], ind['brk_dn']
    hours, dows = ind['hours'], ind['dows']
    months, days = ind['months'], ind['days']
    n = len(o)

    trades = []

    # --- State ---
    # L position
    lp_active = False
    lp_entry = 0.0
    lp_bar = 0
    lp_held = 0
    lp_mfe = 0.0
    lp_reduced = False
    lp_ext = False
    lp_ext_bars = 0
    l_pending = False

    # S position
    sp_active = False
    sp_entry = 0.0
    sp_bar = 0
    sp_held = 0
    sp_ext = False
    sp_ext_bars = 0
    s_pending = False

    # Cooldowns & caps
    l_last_exit = -999
    s_last_exit = -999
    cur_month = -1
    l_m_entries = 0
    s_m_entries = 0
    l_m_pnl = 0.0
    s_m_pnl = 0.0
    cur_day = -1
    d_pnl = 0.0
    consec = 0
    consec_end = -999

    for i in range(WARMUP, n):
        oi, hi, li, ci = o[i], h[i], l[i], c[i]
        hr = hours[i]
        dw = dows[i]
        mk = months[i]
        dk = days[i]

        # Month rollover
        if mk != cur_month:
            cur_month = mk
            l_m_entries = 0
            s_m_entries = 0
            l_m_pnl = 0.0
            s_m_pnl = 0.0

        # Day rollover
        if dk != cur_day:
            cur_day = dk
            d_pnl = 0.0

        # --- Execute pending entries ---
        if l_pending and not lp_active:
            lp_active = True
            lp_entry = oi
            lp_bar = i
            lp_held = 0
            lp_mfe = 0.0
            lp_reduced = False
            lp_ext = False
            lp_ext_bars = 0
            l_m_entries += 1
            l_pending = False

        if s_pending and not sp_active:
            sp_active = True
            sp_entry = oi
            sp_bar = i
            sp_held = 0
            sp_ext = False
            sp_ext_bars = 0
            s_m_entries += 1
            s_pending = False

        # --- L EXIT ---
        if lp_active:
            lp_held += 1
            ep = lp_entry
            bh = lp_held

            # Update MFE
            bar_mfe = (hi - ep) / ep
            if bar_mfe > lp_mfe:
                lp_mfe = bar_mfe

            ex_price = 0.0
            ex_reason = ''

            # 1. SafeNet
            sn_lv = ep * (1 - L_SN)
            if li <= sn_lv:
                ex_price = ep * (1 - L_SN * (1 + L_SN_SLIP))
                ex_reason = 'SN'
            # 2. TP
            elif hi >= ep * (1 + L_TP):
                ex_price = ep * (1 + L_TP)
                ex_reason = 'TP'
            else:
                cpnl = (ci - ep) / ep

                # 3. MFE Trail
                if lp_mfe >= L_MFE_ACT and (lp_mfe - cpnl) >= L_MFE_TR and bh >= 1:
                    ex_price = ci
                    ex_reason = 'MFE'
                else:
                    # 4. Conditional MH
                    if bh == L_CMH_BAR and cpnl <= L_CMH_TH:
                        lp_reduced = True

                    # 5. MaxHold / Extension
                    mh = L_CMH_MH if lp_reduced else L_MH
                    if not lp_ext:
                        if bh >= mh:
                            if cpnl > 0:
                                lp_ext = True
                                lp_ext_bars = 0
                            else:
                                ex_price = ci
                                ex_reason = 'MH'
                    else:
                        lp_ext_bars += 1
                        if li <= ep:
                            ex_price = ep
                            ex_reason = 'BE'
                        elif lp_ext_bars >= L_EXT:
                            ex_price = ci
                            ex_reason = 'MHx'

            if ex_price > 0:
                pnl_pct = (ex_price - ep) / ep
                pnl = pnl_pct * NOTIONAL - FEE
                trades.append({
                    'sym': symbol, 'side': 'L', 'entry_bar': lp_bar, 'exit_bar': i,
                    'entry_p': ep, 'exit_p': ex_price, 'pnl': pnl, 'reason': ex_reason,
                    'held': bh,
                })
                lp_active = False
                l_last_exit = i
                l_m_pnl += pnl
                d_pnl += pnl
                if pnl < 0:
                    consec += 1
                else:
                    consec = 0
                if consec >= CB_CONSEC:
                    consec_end = i + CB_CONSEC_CD

        # --- S EXIT ---
        if sp_active:
            sp_held += 1
            ep = sp_entry
            bh = sp_held

            ex_price = 0.0
            ex_reason = ''

            # 1. SafeNet
            sn_lv = ep * (1 + S_SN)
            if hi >= sn_lv:
                ex_price = ep * (1 + S_SN * (1 + S_SN_SLIP))
                ex_reason = 'SN'
            # 2. TP
            elif li <= ep * (1 - S_TP):
                ex_price = ep * (1 - S_TP)
                ex_reason = 'TP'
            else:
                cpnl = (ep - ci) / ep  # Short PnL

                # 3. MaxHold / Extension (S has no MFE trail / Cond MH)
                if not sp_ext:
                    if bh >= S_MH:
                        if cpnl > 0:
                            sp_ext = True
                            sp_ext_bars = 0
                        else:
                            ex_price = ci
                            ex_reason = 'MH'
                else:
                    sp_ext_bars += 1
                    if hi >= ep:
                        ex_price = ep
                        ex_reason = 'BE'
                    elif sp_ext_bars >= S_EXT:
                        ex_price = ci
                        ex_reason = 'MHx'

            if ex_price > 0:
                pnl_pct = (ep - ex_price) / ep
                pnl = pnl_pct * NOTIONAL - FEE
                trades.append({
                    'sym': symbol, 'side': 'S', 'entry_bar': sp_bar, 'exit_bar': i,
                    'entry_p': ep, 'exit_p': ex_price, 'pnl': pnl, 'reason': ex_reason,
                    'held': bh,
                })
                sp_active = False
                s_last_exit = i
                s_m_pnl += pnl
                d_pnl += pnl
                if pnl < 0:
                    consec += 1
                else:
                    consec = 0
                if consec >= CB_CONSEC:
                    consec_end = i + CB_CONSEC_CD

        # --- ENTRY CHECKS ---
        l_cb = (d_pnl <= CB_DAILY or l_m_pnl <= CB_L_MONTH or i < consec_end)
        s_cb = (d_pnl <= CB_DAILY or s_m_pnl <= CB_S_MONTH or i < consec_end)

        # L entry
        if (not lp_active and not l_pending and not l_cb and
            i - l_last_exit >= L_CD and l_m_entries < L_CAP and
            hr not in L_BLK_H and dw not in L_BLK_D and
            not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):
            l_pending = True

        # S entry
        if (not sp_active and not s_pending and not s_cb and
            i - s_last_exit >= S_CD and s_m_entries < S_CAP and
            hr not in S_BLK_H and dw not in S_BLK_D and
            not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):
            s_pending = True

    return trades


# =========================================================================
# Metrics
# =========================================================================

def compute_metrics(trades, n_bars):
    """Compute IS/OOS/monthly/WF metrics."""
    mid = n_bars // 2

    # Split IS/OOS
    is_t = [t for t in trades if t['entry_bar'] < mid]
    oos_t = [t for t in trades if t['entry_bar'] >= mid]

    def stats(tlist, label):
        if not tlist:
            return {'n': 0, 'pnl': 0, 'wr': 0, 'mdd': 0,
                    'l_n': 0, 'l_pnl': 0, 's_n': 0, 's_pnl': 0}
        pnls = [t['pnl'] for t in tlist]
        l_pnls = [t['pnl'] for t in tlist if t['side'] == 'L']
        s_pnls = [t['pnl'] for t in tlist if t['side'] == 'S']

        # MDD
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        mdd = np.max(dd) if len(dd) > 0 else 0

        return {
            'n': len(pnls), 'pnl': sum(pnls),
            'wr': sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0,
            'mdd': mdd,
            'l_n': len(l_pnls), 'l_pnl': sum(l_pnls) if l_pnls else 0,
            's_n': len(s_pnls), 's_pnl': sum(s_pnls) if s_pnls else 0,
        }

    is_s = stats(is_t, 'IS')
    oos_s = stats(oos_t, 'OOS')

    # Monthly breakdown (OOS only for screening)
    monthly = defaultdict(float)
    for t in oos_t:
        # Use exit_bar to determine month? Or entry? Use entry for consistency
        bar_idx = t['entry_bar']
        monthly[bar_idx // (24 * 30)] += t['pnl']  # Rough monthly bucketing

    # Better: use actual months from trade data
    # We'll compute this from the datetime in the main loop
    # For now, return raw OOS trades for monthly calculation

    # Walk-Forward 6-fold
    fold_size = n_bars // 6
    fold_pnl = [0.0] * 6
    for t in trades:
        fold_idx = min(t['entry_bar'] // fold_size, 5)
        fold_pnl[fold_idx] += t['pnl']
    wf6 = sum(1 for p in fold_pnl if p > 0)

    return {
        'is': is_s, 'oos': oos_s,
        'wf6': wf6, 'fold_pnl': fold_pnl,
        'oos_trades': oos_t, 'is_trades': is_t,
    }


def compute_monthly(trades, datetimes):
    """Compute monthly PnL from trades using datetime mapping."""
    monthly = defaultdict(float)
    for t in trades:
        bar_idx = t['entry_bar']
        if bar_idx < len(datetimes):
            dt = pd.Timestamp(datetimes[bar_idx])
            key = f"{dt.year}-{dt.month:02d}"
            monthly[key] += t['pnl']
    return dict(monthly)


# =========================================================================
# Main
# =========================================================================

if __name__ == '__main__':
    print("V20 R0: V14 Multi-Asset Screening")
    print("=" * 100)
    print(f"V14 LOCKED params | $1K/20x/$4K/$4fee | IS/OOS 50/50")
    print()

    all_results = {}

    for symbol in SYMBOLS:
        filename = f'{symbol}_1h_latest730d.csv'
        filepath = os.path.join(DATA_DIR, filename)

        if not os.path.exists(filepath):
            print(f"{symbol}: data not found, skipping")
            continue

        df = pd.read_csv(filepath)
        if len(df) < 1000:
            print(f"{symbol}: only {len(df)} bars, skipping")
            continue

        print(f"\n--- {symbol} ({len(df)} bars) ---")

        # Compute indicators
        ind = compute_indicators(df)

        # Basic stats
        returns = np.diff(ind['c']) / ind['c'][:-1]
        avg_abs_ret = np.nanmean(np.abs(returns))
        avg_move_usd = avg_abs_ret * NOTIONAL
        fee_pct = FEE / avg_move_usd * 100 if avg_move_usd > 0 else 999
        brk_freq = (np.nansum(ind['brk_up']) + np.nansum(ind['brk_dn'])) / len(df) * 100

        print(f"  avg|move|: ${avg_move_usd:.1f}  fee%: {fee_pct:.1f}%  brk_freq: {brk_freq:.1f}%")

        # Run V14 backtest
        trades = simulate_v14(ind, symbol)
        n_bars = len(df)
        metrics = compute_metrics(trades, n_bars)

        # Monthly PnL
        datetimes = df['datetime'].values
        oos_monthly = compute_monthly(metrics['oos_trades'], datetimes)
        all_monthly = compute_monthly(trades, datetimes)

        # Count positive months and worst month (OOS)
        oos_month_vals = list(oos_monthly.values()) if oos_monthly else [0]
        pos_months = sum(1 for v in oos_month_vals if v > 0)
        total_months = len(oos_month_vals)
        worst_month = min(oos_month_vals) if oos_month_vals else 0

        # IS monthly
        is_monthly = compute_monthly(metrics['is_trades'], datetimes)

        is_s = metrics['is']
        oos_s = metrics['oos']

        print(f"  IS:  {is_s['n']:>3}t  L:{is_s['l_n']:>2}/${is_s['l_pnl']:>+7.0f}  "
              f"S:{is_s['s_n']:>2}/${is_s['s_pnl']:>+7.0f}  "
              f"Total: ${is_s['pnl']:>+7.0f}  WR:{is_s['wr']:>5.1f}%  MDD:${is_s['mdd']:>.0f}")
        print(f"  OOS: {oos_s['n']:>3}t  L:{oos_s['l_n']:>2}/${oos_s['l_pnl']:>+7.0f}  "
              f"S:{oos_s['s_n']:>2}/${oos_s['s_pnl']:>+7.0f}  "
              f"Total: ${oos_s['pnl']:>+7.0f}  WR:{oos_s['wr']:>5.1f}%  MDD:${oos_s['mdd']:>.0f}")
        print(f"  OOS months: {pos_months}/{total_months} positive, worst: ${worst_month:+.0f}")
        print(f"  WF6: {metrics['wf6']}/6  folds: {['${:+.0f}'.format(p) for p in metrics['fold_pnl']]}")

        # Store
        all_results[symbol] = {
            'bars': n_bars,
            'avg_move': avg_move_usd,
            'fee_pct': fee_pct,
            'brk_freq': brk_freq,
            'is': is_s,
            'oos': oos_s,
            'pos_months': pos_months,
            'total_months': total_months,
            'worst_month': worst_month,
            'wf6': metrics['wf6'],
            'oos_monthly': oos_monthly,
        }

    # =====================================================================
    # SCREENING TABLE
    # =====================================================================

    print("\n\n" + "=" * 130)
    print("V20 R0 SCREENING TABLE")
    print("=" * 130)

    header = (f"{'Symbol':<10} {'Bars':>6} {'avg$':>6} {'Fee%':>5} {'BRK%':>5} "
              f"{'IS_PnL':>8} {'OOS_PnL':>8} {'OOS_WR':>6} {'OOS_L':>8} {'OOS_S':>8} "
              f"{'+Mon':>5} {'Worst':>7} {'MDD':>6} {'WF6':>4} {'Grade':>6}")
    print(header)
    print("-" * 130)

    for symbol in SYMBOLS:
        if symbol not in all_results:
            continue
        r = all_results[symbol]
        is_s = r['is']
        oos_s = r['oos']

        # Grading
        grade = ''
        pass_count = 0
        if oos_s['pnl'] > 0:
            pass_count += 1
        if is_s['pnl'] > 0:
            pass_count += 1
        if r['fee_pct'] < 40:
            pass_count += 1
        if r['wf6'] >= 3:
            pass_count += 1
        if r['pos_months'] >= 10 and r['total_months'] >= 13:
            pass_count += 1
        if r['worst_month'] >= -200:
            pass_count += 1

        if pass_count >= 5:
            grade = 'A'
        elif pass_count >= 4:
            grade = 'B'
        elif pass_count >= 3:
            grade = 'C'
        else:
            grade = 'F'

        marker = ''
        if symbol == 'ETHUSDT':
            marker = ' *BASE'
        elif grade in ('A', 'B'):
            marker = ' <<'

        print(f"{symbol:<10} {r['bars']:>6} {r['avg_move']:>5.0f} {r['fee_pct']:>4.0f}% {r['brk_freq']:>4.1f}% "
              f"{is_s['pnl']:>+7.0f} {oos_s['pnl']:>+7.0f} {oos_s['wr']:>5.1f}% "
              f"{oos_s['l_pnl']:>+7.0f} {oos_s['s_pnl']:>+7.0f} "
              f"{r['pos_months']:>2}/{r['total_months']:<2} {r['worst_month']:>+6.0f} "
              f"{oos_s['mdd']:>5.0f} {r['wf6']:>3}/6 {grade:>4}{marker}")

    # =====================================================================
    # SCREENING CRITERIA CHECK
    # =====================================================================

    print("\n\n" + "=" * 80)
    print("SCREENING CRITERIA")
    print("=" * 80)
    print("Required: IS>0, OOS>0, Fee%<40, WF6>=3")
    print("Bonus: +Mon>=10/13, Worst>=-$200, OOS>ETH")

    passed = []
    for symbol in SYMBOLS:
        if symbol == 'ETHUSDT' or symbol not in all_results:
            continue
        r = all_results[symbol]
        checks = {
            'IS>0': r['is']['pnl'] > 0,
            'OOS>0': r['oos']['pnl'] > 0,
            'Fee<40%': r['fee_pct'] < 40,
            'WF>=3': r['wf6'] >= 3,
        }
        all_pass = all(checks.values())
        status = 'PASS' if all_pass else 'FAIL'
        reasons = [k for k, v in checks.items() if not v]

        print(f"\n  {symbol}: {status}")
        for k, v in checks.items():
            print(f"    {'[x]' if v else '[ ]'} {k}: {v}")
        if not all_pass:
            print(f"    Failed: {', '.join(reasons)}")
        else:
            passed.append(symbol)
            # Bonus checks
            eth_oos = all_results.get('ETHUSDT', {}).get('oos', {}).get('pnl', 0)
            bonus = []
            if r['pos_months'] >= 10:
                bonus.append(f"+Mon {r['pos_months']}/{r['total_months']}")
            if r['worst_month'] >= -200:
                bonus.append(f"Worst ${r['worst_month']:+.0f}")
            if r['oos']['pnl'] > eth_oos:
                bonus.append(f"OOS > ETH")
            if bonus:
                print(f"    Bonus: {', '.join(bonus)}")

    # =====================================================================
    # RECOMMENDATION
    # =====================================================================

    print("\n\n" + "=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)

    if not passed:
        print("\nNO symbols passed all screening criteria.")
        print("V14 framework may only work on ETH.")
        print("Consider: some symbols might work with parameter adjustment (R1).")
    else:
        print(f"\n{len(passed)} symbol(s) passed screening: {', '.join(passed)}")
        print("\nRecommend for deep testing (R1-R3):")
        # Sort by OOS PnL
        passed_sorted = sorted(passed, key=lambda s: all_results[s]['oos']['pnl'], reverse=True)
        for rank, sym in enumerate(passed_sorted[:3], 1):
            r = all_results[sym]
            print(f"  #{rank} {sym}: OOS ${r['oos']['pnl']:+.0f}, "
                  f"WR {r['oos']['wr']:.1f}%, WF {r['wf6']}/6, "
                  f"+Mon {r['pos_months']}/{r['total_months']}")

    # Monthly detail for top candidates
    if passed:
        print("\n\n--- Monthly Detail (OOS) ---")
        eth_monthly = all_results.get('ETHUSDT', {}).get('oos_monthly', {})
        all_months_set = set()
        for sym in ['ETHUSDT'] + passed[:3]:
            if sym in all_results:
                all_months_set.update(all_results[sym].get('oos_monthly', {}).keys())

        sorted_months = sorted(all_months_set)
        print(f"{'Month':<10}", end='')
        for sym in ['ETHUSDT'] + passed[:3]:
            print(f" {sym:>10}", end='')
        print()

        for month in sorted_months:
            print(f"{month:<10}", end='')
            for sym in ['ETHUSDT'] + passed[:3]:
                mp = all_results.get(sym, {}).get('oos_monthly', {}).get(month, 0)
                print(f" {mp:>+9.0f}", end='')
            print()
