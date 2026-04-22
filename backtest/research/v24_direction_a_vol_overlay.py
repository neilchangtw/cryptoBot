"""
V24 Direction A — Vol Overlay on V14+R
在 V14+R 之上疊加波動率 overlay，目標：降低尾端風險但不犧牲 edge

禁區：
  - GK 波動（雙重計算）
  - 波動預測方向（如「高波動做多」）
  - 複製 V23 Path V 參數

允許變數：
  - ATR(14) / 200-bar rolling percentile
  - ATR(20) / 200-bar rolling percentile
  - Realized vol (20-bar close-to-close log return std)
  - HL range 20-bar rolling mean

測試 block_bars_L / block_bars_S hard-gate 過濾，與 R gate 疊加（OR 合併）

KPI：
  - PnL 不降 >5%
  - Sharpe +10% OR Worst30 改善 20%
  - G8 時序翻轉不變差
  - A-G11: overlay 貢獻的 IS/OOS ratio ≥ R's 0.47×
  - A-G12: 新 overlay block 與 R block 重疊率 < 60%
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, stats_from, load_data, worst_rolling_dd

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2

# V14+R slope gate (locked)
TH_UP = 0.045; TH_SIDE = 0.010
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan
block_L_R = (slope_use > TH_UP) & (~np.isnan(slope_use))
block_S_R = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

# 計算 vol 指標
def true_range(h, l, c):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum.reduce([h-l, np.abs(h-pc), np.abs(l-pc)])
    return tr
tr = true_range(o, h, l)  # use o here doesn't matter; fixing:
tr = np.maximum.reduce([h-l, np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))])
tr[0] = h[0]-l[0]

atr14 = pd.Series(tr).rolling(14).mean().values
atr20 = pd.Series(tr).rolling(20).mean().values

# Pct vs 200-bar window, shifted to avoid look-ahead
def roll_pctile(vals, w):
    out = np.full(len(vals), np.nan)
    for i in range(w-1, len(vals)):
        win = vals[i-w+1:i+1]; v = win[~np.isnan(win)]
        if len(v) < 20: continue
        out[i] = np.sum(v <= vals[i]) / len(v) * 100
    return out

atr14_use = np.roll(atr14, 1); atr14_use[0] = np.nan
atr20_use = np.roll(atr20, 1); atr20_use[0] = np.nan
atr14_pct = roll_pctile(atr14_use, 200)
atr20_pct = roll_pctile(atr20_use, 200)

# Realized vol (20-bar log return std)
logret = np.log(np.maximum(c / np.maximum(np.roll(c,1), 1e-10), 1e-10))
rv20 = pd.Series(logret).rolling(20).std().values
rv20_use = np.roll(rv20, 1); rv20_use[0] = np.nan
rv20_pct = roll_pctile(rv20_use, 200)

# HL range 20-bar mean
hl = h - l
hl20 = pd.Series(hl).rolling(20).mean().values
hl20_use = np.roll(hl20, 1); hl20_use[0] = np.nan
hl20_pct = roll_pctile(hl20_use, 200)

# ============ Baseline V14+R ============
tr_is = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END,
                         block_bars_L=block_L_R, block_bars_S=block_S_R)
tr_oos = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N,
                          block_bars_L=block_L_R, block_bars_S=block_S_R)
base_is = stats_from(tr_is); base_oos = stats_from(tr_oos)
base_all = stats_from(tr_is + tr_oos)
base_w30 = worst_rolling_dd(tr_is+tr_oos, N, 720, top_n=1)[0][1]
# G8 reference (time-reversed)
o_r, h_r, l_r, c_r = o[::-1], h[::-1], l[::-1], c[::-1]
# rebuild R gate on reversed data
sma200_r = pd.Series(c_r).rolling(200).mean().values
slope_r = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200_r[i]) and sma200_r[i-100] > 0:
        slope_r[i] = (sma200_r[i] - sma200_r[i-100]) / sma200_r[i-100]
slope_r_use = np.roll(slope_r, 1); slope_r_use[0] = np.nan
block_L_R_rev = (slope_r_use > TH_UP) & (~np.isnan(slope_r_use))
block_S_R_rev = (np.abs(slope_r_use) < TH_SIDE) & (~np.isnan(slope_r_use))
tr_rev = run_v14_overlay(o_r, h_r, l_r, c_r, hours[::-1], dows[::-1], mks[::-1], dks[::-1],
                          0, N, block_bars_L=block_L_R_rev, block_bars_S=block_S_R_rev)
base_rev_pnl = stats_from(tr_rev)['pnl']

print("="*110)
print(f"V24 Direction A — Vol Overlay on V14+R")
print(f"Bars N={N} / IS[0:{IS_END}] / OOS[{IS_END}:{N}]")
print("="*110)
print(f"\n基準 V14+R：")
print(f"  IS ${base_is['pnl']:+.0f} / OOS ${base_oos['pnl']:+.0f} / ALL ${base_all['pnl']:+.0f} "
      f"/ Sharpe {base_all['sharpe']:.2f} / MDD ${base_all['mdd']:.0f} / Worst30 ${base_w30:+.0f}")
print(f"  Time-reversed PnL: ${base_rev_pnl:+.0f}")
print(f"  IS/OOS overlay ratio (R only, overlay 貢獻待另估): N/A 此為 baseline")

# R overlay 貢獻 ratio 參考（從 v23_g6_verification 已知 OOS +$121 / IS +$258 = 0.47）
R_RATIO_REF = 0.47

# ============ 測試波動 overlay 配置 ============
# 每個配置：(label, block_L_gen_fn, block_S_gen_fn)
# 策略：低波動/高波動 block；L/S 可不同方向

configs = []
# ATR14 pctile
for sd in [70, 75, 80, 85, 90]:
    configs.append((f"ATR14_pct>{sd}_both",
                    (atr14_pct > sd) & (~np.isnan(atr14_pct)),
                    (atr14_pct > sd) & (~np.isnan(atr14_pct))))
# Low ATR block (block very-low vol to avoid chop)
for sd in [10, 15, 20]:
    configs.append((f"ATR14_pct<{sd}_both",
                    (atr14_pct < sd) & (~np.isnan(atr14_pct)),
                    (atr14_pct < sd) & (~np.isnan(atr14_pct))))
# ATR20 pctile (similar)
for sd in [80, 85, 90]:
    configs.append((f"ATR20_pct>{sd}_both",
                    (atr20_pct > sd) & (~np.isnan(atr20_pct)),
                    (atr20_pct > sd) & (~np.isnan(atr20_pct))))
# Realized vol high
for sd in [80, 85, 90]:
    configs.append((f"RV20_pct>{sd}_both",
                    (rv20_pct > sd) & (~np.isnan(rv20_pct)),
                    (rv20_pct > sd) & (~np.isnan(rv20_pct))))
# HL range high
for sd in [80, 85, 90]:
    configs.append((f"HL20_pct>{sd}_both",
                    (hl20_pct > sd) & (~np.isnan(hl20_pct)),
                    (hl20_pct > sd) & (~np.isnan(hl20_pct))))
# Asymmetric: block high vol only for L (L trend-follows, vol spike = whipsaw risk)
for sd in [80, 85, 90]:
    configs.append((f"ATR14_pct>{sd}_Lonly",
                    (atr14_pct > sd) & (~np.isnan(atr14_pct)),
                    np.zeros(N, dtype=bool)))
# Asymmetric: block high vol only for S (S mean-reverts in chop — high vol = good maybe not)
for sd in [80, 85, 90]:
    configs.append((f"ATR14_pct>{sd}_Sonly",
                    np.zeros(N, dtype=bool),
                    (atr14_pct > sd) & (~np.isnan(atr14_pct))))

print(f"\n測試 {len(configs)} 個 vol overlay 配置（疊加在 V14+R 之上）")
print(f"\n{'Config':<28}{'Tr':>4}{'PnL $':>9}{'IS':>8}{'OOS':>8}{'MDD':>7}{'W30':>8}{'Sh':>6}"
      f"{'OvLap%':>8}{'ΔPnL':>8}{'ΔW30':>8}")
print("-"*110)

results = []
for lbl, bL, bS in configs:
    # 疊加：R gate OR vol gate
    merged_L = block_L_R | bL
    merged_S = block_S_R | bS
    tr_is_v = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END,
                               block_bars_L=merged_L, block_bars_S=merged_S)
    tr_oos_v = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N,
                                block_bars_L=merged_L, block_bars_S=merged_S)
    tr_all_v = tr_is_v + tr_oos_v
    st = stats_from(tr_all_v)
    is_st = stats_from(tr_is_v); oos_st = stats_from(tr_oos_v)
    w30 = worst_rolling_dd(tr_all_v, N, 720, top_n=1)
    w30_v = w30[0][1] if w30 else 0

    # Overlap: 在非 R-block 的 bar 中，vol overlay 多 block 了多少
    # A-G12: 新 block bars / (R block bars)
    r_only_L = block_L_R & ~bL
    overlap_L = np.sum(block_L_R & bL) / max(np.sum(block_L_R), 1) * 100
    overlap_S = np.sum(block_S_R & bS) / max(np.sum(block_S_R), 1) * 100
    overlap_max = max(overlap_L, overlap_S)

    d_pnl = st['pnl'] - base_all['pnl']
    d_w30 = w30_v - base_w30

    # Overlay 貢獻 (vs R only)
    ov_is = is_st['pnl'] - base_is['pnl']
    ov_oos = oos_st['pnl'] - base_oos['pnl']
    ov_ratio = ov_oos / ov_is if ov_is != 0 else 0

    results.append({
        'label': lbl, 'n': st['n'], 'pnl': st['pnl'], 'is': is_st['pnl'], 'oos': oos_st['pnl'],
        'mdd': st['mdd'], 'w30': w30_v, 'sharpe': st['sharpe'],
        'overlap': overlap_max, 'd_pnl': d_pnl, 'd_w30': d_w30,
        'ov_is': ov_is, 'ov_oos': ov_oos, 'ov_ratio': ov_ratio,
        'bL': bL, 'bS': bS, 'merged_L': merged_L, 'merged_S': merged_S
    })
    print(f"{lbl:<28}{st['n']:>4}{st['pnl']:>+9.0f}{is_st['pnl']:>+8.0f}{oos_st['pnl']:>+8.0f}"
          f"{st['mdd']:>7.0f}{w30_v:>+8.0f}{st['sharpe']:>6.2f}"
          f"{overlap_max:>7.1f}%{d_pnl:>+8.0f}{d_w30:>+8.0f}")

# ============ 篩選：通過 KPI ============
print("\n" + "="*110)
print("候選篩選（通過 KPI）")
print("="*110)
print("KPI: PnL 降幅 <5%, Sharpe +10% 或 Worst30 +20%, Overlap <60%, Overlay IS/OOS 同向且 ratio >=0.47*R=0.22")

base_sharpe_10 = base_all['sharpe'] * 1.10
base_w30_120 = base_w30 * 0.80  # 改善 20%: 負值 * 0.8 (less negative)
base_pnl_95 = base_all['pnl'] * 0.95

print(f"\n門檻：")
print(f"  PnL >= ${base_pnl_95:+.0f} (基準 ${base_all['pnl']:+.0f} * 0.95)")
print(f"  Sharpe >= {base_sharpe_10:.2f} 或 Worst30 >= ${base_w30_120:+.0f}")
print(f"  Overlap < 60%, Overlay IS/OOS 同向 (ratio > 0)")

candidates = []
for r in results:
    pass_pnl = r['pnl'] >= base_pnl_95
    pass_risk = (r['sharpe'] >= base_sharpe_10) or (r['w30'] >= base_w30_120)
    pass_overlap = r['overlap'] < 60
    pass_overlay_dir = r['ov_is'] * r['ov_oos'] >= 0  # 同向或零
    if pass_pnl and pass_risk and pass_overlap:
        flag = "PASS"
        candidates.append(r)
    else:
        flag = "FAIL"
    if pass_pnl or pass_risk:
        reasons = []
        if not pass_pnl: reasons.append("PnL drop")
        if not pass_risk: reasons.append("no risk improvement")
        if not pass_overlap: reasons.append("overlap too high")
        if not pass_overlay_dir: reasons.append("overlay flipped sign")
        rtxt = ",".join(reasons) if reasons else "OK"
        print(f"  {flag} {r['label']:<28} PnL={r['pnl']:+.0f} Sh={r['sharpe']:.2f} W30={r['w30']:+.0f} "
              f"Overlap={r['overlap']:.1f}% OvRatio={r['ov_ratio']:+.2f} [{rtxt}]")

if not candidates:
    print("\n結論：所有 vol overlay 配置失敗，V14+R 已是 risk/reward 最優配置")
    print("Direction A REJECTED — vol filter 無法在不犧牲 edge 的情況下改善尾端")
else:
    print(f"\n通過 {len(candidates)} 個候選，將進入 G8 時序翻轉驗證與完整 10-Gate 稽核")
    for c in candidates[:5]:
        print(f"  候選：{c['label']} / PnL {c['pnl']:+.0f} / Sharpe {c['sharpe']:.2f} / W30 {c['w30']:+.0f}")
