"""
極簡回測：純 EMA20 上下做多空（user 提問驗證用，非策略候選）

規則：
  - close > EMA20 → 做多 (LONG)
  - close < EMA20 → 做空 (SHORT)
  - 反向時 flip（出場 + 反手），不設 TP/SL
  - 無 cooldown / session / regime 過濾，純跟隨

帳戶模型：
  $1,000 餘額 / $200 margin / 20x / $4,000 notional / $4 fee/筆
"""
import pandas as pd
import numpy as np
from pathlib import Path

# ────── 帳戶 ──────
NOTIONAL = 4000.0
FEE = 4.0  # taker 0.04% × 2 + slip 0.01% × 2 = $4 / round-trip
EMA_PERIOD = 20

# ────── 資料 ──────
DATA = Path(__file__).parent.parent.parent / "data" / "ETHUSDT_1h_latest730d.csv"
df = pd.read_csv(DATA)
df["datetime"] = pd.to_datetime(df["datetime"])
df["ema20"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
df = df.dropna().reset_index(drop=True)

# ────── 回測 ──────
trades = []
pos = None  # {"side": "L"/"S", "entry": price, "entry_idx": i}

for i in range(EMA_PERIOD, len(df)):
    c = df.loc[i, "close"]
    e = df.loc[i, "ema20"]
    sig = "L" if c > e else "S"

    if pos is None:
        pos = {"side": sig, "entry": c, "entry_idx": i, "entry_time": df.loc[i, "datetime"]}
        continue

    if pos["side"] != sig:
        # flip
        ep = pos["entry"]
        if pos["side"] == "L":
            ret = (c - ep) / ep
        else:
            ret = (ep - c) / ep
        gross = ret * NOTIONAL
        net = gross - FEE
        trades.append({
            "side": pos["side"],
            "entry_time": pos["entry_time"],
            "exit_time": df.loc[i, "datetime"],
            "entry": ep, "exit": c,
            "ret_pct": ret * 100,
            "net_pnl": net,
            "hold_bars": i - pos["entry_idx"],
        })
        pos = {"side": sig, "entry": c, "entry_idx": i, "entry_time": df.loc[i, "datetime"]}

td = pd.DataFrame(trades)

# ────── 統計 ──────
n = len(td)
wins = (td["net_pnl"] > 0).sum()
losses = (td["net_pnl"] <= 0).sum()
wr = wins / n * 100 if n > 0 else 0
total_pnl = td["net_pnl"].sum()
avg_win = td.loc[td["net_pnl"] > 0, "net_pnl"].mean() if wins > 0 else 0
avg_loss = td.loc[td["net_pnl"] <= 0, "net_pnl"].mean() if losses > 0 else 0
gross_profit = td.loc[td["net_pnl"] > 0, "net_pnl"].sum()
gross_loss = -td.loc[td["net_pnl"] <= 0, "net_pnl"].sum()
pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
total_fees = n * FEE

# Equity curve + MDD
td = td.sort_values("exit_time").reset_index(drop=True)
td["equity"] = 1000 + td["net_pnl"].cumsum()
td["peak"] = td["equity"].cummax()
td["dd"] = td["equity"] - td["peak"]
mdd = abs(td["dd"].min()) if len(td) > 0 else 0

# 月度
td["ym"] = td["exit_time"].dt.to_period("M")
monthly = td.groupby("ym")["net_pnl"].sum()
pos_months = (monthly > 0).sum()
neg_months = (monthly <= 0).sum()
worst_month = monthly.min() if len(monthly) > 0 else 0
best_month = monthly.max() if len(monthly) > 0 else 0

# 平均持倉
avg_hold = td["hold_bars"].mean() if n > 0 else 0

# ────── 報告 ──────
days = (df["datetime"].iloc[-1] - df["datetime"].iloc[EMA_PERIOD]).days
period = f"{df['datetime'].iloc[EMA_PERIOD].date()} → {df['datetime'].iloc[-1].date()} ({days} 天)"

print("═" * 60)
print(f"純 EMA20 跟隨回測 (ETH 1h, {period})")
print("═" * 60)
print(f"交易筆數    : {n:>7,}")
print(f"勝率 (WR)  : {wr:>6.2f}%   ({wins} W / {losses} L)")
print(f"PF         : {pf:>7.2f}")
print(f"總損益     : ${total_pnl:>+10,.2f}")
print(f"手續費總計 : ${total_fees:>+10,.2f}   (-${total_fees:,.0f})")
print(f"平均贏     : ${avg_win:>+8.2f}")
print(f"平均輸     : ${avg_loss:>+8.2f}")
print(f"平均持倉   : {avg_hold:>6.2f} bar")
print(f"MDD        : ${mdd:>10,.2f}")
print(f"最終餘額   : ${1000 + total_pnl:>10,.2f}   (起始 $1,000)")
print()
print(f"月度       : {pos_months} 正 / {neg_months} 負  (共 {len(monthly)} 月)")
print(f"最佳月     : ${best_month:>+10,.2f}")
print(f"最差月     : ${worst_month:>+10,.2f}")
print()

# L vs S 拆解
for side in ["L", "S"]:
    sub = td[td["side"] == side]
    if len(sub) == 0:
        continue
    swr = (sub["net_pnl"] > 0).sum() / len(sub) * 100
    print(f"  {side}: {len(sub):>5} 筆, WR {swr:5.2f}%, PnL ${sub['net_pnl'].sum():+10,.2f}")

print()
print("─" * 60)
print("對照 V14+R 2 年 OOS：$4,549 / WR ~61% / MDD $334 / 12 正月")
print("─" * 60)
