"""
V14 ETH Backtest Trade Export
==============================
Run V14 backtest on ETHUSDT and export every trade to Excel.
Includes: entry/exit datetime & price, PnL, MAE/MFE, exit reason, cumulative equity.
"""

import pandas as pd
import numpy as np
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')
OUT_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'doc')

# =========================================================================
# V14 Parameters (LOCKED)
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


def rolling_pctile(vals, window):
    out = np.full(len(vals), np.nan)
    for i in range(window - 1, len(vals)):
        w = vals[i - window + 1: i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < 10:
            continue
        out[i] = np.sum(valid <= vals[i]) / len(valid) * 100
    return out


def compute_indicators(df):
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values

    log_hl = np.log(h / np.maximum(l, 1e-10))
    co_ratio = c / np.maximum(o, 1e-10)
    co_ratio = np.maximum(co_ratio, 1e-10)
    log_co = np.log(co_ratio)
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2

    gk_s = pd.Series(gk)
    close_s = pd.Series(c)

    gk_mean_5 = gk_s.rolling(L_GK_S, min_periods=L_GK_S).mean()
    gk_mean_20 = gk_s.rolling(L_GK_L, min_periods=L_GK_L).mean()
    ratio_L = (gk_mean_5 / gk_mean_20.replace(0, np.nan)).values
    shifted_L = np.roll(ratio_L, 1)
    shifted_L[0] = np.nan
    pctile_L = rolling_pctile(shifted_L, 100)

    gk_mean_10 = gk_s.rolling(S_GK_S, min_periods=S_GK_S).mean()
    gk_mean_30 = gk_s.rolling(S_GK_L, min_periods=S_GK_L).mean()
    ratio_S = (gk_mean_10 / gk_mean_30.replace(0, np.nan)).values
    shifted_S = np.roll(ratio_S, 1)
    shifted_S[0] = np.nan
    pctile_S = rolling_pctile(shifted_S, 100)

    shifted_close = np.roll(c, 1)
    shifted_close[0] = np.nan
    high_15 = pd.Series(shifted_close).rolling(L_BRK, min_periods=L_BRK).max().values
    low_15 = pd.Series(shifted_close).rolling(S_BRK, min_periods=S_BRK).min().values
    brk_up = c > high_15
    brk_dn = c < low_15

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


def simulate_v14_detailed(ind, datetimes):
    """Run V14 L+S simulation with full trade detail (MAE/MFE/GK pctile)."""
    o, h, l, c = ind['o'], ind['h'], ind['l'], ind['c']
    pL, pS = ind['pctile_L'], ind['pctile_S']
    brk_up, brk_dn = ind['brk_up'], ind['brk_dn']
    hours, dows = ind['hours'], ind['dows']
    months, days = ind['months'], ind['days']
    n = len(o)

    trades = []

    # L position state
    lp_active = False
    lp_entry = 0.0
    lp_bar = 0
    lp_held = 0
    lp_mfe = 0.0
    lp_mae = 0.0
    lp_reduced = False
    lp_ext = False
    lp_ext_bars = 0
    lp_gk_pctile = 0.0

    # S position state
    sp_active = False
    sp_entry = 0.0
    sp_bar = 0
    sp_held = 0
    sp_mfe = 0.0
    sp_mae = 0.0
    sp_ext = False
    sp_ext_bars = 0
    sp_gk_pctile = 0.0

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

        if mk != cur_month:
            cur_month = mk
            l_m_entries = 0
            s_m_entries = 0
            l_m_pnl = 0.0
            s_m_pnl = 0.0

        if dk != cur_day:
            cur_day = dk
            d_pnl = 0.0

        # --- L EXIT ---
        if lp_active:
            lp_held += 1
            ep = lp_entry
            bh = lp_held

            bar_mfe = (hi - ep) / ep
            bar_mae = (li - ep) / ep  # negative = adverse
            if bar_mfe > lp_mfe:
                lp_mfe = bar_mfe
            if bar_mae < lp_mae:
                lp_mae = bar_mae

            ex_price = 0.0
            ex_reason = ''

            sn_lv = ep * (1 - L_SN)
            if li <= sn_lv:
                ex_price = ep * (1 - L_SN * (1 + L_SN_SLIP))
                ex_reason = 'SN'
            elif hi >= ep * (1 + L_TP):
                ex_price = ep * (1 + L_TP)
                ex_reason = 'TP'
            else:
                cpnl = (ci - ep) / ep

                if lp_mfe >= L_MFE_ACT and (lp_mfe - cpnl) >= L_MFE_TR and bh >= 1:
                    ex_price = ci
                    ex_reason = 'MFE'
                else:
                    if bh == L_CMH_BAR and cpnl <= L_CMH_TH:
                        lp_reduced = True

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
                    'side': 'L',
                    'entry_bar': lp_bar,
                    'exit_bar': i,
                    'entry_dt': datetimes[lp_bar],
                    'exit_dt': datetimes[i],
                    'entry_price': round(ep, 2),
                    'exit_price': round(ex_price, 2),
                    'pnl_pct': round(pnl_pct * 100, 3),
                    'pnl_usd': round(pnl, 2),
                    'exit_reason': ex_reason,
                    'bars_held': bh,
                    'mfe_pct': round(lp_mfe * 100, 3),
                    'mae_pct': round(lp_mae * 100, 3),
                    'gk_pctile': round(lp_gk_pctile, 1),
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

            bar_mfe = (ep - li) / ep  # S: low is favorable
            bar_mae = (ep - hi) / ep  # S: high is adverse (negative = adverse)
            if bar_mfe > sp_mfe:
                sp_mfe = bar_mfe
            if bar_mae < sp_mae:
                sp_mae = bar_mae

            ex_price = 0.0
            ex_reason = ''

            sn_lv = ep * (1 + S_SN)
            if hi >= sn_lv:
                ex_price = ep * (1 + S_SN * (1 + S_SN_SLIP))
                ex_reason = 'SN'
            elif li <= ep * (1 - S_TP):
                ex_price = ep * (1 - S_TP)
                ex_reason = 'TP'
            else:
                cpnl = (ep - ci) / ep

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
                    'side': 'S',
                    'entry_bar': sp_bar,
                    'exit_bar': i,
                    'entry_dt': datetimes[sp_bar],
                    'exit_dt': datetimes[i],
                    'entry_price': round(ep, 2),
                    'exit_price': round(ex_price, 2),
                    'pnl_pct': round(pnl_pct * 100, 3),
                    'pnl_usd': round(pnl, 2),
                    'exit_reason': ex_reason,
                    'bars_held': bh,
                    'mfe_pct': round(sp_mfe * 100, 3),
                    'mae_pct': round(sp_mae * 100, 3),
                    'gk_pctile': round(sp_gk_pctile, 1),
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

        # L entry: signal bar close 立即進場（與實盤一致）
        if (not lp_active and not l_cb and
            i - l_last_exit >= L_CD and l_m_entries < L_CAP and
            hr not in L_BLK_H and dw not in L_BLK_D and
            not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):
            lp_active = True
            lp_entry = ci
            lp_bar = i
            lp_held = 0
            lp_mfe = 0.0
            lp_mae = 0.0
            lp_reduced = False
            lp_ext = False
            lp_ext_bars = 0
            lp_gk_pctile = pL[i]
            l_m_entries += 1

        # S entry: signal bar close 立即進場（與實盤一致）
        if (not sp_active and not s_cb and
            i - s_last_exit >= S_CD and s_m_entries < S_CAP and
            hr not in S_BLK_H and dw not in S_BLK_D and
            not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):
            sp_active = True
            sp_entry = ci
            sp_bar = i
            sp_held = 0
            sp_mfe = 0.0
            sp_mae = 0.0
            sp_ext = False
            sp_ext_bars = 0
            sp_gk_pctile = pS[i]
            s_m_entries += 1

    return trades


if __name__ == '__main__':
    filepath = os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv')
    df = pd.read_csv(filepath)
    print(f"ETHUSDT: {len(df)} bars, {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

    ind = compute_indicators(df)
    datetimes = df['datetime'].values
    trades = simulate_v14_detailed(ind, datetimes)

    print(f"Total trades: {len(trades)}")

    # Build DataFrame
    tdf = pd.DataFrame(trades)
    tdf.insert(0, 'trade_no', range(1, len(tdf) + 1))

    # Cumulative PnL
    tdf['cum_pnl'] = tdf['pnl_usd'].cumsum().round(2)

    # Win/Loss label
    tdf['result'] = tdf['pnl_usd'].apply(lambda x: 'Win' if x > 0 else 'Loss')

    # IS/OOS label (50/50 split)
    mid = len(df) // 2
    tdf['sample'] = tdf['entry_bar'].apply(lambda b: 'IS' if b < mid else 'OOS')

    # Month label
    tdf['month'] = pd.to_datetime(tdf['entry_dt']).dt.strftime('%Y-%m')

    # Reorder columns
    cols = [
        'trade_no', 'side', 'sample', 'month',
        'entry_dt', 'exit_dt', 'bars_held',
        'entry_price', 'exit_price',
        'pnl_pct', 'pnl_usd', 'cum_pnl', 'result',
        'exit_reason', 'gk_pctile',
        'mfe_pct', 'mae_pct',
    ]
    tdf = tdf[cols]

    # Rename for readability
    tdf.columns = [
        '#', 'Side', 'Sample', 'Month',
        'Entry Time', 'Exit Time', 'Bars Held',
        'Entry Price', 'Exit Price',
        'PnL %', 'PnL $', 'Cum PnL $', 'Result',
        'Exit Reason', 'GK Pctile',
        'MFE %', 'MAE %',
    ]

    # Summary stats
    l_trades = tdf[tdf['Side'] == 'L']
    s_trades = tdf[tdf['Side'] == 'S']
    is_trades = tdf[tdf['Sample'] == 'IS']
    oos_trades = tdf[tdf['Sample'] == 'OOS']

    print(f"\n--- Summary ---")
    print(f"L trades: {len(l_trades)}, PnL: ${l_trades['PnL $'].sum():+.0f}, "
          f"WR: {(l_trades['PnL $'] > 0).mean()*100:.1f}%")
    print(f"S trades: {len(s_trades)}, PnL: ${s_trades['PnL $'].sum():+.0f}, "
          f"WR: {(s_trades['PnL $'] > 0).mean()*100:.1f}%")
    print(f"IS:  {len(is_trades)}t, ${is_trades['PnL $'].sum():+.0f}")
    print(f"OOS: {len(oos_trades)}t, ${oos_trades['PnL $'].sum():+.0f}")
    print(f"Total: {len(tdf)}t, ${tdf['PnL $'].sum():+.0f}")

    # Exit reason distribution
    print(f"\n--- Exit Reasons ---")
    for reason in ['TP', 'SN', 'MH', 'MHx', 'BE', 'MFE']:
        n = (tdf['Exit Reason'] == reason).sum()
        if n > 0:
            avg = tdf.loc[tdf['Exit Reason'] == reason, 'PnL $'].mean()
            print(f"  {reason:4s}: {n:>3} trades, avg ${avg:+.1f}")

    # Export to Excel
    out_path = os.path.join(OUT_DIR, 'V14_backtest_trades.xlsx')

    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        # Sheet 1: All trades
        tdf.to_excel(writer, sheet_name='All Trades', index=False)

        # Sheet 2: L trades only
        l_trades.to_excel(writer, sheet_name='Long', index=False)

        # Sheet 3: S trades only
        s_trades.to_excel(writer, sheet_name='Short', index=False)

        # Sheet 4: Monthly summary
        monthly = tdf.groupby(['Month', 'Side']).agg(
            Trades=('PnL $', 'count'),
            PnL=('PnL $', 'sum'),
            WR=('PnL $', lambda x: (x > 0).mean() * 100),
            Avg_PnL=('PnL $', 'mean'),
        ).round(1).reset_index()
        monthly.to_excel(writer, sheet_name='Monthly', index=False)

        # Sheet 5: Summary stats
        summary_data = {
            'Metric': [
                'Total Trades', 'L Trades', 'S Trades',
                'Total PnL', 'L PnL', 'S PnL',
                'Win Rate', 'L Win Rate', 'S Win Rate',
                'IS PnL', 'OOS PnL',
                'IS Trades', 'OOS Trades',
                'Avg PnL/Trade', 'Avg Win', 'Avg Loss',
                'Best Trade', 'Worst Trade',
                'Max MFE %', 'Min MAE %',
                'Account', 'Margin', 'Leverage', 'Notional', 'Fee/Trade',
            ],
            'Value': [
                len(tdf), len(l_trades), len(s_trades),
                f"${tdf['PnL $'].sum():+,.0f}",
                f"${l_trades['PnL $'].sum():+,.0f}",
                f"${s_trades['PnL $'].sum():+,.0f}",
                f"{(tdf['PnL $'] > 0).mean()*100:.1f}%",
                f"{(l_trades['PnL $'] > 0).mean()*100:.1f}%",
                f"{(s_trades['PnL $'] > 0).mean()*100:.1f}%",
                f"${is_trades['PnL $'].sum():+,.0f}",
                f"${oos_trades['PnL $'].sum():+,.0f}",
                len(is_trades), len(oos_trades),
                f"${tdf['PnL $'].mean():+.1f}",
                f"${tdf.loc[tdf['PnL $'] > 0, 'PnL $'].mean():+.1f}",
                f"${tdf.loc[tdf['PnL $'] <= 0, 'PnL $'].mean():+.1f}",
                f"${tdf['PnL $'].max():+.1f}",
                f"${tdf['PnL $'].min():+.1f}",
                f"{tdf['MFE %'].max():.2f}%",
                f"{tdf['MAE %'].min():.2f}%",
                '$1,000', '$200', '20x', '$4,000', '$4',
            ],
        }
        pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)

    print(f"\nExported to: {os.path.abspath(out_path)}")
    print(f"Sheets: All Trades, Long, Short, Monthly, Summary")
