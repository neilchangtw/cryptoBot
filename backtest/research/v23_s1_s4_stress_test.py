"""
V23 階段 1：V14 壓力測試 S1-S4
  S1 時序翻轉（重現 V22 結果 + 逐月對照）
  S2 Regime PnL 拆解（200-bar SMA 斜率分類 up/side/down）
  S3 橫盤段測試（4/8/12 週、價格範圍 < 10/15/20% 的 rolling sideways）
  S4 Rolling Drawdown（30/60/90 天 worst windows）
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v22_v14_self_audit import run_v14, stats_from, compute_indicators

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(SCRIPT_DIR, '..', '..', 'data', 'ETHUSDT_1h_latest730d.csv')

df = pd.read_csv(DATA)
o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
dt = pd.to_datetime(df['datetime'])
hours = dt.dt.hour.values; dows = dt.dt.dayofweek.values
mks = (dt.dt.year*100 + dt.dt.month).values
dks = (dt.dt.year*10000 + dt.dt.month*100 + dt.dt.day).values
N = len(o); IS_END = N // 2
print(f"Bars={N}  IS[0:{IS_END}]  OOS[{IS_END}:{N}]  Period {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

# ============ Baseline V14 trades ============
trades_all = run_v14(o,h,l,c,hours,dows,mks,dks,0,N)
print(f"\nV14 baseline: {len(trades_all)} trades")
sL = stats_from(trades_all,'L'); sS = stats_from(trades_all,'S')
print(f"  L: {sL['n']}t ${sL['pnl']:+.0f} WR{sL['wr']:.1f}% PF{sL['pf']:.2f}")
print(f"  S: {sS['n']}t ${sS['pnl']:+.0f} WR{sS['wr']:.1f}% PF{sS['pf']:.2f}")
total_baseline = sL['pnl'] + sS['pnl']
print(f"  Total ${total_baseline:+.0f}")

# ============================================================
# S1：時序翻轉 + 逐月對照
# ============================================================
print("\n" + "="*80)
print("S1  時序翻轉")
print("="*80)
o_r=o[::-1].copy(); h_r=h[::-1].copy(); l_r=l[::-1].copy(); c_r=c[::-1].copy()
hours_r=hours[::-1].copy(); dows_r=dows[::-1].copy()
mks_r=mks[::-1].copy(); dks_r=dks[::-1].copy()

trades_rev = run_v14(o_r,h_r,l_r,c_r,hours_r,dows_r,mks_r,dks_r,0,N)
sL_r = stats_from(trades_rev,'L'); sS_r = stats_from(trades_rev,'S')
total_rev = sL_r['pnl'] + sS_r['pnl']

print(f"原始: L ${sL['pnl']:+.0f} WR{sL['wr']:.1f}%  S ${sS['pnl']:+.0f} WR{sS['wr']:.1f}%  Total ${total_baseline:+.0f}")
print(f"翻轉: L ${sL_r['pnl']:+.0f} WR{sL_r['wr']:.1f}%  S ${sS_r['pnl']:+.0f} WR{sS_r['wr']:.1f}%  Total ${total_rev:+.0f}")
print(f"差值: ${total_rev - total_baseline:+.0f}")

# 逐月對照：原始
by_mo_orig = {}
for t in trades_all:
    by_mo_orig[t['entry_mk']] = by_mo_orig.get(t['entry_mk'],0) + t['pnl']

# 翻轉的 month key 需轉回原始序（翻轉後 bar i 對應原始 bar N-1-i）
by_mo_rev = {}
for t in trades_rev:
    orig_bar = N - 1 - t['entry_bar']
    orig_mk = mks[orig_bar]
    by_mo_rev[orig_mk] = by_mo_rev.get(orig_mk,0) + t['pnl']

all_months = sorted(set(list(by_mo_orig.keys()) + list(by_mo_rev.keys())))
print(f"\n逐月對照 (原始 vs 翻轉時序，翻轉的月份已映射回原始月):")
print(f"{'月':>7} {'原始 $':>8} {'翻轉 $':>8} {'差':>8}")
diffs = []
for m in all_months:
    po = by_mo_orig.get(m, 0); pr = by_mo_rev.get(m, 0)
    print(f"{m:>7} ${po:>+7.0f} ${pr:>+7.0f} ${pr-po:>+7.0f}")
    diffs.append((m, pr-po))
diffs_sorted = sorted(diffs, key=lambda x: abs(x[1]), reverse=True)
print(f"\n差異最大 3 個月:")
for m, d in diffs_sorted[:3]:
    print(f"  {m}: 差 ${d:+.0f}")

# ============================================================
# S2：Regime PnL 拆解（200-bar SMA 斜率）
# ============================================================
print("\n" + "="*80)
print("S2  Regime 拆解 (SMA200 斜率分類)")
print("="*80)

sma200 = pd.Series(c).rolling(200).mean().values
# slope over last 100 bars (pct change)
slope = np.full(N, np.nan)
for i in range(300, N):
    if not np.isnan(sma200[i]) and not np.isnan(sma200[i-100]) and sma200[i-100] > 0:
        slope[i] = (sma200[i] - sma200[i-100]) / sma200[i-100]

# 三類閾值：±2% over 100 bars
UP_TH = 0.02; DN_TH = -0.02
regime = np.full(N, 'UNK', dtype=object)
for i in range(N):
    if np.isnan(slope[i]): regime[i] = 'UNK'
    elif slope[i] > UP_TH: regime[i] = 'UP'
    elif slope[i] < DN_TH: regime[i] = 'DN'
    else: regime[i] = 'SIDE'

# Bar counts
for r in ['UP','SIDE','DN','UNK']:
    cnt = int((regime==r).sum())
    pct = cnt/N*100
    print(f"  {r:>4}: {cnt:>5} bars ({pct:.1f}%)")

# 每 regime 的 V14 表現
print(f"\n{'Regime':>6} {'bars':>6} {'L n':>4} {'L PnL':>7} {'L /t':>6} {'L WR':>5} "
      f"{'S n':>4} {'S PnL':>7} {'S /t':>6} {'S WR':>5} {'Total':>8}")
for r in ['UP','SIDE','DN','UNK']:
    bars_in = int((regime==r).sum())
    L_in = [t for t in trades_all if t['side']=='L' and regime[t['entry_bar']]==r]
    S_in = [t for t in trades_all if t['side']=='S' and regime[t['entry_bar']]==r]
    Lp = sum(t['pnl'] for t in L_in); Sp = sum(t['pnl'] for t in S_in)
    Lwr = sum(1 for t in L_in if t['pnl']>0)/len(L_in)*100 if L_in else 0
    Swr = sum(1 for t in S_in if t['pnl']>0)/len(S_in)*100 if S_in else 0
    Lpt = Lp/len(L_in) if L_in else 0
    Spt = Sp/len(S_in) if S_in else 0
    print(f"{r:>6} {bars_in:>6} {len(L_in):>4} ${Lp:>+6.0f} ${Lpt:>+5.1f} {Lwr:>4.1f}% "
          f"{len(S_in):>4} ${Sp:>+6.0f} ${Spt:>+5.1f} {Swr:>4.1f}% ${Lp+Sp:>+7.0f}")

# MDD per regime（以該 regime bar 發生的交易做 equity curve）
print(f"\nRegime 區段 MDD:")
for r in ['UP','SIDE','DN']:
    trs = sorted([t for t in trades_all if regime[t['entry_bar']]==r], key=lambda x: x['exit_bar'])
    if not trs: continue
    eq = np.cumsum([t['pnl'] for t in trs])
    peak = np.maximum.accumulate(eq); dd = eq - peak
    mdd = -dd.min() if len(dd) else 0
    print(f"  {r}: n={len(trs)} PnL ${eq[-1]:+.0f} MDD ${mdd:.0f}")

# ============================================================
# S3：橫盤段測試
# ============================================================
print("\n" + "="*80)
print("S3  橫盤段 (rolling window price range)")
print("="*80)

# 對每 bar 計算過去 window bar 的 (max-min)/min 作為 range_pct
def rolling_range_pct(c_arr, window):
    out = np.full(len(c_arr), np.nan)
    for i in range(window, len(c_arr)):
        win = c_arr[i-window:i]
        if win.min() > 0:
            out[i] = (win.max() - win.min()) / win.min()
    return out

# 先把 V14 trades 按 entry_bar 排序
trades_sorted = sorted(trades_all, key=lambda x: x['entry_bar'])

print(f"{'Window':>8} {'Thresh':>7} {'bars in side':>12} {'trades':>7} {'PnL $':>8} {'/t':>6} {'WR':>5}")
# ETH 2024-2026 波動大，4w/10% 幾乎不存在，放寬到適合 ETH 的範圍
combos = [(2, 2*7*24, 0.10), (2, 2*7*24, 0.15),
          (4, 4*7*24, 0.15), (4, 4*7*24, 0.20), (4, 4*7*24, 0.25),
          (8, 8*7*24, 0.20), (8, 8*7*24, 0.25), (8, 8*7*24, 0.30),
          (12, 12*7*24, 0.25), (12, 12*7*24, 0.30), (12, 12*7*24, 0.35)]
for weeks, bars, th in combos:
    rng = rolling_range_pct(c, bars)
    in_side = (rng < th) & (~np.isnan(rng))
    bar_ct = int(in_side.sum())
    pct = bar_ct/N*100
    trs_in = [t for t in trades_sorted if in_side[t['entry_bar']]]
    pnl = sum(t['pnl'] for t in trs_in)
    wr = sum(1 for t in trs_in if t['pnl']>0)/len(trs_in)*100 if trs_in else 0
    ppt = pnl/len(trs_in) if trs_in else 0
    print(f"{weeks:>3}w     {th*100:>4.0f}%  {bar_ct:>5} ({pct:>4.1f}%) {len(trs_in):>6} "
          f"${pnl:>+6.0f} ${ppt:>+5.1f} {wr:>4.1f}%")

# 找最長連續橫盤段 (4w / 20% 作為 illustrative，因為 8w/10% 在 ETH 不存在)
print(f"\nLongest consecutive sideways runs (4-week rolling range < 20%):")
rng_side = rolling_range_pct(c, 4*7*24)
in_side = (rng_side < 0.20) & (~np.isnan(rng_side))
# Find runs
runs = []
i = 0
while i < N:
    if in_side[i]:
        j = i
        while j < N and in_side[j]: j += 1
        runs.append((i, j, j-i))
        i = j
    else:
        i += 1
runs_sorted = sorted(runs, key=lambda x: -x[2])
print(f"  Top 3 longest sideways runs (8w/10%):")
for s, e, L in runs_sorted[:3]:
    dt_s = df['datetime'].iloc[s]; dt_e = df['datetime'].iloc[e-1]
    trs_in = [t for t in trades_sorted if s <= t['entry_bar'] < e]
    pnl = sum(t['pnl'] for t in trs_in)
    print(f"    {dt_s} -> {dt_e}  {L} bars ({L/24:.0f} days)  V14: {len(trs_in)}t ${pnl:+.0f}")

# ============================================================
# S4：Rolling Drawdown
# ============================================================
print("\n" + "="*80)
print("S4  Rolling Drawdown (worst 30/60/90-day windows)")
print("="*80)

# 建 per-bar equity: equity[i] = sum of pnl for trades with exit_bar <= i
equity = np.zeros(N)
trades_by_exit = sorted(trades_all, key=lambda x: x['exit_bar'])
ptr = 0; cum = 0
for i in range(N):
    while ptr < len(trades_by_exit) and trades_by_exit[ptr]['exit_bar'] <= i:
        cum += trades_by_exit[ptr]['pnl']
        ptr += 1
    equity[i] = cum

# Rolling drawdown over window W：對每個 bar i 計算 equity[i] - max(equity[i-W:i])
def worst_windows(equity_arr, window, top_n=5):
    """Return top-N non-overlapping worst drawdown windows."""
    drawdowns = []
    for i in range(window, len(equity_arr)):
        win_eq = equity_arr[i-window:i+1]
        win_peak = np.maximum.accumulate(win_eq)
        win_dd = (win_eq - win_peak).min()
        drawdowns.append((i, win_dd))
    drawdowns.sort(key=lambda x: x[1])
    # Non-overlapping: 下一個 pick 必須距離已選 pick >= window bars
    picks = []
    for end_bar, dd in drawdowns:
        conflict = any(abs(end_bar - ep) < window for ep, _ in picks)
        if not conflict:
            picks.append((end_bar, dd))
        if len(picks) >= top_n: break
    return picks

for window_days, window_bars in [(30, 30*24), (60, 60*24), (90, 90*24)]:
    worst = worst_windows(equity, window_bars, top_n=5)
    print(f"\nWorst 5 {window_days}-day windows:")
    for end_bar, dd in worst:
        start_bar = end_bar - window_bars
        dt_s = df['datetime'].iloc[max(0,start_bar)]
        dt_e = df['datetime'].iloc[end_bar]
        # 該 window 內交易數
        trs_in = [t for t in trades_all if start_bar <= t['entry_bar'] <= end_bar]
        acc_pct = dd / 1000 * 100  # $1K 帳戶
        print(f"  {dt_s[:16]} -> {dt_e[:16]}  DD ${dd:+.0f}  "
              f"({acc_pct:+.1f}% of $1K)  {len(trs_in)} trades in window")

# ============================================================
# 實戰意義摘要
# ============================================================
print("\n" + "="*80)
print("實戰意義摘要")
print("="*80)

up_pnl = sum(t['pnl'] for t in trades_all if regime[t['entry_bar']]=='UP')
side_pnl = sum(t['pnl'] for t in trades_all if regime[t['entry_bar']]=='SIDE')
dn_pnl = sum(t['pnl'] for t in trades_all if regime[t['entry_bar']]=='DN')
print(f"V14 regime 貢獻: UP ${up_pnl:+.0f}  SIDE ${side_pnl:+.0f}  DN ${dn_pnl:+.0f}")
print(f"時序翻轉損失: ${total_rev - total_baseline:+.0f}")

# 最壞 30 天 %
worst_30 = worst_windows(equity, 30*24, 1)[0][1]
print(f"最壞 30 天 drawdown: ${worst_30:+.0f} ({worst_30/1000*100:+.1f}% of $1K)")

# 橫盤段 V14 表現（8w/10% 最長段）
if runs_sorted:
    s,e,L = runs_sorted[0]
    trs_in = [t for t in trades_sorted if s <= t['entry_bar'] < e]
    pnl_side = sum(t['pnl'] for t in trs_in)
    print(f"最長橫盤段 V14 PnL: ${pnl_side:+.0f} 於 {df['datetime'].iloc[s][:10]}~{df['datetime'].iloc[e-1][:10]}")

print("\nDONE")
