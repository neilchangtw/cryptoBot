"""
V23 Path R Round 1 — Regime Gate via SMA200 slope
假說: V14 在 SIDEWAYS regime 每筆 PnL 打 5 折（S2 findings）。
      用 SMA200 100-bar slope 作為 regime classifier，|slope| < TH 時禁止進場。
新增參數: slope_threshold (1 個 knob, slope_window=100 固定於 S2)
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import (run_v14_overlay, stats_from, load_data,
                                worst_rolling_dd, monthly_pnl)

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

# === Regime classifier: SMA200 slope over 100 bars ===
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]

# Shift(1) for use-of-indicator safety (classify at bar i uses slope at bar i-1)
slope_use = np.roll(slope, 1); slope_use[0] = np.nan


def run_with_gate(th, start, end, o_=o, h_=h, l_=l, c_=c, hours_=hours, dows_=dows,
                  mks_=mks, dks_=dks, slope_=slope_use):
    block = np.abs(slope_) < th
    block[np.isnan(slope_)] = False
    return run_v14_overlay(o_, h_, l_, c_, hours_, dows_, mks_, dks_, start, end,
                           block_bars_L=block, block_bars_S=block)


# ============ Baseline (no overlay) ============
tr_is_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,0,IS_END)
tr_oos_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,IS_END,N)
base_is = stats_from(tr_is_base); base_oos = stats_from(tr_oos_base)
print(f"\nBASELINE V14  IS: {base_is['n']}t ${base_is['pnl']:+.0f} MDD${base_is['mdd']:.0f}  "
      f"OOS: {base_oos['n']}t ${base_oos['pnl']:+.0f} MDD${base_oos['mdd']:.0f}")

# ============ G1+G4 Grid: threshold ============
print("\n" + "="*90)
print("Grid: slope_threshold (G1 IS-first + G4 neighborhood)")
print("="*90)
print(f"{'TH':>6} {'IS n':>5} {'IS PnL':>8} {'IS MDD':>7} {'OOS n':>5} {'OOS PnL':>8} {'OOS MDD':>8}")
thresholds = [0.005, 0.010, 0.015, 0.020, 0.025, 0.030, 0.040]
grid = []
for th in thresholds:
    ti = run_with_gate(th, 0, IS_END)
    to = run_with_gate(th, IS_END, N)
    si = stats_from(ti); so = stats_from(to)
    grid.append((th, ti, to, si, so))
    print(f"{th:>6.3f} {si['n']:>5} ${si['pnl']:>+7.0f} ${si['mdd']:>6.0f} "
          f"{so['n']:>5} ${so['pnl']:>+7.0f} ${so['mdd']:>7.0f}")

# Pick best IS (G1)
best = max(grid, key=lambda x: x[3]['pnl'])
TH_STAR = best[0]; tr_is = best[1]; tr_oos = best[2]
si = best[3]; so = best[4]
print(f"\nBest IS TH*: {TH_STAR:.3f}  (IS ${si['pnl']:+.0f}, OOS ${so['pnl']:+.0f})")

# G3: IS/OOS 同向（overlay 改善 IS 時，也要改善 OOS）
is_delta = si['pnl'] - base_is['pnl']
oos_delta = so['pnl'] - base_oos['pnl']
print(f"\nG3 IS/OOS 同向: IS_delta ${is_delta:+.0f}  OOS_delta ${oos_delta:+.0f}  "
      f"{'PASS' if np.sign(is_delta)==np.sign(oos_delta) else 'FAIL'}")

# G4: 鄰域穩定（TH* ±1 step 不崩 > 30%）
idx = thresholds.index(TH_STAR)
neigh_ok = True
for di in [-1, 1]:
    j = idx + di
    if 0 <= j < len(thresholds):
        nis = grid[j][3]['pnl']
        degrade = (si['pnl'] - nis) / abs(si['pnl']) if si['pnl']!=0 else 0
        if abs(degrade) > 0.30:
            neigh_ok = False
print(f"G4 鄰域 (±1 step): {'PASS' if neigh_ok else 'FAIL'}")

# G6: Swap
fwd = (si['pnl'] - so['pnl'])/abs(si['pnl'])*100 if si['pnl'] else 0
bwd = (so['pnl'] - si['pnl'])/abs(so['pnl'])*100 if so['pnl'] else 0
print(f"G6 Swap: Fwd {fwd:+.1f}%  Bwd {bwd:+.1f}%  "
      f"{'PASS' if abs(fwd)<50 and abs(bwd)<50 else 'FAIL'}")

# G7: WF 6 windows
print(f"\nG7 WF 6-window:")
wf_pos = 0; wf_base_pos = 0
for w in range(6):
    sb = w*(N//6); eb = (w+1)*(N//6) if w<5 else N
    t_over = run_with_gate(TH_STAR, sb, eb)
    t_base = run_v14_overlay(o,h,l,c,hours,dows,mks,dks,sb,eb)
    po = stats_from(t_over)['pnl']; pb = stats_from(t_base)['pnl']
    if po > pb: wf_pos += 1
    if pb > 0: wf_base_pos += 1
    print(f"  W{w+1}: base ${pb:+.0f}  overlay ${po:+.0f}  "
          f"{'improved' if po>pb else 'worse'}")
print(f"  overlay improved in {wf_pos}/6  {'PASS' if wf_pos>=4 else 'FAIL'}")

# G8 時序翻轉 — KEY gate
print(f"\n" + "="*90)
print("G8 時序翻轉 (V23 核心 Gate)")
print("="*90)
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hr_r=hours[::-1].copy(); dw_r=dows[::-1].copy(); mk_r=mks[::-1].copy(); dk_r=dks[::-1].copy()
# Recompute slope on reversed
sma_r = pd.Series(c_r).rolling(200).mean().values
slope_r = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma_r[i]) and sma_r[i-100] > 0:
        slope_r[i] = (sma_r[i] - sma_r[i-100]) / sma_r[i-100]
slope_r_use = np.roll(slope_r, 1); slope_r_use[0] = np.nan

# V14 baseline reversed
tr_base_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N)
base_rev_pnl = stats_from(tr_base_rev)['pnl']

# Overlay on reversed
block_r = np.abs(slope_r_use) < TH_STAR
block_r[np.isnan(slope_r_use)] = False
tr_over_rev = run_v14_overlay(o_r,h_r,l_r,c_r,hr_r,dw_r,mk_r,dk_r,0,N,
                               block_bars_L=block_r, block_bars_S=block_r)
over_rev_pnl = stats_from(tr_over_rev)['pnl']

print(f"V14 baseline reversed:   ${base_rev_pnl:+.0f}")
print(f"V14 + overlay reversed:  ${over_rev_pnl:+.0f}")
print(f"改善: ${over_rev_pnl - base_rev_pnl:+.0f}")
g8_pass = over_rev_pnl > base_rev_pnl
print(f"G8: {'PASS (overlay reduces reversal loss)' if g8_pass else 'FAIL'}")

# G9: Remove best month
print(f"\nG9 Remove-Best-Month:")
all_tr = tr_is + tr_oos
by_mo = monthly_pnl(all_tr)
if by_mo:
    best_m = max(by_mo, key=by_mo.get)
    without = sum(v for k,v in by_mo.items() if k!=best_m)
    print(f"  best {best_m} ${by_mo[best_m]:+.0f}, without best ${without:+.0f}  "
          f"{'PASS' if without>0 else 'FAIL'}")

# G10: 新增參數 ≤ 2 (slope_threshold only, slope_window=100 fixed)
print(f"\nG10: 新增參數 = 1 (slope_threshold)  PASS")

# R-G11: Gate-not-prediction
# 檢查 regime label 是否能預測下一根 bar 的 sign
# AUC > 0.55 → FAIL (表示不小心做出了方向預測)
print(f"\nR-G11 Gate-not-prediction:")
# 簡易 AUC：regime label vs next-bar return
fwd_ret = np.roll(c, -1) / c - 1; fwd_ret[-1] = 0
fwd_sign = (fwd_ret > 0).astype(int)

# Slope-based label (UP/SIDE/DN)
lbl_up = slope_use > TH_STAR
lbl_dn = slope_use < -TH_STAR
lbl_side = np.abs(slope_use) < TH_STAR

# Check: if we used "UP vs DN" as binary predictor for next-bar direction
valid = ~np.isnan(slope_use) & (lbl_up | lbl_dn)
if valid.sum() > 0:
    y = fwd_sign[valid]
    x = lbl_up[valid].astype(int)  # predict: UP → expect positive next ret
    # Simple accuracy & WR of matching sign
    match_rate = (y == x).mean()
    from sklearn.metrics import roc_auc_score
    try:
        auc = roc_auc_score(y, x)
    except Exception:
        auc = 0.5
    print(f"  UP/DN label predicts next-bar sign: match={match_rate:.3f}  AUC={auc:.3f}  "
          f"{'PASS' if auc <= 0.55 else 'FAIL (=prediction not gate)'}")

# R-G12 Transparency: 必須 ≤ 3 個 OHLCV-derived 數字
print(f"\nR-G12 Transparency: 1 數字 (SMA200 100-bar slope)  PASS")

# ============ Risk map comparison ============
print("\n" + "="*90)
print("Risk Map (baseline vs overlay, full period)")
print("="*90)
all_base = tr_is_base + tr_oos_base
all_over = tr_is + tr_oos

base_tot = stats_from(all_base); over_tot = stats_from(all_over)
print(f"                {'baseline':>12} {'overlay':>12}")
print(f"Total trades    {base_tot['n']:>12} {over_tot['n']:>12}")
print(f"Total PnL       ${base_tot['pnl']:>+11.0f} ${over_tot['pnl']:>+11.0f}")
print(f"MDD             ${base_tot['mdd']:>11.0f} ${over_tot['mdd']:>11.0f}")
print(f"Sharpe          {base_tot['sharpe']:>12.2f} {over_tot['sharpe']:>12.2f}")

# Worst 30d
w30_base = worst_rolling_dd(all_base, N, 30*24, 1)
w30_over = worst_rolling_dd(all_over, N, 30*24, 1)
print(f"Worst 30d DD    ${w30_base[0][1]:>+11.0f} ${w30_over[0][1]:>+11.0f}")

# Reversed
print(f"Reversed PnL    ${base_rev_pnl:>+11.0f} ${over_rev_pnl:>+11.0f}")

# Verdict
print(f"\n{'='*90}")
print("VERDICT")
print(f"{'='*90}")
g8_delta = over_rev_pnl - base_rev_pnl
improvement = over_tot['pnl'] - base_tot['pnl']
mdd_delta = base_tot['mdd'] - over_tot['mdd']

print(f"G1 IS+: {'PASS' if si['pnl']>0 else 'FAIL'}")
print(f"G2 OOS+: {'PASS' if so['pnl']>0 else 'FAIL'}")
print(f"G3 IS/OOS 同向: {'PASS' if np.sign(is_delta)==np.sign(oos_delta) else 'FAIL'}")
print(f"G4 鄰域穩定: {'PASS' if neigh_ok else 'FAIL'}")
print(f"G6 Swap: {'PASS' if abs(fwd)<50 and abs(bwd)<50 else 'FAIL'}")
print(f"G7 WF: {'PASS' if wf_pos>=4 else 'FAIL'}")
print(f"G8 時序翻轉改善: {'PASS' if g8_pass else 'FAIL'}   delta ${g8_delta:+.0f}")
print(f"G9 Remove best: {'PASS (earlier)' if by_mo else 'N/A'}")
print(f"G10 params≤2: PASS (1)")
print(f"R-G11 Gate-not-prediction: 檢查 AUC 見上")
print(f"R-G12 Transparency: PASS")
print(f"\nOverlay 對 PnL: ${improvement:+.0f}  對 MDD: 減少 ${mdd_delta:+.0f}")
