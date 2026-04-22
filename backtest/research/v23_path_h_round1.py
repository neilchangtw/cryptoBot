"""
V23 Path H Round 1 — BTC V14 as hedge candidate
假設：BTC V14 可能在 ETH V14 的輸月（2024-04/06, 2026-04）反向賺錢。
檢查：月度 correlation、合併後最壞月、合併後 Sharpe。
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, worst_rolling_dd, monthly_pnl)

SCRIPT = os.path.dirname(os.path.abspath(__file__))

def load_sym(sym):
    f = os.path.join(SCRIPT, '..', '..', 'data', f'{sym}USDT_1h_latest730d.csv')
    df = pd.read_csv(f)
    o = df['open'].values; h = df['high'].values
    l = df['low'].values; c = df['close'].values
    dt = pd.to_datetime(df['datetime'])
    hours = dt.dt.hour.values; dows = dt.dt.dayofweek.values
    mks = (dt.dt.year*100 + dt.dt.month).values
    dks = (dt.dt.year*10000 + dt.dt.month*100 + dt.dt.day).values
    return df, o, h, l, c, hours, dows, mks, dks


df_e, oe, he, le, ce, hrE, dwE, mkE, dkE = load_sym('ETH')
df_b, ob, hb, lb, cb, hrB, dwB, mkB, dkB = load_sym('BTC')
Ne = len(oe); Nb = len(ob)
print(f"ETH bars={Ne}  BTC bars={Nb}")

# Align: use min length from start (Binance has consistent start)
N = min(Ne, Nb)
print(f"Aligned N={N}")

# Run V14 on both
tr_eth = run_v14_overlay(oe[:N], he[:N], le[:N], ce[:N],
                          hrE[:N], dwE[:N], mkE[:N], dkE[:N], 0, N)
tr_btc = run_v14_overlay(ob[:N], hb[:N], lb[:N], cb[:N],
                          hrB[:N], dwB[:N], mkB[:N], dkB[:N], 0, N)

se = stats_from(tr_eth); sb = stats_from(tr_btc)
print(f"\nV14 on ETH: {se['n']}t ${se['pnl']:+.0f} WR {se['wr']:.0f}% MDD${se['mdd']:.0f} Sharpe {se['sharpe']:.2f}")
print(f"V14 on BTC: {sb['n']}t ${sb['pnl']:+.0f} WR {sb['wr']:.0f}% MDD${sb['mdd']:.0f} Sharpe {sb['sharpe']:.2f}")

# Monthly PnL both
mo_e = monthly_pnl(tr_eth)
mo_b = monthly_pnl(tr_btc)
all_mo = sorted(set(mo_e.keys()) | set(mo_b.keys()))

print(f"\n{'Month':>7} {'ETH':>10} {'BTC':>10} {'Sum':>10}  hedge?")
print("-" * 55)
eth_arr = []; btc_arr = []; sum_arr = []
for m in all_mo:
    e = mo_e.get(m, 0); b = mo_b.get(m, 0)
    s = e + b
    flag = ''
    if e < 0 and b > 0: flag = '   <-- BTC hedges ETH loss'
    elif e < 0 and b < 0: flag = '   BOTH LOSE'
    print(f"{m:>7} ${e:>+8.0f} ${b:>+8.0f} ${s:>+8.0f}  {flag}")
    eth_arr.append(e); btc_arr.append(b); sum_arr.append(s)

eth_arr = np.array(eth_arr); btc_arr = np.array(btc_arr); sum_arr = np.array(sum_arr)
corr = np.corrcoef(eth_arr, btc_arr)[0,1]
print(f"\nMonthly correlation ETH V14 vs BTC V14: {corr:+.3f}")

eth_neg_mo = eth_arr < 0
if eth_neg_mo.sum() > 0:
    btc_when_eth_loses = btc_arr[eth_neg_mo]
    print(f"ETH negative months: {eth_neg_mo.sum()}")
    print(f"  BTC avg in those months: ${btc_when_eth_loses.mean():+.0f}")
    print(f"  BTC positive in those months: {(btc_when_eth_loses > 0).sum()}/{eth_neg_mo.sum()}")

# Combined stats
print(f"\n{'='*60}")
print("Combined ETH V14 + BTC V14 (both run simultaneously, naive 50/50)")
print(f"{'='*60}")
combined_pnl = sum_arr.sum()
worst_sum = sum_arr.min()
worst_eth = eth_arr.min()
n_pos = (sum_arr > 0).sum(); n_tot = len(sum_arr)
print(f"Total PnL:        ETH ${eth_arr.sum():+.0f}  BTC ${btc_arr.sum():+.0f}  Combined ${combined_pnl:+.0f}")
print(f"Worst month:      ETH ${worst_eth:+.0f}                   Combined ${worst_sum:+.0f}")
print(f"Positive months:  ETH {(eth_arr>0).sum()}/{n_tot}  Combined {n_pos}/{n_tot}")
print(f"Std of months:    ETH {eth_arr.std():.0f}  Combined {sum_arr.std():.0f}")

# Hard constraint: $1K account — running 2 strategies means 4 total positions (2L + 2S)
# V14 already uses $800 margin ($200 L + $200 S × 2 positions possible)
# Adding BTC V14 doubles = $1600 margin needed → exceeds $1K account
print(f"\n{'='*60}")
print("Capacity check")
print(f"{'='*60}")
print(f"ETH V14: max $200 L + $200 S = $400 margin concurrent")
print(f"BTC V14: max $200 L + $200 S = $400 margin concurrent")
print(f"Total potential concurrent: $800 / $1000 = 80% margin usage")
print(f"Within $1K account constraint if positions are ever concurrent")

# Need to see concurrent position overlap
def position_bars(trades, n):
    has_pos = np.zeros(n)
    for t in trades:
        has_pos[t['entry_bar']:t['exit_bar']+1] = 1
    return has_pos

eth_pos = position_bars(tr_eth, N)
btc_pos = position_bars(tr_btc, N)
overlap = (eth_pos * btc_pos).sum()
print(f"Bars with concurrent ETH+BTC positions: {int(overlap)}/{N} ({overlap/N*100:.1f}%)")

# Verdict
print(f"\n{'='*60}")
print("VERDICT R1:")
print(f"{'='*60}")
btc_profitable = sb['pnl'] > 0
corr_good = corr < 0.5  # want low or negative
worst_improved = worst_sum > worst_eth
print(f"BTC V14 standalone profitable: {btc_profitable} (${sb['pnl']:+.0f})")
print(f"Monthly correlation low: {corr_good} ({corr:+.3f})")
print(f"Worst month improved: {worst_improved} (ETH {worst_eth:+.0f} -> Combined {worst_sum:+.0f})")

if btc_profitable and corr_good and worst_improved:
    print("-> Path H viable direction")
elif worst_improved or corr_good:
    print("-> CONDITIONAL (risk diversification without PnL improvement)")
else:
    print("-> REJECTED (no hedge value)")
