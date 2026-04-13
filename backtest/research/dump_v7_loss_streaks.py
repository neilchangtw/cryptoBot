"""
V7 連續虧損分析 — 找出所有累計虧損超過 $500 的連續虧損段
"""
import sys, io, warnings
import numpy as np, pandas as pd
from math import log as mlog

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")
np.random.seed(42)

NOTIONAL = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT = 0.045; SN_PEN = 0.25
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
WARMUP = 150; MAX_PER_BAR = 2

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df["ret"] = np.log(df["close"] / df["close"].shift(1))
N = len(df)
mid = df["datetime"].iloc[0] + pd.Timedelta(days=365)
end = df["datetime"].iloc[-1] + pd.Timedelta(hours=1)

for w in [8, 10, 12, 15]:
    cs1 = df["close"].shift(1)
    df[f"bl_dn_{w}"] = cs1 < df["close"].shift(2).rolling(w - 1).min()
    df[f"bl_up_{w}"] = cs1 > df["close"].shift(2).rolling(w - 1).max()

df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
df["hour"] = df["datetime"].dt.hour; df["dow"] = df["datetime"].dt.weekday
df["sok"] = ~(df["hour"].isin(BLOCK_H) | df["dow"].isin(BLOCK_D))
df["ym"] = df["datetime"].dt.to_period("M")
df["dd50"] = ((df["close"] - df["close"].rolling(50).max()) / df["close"].rolling(50).max()).shift(1)
dd_mild = (df["dd50"] < -0.01).fillna(False)

def volume_entropy(vol, nbins=5):
    v = np.array(vol)
    if v.sum() == 0: return np.nan
    mn, mx = v.min(), v.max()
    if mx == mn: return 0.0
    edges = np.linspace(mn, mx, nbins + 1)
    counts = np.histogram(v, bins=edges)[0]
    total = counts.sum()
    if total == 0: return np.nan
    max_ent = mlog(nbins)
    if max_ent == 0: return 0.0
    ent = 0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * mlog(p)
    return ent / max_ent

ve_vals = np.full(N, np.nan)
vol_arr = df["volume"].values
for i in range(20, N):
    ve_vals[i] = volume_entropy(vol_arr[i-20:i])
df["ve20"] = ve_vals
df["ve20_shift"] = df["ve20"].shift(1)
df["ve20_pct"] = df["ve20_shift"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() > x.min() else 50, raw=False)

ve60_mask = (df["ve20_pct"] < 60).fillna(False) & df["bl_up_10"].fillna(False)

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_long_trail(df, mask, max_same=9, exit_cd=8, cap=15, tag="L"):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}
    for i in range(WARMUP, n-1):
        lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                    "entry_price":round(p["e"],2),"exit_price":round(ep,2),
                    "exit_type":"SN","bars_held":bh,"pnl":round(pnl,2)})
                lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                        "entry_price":round(p["e"],2),"exit_price":round(c,2),
                        "exit_type":t_,"bars_held":bh,"pnl":round(pnl,2)})
                    lx=i; done=True
            if not done and bh>=12 and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                    "entry_price":round(p["e"],2),"exit_price":round(c,2),
                    "exit_type":"Trail","bars_held":bh,"pnl":round(pnl,2)})
                lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        pos.append({"e":nxo,"ei":i,"edt":dt})
    return pd.DataFrame(trades)

def bt_short(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
             max_same=5, exit_cd=6, tag="S"):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    IND=ind_mask.values; BL=df[bl_col].fillna(False).values
    SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}
    for i in range(WARMUP, n-1):
        h=H[i]; lo_=Lo[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1+SAFENET_PCT)
            if h>=sn:
                ep=sn+(h-sn)*SN_PEN
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                    "entry_price":round(p["e"],2),"exit_price":round(ep,2),
                    "exit_type":"SN","bars_held":bh,"pnl":round(pnl,2)})
                lx=i; done=True
            if not done:
                tp=p["e"]*(1-tp_pct)
                if lo_<=tp:
                    pnl=(p["e"]-tp)*NOTIONAL/p["e"]-FEE
                    trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                        "entry_price":round(p["e"],2),"exit_price":round(tp,2),
                        "exit_type":"TP","bars_held":bh,"pnl":round(pnl,2)})
                    lx=i; done=True
            if not done and bh>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"sub":tag,"entry_dt":p["edt"],"exit_dt":dt,
                    "entry_price":round(p["e"],2),"exit_price":round(c,2),
                    "exit_type":"MH","bars_held":bh,"pnl":round(pnl,2)})
                lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i,"edt":dt})
    return pd.DataFrame(trades)

# ─── Run backtests ───
l_trades = bt_long_trail(df, ve60_mask)
s_all = []
for sn, tp, bl, mh in [("S1",0.02,10,19),("S2",0.015,10,19),("S3",0.02,12,19),("S4",0.015,10,12)]:
    s_all.append(bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp, max_hold=mh, tag=sn))
