"""
2 年 V14+R+V25-D 完整回測 — 按月拆解，看 May 2026 -$30 在歷史分佈中是否合理。
重用 live_review_37d.simulate() 確保邏輯與線上一致。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strategy as S  # noqa: E402
from live_review_37d import simulate  # noqa: E402

DATA = ROOT / "data"

raw = pd.read_csv(DATA / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
ind = S.compute_indicators(raw).reset_index(drop=True)

START = str(ind["datetime"].iloc[400])  # 跳過 warmup (~17 天)
END = str(ind["datetime"].iloc[-1])
print(f"Backtest window: {START} → {END}")

bt = simulate(ind, START, END)
bt["entry_dt"] = pd.to_datetime(bt["entry_dt"])
bt["month"] = bt["entry_dt"].dt.to_period("M")

# ── 月度統計 ──
mon = bt.groupby("month").agg(
    n=("pnl", "size"),
    pnl=("pnl", "sum"),
    wr=("pnl", lambda x: (x > 0).mean() * 100),
    best=("pnl", "max"),
    worst=("pnl", "min"),
).round(2)

print("\n按月 PnL 分佈：")
print(mon.to_string())

# ── 月度 PnL 統計 ──
m_pnl = mon["pnl"]
print(f"\n月度 PnL 統計（{len(m_pnl)} 個月）：")
print(f"  mean    : ${m_pnl.mean():7.2f}")
print(f"  median  : ${m_pnl.median():7.2f}")
print(f"  std     : ${m_pnl.std():7.2f}")
print(f"  min     : ${m_pnl.min():7.2f}")
print(f"  max     : ${m_pnl.max():7.2f}")
print(f"  正月    : {(m_pnl > 0).sum()}/{len(m_pnl)} = {(m_pnl > 0).mean() * 100:.1f}%")
print(f"  虧損月  : {(m_pnl < 0).sum()}")
print(f"  最大虧月: ${m_pnl.min():.2f}")

# 與 May 2026 對比
may_pnl = -29.92  # Live 5 月實際
print(f"\n  Live 2026-05 (37 天中的 21 天)：${may_pnl:.2f}")
percentile = (m_pnl <= may_pnl).mean() * 100
print(f"  落在歷史月度分佈 {percentile:.1f} 百分位（越低越罕見）")

# ── 連續月份的「最壞 21 天滾動」 ──
# 把 trades 依 entry_dt 排序，21 天滾動 PnL
bt_sorted = bt.sort_values("entry_dt").reset_index(drop=True)
windows = []
for i, t in bt_sorted.iterrows():
    end = t["entry_dt"]
    start = end - pd.Timedelta(days=21)
    in_win = bt_sorted[(bt_sorted["entry_dt"] >= start) & (bt_sorted["entry_dt"] <= end)]
    windows.append({
        "end": end,
        "n_trades": len(in_win),
        "pnl": in_win["pnl"].sum(),
    })
wd = pd.DataFrame(windows)
print(f"\n21 天滾動 PnL 統計（{len(wd)} 個窗口）：")
print(f"  mean    : ${wd['pnl'].mean():7.2f}")
print(f"  worst   : ${wd['pnl'].min():7.2f}")
print(f"  best    : ${wd['pnl'].max():7.2f}")
print(f"  負窗口  : {(wd['pnl'] < 0).sum()}/{len(wd)} = {(wd['pnl'] < 0).mean() * 100:.1f}%")
print(f"  Live 5 月 -$30 vs 此分佈：{(wd['pnl'] <= may_pnl).mean() * 100:.1f} 百分位")

# ── ETH 在 5 月的 regime 分佈 vs 全期 ──
ind["month"] = ind["datetime"].dt.to_period("M")
ind["regime"] = ind["sma_slope"].apply(S.classify_regime)
may_bars = ind[ind["month"].astype(str) == "2026-05"]
all_bars = ind[ind["sma_slope"].notna()]

print("\n5 月 vs 全期 regime 占比：")
print(f"{'regime':>10} {'May 2026':>10} {'全期':>10}")
for r in ["UP", "MILD_UP", "SIDE", "DOWN"]:
    may_pct = (may_bars["regime"] == r).mean() * 100 if len(may_bars) else 0
    all_pct = (all_bars["regime"] == r).mean() * 100
    print(f"{r:>10} {may_pct:>9.1f}% {all_pct:>9.1f}%")
