"""
V14+R 進場時段過濾審計
當前：
  L 禁止小時 {0,1,2,12} UTC+8，禁止日 {Sat, Sun}
  S 禁止小時 {0,1,2,12} UTC+8，禁止日 {Mon, Sat, Sun}

測試：
  1. 每個被「禁止」的小時/日 — 若解禁 PnL 如何變
  2. 每個被「允許」的小時/日 — 若加入禁止名單 PnL 如何變
  3. 以 IS/OOS 同時改善為標準（避免 IS-overfit）

基準 = V14+R 當前 session filter
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, stats_from, load_data
import v24_engine as eng

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2

TH_UP = 0.045; TH_SIDE = 0.010
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan
block_L_R = (slope_use > TH_UP) & (~np.isnan(slope_use))
block_S_R = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

L_BLK_H_CUR = {0,1,2,12}
L_BLK_D_CUR = {5,6}
S_BLK_H_CUR = {0,1,2,12}
S_BLK_D_CUR = {0,5,6}

DAYNAMES = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

def run_with_sessions(l_bh, l_bd, s_bh, s_bd, start, end):
    """通過 monkey-patch 改 session filter 跑"""
    eng.L_BLK_H = set(l_bh); eng.L_BLK_D = set(l_bd)
    eng.S_BLK_H = set(s_bh); eng.S_BLK_D = set(s_bd)
    trades = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, start, end,
                              block_bars_L=block_L_R, block_bars_S=block_S_R)
    return trades

# ============ Baseline ============
tr_is = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, 0, IS_END)
tr_oos = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, IS_END, N)
base_is = sum(t['pnl'] for t in tr_is)
base_oos = sum(t['pnl'] for t in tr_oos)
base_tot = base_is + base_oos

print("="*95)
print("V14+R Session Filter Audit")
print(f"Baseline：L blk_h={sorted(L_BLK_H_CUR)} blk_d={[DAYNAMES[d] for d in sorted(L_BLK_D_CUR)]}")
print(f"          S blk_h={sorted(S_BLK_H_CUR)} blk_d={[DAYNAMES[d] for d in sorted(S_BLK_D_CUR)]}")
print(f"Baseline IS ${base_is:+.0f} / OOS ${base_oos:+.0f} / ALL ${base_tot:+.0f}")
print("="*95)

def fmt(lbl, is_pnl, oos_pnl):
    tot = is_pnl + oos_pnl
    d_is = is_pnl - base_is; d_oos = oos_pnl - base_oos; d_tot = tot - base_tot
    both = (d_is > 0 and d_oos > 0)
    flag = "   ← 雙向改善" if both else ""
    return f"  {lbl:<38} IS ${is_pnl:+7.0f}({d_is:+5.0f}) OOS ${oos_pnl:+7.0f}({d_oos:+5.0f}) ALL ${tot:+7.0f}({d_tot:+5.0f}){flag}"

# ============ Test 1: 解除一個 L 的 block hour ============
print("\n【測試 1】解禁 L 目前被 block 的小時（看原本的擋是否正確）")
for hr8 in sorted(L_BLK_H_CUR):
    new_L = L_BLK_H_CUR - {hr8}
    tr_is_t = run_with_sessions(new_L, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(new_L, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, IS_END, N)
    print(fmt(f"L 解禁 {hr8:02d}:00", sum(t['pnl'] for t in tr_is_t), sum(t['pnl'] for t in tr_oos_t)))

print("\n【測試 2】解禁 S 目前被 block 的小時")
for hr8 in sorted(S_BLK_H_CUR):
    new_S = S_BLK_H_CUR - {hr8}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, new_S, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, new_S, S_BLK_D_CUR, IS_END, N)
    print(fmt(f"S 解禁 {hr8:02d}:00", sum(t['pnl'] for t in tr_is_t), sum(t['pnl'] for t in tr_oos_t)))

# ============ Test 3-4: 新增 block hour ============
print("\n【測試 3】新增 L block 小時（看有沒有漏網的壞時段）")
allowed_hrs = [h for h in range(24) if h not in L_BLK_H_CUR]
for hr8 in allowed_hrs:
    new_L = L_BLK_H_CUR | {hr8}
    tr_is_t = run_with_sessions(new_L, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(new_L, L_BLK_D_CUR, S_BLK_H_CUR, S_BLK_D_CUR, IS_END, N)
    is_p = sum(t['pnl'] for t in tr_is_t); oos_p = sum(t['pnl'] for t in tr_oos_t)
    tot = is_p + oos_p
    if (is_p > base_is and oos_p > base_oos) or tot > base_tot + 100:
        print(fmt(f"L 新增禁 {hr8:02d}:00", is_p, oos_p))

print("\n【測試 4】新增 S block 小時")
allowed_hrs = [h for h in range(24) if h not in S_BLK_H_CUR]
for hr8 in allowed_hrs:
    new_S = S_BLK_H_CUR | {hr8}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, new_S, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, new_S, S_BLK_D_CUR, IS_END, N)
    is_p = sum(t['pnl'] for t in tr_is_t); oos_p = sum(t['pnl'] for t in tr_oos_t)
    tot = is_p + oos_p
    if (is_p > base_is and oos_p > base_oos) or tot > base_tot + 100:
        print(fmt(f"S 新增禁 {hr8:02d}:00", is_p, oos_p))

# ============ Test 5-6: 週日 ============
print("\n【測試 5】L 解禁日 / 新增禁日")
for d in sorted(L_BLK_D_CUR):
    new_L = L_BLK_D_CUR - {d}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, new_L, S_BLK_H_CUR, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, new_L, S_BLK_H_CUR, S_BLK_D_CUR, IS_END, N)
    print(fmt(f"L 解禁 {DAYNAMES[d]}", sum(t['pnl'] for t in tr_is_t), sum(t['pnl'] for t in tr_oos_t)))
for d in [0,1,2,3,4]:
    if d in L_BLK_D_CUR: continue
    new_L = L_BLK_D_CUR | {d}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, new_L, S_BLK_H_CUR, S_BLK_D_CUR, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, new_L, S_BLK_H_CUR, S_BLK_D_CUR, IS_END, N)
    is_p = sum(t['pnl'] for t in tr_is_t); oos_p = sum(t['pnl'] for t in tr_oos_t)
    tot = is_p + oos_p
    if (is_p > base_is and oos_p > base_oos) or tot > base_tot + 100:
        print(fmt(f"L 新增禁 {DAYNAMES[d]}", is_p, oos_p))

print("\n【測試 6】S 解禁日 / 新增禁日")
for d in sorted(S_BLK_D_CUR):
    new_S = S_BLK_D_CUR - {d}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, new_S, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, new_S, IS_END, N)
    print(fmt(f"S 解禁 {DAYNAMES[d]}", sum(t['pnl'] for t in tr_is_t), sum(t['pnl'] for t in tr_oos_t)))
for d in [1,2,3,4]:
    if d in S_BLK_D_CUR: continue
    new_S = S_BLK_D_CUR | {d}
    tr_is_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, new_S, 0, IS_END)
    tr_oos_t = run_with_sessions(L_BLK_H_CUR, L_BLK_D_CUR, S_BLK_H_CUR, new_S, IS_END, N)
    is_p = sum(t['pnl'] for t in tr_is_t); oos_p = sum(t['pnl'] for t in tr_oos_t)
    tot = is_p + oos_p
    if (is_p > base_is and oos_p > base_oos) or tot > base_tot + 100:
        print(fmt(f"S 新增禁 {DAYNAMES[d]}", is_p, oos_p))

print("\n" + "="*95)
print("結論判讀：")
print("  - 若「解禁」顯示正改善（雙向），代表原禁止是 overkill，可考慮放寬")
print("  - 若「新增禁」顯示正改善（雙向），代表有漏網時段可補")
print("  - 單向改善 = 可能 IS-overfit，不建議改")
print("  - 無任何雙向改善 = 當前 session filter 已是 local optimum")
print("="*95)