s_trades = pd.concat(s_all, ignore_index=True)

# Combine and filter OOS
all_t = pd.concat([l_trades, s_trades], ignore_index=True)
all_t["exit_dt"] = pd.to_datetime(all_t["exit_dt"])
all_t["entry_dt"] = pd.to_datetime(all_t["entry_dt"])
oos = all_t[all_t["exit_dt"] >= mid].sort_values("exit_dt").reset_index(drop=True)

# ═══════════════════════════════════════════════════════════════
# Analysis 1: Consecutive losing trades streaks
# ═══════════════════════════════════════════════════════════════
print("=" * 110)
print("分析一：連續虧損交易段（consecutive losing trades, 累計 < -$500）")
print("=" * 110)

streaks = []
curr_start = None
curr_loss = 0
curr_count = 0
curr_trades = []

for idx, r in oos.iterrows():
    if r["pnl"] <= 0:
        if curr_start is None:
            curr_start = idx
            curr_loss = 0
            curr_count = 0
            curr_trades = []
        curr_loss += r["pnl"]
        curr_count += 1
        curr_trades.append(r)
    else:
        if curr_start is not None and curr_loss < -500:
            streaks.append({
                "start_idx": curr_start,
                "end_idx": idx - 1,
                "count": curr_count,
                "total_loss": curr_loss,
                "start_dt": curr_trades[0]["exit_dt"],
                "end_dt": curr_trades[-1]["exit_dt"],
                "trades": curr_trades
            })
        curr_start = None
        curr_loss = 0
        curr_count = 0
        curr_trades = []

# Check last streak
if curr_start is not None and curr_loss < -500:
    streaks.append({
        "start_idx": curr_start,
        "end_idx": len(oos) - 1,
        "count": curr_count,
        "total_loss": curr_loss,
        "start_dt": curr_trades[0]["exit_dt"],
        "end_dt": curr_trades[-1]["exit_dt"],
        "trades": curr_trades
    })

print(f"\n  共 {len(streaks)} 次連續虧損 > $500\n")

for i, s in enumerate(streaks):
    duration_h = (s["end_dt"] - s["start_dt"]).total_seconds() / 3600
    print(f"  ── 第 {i+1} 次 ──  {s['start_dt'].strftime('%Y-%m-%d %H:%M')} ~ {s['end_dt'].strftime('%Y-%m-%d %H:%M')}  "
          f"({duration_h:.0f}h)  連續 {s['count']} 筆虧損  累計 ${s['total_loss']:+,.2f}")
    for t in s["trades"]:
        print(f"    {t['sub']:>3s}  {t['entry_dt'].strftime('%m-%d %H:%M')} → {t['exit_dt'].strftime('%m-%d %H:%M')}  "
              f"${t['entry_price']:.0f}→${t['exit_price']:.0f}  {t['exit_type']:>5s}  {t['bars_held']:2d}b  ${t['pnl']:+.2f}")
    print()

# ═══════════════════════════════════════════════════════════════
# Analysis 2: Equity drawdown periods > $500
# ═══════════════════════════════════════════════════════════════
print("=" * 110)
print("分析二：Equity 回撤段（peak-to-trough drawdown > $500）")
print("=" * 110)

eq = oos["pnl"].cumsum().values
peak = eq[0]
peak_idx = 0
dd_events = []
in_dd = False
dd_start = 0

for i in range(len(eq)):
    if eq[i] > peak:
        if in_dd and (peak - trough) > 500:
            dd_events.append({
                "peak_idx": dd_start,
                "trough_idx": trough_idx,
                "recovery_idx": i,
                "peak_val": peak_at_start,
                "trough_val": trough,
                "drawdown": peak_at_start - trough,
                "peak_dt": oos.iloc[dd_start]["exit_dt"],
                "trough_dt": oos.iloc[trough_idx]["exit_dt"],
                "recovery_dt": oos.iloc[i]["exit_dt"],
                "n_trades": trough_idx - dd_start + 1
            })
        peak = eq[i]
        peak_idx = i
        in_dd = False
    elif eq[i] < peak:
        if not in_dd:
            in_dd = True
            dd_start = peak_idx
            peak_at_start = peak
            trough = eq[i]
            trough_idx = i
        if eq[i] < trough:
            trough = eq[i]
            trough_idx = i

# Check if still in drawdown at end
if in_dd and (peak_at_start - trough) > 500:
    dd_events.append({
        "peak_idx": dd_start,
        "trough_idx": trough_idx,
        "recovery_idx": None,
        "peak_val": peak_at_start,
        "trough_val": trough,
        "drawdown": peak_at_start - trough,
        "peak_dt": oos.iloc[dd_start]["exit_dt"],
        "trough_dt": oos.iloc[trough_idx]["exit_dt"],
        "recovery_dt": None,
        "n_trades": trough_idx - dd_start + 1
    })

print(f"\n  共 {len(dd_events)} 次回撤 > $500\n")

