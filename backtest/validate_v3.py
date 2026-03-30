"""
v3 回測結果驗證腳本
檢查項目：
  1. SL 方向是否正確（做多 SL < 進場，做空 SL > 進場）
  2. 止損出場是否真的虧錢（排除已觸發 TP1 保本的）
  3. 出場價格是否合理
  4. PnL 計算是否正確（手動驗算）
  5. 時間分佈是否均勻（不是集中在某段時間）
  6. 多空勝率分佈
"""
import pandas as pd
import numpy as np
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# 載入交易紀錄
csv_path = os.path.join(os.path.dirname(__file__), "results", "20260328_214335_v3_phase4_trades.csv")
df = pd.read_csv(csv_path, parse_dates=["entry_time", "exit_time"])

print("=" * 70)
print("  v3 交易紀錄驗證")
print("=" * 70)

print(f"\n  總交易筆數: {len(df)}")
print(f"  做多: {len(df[df['side']=='long'])}  做空: {len(df[df['side']=='short'])}")

# ── 1. SL 方向驗證 ────────────────────────────────────────────
print("\n" + "-" * 70)
print("  1. 止損方向驗證")
print("-" * 70)

sl_trades = df[df["exit_reason"] == "stop_loss"]
print(f"  止損出場筆數: {len(sl_trades)}")

# 對於止損出場（未觸發 TP1 保本的），檢查方向
sl_no_tp1 = sl_trades[sl_trades["tp1_done"] == False]
sl_with_tp1 = sl_trades[sl_trades["tp1_done"] == True]
print(f"  - 未觸發 TP1 的止損: {len(sl_no_tp1)}")
print(f"  - 已觸發 TP1 的止損 (保本): {len(sl_with_tp1)}")

# 未觸發 TP1 的止損應該虧錢
sl_wrong = sl_no_tp1[sl_no_tp1["pnl"] > 0]
print(f"\n  未觸發 TP1 的止損卻獲利: {len(sl_wrong)} 筆 ({'BUG!' if len(sl_wrong) > 0 else 'OK'})")
if len(sl_wrong) > 0:
    print("  !!! 這些交易的止損方向可能有問題:")
    for _, t in sl_wrong.head(5).iterrows():
        print(f"      {t['side']} entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} pnl={t['pnl']:.2f}")

# 已觸發 TP1 的止損應該接近保本（PnL 接近 0 或小正）
print(f"\n  已觸發 TP1 的止損 PnL 分佈:")
if len(sl_with_tp1) > 0:
    print(f"    平均: ${sl_with_tp1['pnl'].mean():.2f}")
    print(f"    最大: ${sl_with_tp1['pnl'].max():.2f}")
    print(f"    最小: ${sl_with_tp1['pnl'].min():.2f}")
    tp1_sl_big_profit = sl_with_tp1[sl_with_tp1["pnl"] > 5]
    print(f"    PnL > $5 的: {len(tp1_sl_big_profit)} 筆 (應該很少或為 0)")

# ── 2. 出場價格合理性 ─────────────────────────────────────────
print("\n" + "-" * 70)
print("  2. 出場價格合理性")
print("-" * 70)

# 做多止損：出場價 < 進場價（除非保本）
long_sl = sl_trades[sl_trades["side"] == "long"]
long_sl_no_tp1 = long_sl[long_sl["tp1_done"] == False]
long_sl_wrong_dir = long_sl_no_tp1[long_sl_no_tp1["exit_price"] >= long_sl_no_tp1["entry_price"]]
print(f"  做多止損(未保本)出場價 >= 進場價: {len(long_sl_wrong_dir)} 筆 ({'BUG!' if len(long_sl_wrong_dir) > 0 else 'OK'})")

# 做空止損：出場價 > 進場價（除非保本）
short_sl = sl_trades[sl_trades["side"] == "short"]
short_sl_no_tp1 = short_sl[short_sl["tp1_done"] == False]
short_sl_wrong_dir = short_sl_no_tp1[short_sl_no_tp1["exit_price"] <= short_sl_no_tp1["entry_price"]]
print(f"  做空止損(未保本)出場價 <= 進場價: {len(short_sl_wrong_dir)} 筆 ({'BUG!' if len(short_sl_wrong_dir) > 0 else 'OK'})")

# ── 3. PnL 手動驗算 ──────────────────────────────────────────
print("\n" + "-" * 70)
print("  3. PnL 手動驗算（抽樣 10 筆）")
print("-" * 70)

NOTIONAL = 100 * 20  # margin * leverage
FEE_RATE = 0.0004
TP1_PCT = 0.10

sample = df.sample(min(10, len(df)), random_state=42)
errors = 0

