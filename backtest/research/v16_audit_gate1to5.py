"""
V16 S2 TBR Flow Reversal - Skeptical Audit Gates 1-5
Independent auditor perspective: S2 is a happy table until proven otherwise.

Gate 1: TBR vs Momentum (換皮?)
Gate 2: IS/OOS 0.99 too perfect?
Gate 3: Engine inflation calibration
Gate 4: Threshold 40/50 robustness
Gate 5: Breakout shared component analysis
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from scipy import stats

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Account
MARGIN, LEVERAGE = 200, 20
NOTIONAL = MARGIN * LEVERAGE
FEE = 4.0
WARMUP = 150
SN_SLIP = 0.25

# V14 exit params
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

    # TBR
    d['tbr'] = d['taker_buy_volume'] / d['volume'].clip(lower=1)
    d['tbr_ma5'] = d['tbr'].rolling(5).mean()
    d['tbr_ma5_pctile'] = d['tbr_ma5'].shift(1).rolling(100).rank(pct=True) * 100

    # Breakout
    d['high_15'] = d['close'].shift(1).rolling(15).max()
    d['low_15'] = d['close'].shift(1).rolling(15).min()

    # GK
    ln_hl = np.log(d['high'] / d['low'])
    ln_co = np.log(d['close'] / d['open'])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    d['gk_ratio_L'] = gk.rolling(5).mean() / gk.rolling(20).mean()
    d['gk_pctile_L'] = d['gk_ratio_L'].shift(1).rolling(100).rank(pct=True) * 100
    d['gk_ratio_S'] = gk.rolling(10).mean() / gk.rolling(30).mean()
    d['gk_pctile_S'] = d['gk_ratio_S'].shift(1).rolling(100).rank(pct=True) * 100

    # Momentum indicators for Gate 1
    d['ret_1'] = d['close'].pct_change(1)
    d['ret_3'] = d['close'].pct_change(3)
    d['ret_5'] = d['close'].pct_change(5)
    d['ret_10'] = d['close'].pct_change(10)
    d['ema20'] = d['close'].ewm(span=20).mean()
    d['ema_dev'] = (d['close'] - d['ema20']) / d['ema20']
    d['dist_high15'] = (d['close'] - d['high_15']) / d['high_15']
    d['dist_low15'] = (d['close'] - d['low_15']) / d['low_15']

    # Momentum pctile for Gate 1C
    d['ret5_pctile'] = d['ret_5'].shift(1).rolling(100).rank(pct=True) * 100

    return d


# ══════════════════════════════════════════
#  Backtest Engine (from R3, simple exits)
# ══════════════════════════════════════════

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

        # Exit S
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

        # Entry
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


def split_metrics(trades, sp):
    is_t = [t for t in trades if t['eb'] < sp]
    oos_t = [t for t in trades if t['eb'] >= sp]
    return {'is': _metrics(is_t), 'oos': _metrics(oos_t), 'is_trades': is_t, 'oos_trades': oos_t}


def fmt(m):
    return (f"{m['t']:3d}t ${m['pnl']:+7.0f} WR {m['wr']:4.0f}% "
            f"PF {m['pf']:4.1f} MDD ${m['mdd']:4.0f} PM {m['pm']}/{m['pm_n']}")


# Entry signals
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

# Gate 1C: momentum proxy for S2
def make_momentum_s2(lo_pct, hi_pct):
    """Replace TBR with 5-bar return percentile (same rolling 100 rank)."""
    def entry(row, i):
        sig = ''
        p = row.ret5_pctile
        if pd.isna(p): return None
        if p < lo_pct and row.close > row.high_15: sig += 'L'
        if p > hi_pct and row.close < row.low_15: sig += 'S'
        return sig or None
    return entry


def main():
    df = load_and_prepare()
    sp = len(df) // 2
    n = len(df)

    print("=" * 70)
    print("V16 S2 TBR Flow Reversal - Skeptical Audit Gates 1-5")
    print("=" * 70)
    print(f"Data: {n} bars, split at {sp}")
    print(f"IS:  {df.iloc[WARMUP].datetime} ~ {df.iloc[sp-1].datetime}")
    print(f"OOS: {df.iloc[sp].datetime} ~ {df.iloc[-1].datetime}")

    # ══════════════════════════════════════════════════════════
    #  Gate 1: TBR vs Momentum
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 1: TBR vs Momentum ===")
    print("=" * 70)

    # 1A: Correlation analysis
    print("\n--- 1A: TBR vs Momentum Correlations ---")
    valid = df.dropna(subset=['tbr_ma5_pctile', 'ret_1', 'ret_3', 'ret_5', 'ret_10',
                              'ema_dev', 'dist_high15', 'dist_low15'])

    mom_cols = {
        '1-bar return': 'ret_1',
        '3-bar return': 'ret_3',
        '5-bar return': 'ret_5',
        '10-bar return': 'ret_10',
        'EMA20 deviation': 'ema_dev',
        'Dist to 15-high': 'dist_high15',
        'Dist to 15-low': 'dist_low15',
    }

    print(f"\n  {'Indicator':20s} {'Pearson':>8s} {'Spearman':>9s}")
    print(f"  {'-'*20} {'-'*8} {'-'*9}")
    all_high = True
    for name, col in mom_cols.items():
        pearson_r, _ = stats.pearsonr(valid['tbr_ma5_pctile'], valid[col])
        spearman_r, _ = stats.spearmanr(valid['tbr_ma5_pctile'], valid[col])
        print(f"  {name:20s} {pearson_r:+.4f}   {spearman_r:+.4f}")
        if abs(pearson_r) < 0.5:
            all_high = False

    if all_high:
        print("  >>> WARNING: All correlations > 0.5 -- TBR may be momentum proxy")
    else:
        print("  >>> Some correlations < 0.5 -- TBR has independent component")

    # 1B: Residual predictive power after controlling for momentum
    print("\n--- 1B: TBR Residual Predictive Power ---")
    # Use IS only for this analysis
    is_df = valid[valid.index < sp].copy()
    is_df['next_ret'] = is_df['close'].pct_change(1).shift(-1)
    is_df = is_df.dropna(subset=['next_ret'])

    # OLS via numpy (no sklearn needed)
    X_mom_raw = is_df[['ret_5']].values
    X_both_raw = is_df[['ret_5', 'tbr_ma5_pctile']].values
    y = is_df['next_ret'].values
    n_obs = len(y)

    # Add intercept
    X_mom = np.column_stack([np.ones(n_obs), X_mom_raw])
    X_both = np.column_stack([np.ones(n_obs), X_both_raw])

    # Model 1: momentum only
    beta1 = np.linalg.lstsq(X_mom, y, rcond=None)[0]
    y_pred1 = X_mom @ beta1
    ss_res1 = np.sum((y - y_pred1)**2)
    ss_tot = np.sum((y - y.mean())**2)
    r2_mom = 1 - ss_res1 / ss_tot

    # Model 2: momentum + TBR
    beta2 = np.linalg.lstsq(X_both, y, rcond=None)[0]
    y_pred2 = X_both @ beta2
    ss_res2 = np.sum((y - y_pred2)**2)
    r2_both = 1 - ss_res2 / ss_tot

    # T-test for TBR coefficient significance
    residuals = y - y_pred2
    mse = np.sum(residuals**2) / (n_obs - 3)  # 3 params: intercept + 2 features
    XtX_inv = np.linalg.inv(X_both.T @ X_both)
    se_beta = np.sqrt(mse * np.diag(XtX_inv))
    t_stat_tbr = beta2[2] / se_beta[2]  # index 2 = TBR (after intercept)
    p_val_tbr = 2 * stats.t.sf(abs(t_stat_tbr), n_obs - 3)

    print(f"  R2 (momentum only): {r2_mom:.6f}")
    print(f"  R2 (momentum + TBR): {r2_both:.6f}")
    print(f"  R2 improvement: {r2_both - r2_mom:.6f}")
    print(f"  TBR coefficient: {beta2[2]:.8f}")
    print(f"  TBR t-stat: {t_stat_tbr:.3f}")
    print(f"  TBR p-value: {p_val_tbr:.4f}")
    if p_val_tbr < 0.05:
        print(f"  >>> TBR has SIGNIFICANT residual predictive power (p={p_val_tbr:.4f})")
    else:
        print(f"  >>> TBR has NO significant residual power (p={p_val_tbr:.4f})")

    # 1C: Pure momentum version of S2
    print("\n--- 1C: S2 vs Momentum-Proxy S2 ---")
    s2_m = split_metrics(run_bt(df, WARMUP, n, make_s2(40, 50)), sp)
    mom_m = split_metrics(run_bt(df, WARMUP, n, make_momentum_s2(40, 50)), sp)

    print(f"  S2 (TBR):      IS {fmt(s2_m['is'])}  |  OOS {fmt(s2_m['oos'])}")
    print(f"  S2 (Momentum): IS {fmt(mom_m['is'])}  |  OOS {fmt(mom_m['oos'])}")

    s2_oos = s2_m['oos']['pnl']
    mom_oos = mom_m['oos']['pnl']
    ratio = mom_oos / s2_oos * 100 if s2_oos > 0 else 999
    print(f"\n  Momentum / S2 ratio: {ratio:.0f}%")
    if ratio >= 80:
        print(f"  >>> FAIL: Momentum proxy achieves {ratio:.0f}% of S2 -- TBR adds minimal edge")
    else:
        print(f"  >>> PASS: Momentum proxy only {ratio:.0f}% of S2 -- TBR has independent value")

    # 1D: Physical meaning check
    print("\n--- 1D: Physical Meaning Check ---")
    # At all L entry signals (TBR_pctile < 40 + breakout), check prior 5-bar return
    s2_trades = run_bt(df, WARMUP, n, make_s2(40, 50))
    l_entries = [t for t in s2_trades if t['side'] == 'L']
    s_entries = [t for t in s2_trades if t['side'] == 'S']

    l_prior_rets = []
    for t in l_entries:
        bi = t['eb']
        if bi >= 5:
            pr = (df.iloc[bi].close - df.iloc[bi-5].close) / df.iloc[bi-5].close * 100
            l_prior_rets.append(pr)

    s_prior_rets = []
    for t in s_entries:
        bi = t['eb']
        if bi >= 5:
            pr = (df.iloc[bi].close - df.iloc[bi-5].close) / df.iloc[bi-5].close * 100
            s_prior_rets.append(pr)

    if l_prior_rets:
        l_arr = np.array(l_prior_rets)
        pct_neg = np.mean(l_arr < 0) * 100
        print(f"\n  L entries ({len(l_arr)} trades):")
        print(f"    Prior 5-bar return: mean {l_arr.mean():+.2f}%, median {np.median(l_arr):+.2f}%")
        print(f"    {pct_neg:.0f}% had negative prior 5-bar return")
        if pct_neg > 60:
            print(f"    >>> L = 'breakout after dip' -- TBR low = price falling = taker sell")
        else:
            print(f"    >>> L has mixed prior returns -- TBR captures more than just price direction")

    if s_prior_rets:
        s_arr = np.array(s_prior_rets)
        pct_pos = np.mean(s_arr > 0) * 100
        print(f"\n  S entries ({len(s_arr)} trades):")
        print(f"    Prior 5-bar return: mean {s_arr.mean():+.2f}%, median {np.median(s_arr):+.2f}%")
        print(f"    {pct_pos:.0f}% had positive prior 5-bar return")
        if pct_pos > 60:
            print(f"    >>> S = 'breakout after rally' -- TBR high = price rising = taker buy")
        else:
            print(f"    >>> S has mixed prior returns -- TBR captures more than just price direction")

    # Gate 1 Verdict
    print("\n--- Gate 1 Verdict ---")
    g1_pass = ratio < 80 and p_val_tbr < 0.05
    g1_cond = ratio < 80 or p_val_tbr < 0.05
    if g1_pass:
        print("  PASS: TBR has independent predictive power AND outperforms momentum proxy")
    elif g1_cond:
        print("  CONDITIONAL: Partial evidence of TBR independence")
    else:
        print("  FAIL: TBR is essentially a momentum proxy")

    # ══════════════════════════════════════════════════════════
    #  Gate 2: IS/OOS Ratio 0.99 -- Too Perfect?
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 2: IS/OOS Ratio 0.99 -- Too Perfect? ===")
    print("=" * 70)

    # 2A: Engine inflation comparison
    print("\n--- 2A: Engine Inflation Effect on IS/OOS Ratio ---")
    v14_m = split_metrics(run_bt(df, WARMUP, n, entry_v14), sp)
    brk_m = split_metrics(run_bt(df, WARMUP, n, entry_brk), sp)

    configs_for_ratio = [
        ("V14 (GK)", v14_m),
        ("S2 (TBR)", s2_m),
        ("Breakout (pure)", brk_m),
    ]
    print(f"\n  {'Strategy':20s} {'IS PnL':>8s} {'OOS PnL':>9s} {'Ratio':>6s}")
    print(f"  {'-'*20} {'-'*8} {'-'*9} {'-'*6}")
    for name, m in configs_for_ratio:
        r = m['is']['pnl'] / m['oos']['pnl'] if m['oos']['pnl'] != 0 else 99
        print(f"  {name:20s} ${m['is']['pnl']:+7.0f} ${m['oos']['pnl']:+8.0f}  {r:.2f}")

    # 2B: Monthly IS vs OOS distribution
    print("\n--- 2B: Monthly PnL Distribution ---")
    is_months = s2_m['is']['months']
    oos_months = s2_m['oos']['months']
    is_vals = sorted(is_months.values())
    oos_vals = sorted(oos_months.values())

    print(f"\n  IS monthly PnL ({len(is_vals)} months):")
    print(f"    Mean: ${np.mean(is_vals):+.0f}, Median: ${np.median(is_vals):+.0f}")
    print(f"    Min:  ${min(is_vals):+.0f}, Max: ${max(is_vals):+.0f}")
    print(f"    Std:  ${np.std(is_vals):.0f}")
    print(f"    Top 3: {['${:+.0f}'.format(v) for v in sorted(is_vals, reverse=True)[:3]]}")

    print(f"\n  OOS monthly PnL ({len(oos_vals)} months):")
    print(f"    Mean: ${np.mean(oos_vals):+.0f}, Median: ${np.median(oos_vals):+.0f}")
    print(f"    Min:  ${min(oos_vals):+.0f}, Max: ${max(oos_vals):+.0f}")
    print(f"    Std:  ${np.std(oos_vals):.0f}")
    print(f"    Top 3: {['${:+.0f}'.format(v) for v in sorted(oos_vals, reverse=True)[:3]]}")

    # IS top month dominance
    is_total = sum(is_vals)
    is_top1 = max(is_vals)
    is_top3 = sum(sorted(is_vals, reverse=True)[:3])
    print(f"\n  IS top-1 month share: {is_top1/is_total*100:.0f}% of total IS PnL")
    print(f"  IS top-3 months share: {is_top3/is_total*100:.0f}% of total IS PnL")

    oos_total = sum(oos_vals)
    oos_top1 = max(oos_vals)
    oos_top3 = sum(sorted(oos_vals, reverse=True)[:3])
    print(f"  OOS top-1 month share: {oos_top1/oos_total*100:.0f}% of total OOS PnL")
    print(f"  OOS top-3 months share: {oos_top3/oos_total*100:.0f}% of total OOS PnL")

    # 2C: Trade-level distribution
    print("\n--- 2C: Trade-Level Distribution ---")
    is_trades = s2_m['is_trades']
    oos_trades = s2_m['oos_trades']

    is_pnls = [t['p'] for t in is_trades]
    oos_pnls = [t['p'] for t in oos_trades]

    print(f"\n  IS trades ({len(is_pnls)}):")
    print(f"    Avg win:  ${s2_m['is']['avg_win']:+.1f}")
    print(f"    Avg loss: ${s2_m['is']['avg_loss']:+.1f}")
    print(f"    WR: {s2_m['is']['wr']:.0f}%")
    print(f"    PnL std: ${np.std(is_pnls):.1f}")

    print(f"\n  OOS trades ({len(oos_pnls)}):")
    print(f"    Avg win:  ${s2_m['oos']['avg_win']:+.1f}")
    print(f"    Avg loss: ${s2_m['oos']['avg_loss']:+.1f}")
    print(f"    WR: {s2_m['oos']['wr']:.0f}%")
    print(f"    PnL std: ${np.std(oos_pnls):.1f}")

    # KS test for distribution similarity
    ks_stat, ks_p = stats.ks_2samp(is_pnls, oos_pnls)
    print(f"\n  KS test (IS vs OOS PnL distributions): stat={ks_stat:.3f}, p={ks_p:.4f}")
    if ks_p > 0.05:
        print(f"  >>> Distributions NOT significantly different (good for ratio 0.99)")
    else:
        print(f"  >>> Distributions SIGNIFICANTLY different -- ratio 0.99 may be coincidence")

    # 2D: Ratio at different splits
    print("\n--- 2D: IS/OOS Ratio at Different Split Points ---")
    for pct in [0.3, 0.4, 0.5, 0.6, 0.7]:
        sp_test = WARMUP + int((n - WARMUP) * pct)
        m_test = split_metrics(run_bt(df, WARMUP, n, make_s2(40, 50)), sp_test)
        r_test = m_test['is']['pnl'] / m_test['oos']['pnl'] if m_test['oos']['pnl'] != 0 else 99
        print(f"  Split {pct:.0%}: IS ${m_test['is']['pnl']:+.0f} / OOS ${m_test['oos']['pnl']:+.0f} = ratio {r_test:.2f}")

    # Gate 2 verdict
    print("\n--- Gate 2 Verdict ---")
    print("  (Assessment based on IS/OOS ratio consistency across splits and distribution similarity)")

    # ══════════════════════════════════════════════════════════
    #  Gate 3: Engine Inflation 2x -- What's Real?
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 3: Engine Inflation 2x -- What's Real? ===")
    print("=" * 70)

    # 3A: Inflation source decomposition
    print("\n--- 3A: Inflation Source Decomposition ---")
    # V14 official: ~174 trades, IS ~$1,764, OOS ~$4,549
    # V14 this engine: 334 trades, IS $5,173, OOS $8,269
    print("  V14 official:     ~174t, IS ~$1,764, OOS ~$4,549, total ~$6,313")
    v14_this = split_metrics(run_bt(df, WARMUP, n, entry_v14), sp)
    print(f"  V14 this engine:  {v14_this['is']['t']+v14_this['oos']['t']}t, "
          f"IS ${v14_this['is']['pnl']:+.0f}, OOS ${v14_this['oos']['pnl']:+.0f}, "
          f"total ${v14_this['is']['pnl']+v14_this['oos']['pnl']:+.0f}")
    inflation = (v14_this['is']['pnl'] + v14_this['oos']['pnl']) / 6313
    print(f"  Inflation factor: {inflation:.2f}x")

    # Trade count difference
    off_trades = 174
    this_trades = v14_this['is']['t'] + v14_this['oos']['t']
    print(f"\n  Trade count: {this_trades} vs official ~{off_trades} ({this_trades/off_trades:.1f}x)")
    print(f"  Per-trade avg PnL: this ${(v14_this['is']['pnl']+v14_this['oos']['pnl'])/this_trades:.1f} vs official ${6313/off_trades:.1f}")

    # 3B: S2 deflated estimate
    print("\n--- 3B: S2 Deflated Estimate ---")
    s2_total = s2_m['is']['pnl'] + s2_m['oos']['pnl']
    s2_trades_total = s2_m['is']['t'] + s2_m['oos']['t']
    s2_deflated = s2_total / inflation
    print(f"  S2 this engine: ${s2_total:+.0f} ({s2_trades_total}t)")
    print(f"  Deflation factor: {inflation:.2f}x")
    print(f"  S2 deflated estimate: ${s2_deflated:+.0f}")
    s2_oos_deflated = s2_m['oos']['pnl'] / inflation
    print(f"  S2 OOS deflated estimate: ${s2_oos_deflated:+.0f}")

    # 3C: Relative comparison (same engine)
    print("\n--- 3C: Relative Comparison (same engine, valid) ---")
    v14_total = v14_this['is']['pnl'] + v14_this['oos']['pnl']
    s2_vs_v14 = s2_total / v14_total if v14_total > 0 else 99
    print(f"  V14 total: ${v14_total:+.0f}")
    print(f"  S2 total: ${s2_total:+.0f}")
    print(f"  S2 / V14 ratio (same engine): {s2_vs_v14:.2f}x")
    print(f"  S2 OOS / V14 OOS ratio: {s2_m['oos']['pnl'] / v14_this['oos']['pnl']:.2f}x" if v14_this['oos']['pnl'] > 0 else "")

    # 3D: Fee sensitivity
    print("\n--- 3D: Fee Sensitivity ---")
    for fee_test in [4, 6, 8, 10, 12]:
        tr = run_bt(df, WARMUP, n, make_s2(40, 50), fee=fee_test)
        m_fee = split_metrics(tr, sp)
        print(f"  Fee ${fee_test:2d}: IS ${m_fee['is']['pnl']:+.0f} OOS ${m_fee['oos']['pnl']:+.0f} "
              f"Total ${m_fee['is']['pnl']+m_fee['oos']['pnl']:+.0f} "
              f"({m_fee['is']['t']+m_fee['oos']['t']}t)")

    # Breakeven fee
    for fee_test in range(4, 30):
        tr = run_bt(df, WARMUP, n, make_s2(40, 50), fee=fee_test)
        total = sum(t['p'] for t in tr)
        if total <= 0:
            print(f"\n  Breakeven fee: ~${fee_test-1} (total goes negative at ${fee_test})")
            break

    # Gate 3 verdict
    print("\n--- Gate 3 Verdict ---")
    if s2_oos_deflated > 3000:
        print(f"  PASS: Deflated S2 OOS ~${s2_oos_deflated:.0f} > $3,000 threshold")
    elif s2_oos_deflated > 2000:
        print(f"  CONDITIONAL: Deflated S2 OOS ~${s2_oos_deflated:.0f} -- marginal")
    else:
        print(f"  FAIL: Deflated S2 OOS ~${s2_oos_deflated:.0f} < $2,000")

    # ══════════════════════════════════════════════════════════
    #  Gate 4: Threshold 40/50 Robustness
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 4: Threshold 40/50 Robustness ===")
    print("=" * 70)

    # 4A: Fine-grained scan
    print("\n--- 4A: Fine-Grained Threshold Scan ---")
    print(f"  {'lo':>4s} {'hi':>4s} | {'IS PnL':>8s} {'OOS PnL':>8s} {'Total':>8s} {'OOS t':>5s} {'OOS WR':>6s}")
    print(f"  {'-'*4} {'-'*4}-+-{'-'*8}-{'-'*8}-{'-'*8}-{'-'*5}-{'-'*6}")

    grid_results = {}
    for lo in [20, 25, 30, 35, 38, 39, 40, 41, 42, 45, 50, 55]:
        for hi in [40, 45, 48, 49, 50, 51, 52, 55, 60, 65, 70, 75]:
            m = split_metrics(run_bt(df, WARMUP, n, make_s2(lo, hi)), sp)
            grid_results[(lo, hi)] = m
            marker = " <<<" if lo == 40 and hi == 50 else ""
            print(f"  {lo:4d} {hi:4d} | ${m['is']['pnl']:+7.0f} ${m['oos']['pnl']:+7.0f} "
                  f"${m['is']['pnl']+m['oos']['pnl']:+7.0f} {m['oos']['t']:4d} {m['oos']['wr']:5.0f}%{marker}")

    # 4B: Cliff edge check
    print("\n--- 4B: Cliff Edge Check ---")
    for lo_center in [40]:
        for hi_center in [50]:
            c_m = grid_results.get((lo_center, hi_center))
            c_pnl = c_m['oos']['pnl'] if c_m else 0
            print(f"  Center ({lo_center}/{hi_center}): OOS ${c_pnl:+.0f}")

            for dlo, dhi in [(-2, 0), (-1, 0), (1, 0), (2, 0), (0, -2), (0, -1), (0, 1), (0, 2)]:
                nb = (lo_center + dlo, hi_center + dhi)
                nb_m = grid_results.get(nb)
                if nb_m:
                    nb_pnl = nb_m['oos']['pnl']
                    diff_pct = (nb_pnl - c_pnl) / abs(c_pnl) * 100 if c_pnl != 0 else 0
                    cliff = "CLIFF!" if abs(diff_pct) > 40 else ""
                    print(f"    ({nb[0]}/{nb[1]}): OOS ${nb_pnl:+.0f} ({diff_pct:+.0f}%) {cliff}")

    # 4C: S signal frequency
    print("\n--- 4C: S Signal Frequency (TBR > 50) ---")
    valid_bars = df.iloc[WARMUP:].dropna(subset=['tbr_ma5_pctile'])
    s_filter_bars = (valid_bars['tbr_ma5_pctile'] > 50).sum()
    total_valid = len(valid_bars)
    s_pass_rate = s_filter_bars / total_valid * 100
    print(f"  Total bars: {total_valid}")
    print(f"  Bars with TBR_pctile > 50: {s_filter_bars} ({s_pass_rate:.1f}%)")
    if s_pass_rate > 40:
        print(f"  >>> WARNING: TBR > 50 passes {s_pass_rate:.0f}% of bars -- very weak filter for S")

    # L filter bars
    l_filter_bars = (valid_bars['tbr_ma5_pctile'] < 40).sum()
    l_pass_rate = l_filter_bars / total_valid * 100
    print(f"  Bars with TBR_pctile < 40: {l_filter_bars} ({l_pass_rate:.1f}%)")

    # 4D: S side TBR effect
    print("\n--- 4D: S-Side TBR Effect ---")
    brk_full = split_metrics(run_bt(df, WARMUP, n, entry_brk), sp)

    # S-only breakout (no TBR)
    def entry_brk_short_only(row, i):
        if row.close < row.low_15: return 'S'
        return None

    def make_s2_short_only(hi_pct):
        def entry(row, i):
            p = row.tbr_ma5_pctile
            if pd.isna(p): return None
            if p > hi_pct and row.close < row.low_15: return 'S'
            return None
        return entry

    brk_s_only = split_metrics(run_bt(df, WARMUP, n, entry_brk_short_only), sp)
    s2_s_only = split_metrics(run_bt(df, WARMUP, n, make_s2_short_only(50)), sp)

    print(f"  Pure breakout S:     IS {fmt(brk_s_only['is'])}  OOS {fmt(brk_s_only['oos'])}")
    print(f"  S2 S (TBR > 50):     IS {fmt(s2_s_only['is'])}  OOS {fmt(s2_s_only['oos'])}")

    s_tbr_effect = s2_s_only['oos']['pnl'] - brk_s_only['oos']['pnl']
    print(f"\n  S TBR effect (OOS): ${s_tbr_effect:+.0f}")
    if abs(s_tbr_effect) < 200:
        print(f"  >>> CONDITIONAL: TBR effect on S < $200 -- S side TBR is decorative")
    else:
        print(f"  >>> PASS: TBR has meaningful effect on S (${s_tbr_effect:+.0f})")

    # Same for L
    def entry_brk_long_only(row, i):
        if row.close > row.high_15: return 'L'
        return None

    def make_s2_long_only(lo_pct):
        def entry(row, i):
            p = row.tbr_ma5_pctile
            if pd.isna(p): return None
            if p < lo_pct and row.close > row.high_15: return 'L'
            return None
        return entry

    brk_l_only = split_metrics(run_bt(df, WARMUP, n, entry_brk_long_only), sp)
    s2_l_only = split_metrics(run_bt(df, WARMUP, n, make_s2_long_only(40)), sp)

    print(f"\n  Pure breakout L:     IS {fmt(brk_l_only['is'])}  OOS {fmt(brk_l_only['oos'])}")
    print(f"  S2 L (TBR < 40):     IS {fmt(s2_l_only['is'])}  OOS {fmt(s2_l_only['oos'])}")

    l_tbr_effect = s2_l_only['oos']['pnl'] - brk_l_only['oos']['pnl']
    print(f"\n  L TBR effect (OOS): ${l_tbr_effect:+.0f}")

    # ══════════════════════════════════════════════════════════
    #  Gate 5: Breakout Shared Component Analysis
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("=== Gate 5: Breakout Shared Component Analysis ===")
    print("=" * 70)

    # 5A: Pure breakout performance
    print("\n--- 5A: Pure Breakout (no filter) ---")
    print(f"  Breakout L+S: IS {fmt(brk_full['is'])}  OOS {fmt(brk_full['oos'])}")
    print(f"  V14 (GK+brk): IS {fmt(v14_this['is'])}  OOS {fmt(v14_this['oos'])}")
    print(f"  S2 (TBR+brk): IS {fmt(s2_m['is'])}  OOS {fmt(s2_m['oos'])}")

    # 5B: Marginal contribution
    print("\n--- 5B: Marginal Contribution of Filters ---")
    brk_oos = brk_full['oos']['pnl']
    v14_oos = v14_this['oos']['pnl']
    s2_oos_pnl = s2_m['oos']['pnl']

    gk_marginal = v14_oos - brk_oos
    tbr_marginal = s2_oos_pnl - brk_oos
    print(f"  Pure breakout OOS: ${brk_oos:+.0f}")
    print(f"  GK filter marginal (V14 - breakout): ${gk_marginal:+.0f}")
    print(f"  TBR filter marginal (S2 - breakout): ${tbr_marginal:+.0f}")

    if brk_oos > 0:
        print(f"  GK marginal as % of breakout: {gk_marginal/brk_oos*100:+.0f}%")
        print(f"  TBR marginal as % of breakout: {tbr_marginal/brk_oos*100:+.0f}%")

    if tbr_marginal < 0:
        print(f"\n  >>> TBR REDUCES OOS PnL vs pure breakout by ${abs(tbr_marginal):.0f}")
        print(f"  >>> TBR value must be in risk-adjusted metrics, not absolute PnL")

    # 5C: Risk-adjusted comparison
    print("\n--- 5C: Risk-Adjusted Comparison ---")
    configs_risk = [
        ("Breakout", brk_full),
        ("V14 (GK)", v14_this),
        ("S2 (TBR)", s2_m),
    ]
    print(f"  {'Strategy':15s} {'OOS PnL':>8s} {'OOS MDD':>8s} {'PnL/MDD':>8s} {'OOS WR':>6s} {'OOS PF':>6s}")
    print(f"  {'-'*15} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")
    for name, m in configs_risk:
        ratio_rm = m['oos']['pnl'] / m['oos']['mdd'] if m['oos']['mdd'] > 0 else 99
        print(f"  {name:15s} ${m['oos']['pnl']:+7.0f} ${m['oos']['mdd']:7.0f} {ratio_rm:7.1f} {m['oos']['wr']:5.0f}% {m['oos']['pf']:5.1f}")

    # Gate 5 verdict
    print("\n--- Gate 5 Verdict ---")
    if tbr_marginal > 0:
        print("  PASS: TBR adds positive marginal contribution over breakout")
    elif s2_m['oos']['mdd'] < brk_full['oos']['mdd'] * 0.8:
        print("  CONDITIONAL: TBR reduces PnL but significantly improves risk (MDD/WR)")
    else:
        print("  FAIL: TBR neither improves PnL nor significantly improves risk")


if __name__ == '__main__':
    main()