print(f"  {'#':>3s}  {'Peak Date':>16s}  {'Trough Date':>16s}  {'Recovery':>16s}  "
      f"{'Peak$':>8s}  {'Trough$':>8s}  {'DD$':>8s}  {'DD%':>6s}  {'Trades':>6s}  {'Duration':>10s}")
print(f"  {'-'*110}")

for i, d in enumerate(dd_events):
    dd_pct = d["drawdown"] / (ACCOUNT + d["peak_val"]) * 100
    if d["recovery_dt"] is not None:
        dur = d["recovery_dt"] - d["peak_dt"]
        dur_str = f"{dur.days}d"
        rec_str = d["recovery_dt"].strftime("%Y-%m-%d %H:%M")
    else:
        dur = oos.iloc[-1]["exit_dt"] - d["peak_dt"]
        dur_str = f"{dur.days}d (open)"
        rec_str = "未恢復"
    print(f"  {i+1:3d}  {d['peak_dt'].strftime('%Y-%m-%d %H:%M'):>16s}  "
          f"{d['trough_dt'].strftime('%Y-%m-%d %H:%M'):>16s}  {rec_str:>16s}  "
          f"{d['peak_val']:+8.0f}  {d['trough_val']:+8.0f}  {d['drawdown']:8.0f}  "
          f"{dd_pct:5.1f}%  {d['n_trades']:6d}  {dur_str:>10s}")

# ═══════════════════════════════════════════════════════════════
# Analysis 3: Worst single-day losses
# ═══════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 110}")
print("分析三：單日 PnL 最差 Top 10")
print("=" * 110)

oos_copy = oos.copy()
oos_copy["date"] = oos_copy["exit_dt"].dt.date
daily = oos_copy.groupby("date").agg(
    n_trades=("pnl", "count"),
    pnl=("pnl", "sum"),
    n_loss=("pnl", lambda x: (x <= 0).sum()),
    worst_trade=("pnl", "min"),
    subs=("sub", lambda x: ", ".join(sorted(x.unique())))
).sort_values("pnl")

print(f"\n  {'#':>3s}  {'Date':>12s}  {'Trades':>6s}  {'Losses':>6s}  {'Day PnL':>9s}  {'Worst Trade':>11s}  {'Strategies'}")
print(f"  {'-'*85}")
for i, (dt, r) in enumerate(daily.head(10).iterrows()):
    print(f"  {i+1:3d}  {str(dt):>12s}  {r['n_trades']:6d}  {r['n_loss']:6d}  ${r['pnl']:+8.2f}  ${r['worst_trade']:+10.2f}  {r['subs']}")

# ═══════════════════════════════════════════════════════════════
# Analysis 4: Summary statistics
# ═══════════════════════════════════════════════════════════════
print(f"\n\n{'=' * 110}")
print("分析四：綜合統計")
print("=" * 110)

# Consecutive losing trades stats
all_streaks = []
curr_start = None; curr_loss = 0; curr_count = 0
for idx, r in oos.iterrows():
    if r["pnl"] <= 0:
        if curr_start is None: curr_start = idx
        curr_loss += r["pnl"]; curr_count += 1
    else:
        if curr_start is not None:
            all_streaks.append({"count": curr_count, "loss": curr_loss})
        curr_start = None; curr_loss = 0; curr_count = 0
if curr_start is not None:
    all_streaks.append({"count": curr_count, "loss": curr_loss})

streak_df = pd.DataFrame(all_streaks)

eq_series = oos["pnl"].cumsum()
dd_series = eq_series - eq_series.cummax()

print(f"""
  L+S OOS 合計: {len(oos)} 筆交易, PnL ${oos['pnl'].sum():+,.2f}

  連續虧損段統計:
    總連續虧損段數:           {len(streak_df)}
    連續虧損 > $500 次數:     {len(streaks)}
    連續虧損 > $1000 次數:    {len([s for s in streaks if s['total_loss'] < -1000])}
    最長連續虧損筆數:         {streak_df['count'].max()} 筆
    最大連續虧損金額:         ${streak_df['loss'].min():+,.2f}
    平均連續虧損筆數:         {streak_df['count'].mean():.1f} 筆
    平均連續虧損金額:         ${streak_df['loss'].mean():+,.2f}

  Equity 回撤統計:
    回撤 > $500 次數:         {len(dd_events)}
    最大回撤 (MDD):           ${abs(dd_series.min()):,.2f} ({abs(dd_series.min())/ACCOUNT*100:.1f}% of $10K)
    平均回撤深度 (>$500):     ${np.mean([d['drawdown'] for d in dd_events]):,.2f} (if any)

  單日統計:
    最差單日:                 ${daily['pnl'].min():+,.2f}
    最好單日:                 ${daily['pnl'].max():+,.2f}
    虧損日數/總交易日數:      {(daily['pnl']<0).sum()}/{len(daily)} ({(daily['pnl']<0).mean()*100:.1f}%)
""")