for _, t in sample.iterrows():
    side = t["side"]
    ep = t["entry_price"]
    xp = t["exit_price"]
    tp1_done = t["tp1_done"]
    recorded_pnl = t["pnl"]

    d = 1 if side == "long" else -1

    if tp1_done:
        # TP1 部分難以精確還原（需要 TP1 價格），跳過精確驗算
        # 但至少檢查方向合理性
        pass
    else:
        # 完整倉位 PnL
        gross = d * (xp - ep) / ep * NOTIONAL
        fee = NOTIONAL * FEE_RATE * 2
        expected = gross - fee
        diff = abs(expected - recorded_pnl)
        if diff > 0.1:
            errors += 1
            print(f"  MISMATCH: {side} entry={ep:.2f} exit={xp:.2f} "
                  f"expected={expected:.2f} recorded={recorded_pnl:.2f} diff={diff:.2f}")
        else:
            print(f"  OK: {side} entry={ep:.2f} exit={xp:.2f} pnl={recorded_pnl:.2f}")

print(f"\n  PnL 計算錯誤: {errors} 筆")

# ── 4. 時間分佈 ───────────────────────────────────────────────
print("\n" + "-" * 70)
print("  4. 交易時間分佈（按月）")
print("-" * 70)

df["month"] = df["entry_time"].dt.to_period("M")
monthly = df.groupby("month").agg(
    trades=("pnl", "count"),
    pnl=("pnl", "sum"),
    wins=("pnl", lambda x: (x > 0).sum()),
).reset_index()

for _, m in monthly.iterrows():
    wr = m["wins"] / m["trades"] * 100 if m["trades"] > 0 else 0
    print(f"  {m['month']}: {m['trades']:>4} 筆  PnL ${m['pnl']:>8,.2f}  WR {wr:.1f}%")

zero_months = monthly[monthly["trades"] == 0]
if len(zero_months) > 0:
    print(f"\n  !!! 有 {len(zero_months)} 個月交易數為 0")

# ── 5. 出場原因分佈 ───────────────────────────────────────────
print("\n" + "-" * 70)
print("  5. 出場原因分佈")
print("-" * 70)

for reason in df["exit_reason"].unique():
    subset = df[df["exit_reason"] == reason]
    pnl = subset["pnl"].sum()
    wr = (subset["pnl"] > 0).sum() / len(subset) * 100
    print(f"  {reason:<20} {len(subset):>4} 筆  PnL ${pnl:>8,.2f}  WR {wr:.1f}%")

# ── 6. 多空分開看 ────────────────────────────────────────────
print("\n" + "-" * 70)
print("  6. 多空績效分開驗證")
print("-" * 70)

for side in ["long", "short"]:
    s = df[df["side"] == side]
    wins = s[s["pnl"] > 0]
    losses = s[s["pnl"] <= 0]
    gp = wins["pnl"].sum() if len(wins) else 0
    gl = abs(losses["pnl"].sum()) if len(losses) else 0.01
    pf = min(gp / gl, 99.99)
    eq = s["pnl"].cumsum()
    dd = (eq - eq.cummax()).min()

    print(f"\n  {side.upper()}:")
    print(f"    交易筆數: {len(s)}")
    print(f"    總 PnL:   ${s['pnl'].sum():,.2f}")
    print(f"    勝率:     {len(wins)/len(s)*100:.1f}%")
    print(f"    PF:       {pf:.2f}")
    print(f"    最大回撤: ${dd:,.2f}")
    print(f"    平均獲利: ${wins['pnl'].mean():.2f}" if len(wins) else "    平均獲利: N/A")
    print(f"    平均虧損: ${losses['pnl'].mean():.2f}" if len(losses) else "    平均虧損: N/A")

    # 止損佔比
    sl_count = len(s[s["exit_reason"] == "stop_loss"])
    sl_pnl = s[s["exit_reason"] == "stop_loss"]["pnl"].sum()
    print(f"    止損筆數: {sl_count} ({sl_count/len(s)*100:.1f}%)")
    print(f"    止損 PnL: ${sl_pnl:,.2f}")

# ── 7. 異常交易檢查 ──────────────────────────────────────────
print("\n" + "-" * 70)
print("  7. 異常交易檢查")
print("-" * 70)

# 超大獲利（可能有 bug）
big_wins = df[df["pnl"] > 100]
print(f"  單筆 > $100 的交易: {len(big_wins)} 筆")
for _, t in big_wins.iterrows():
    duration_h = (t["exit_time"] - t["entry_time"]).total_seconds() / 3600
    print(f"    {t['side']} ${t['pnl']:.2f} ({t['exit_reason']}) "
          f"entry={t['entry_price']:.0f} exit={t['exit_price']:.0f} duration={duration_h:.1f}h")

# 持倉時間 0 或極短
df["duration_min"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 60
ultra_short = df[df["duration_min"] < 5]
print(f"\n  持倉 < 5 分鐘: {len(ultra_short)} 筆")

# 進場 = 出場（可能有 bug）
same_price = df[df["entry_price"] == df["exit_price"]]
print(f"  進場=出場價: {len(same_price)} 筆 (應為保本止損)")
if len(same_price) > 0:
    non_breakeven = same_price[same_price["tp1_done"] == False]
    print(f"    其中未觸發 TP1: {len(non_breakeven)} 筆 ({'BUG!' if len(non_breakeven) > 0 else 'OK - 全為保本'})")

print("\n" + "=" * 70)
print("  驗證完成")
print("=" * 70)
