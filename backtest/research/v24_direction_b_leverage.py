"""
V24 Direction B — Leverage Recalibration Trade-off Table
基底：V14+R (TH_UP=0.045, TH_SIDE=0.010)
測試多種 notional / leverage 組合，產出 risk-return 權衡表

帳戶假設：$1,000 起始
槓桿計算：notional / margin（此處 margin 視為 $200）
  $4000 = 20x (V14 基準)
  $3000 = 15x
  $2000 = 10x
  $1500 = 7.5x
  $1000 = 5x
Fee 按 notional 比例縮放（$4 per $4000）
CB 按 notional 比例縮放（保持 5% 日損 / 1.875% L 月 / 3.75% S 月）

輸出：每配置的 PnL / MDD / Sharpe / Worst30d / 帳戶 drawdown %
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, stats_from, load_data, worst_rolling_dd, monthly_pnl

ACCOUNT = 1000.0  # 起始帳戶

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2

# V14+R slope gate (locked from V23 Path R R5)
TH_UP = 0.045; TH_SIDE = 0.010
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan
block_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
block_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

# 測試配置：(label, notional_L, notional_S, fee_L, fee_S)
CONFIGS = [
    ("20x baseline (V14+R)",   4000, 4000, 4.0, 4.0),
    ("15x uniform",            3000, 3000, 3.0, 3.0),
    ("10x uniform",            2000, 2000, 2.0, 2.0),
    ("7.5x uniform",           1500, 1500, 1.5, 1.5),
    ("5x uniform",             1000, 1000, 1.0, 1.0),
    ("L 20x / S 10x",          4000, 2000, 4.0, 2.0),
    ("L 10x / S 20x",          2000, 4000, 2.0, 4.0),
    ("L 15x / S 10x",          3000, 2000, 3.0, 2.0),
    ("L 10x / S 15x",          2000, 3000, 2.0, 3.0),
]

def lev(notional): return notional / 200.0

print("="*110)
print(f"V24 Direction B — Leverage Recalibration on V14+R")
print(f"Bars N={N} / IS[0:{IS_END}] / OOS[{IS_END}:{N}] / Account ${ACCOUNT:.0f}")
print(f"R gate: TH_UP={TH_UP} TH_SIDE={TH_SIDE} (locked from V23 R5)")
print("="*110)

results = []
for lbl, nL, nS, fL, fS in CONFIGS:
    tr_is = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END,
                             block_bars_L=block_L, block_bars_S=block_S,
                             notional_L=nL, notional_S=nS, fee_L=fL, fee_S=fS)
    tr_oos = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N,
                              block_bars_L=block_L, block_bars_S=block_S,
                              notional_L=nL, notional_S=nS, fee_L=fL, fee_S=fS)
    tr_all = tr_is + tr_oos

    is_st = stats_from(tr_is)
    oos_st = stats_from(tr_oos)
    all_st = stats_from(tr_all)
    l_st = stats_from(tr_all, 'L')
    s_st = stats_from(tr_all, 'S')

    # Worst 30-day (720 bar) drawdown
    w30 = worst_rolling_dd(tr_all, N, 24*30, top_n=1)
    worst30 = w30[0][1] if w30 else 0

    # Monthly PnL
    m_pnl = monthly_pnl(tr_all)
    pos_mo = sum(1 for v in m_pnl.values() if v > 0)
    tot_mo = len(m_pnl)
    worst_mo = min(m_pnl.values()) if m_pnl else 0

    # Account-level metrics
    pct_pnl = all_st['pnl'] / ACCOUNT * 100
    pct_mdd = all_st['mdd'] / ACCOUNT * 100
    pct_w30 = worst30 / ACCOUNT * 100

    results.append({
        'label': lbl, 'lev_L': lev(nL), 'lev_S': lev(nS),
        'n': all_st['n'], 'pnl': all_st['pnl'],
        'is_pnl': is_st['pnl'], 'oos_pnl': oos_st['pnl'],
        'wr': all_st['wr'], 'pf': all_st['pf'],
        'mdd': all_st['mdd'], 'sharpe': all_st['sharpe'],
        'worst30': worst30, 'worst_mo': worst_mo,
        'pos_mo': pos_mo, 'tot_mo': tot_mo,
        'pct_pnl': pct_pnl, 'pct_mdd': pct_mdd, 'pct_w30': pct_w30,
        'l_pnl': l_st['pnl'], 's_pnl': s_st['pnl'],
    })

# ============ 輸出 Trade-off 表 ============
print("\n【表 1】核心指標")
print(f"{'Config':<26}{'L lev':>7}{'S lev':>7}{'Trades':>8}{'PnL $':>10}{'IS $':>9}{'OOS $':>9}{'WR%':>7}{'PF':>6}{'Sharpe':>8}")
print("-"*110)
for r in results:
    print(f"{r['label']:<26}{r['lev_L']:>6.1f}x{r['lev_S']:>6.1f}x{r['n']:>8}"
          f"{r['pnl']:>+10.0f}{r['is_pnl']:>+9.0f}{r['oos_pnl']:>+9.0f}"
          f"{r['wr']:>7.1f}{r['pf']:>6.2f}{r['sharpe']:>8.2f}")

print("\n【表 2】風險指標（含帳戶百分比）")
print(f"{'Config':<26}{'MDD $':>9}{'MDD %':>8}{'W30 $':>9}{'W30 %':>8}{'WorstMo $':>11}{'Pos Mo':>10}")
print("-"*110)
for r in results:
    print(f"{r['label']:<26}{r['mdd']:>9.0f}{r['pct_mdd']:>7.1f}%"
          f"{r['worst30']:>+9.0f}{r['pct_w30']:>7.1f}%"
          f"{r['worst_mo']:>+11.0f}{r['pos_mo']:>4}/{r['tot_mo']:<5}")

print("\n【表 3】Per-side 貢獻")
print(f"{'Config':<26}{'L PnL $':>10}{'S PnL $':>10}{'L/S ratio':>12}")
print("-"*110)
for r in results:
    ratio = r['l_pnl'] / r['s_pnl'] if r['s_pnl'] != 0 else 0
    print(f"{r['label']:<26}{r['l_pnl']:>+10.0f}{r['s_pnl']:>+10.0f}{ratio:>+12.2f}")

# ============ 風險效率排名 ============
print("\n【表 4】風險調整後排名（依 Sharpe 排序）")
print("-"*110)
sorted_by_sharpe = sorted(results, key=lambda r: -r['sharpe'])
for rank, r in enumerate(sorted_by_sharpe, 1):
    pnl_per_mdd = r['pnl'] / r['mdd'] if r['mdd'] > 0 else 0
    print(f"  #{rank} {r['label']:<26}Sharpe={r['sharpe']:.2f}  PnL/MDD={pnl_per_mdd:.2f}  "
          f"PnL={r['pnl']:+.0f}  Worst30={r['worst30']:+.0f}")

# ============ 推薦分類 ============
print("\n【表 5】風險輪廓推薦")
print("-"*110)
# Conservative: 最小 Worst30 drawdown % (絕對值最小)
conservative = min(results, key=lambda r: abs(r['pct_w30']))
# Balanced: 最高 Sharpe
balanced = max(results, key=lambda r: r['sharpe'])
# Aggressive: 最高 PnL
aggressive = max(results, key=lambda r: r['pnl'])

print(f"保守型（最小帳戶 30 日回撤 %）：{conservative['label']}")
print(f"  → PnL ${conservative['pnl']:+.0f} / Worst30 ${conservative['worst30']:+.0f} ({conservative['pct_w30']:+.1f}%) / Sharpe {conservative['sharpe']:.2f}")
print(f"均衡型（最高 Sharpe）：{balanced['label']}")
print(f"  → PnL ${balanced['pnl']:+.0f} / Worst30 ${balanced['worst30']:+.0f} ({balanced['pct_w30']:+.1f}%) / Sharpe {balanced['sharpe']:.2f}")
print(f"積極型（最高 PnL）：{aggressive['label']}")
print(f"  → PnL ${aggressive['pnl']:+.0f} / Worst30 ${aggressive['worst30']:+.0f} ({aggressive['pct_w30']:+.1f}%) / Sharpe {aggressive['sharpe']:.2f}")

print("\n" + "="*110)
print("完成 Direction B 槓桿重校準")
print("="*110)
