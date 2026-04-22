"""
V23 G6 Verification — V14 baseline vs V14+R
獨立驗證 AI 解釋「G6 FAIL 來自 baseline」是否成立
Swap Test 定義同 V22/V23 稽核：Fwd = (IS_PnL - OOS_PnL)/|IS_PnL|
                               Bwd = (OOS_PnL - IS_PnL)/|OOS_PnL|
門檻：衰退率 >= 50% (絕對值) 即 FAIL

V23 split 為 N // 2 = 8816 (非 8793，假設 user typo，以 V23 一致性為準)
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_overlay_engine import run_v14_overlay, stats_from, load_data

df, o, h, l, c, hours, dows, mks, dks = load_data()
N = len(o); IS_END = N // 2
print(f"Bars N={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]")

# 指標先算整段（保守做法），再切 IS/OOS
sma200 = pd.Series(c).rolling(200).mean().values
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]
slope_use = np.roll(slope, 1); slope_use[0] = np.nan

TH_UP = 0.045; TH_SIDE = 0.010
block_L = (slope_use > TH_UP) & (~np.isnan(slope_use))
block_S = (np.abs(slope_use) < TH_SIDE) & (~np.isnan(slope_use))

# === 對照 1：V14 baseline ===
tr_is_base = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END)
tr_oos_base = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N)
base_is_pnl = stats_from(tr_is_base)['pnl']
base_oos_pnl = stats_from(tr_oos_base)['pnl']

# Swap Forward: new IS = original OOS → new_IS_PnL
# Swap Backward: new OOS = original IS → new_OOS_PnL
base_fwd_newIS = base_oos_pnl   # 原 OOS 當 new IS
base_bwd_newOOS = base_is_pnl    # 原 IS 當 new OOS
base_fwd_decay = (base_is_pnl - base_fwd_newIS) / abs(base_is_pnl) * 100 if base_is_pnl else 0
base_bwd_decay = (base_oos_pnl - base_bwd_newOOS) / abs(base_oos_pnl) * 100 if base_oos_pnl else 0
base_g6 = abs(base_fwd_decay) < 50 and abs(base_bwd_decay) < 50

# === 對照 2：V14+R ===
tr_is_r = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, 0, IS_END,
                           block_bars_L=block_L, block_bars_S=block_S)
tr_oos_r = run_v14_overlay(o, h, l, c, hours, dows, mks, dks, IS_END, N,
                            block_bars_L=block_L, block_bars_S=block_S)
r_is_pnl = stats_from(tr_is_r)['pnl']
r_oos_pnl = stats_from(tr_oos_r)['pnl']

r_fwd_newIS = r_oos_pnl
r_bwd_newOOS = r_is_pnl
r_fwd_decay = (r_is_pnl - r_fwd_newIS) / abs(r_is_pnl) * 100 if r_is_pnl else 0
r_bwd_decay = (r_oos_pnl - r_bwd_newOOS) / abs(r_oos_pnl) * 100 if r_oos_pnl else 0
r_g6 = abs(r_fwd_decay) < 50 and abs(r_bwd_decay) < 50

# 一致性驗證：V23 報告 V14+R 應為 IS $+2,209 / OOS $+4,294 / ALL $+6,503 / +$380
v23_expected_is = 2209; v23_expected_oos = 4294; v23_expected_delta = 380
actual_total = r_is_pnl + r_oos_pnl
base_total = base_is_pnl + base_oos_pnl
actual_delta = actual_total - base_total
is_match = abs(r_is_pnl - v23_expected_is) <= 50
oos_match = abs(r_oos_pnl - v23_expected_oos) <= 50
delta_match = abs(actual_delta - v23_expected_delta) <= 50

# === 對照 3：Overlay 單獨貢獻 ===
overlay_is = r_is_pnl - base_is_pnl
overlay_oos = r_oos_pnl - base_oos_pnl
same_dir = (overlay_is > 0 and overlay_oos > 0) or (overlay_is < 0 and overlay_oos < 0)

# 報告
print("\n" + "="*60)
print("=== V14 G6 Baseline 驗證報告 ===")
print("="*60)

print(f"\n一致性檢查（對比 V23 R5 報告值）:")
print(f"  V14+R IS: expected $+2,209, actual ${r_is_pnl:+.0f}  {'MATCH' if is_match else 'MISMATCH'}")
print(f"  V14+R OOS: expected $+4,294, actual ${r_oos_pnl:+.0f}  {'MATCH' if oos_match else 'MISMATCH'}")
print(f"  Overlay delta: expected +$380, actual ${actual_delta:+.0f}  {'MATCH' if delta_match else 'MISMATCH'}")

if not (is_match and oos_match and delta_match):
    print("\n!!! 警告：與 V23 報告值不符，請檢查實作 !!!")

print(f"\n對照 1：V14 單獨")
print(f"  原 IS PnL: ${base_is_pnl:+.0f}")
print(f"  原 OOS PnL: ${base_oos_pnl:+.0f}")
print(f"  Swap Forward new IS PnL: ${base_fwd_newIS:+.0f}")
print(f"    衰退率: (${base_is_pnl:+.0f} - ${base_fwd_newIS:+.0f}) / |${base_is_pnl:+.0f}| = {base_fwd_decay:+.1f}%")
print(f"  Swap Backward new OOS PnL: ${base_bwd_newOOS:+.0f}")
print(f"    衰退率: (${base_oos_pnl:+.0f} - ${base_bwd_newOOS:+.0f}) / |${base_oos_pnl:+.0f}| = {base_bwd_decay:+.1f}%")
print(f"  G6 判定: {'PASS' if base_g6 else 'FAIL'}"
      f" (Fwd {'PASS' if abs(base_fwd_decay)<50 else 'FAIL'} / Bwd {'PASS' if abs(base_bwd_decay)<50 else 'FAIL'})")

print(f"\n對照 2：V14+R")
print(f"  原 IS PnL: ${r_is_pnl:+.0f}")
print(f"  原 OOS PnL: ${r_oos_pnl:+.0f}")
print(f"  Swap Forward: ${r_fwd_newIS:+.0f}, 衰退率 {r_fwd_decay:+.1f}%")
print(f"  Swap Backward: ${r_bwd_newOOS:+.0f}, 衰退率 {r_bwd_decay:+.1f}%")
print(f"  G6 判定: {'PASS' if r_g6 else 'FAIL'}"
      f" (Fwd {'PASS' if abs(r_fwd_decay)<50 else 'FAIL'} / Bwd {'PASS' if abs(r_bwd_decay)<50 else 'FAIL'})")

print(f"\n對照 3：Overlay 單獨貢獻")
print(f"  IS:  V14+R ${r_is_pnl:+.0f} - V14 ${base_is_pnl:+.0f} = ${overlay_is:+.0f}")
print(f"  OOS: V14+R ${r_oos_pnl:+.0f} - V14 ${base_oos_pnl:+.0f} = ${overlay_oos:+.0f}")
print(f"  IS/OOS 同向: {'YES' if same_dir else 'NO'}")
ratio = overlay_oos / overlay_is if overlay_is != 0 else 0
print(f"  ratio OOS/IS: {ratio:+.2f}x")

# 情境判定
print(f"\n" + "="*60)
print("判定結論")
print("="*60)
if base_g6 == False and r_g6 == False and same_dir:
    scenario = "A"
    verdict = "V14+R 可部署（G6 FAIL 不是 overlay 引入，是 V14 baseline 本身的 regime dependency）"
    deploy = "YES，但仍提醒 G6 FAIL 是策略本身屬性；熊市表現不確定"
elif base_g6 == True and r_g6 == False:
    scenario = "B"
    verdict = "V14+R 引入了額外的 regime dependency"
    deploy = "NO，回頭調整 overlay 或退回 V14"
elif base_g6 == False and r_g6 == False and not same_dir:
    scenario = "C"
    verdict = "AI 解釋部分成立但不完整；overlay 貢獻 IS/OOS 背離"
    deploy = "CONDITIONAL，可部署但降級對 +$380 改善的信心；實盤可能看不到此改善"
elif base_g6 == True and r_g6 == True:
    scenario = "D"
    verdict = "不可能狀態 — V23 已報 V14+R Fwd -94.4% FAIL，若出現表示回測實作不一致"
    deploy = "NO，先修正回測實作"
else:
    scenario = "?"
    verdict = "未預期組合"
    deploy = "?"

print(f"判定結論：情境 {scenario}")
print(f"  {verdict}")
print(f"V14+R 是否可部署：{deploy}")

# 補充：看 overlay 貢獻的衰退率
if overlay_is != 0:
    ov_fwd = (overlay_is - overlay_oos) / abs(overlay_is) * 100
    print(f"\n補充：Overlay 貢獻本身的 Fwd 衰退率 = {ov_fwd:+.1f}%")
    print(f"  (IS overlay ${overlay_is:+.0f} → OOS overlay ${overlay_oos:+.0f})")
    print(f"  若 |衰退率| < 50% 表示 overlay 改善本身穩定")
    print(f"  Overlay 穩定性: {'穩定' if abs(ov_fwd) < 50 else '不穩定'}")
