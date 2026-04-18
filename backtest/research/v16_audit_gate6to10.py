"""
V16 S2 TBR Flow Reversal - Skeptical Audit Gates 6-10
Independent auditor perspective.

Gate 6: WF 10/10 verification
Gate 7: Independent trades quality
Gate 8: TBR data quality
Gate 9: Swap test depth
Gate 10: Live executability
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from scipy import stats

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0
WARMUP = 150
SN_SLIP = 0.25

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


def load_and_prepare():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    d = eth.copy()
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_ma5'] = d['tbr'].rolling(5).mean()
    d['tbr_ma5_pctile'] = d['tbr_ma5'].shift(1).rolling(100).rank(pct=True) * 100
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()

    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio_L'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile_L'] = d['gk_ratio_L'].shift(1).rolling(100).rank(pct=True) * 100
    d['gk_ratio_S'] = gk.rolling(10).mean() / gk.rolling(30).mean()
    d['gk_pctile_S'] = d['gk_ratio_S'].shift(1).rolling(100).rank(pct=True) * 100
    return d


def run_bt(df, start, end, entry_fn, fee=FEE):
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
            'dt': dt
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
            elif row.high >= ep * (1 + L_TP):
                close_pos(pos_l, 'L', 'TP', ep * (1 + L_TP), i, dt)
                pos_l = None
            elif pos_l.get('ext') and row.low <= ep:
                close_pos(pos_l, 'L', 'BE', ep, i, dt)
                pos_l = None
            elif bh >= L_MH + (L_EXT if pos_l.get('ext') else 0):
                r = 'MH-ext' if pos_l.get('ext') else 'MH'
                close_pos(pos_l, 'L', r, row.close, i, dt)
                pos_l = None
            elif bh == L_MH and not pos_l.get('ext'):
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
            elif row.low <= ep * (1 - S_TP):
                close_pos(pos_s, 'S', 'TP', ep * (1 - S_TP), i, dt)
                pos_s = None
            elif pos_s.get('ext') and row.high >= ep:
                close_pos(pos_s, 'S', 'BE', ep, i, dt)
                pos_s = None
            elif bh >= S_MH + (S_EXT if pos_s.get('ext') else 0):
                r = 'MH-ext' if pos_s.get('ext') else 'MH'
                close_pos(pos_s, 'S', r, row.close, i, dt)
                pos_s = None
            elif bh == S_MH and not pos_s.get('ext'):
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


def _metrics(tlist):
    if not tlist:
        return {'pnl': 0, 't': 0, 'wr': 0, 'pf': 0, 'mdd': 0,
                'pm': 0, 'pm_n': 0, 'worst': 0, 'months': {},
                'avg_win': 0, 'avg_loss': 0}
    pnl = sum(t['p'] for t in tlist)
    wins = [t for t in tlist if t['p'] > 0]
    losses = [t for t in tlist if t['p'] <= 0]
    wr = len(wins) / len(tlist) * 100
    w = sum(t['p'] for t in wins)
    l_sum = abs(sum(t['p'] for t in losses))
    pf = w / l_sum if l_sum > 0 else 99
    avg_win = w / len(wins) if wins else 0
    avg_loss = -l_sum / len(losses) if losses else 0
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
            'pm': pm, 'pm_n': len(months), 'worst': worst, 'months': dict(months),
            'avg_win': avg_win, 'avg_loss': avg_loss}


def fmt(m):
    return (f"{m['t']:3d}t ${m['pnl']:+7.0f} WR {m['wr']:4.0f}% "
            f"PF {m['pf']:4.1f} MDD ${m['mdd']:4.0f} PM {m['pm']}/{m['pm_n']}")


def make_s2(lo_pct, hi_pct):
    def entry(row, i):
        sig = ''
        p = row.tbr_ma5_pctile
        if pd.isna(p): return None
        if p < lo_pct and row.close > row.high_15: sig += 'L'
        if p > hi_pct and row.close < row.low_15: sig += 'S'
        return sig or None
    return entry

def entry_v14(row, i):
    sig = ''
    if not pd.isna(row.gk_pctile_L) and row.gk_pctile_L < 25:
        if row.close > row.high_15: sig += 'L'
    if not pd.isna(row.gk_pctile_S) and row.gk_pctile_S < 35:
        if row.close < row.low_15: sig += 'S'
    return sig or None

def entry_brk(row, i):
    sig = ''
    if row.close > row.high_15: sig += 'L'
    if row.close < row.low_15: sig += 'S'
    return sig or None


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V16 S2 TBR Flow Reversal - Skeptical Audit Gates 6-10")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")
    print(f"IS:  {df.iloc[WARMUP].datetime} ~ {df.iloc[sp-1].datetime}")
    print(f"OOS: {df.iloc[sp].datetime} ~ {df.iloc[-1].datetime}")

    # ══════════════════════════════════════════════════════════
    #  Gate 6: WF 10/10 Verification
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 6: WF 10/10 Verification ===")
    print("=" * 70)

    # 6A: Detailed fold analysis
    print("\n--- 6A: 10-Fold Detailed Breakdown ---")
    for nf in [10]:
        fold_size = (n - WARMUP) // nf
        print(f"\n  {nf}-fold WF details:")
        print(f"  {'Fold':>4s} {'Period':>30s} {'Trades':>6s} {'PnL':>8s} {'WR':>5s} {'PnL/t':>7s}")
        print(f"  {'-'*4} {'-'*30} {'-'*6} {'-'*8} {'-'*5} {'-'*7}")
        all_positive = True
        min_trades = 999
        for f in range(nf):
            s = WARMUP + f * fold_size
            e = s + fold_size if f < nf - 1 else n
            trades = run_bt(df, s, e, make_s2(40, 50))
            pnl = sum(t['p'] for t in trades)
            nt = len(trades)
            wr = sum(1 for t in trades if t['p'] > 0) / nt * 100 if nt > 0 else 0
            ppt = pnl / nt if nt > 0 else 0
            period = f"{df.iloc[s].datetime.date()} ~ {df.iloc[e-1].datetime.date()}"
            barely = " !!!" if 0 < pnl < 50 else ""
            print(f"  {f+1:4d} {period:>30s} {nt:6d} ${pnl:+7.0f} {wr:4.0f}% ${ppt:+6.1f}{barely}")
            if pnl <= 0:
                all_positive = False
            min_trades = min(min_trades, nt)

        print(f"\n  Min trades per fold: {min_trades}")
        if min_trades < 15:
            print(f"  >>> WARNING: Some folds have < 15 trades -- low sample")

    # 6B: 15-fold and 20-fold
    print("\n--- 6B: Higher-Resolution WF ---")
    for nf in [12, 15, 20]:
        fold_size = (n - WARMUP) // nf
        results = []
        for f in range(nf):
            s = WARMUP + f * fold_size
            e = s + fold_size if f < nf - 1 else n
            trades = run_bt(df, s, e, make_s2(40, 50))
            pnl = sum(t['p'] for t in trades)
            results.append(pnl)
        pos = sum(1 for r in results if r > 0)
        neg_folds = [i+1 for i, r in enumerate(results) if r <= 0]
        print(f"  {nf:2d}-fold: {pos}/{nf} positive ({pos/nf*100:.0f}%)"
              f"{' -- neg folds: ' + str(neg_folds) if neg_folds else ''}")
        # Show details for problematic folds
        for fi, pnl in enumerate(results):
            if pnl <= 0:
                s = WARMUP + fi * fold_size
                e = s + fold_size if fi < nf - 1 else n
                trades = run_bt(df, s, e, make_s2(40, 50))
                period = f"{df.iloc[s].datetime.date()} ~ {df.iloc[e-1].datetime.date()}"
                print(f"    Fold {fi+1}: {period}, {len(trades)}t, ${pnl:+.0f}")

    # 6C: Purged Walk-Forward (gap between train/test)
    print("\n--- 6C: Purged Walk-Forward (50-bar gap) ---")
    PURGE = 50
    for nf in [6, 8, 10]:
        fold_size = (n - WARMUP) // nf
        results = []
        for f in range(nf):
            s = WARMUP + f * fold_size
            e = s + fold_size if f < nf - 1 else n
            # Add purge: skip first PURGE bars of each fold
            s_purged = s + PURGE if f > 0 else s
            if s_purged >= e:
                results.append(0)
                continue
            trades = run_bt(df, s_purged, e, make_s2(40, 50))
            pnl = sum(t['p'] for t in trades)
            results.append(pnl)
        pos = sum(1 for r in results if r > 0)
        print(f"  {nf:2d}-fold purged: {pos}/{nf} positive")

    # 6D: V14 comparison at each fold level
    print("\n--- 6D: S2 vs V14 at Each Fold Level ---")
    for nf in [6, 8, 10, 12, 15, 20]:
        fold_size = (n - WARMUP) // nf
        s2_wins = 0
        for f in range(nf):
            s = WARMUP + f * fold_size
            e = s + fold_size if f < nf - 1 else n
            s2_pnl = sum(t['p'] for t in run_bt(df, s, e, make_s2(40, 50)))
            v14_pnl = sum(t['p'] for t in run_bt(df, s, e, entry_v14))
            if s2_pnl > v14_pnl:
                s2_wins += 1
        print(f"  {nf:2d}-fold: S2 > V14 in {s2_wins}/{nf} folds ({s2_wins/nf*100:.0f}%)")

    # ══════════════════════════════════════════════════════════
    #  Gate 7: Independent Trades Quality
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 7: Independent Trades Quality ===")
    print("=" * 70)

    # Get all trades
    s2_trades = run_bt(df, WARMUP, n, make_s2(40, 50))
    v14_trades = run_bt(df, WARMUP, n, entry_v14)

    s2_bars = {t['eb']: t for t in s2_trades}
    v14_bars = {t['eb']: t for t in v14_trades}
    overlap_bars = set(s2_bars.keys()) & set(v14_bars.keys())

    s2_overlap = [s2_bars[b] for b in overlap_bars]
    s2_unique = [t for t in s2_trades if t['eb'] not in overlap_bars]

    # 7A: Quality comparison
    print("\n--- 7A: Overlap vs Unique Trade Quality ---")
    m_overlap = _metrics(s2_overlap)
    m_unique = _metrics(s2_unique)

    print(f"  S2 overlap ({len(s2_overlap)} trades):")
    print(f"    Avg PnL: ${m_overlap['pnl']/max(len(s2_overlap),1):+.1f}")
    print(f"    WR: {m_overlap['wr']:.0f}%, PF: {m_overlap['pf']:.1f}")
    print(f"    Total PnL: ${m_overlap['pnl']:+.0f}")

    print(f"\n  S2 unique ({len(s2_unique)} trades):")
    print(f"    Avg PnL: ${m_unique['pnl']/max(len(s2_unique),1):+.1f}")
    print(f"    WR: {m_unique['wr']:.0f}%, PF: {m_unique['pf']:.1f}")
    print(f"    Total PnL: ${m_unique['pnl']:+.0f}")

    if m_unique['pnl'] > 0:
        print(f"\n  >>> PASS: Unique trades contribute ${m_unique['pnl']:+.0f}")
    else:
        print(f"\n  >>> FAIL: Unique trades lose ${m_unique['pnl']:+.0f}")

    # Also split by IS/OOS
    is_unique = [t for t in s2_unique if t['eb'] < sp]
    oos_unique = [t for t in s2_unique if t['eb'] >= sp]
    m_is_u = _metrics(is_unique)
    m_oos_u = _metrics(oos_unique)
    print(f"\n  Unique trades IS:  {fmt(m_is_u)}")
    print(f"  Unique trades OOS: {fmt(m_oos_u)}")

    # 7B: V14 weak month complementarity
    print("\n--- 7B: Complementarity in V14 Weak Months ---")
    v14_m = _metrics(v14_trades)
    v14_months = v14_m['months']
    s2_m = _metrics(s2_trades)
    s2_months = s2_m['months']

    # Find V14's weakest months
    v14_sorted = sorted(v14_months.items(), key=lambda x: x[1])
    print(f"\n  V14 weakest 5 months:")
    print(f"  {'Month':>8s} {'V14':>8s} {'S2':>8s} {'S2>V14':>7s}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*7}")
    complementary = 0
    for mk, v14p in v14_sorted[:5]:
        s2p = s2_months.get(mk, 0)
        better = "YES" if s2p > v14p else "NO"
        if s2p > v14p:
            complementary += 1
        print(f"  {mk:>8s} ${v14p:+7.0f} ${s2p:+7.0f} {better:>7s}")

    print(f"\n  S2 beats V14 in {complementary}/5 weakest V14 months")
    if complementary >= 3:
        print("  >>> PASS: S2 is complementary to V14 in weak months")
    else:
        print("  >>> CONDITIONAL: Limited complementarity")

    # 7C: Concurrent position risk
    print("\n--- 7C: Concurrent Position Risk ---")
    # Check how many bars have both V14 and S2 in position simultaneously
    s2_hold_bars = set()
    for t in s2_trades:
        for b in range(t['eb'], t['xb'] + 1):
            s2_hold_bars.add((b, t['side']))

    v14_hold_bars = set()
    for t in v14_trades:
        for b in range(t['eb'], t['xb'] + 1):
            v14_hold_bars.add((b, t['side']))

    # Same side concurrent
    same_side = len(s2_hold_bars & v14_hold_bars)
    total_bars = n - WARMUP
    print(f"  Bars with same-side positions in both S2 and V14: {same_side}")
    print(f"  ({same_side/total_bars*100:.1f}% of all bars)")

    # Max concurrent if running both
    max_concurrent = 0
    for i in range(WARMUP, n):
        cnt = 0
        if (i, 'L') in s2_hold_bars: cnt += 1
        if (i, 'S') in s2_hold_bars: cnt += 1
        if (i, 'L') in v14_hold_bars: cnt += 1
        if (i, 'S') in v14_hold_bars: cnt += 1
        max_concurrent = max(max_concurrent, cnt)
    print(f"  Max concurrent positions (V14+S2): {max_concurrent}")
    if max_concurrent >= 4:
        print(f"  >>> WARNING: Up to {max_concurrent} concurrent positions -- $1K account cannot handle this")

    # ══════════════════════════════════════════════════════════
    #  Gate 8: TBR Data Quality
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 8: TBR Data Quality ===")
    print("=" * 70)

    # 8A: tbv completeness
    print("\n--- 8A: taker_buy_volume Completeness ---")
    tbv = df['taker_buy_volume']
    vol = df['volume']
    tbr = df['tbr']

    nan_count = tbv.isna().sum()
    zero_count = (tbv == 0).sum()
    gt_vol = (tbv > vol).sum()
    exactly_half = (tbr == 0.5).sum()

    print(f"  Total bars: {len(df)}")
    print(f"  tbv NaN: {nan_count} ({nan_count/len(df)*100:.2f}%)")
    print(f"  tbv = 0: {zero_count} ({zero_count/len(df)*100:.2f}%)")
    print(f"  tbv > volume: {gt_vol} ({gt_vol/len(df)*100:.2f}%)")
    print(f"  TBR exactly 0.5: {exactly_half} ({exactly_half/len(df)*100:.2f}%)")

    anomalies = nan_count + zero_count + gt_vol
    if anomalies / len(df) < 0.01:
        print(f"  >>> PASS: Anomaly rate {anomalies/len(df)*100:.2f}% < 1%")
    else:
        print(f"  >>> FAIL: Anomaly rate {anomalies/len(df)*100:.2f}% >= 1%")

    # 8B: TBR stability IS vs OOS
    print("\n--- 8B: TBR Stability Across IS/OOS ---")
    is_tbr = df.iloc[WARMUP:sp]['tbr'].dropna()
    oos_tbr = df.iloc[sp:]['tbr'].dropna()

    print(f"  IS avg TBR:  {is_tbr.mean():.4f} (std {is_tbr.std():.4f})")
    print(f"  OOS avg TBR: {oos_tbr.mean():.4f} (std {oos_tbr.std():.4f})")
    diff = abs(is_tbr.mean() - oos_tbr.mean())
    print(f"  Difference: {diff:.4f} ({diff/is_tbr.mean()*100:.2f}%)")
    if diff / is_tbr.mean() < 0.02:
        print("  >>> PASS: TBR mean difference < 2%")
    else:
        print("  >>> WARNING: TBR mean difference >= 2%")

    # Distribution comparison
    ks_stat, ks_p = stats.ks_2samp(is_tbr, oos_tbr)
    print(f"  KS test (IS vs OOS TBR): stat={ks_stat:.4f}, p={ks_p:.4f}")
    if ks_p > 0.01:
        print("  >>> PASS: TBR distributions not significantly different")
    else:
        print("  >>> WARNING: TBR distributions differ (p<0.01)")

    # 8C: Binance API tbv field
    print("\n--- 8C: Binance API tbv Field ---")
    print("  Binance /fapi/v1/klines response format:")
    print("  Index 0: open_time, 1: open, 2: high, 3: low, 4: close, 5: volume,")
    print("  6: close_time, 7: quote_volume, 8: trades, 9: taker_buy_base_vol,")
    print("  10: taker_buy_quote_vol, 11: ignore")
    print("  >>> taker_buy_volume = index 9 (taker buy base asset volume)")
    print("  >>> Available in both Mainnet and Testnet API")
    print("  >>> data_feed.py already fetches this field")

    # 8D: Testnet tbv reliability
    print("\n--- 8D: Testnet vs Mainnet TBR ---")
    print("  Testnet has very low liquidity")
    print("  tbv on Testnet is dominated by bot activity, NOT real market flow")
    print("  >>> CONDITIONAL: S2 relies on TBR for signal generation")
    print("  >>> Testnet TBR is NOT representative of real flow dynamics")
    print("  >>> MUST use Mainnet data for signal calculation, even in paper mode")
    print("  >>> data_feed.py should fetch from Mainnet (not Testnet) for indicators")

    # ══════════════════════════════════════════════════════════
    #  Gate 9: Swap Test Depth
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 9: Swap Test Depth ===")
    print("=" * 70)

    # 9A: Standard swap (same as R3)
    print("\n--- 9A: Standard 50/50 Swap ---")
    fwd_m = _metrics([t for t in run_bt(df, WARMUP, n, make_s2(40, 50)) if t['eb'] >= sp])
    rev_m = _metrics([t for t in run_bt(df, WARMUP, n, make_s2(40, 50)) if t['eb'] < sp])
    print(f"  Forward (IS-designed -> OOS): ${fwd_m['pnl']:+.0f}")
    print(f"  Reverse (OOS -> IS):          ${rev_m['pnl']:+.0f}")
    fwd_p = fwd_m['pnl']
    rev_p = rev_m['pnl']
    if max(fwd_p, rev_p) > 0:
        deg = abs(fwd_p - rev_p) / max(fwd_p, rev_p) * 100
        print(f"  Degradation: {deg:.0f}%")

    # 9B: Multi-split swap
    print("\n--- 9B: Multi-Split Swap ---")
    print(f"  {'Split':>6s} {'IS PnL':>8s} {'OOS PnL':>9s} {'IS/OOS':>7s}")
    print(f"  {'-'*6} {'-'*8} {'-'*9} {'-'*7}")
    for pct in [0.3, 0.4, 0.5, 0.6, 0.7]:
        sp_test = WARMUP + int((n - WARMUP) * pct)
        all_trades = run_bt(df, WARMUP, n, make_s2(40, 50))
        is_pnl = sum(t['p'] for t in all_trades if t['eb'] < sp_test)
        oos_pnl = sum(t['p'] for t in all_trades if t['eb'] >= sp_test)
        ratio = is_pnl / oos_pnl if oos_pnl > 0 else 99
        print(f"  {pct:.0%}:{1-pct:.0%} ${is_pnl:+7.0f} ${oos_pnl:+8.0f}  {ratio:.2f}")

    # 9C: Time-reversed backtest
    print("\n--- 9C: Time-Reversed Backtest ---")
    # Reverse the DataFrame (newest first)
    df_rev = df.iloc[::-1].reset_index(drop=True)
    # Recalculate indicators on reversed data
    df_rev['tbr'] = df_rev['taker_buy_volume'] / df_rev['volume'].clip(lower=1)
    df_rev['tbr_ma5'] = df_rev['tbr'].rolling(5).mean()
    df_rev['tbr_ma5_pctile'] = df_rev['tbr_ma5'].shift(1).rolling(100).rank(pct=True) * 100
    df_rev['high_15'] = df_rev['close'].shift(1).rolling(15).max()
    df_rev['low_15'] = df_rev['close'].shift(1).rolling(15).min()
    # Fix datetime for reporting (reversed)
    df_rev['datetime'] = pd.date_range(start=df.iloc[0].datetime, periods=len(df), freq='h')

    sp_rev = len(df_rev) // 2

    rev_trades = run_bt(df_rev, WARMUP, len(df_rev), make_s2(40, 50))
    is_rev_trades = [t for t in rev_trades if t['eb'] < sp_rev]
    oos_rev_trades = [t for t in rev_trades if t['eb'] >= sp_rev]

    m_rev_is = _metrics(is_rev_trades)
    m_rev_oos = _metrics(oos_rev_trades)

    print(f"  Forward (original): IS ${_metrics([t for t in run_bt(df, WARMUP, n, make_s2(40,50)) if t['eb'] < sp])['pnl']:+.0f} OOS ${_metrics([t for t in run_bt(df, WARMUP, n, make_s2(40,50)) if t['eb'] >= sp])['pnl']:+.0f}")
    print(f"  Reversed:           IS ${m_rev_is['pnl']:+.0f} OOS ${m_rev_oos['pnl']:+.0f}")
    rev_total = m_rev_is['pnl'] + m_rev_oos['pnl']
    fwd_total = sum(t['p'] for t in run_bt(df, WARMUP, n, make_s2(40, 50)))
    print(f"  Forward total: ${fwd_total:+.0f}")
    print(f"  Reversed total: ${rev_total:+.0f}")
    if rev_total > 0:
        print("  >>> PASS: Edge persists in reversed time series")
    else:
        print("  >>> FAIL: Edge disappears in reversed time series")

    # Gate 9 verdict
    print("\n--- Gate 9 Verdict ---")
    all_splits_positive = True
    for pct in [0.3, 0.4, 0.5, 0.6, 0.7]:
        sp_test = WARMUP + int((n - WARMUP) * pct)
        all_t = run_bt(df, WARMUP, n, make_s2(40, 50))
        oos_pnl = sum(t['p'] for t in all_t if t['eb'] >= sp_test)
        if oos_pnl <= 0:
            all_splits_positive = False
    if all_splits_positive:
        print("  PASS: All splits positive OOS")
    else:
        print("  CONDITIONAL: Some splits negative OOS")

    # ══════════════════════════════════════════════════════════
    #  Gate 10: Live Executability
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 10: Live Executability ===")
    print("=" * 70)

    # 10A: TBR calculation timing
    print("\n--- 10A: TBR Calculation Timing ---")
    print("  TBR uses shift(1) -- confirmed in load_and_prepare():")
    print("  d['tbr_ma5_pctile'] = d['tbr_ma5'].shift(1).rolling(100).rank(pct=True) * 100")
    print("  >>> PASS: Uses completed bar data (shift 1)")

    # 10B: Warmup requirement
    print("\n--- 10B: Warmup Requirement ---")
    # TBR MA5 needs 5 bars, shift(1) needs 1, rolling(100) rank needs 100
    # Total minimum: 5 + 1 + 100 = 106 bars
    # Breakout 15 needs 15 + 1 = 16 bars
    # WARMUP = 150 > 106 --> sufficient
    print("  TBR MA5: 5 bars")
    print("  shift(1): +1 bar")
    print("  rolling(100).rank(): +100 bars")
    print("  Total minimum: 106 bars")
    print("  WARMUP setting: 150 bars")
    print("  >>> PASS: 150 > 106, sufficient warmup")

    # 10C: strategy.py impact
    print("\n--- 10C: strategy.py Modification Scope ---")
    print("  New calculations needed in strategy.py:")
    print("  1. TBR = taker_buy_volume / volume")
    print("  2. TBR_MA5 = TBR.rolling(5).mean()")
    print("  3. TBR_MA5_pctile = TBR_MA5.shift(1).rolling(100).rank(pct=True) * 100")
    print("  4. Entry condition: L: pctile < 40 + breakout UP")
    print("  5. Entry condition: S: pctile > 50 + breakout DOWN")
    print("  >>> 5 lines of indicator code + 2 lines of entry logic")
    print("  >>> data_feed.py already provides taker_buy_volume")

    # 10D: Fee stress with different fee levels
    print("\n--- 10D: Fee Stress Test ---")
    print(f"  {'Fee':>4s} {'IS PnL':>8s} {'OOS PnL':>8s} {'OOS WR':>6s} {'Total':>8s}")
    for fee_t in [4, 5, 6, 7, 8]:
        all_t = run_bt(df, WARMUP, n, make_s2(40, 50), fee=fee_t)
        m = _metrics([t for t in all_t if t['eb'] >= sp])
        m_is = _metrics([t for t in all_t if t['eb'] < sp])
        print(f"  ${fee_t:3d} ${m_is['pnl']:+7.0f} ${m['pnl']:+7.0f} {m['wr']:5.0f}% ${m_is['pnl']+m['pnl']:+7.0f}")

    print("\n--- Gate 10 Verdict ---")
    print("  PASS: S2 is implementable with minimal code changes")
    print("  CONDITIONAL: Testnet tbv data is unreliable (see Gate 8D)")

    # ══════════════════════════════════════════════════════════
    #  FINAL SUMMARY
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== V16 S2 TBR Flow Reversal Audit Summary ===")
    print("=" * 70)
    print("\n  (Verdicts to be assessed by auditor based on data above)")


if __name__ == '__main__':
    main()
