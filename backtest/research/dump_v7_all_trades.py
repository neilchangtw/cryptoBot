"""
V7 OOS 逐筆交易明細 — 輸出所有 L 和 S 交易的進出場價格、損益等資料
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

# ─── Backtest engines with trade detail capture ───
def bt_long_trail(df, mask, max_same=9, exit_cd=8, cap=15, tag="L"):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=mask.values; SOK=df["sok"].values; YM=df["ym"].values
    n=len(df); pos=[]; trades=[]; lx=-9999; boc={}; ment={}
    tid = 0
    for i in range(WARMUP, n-1):
        lo=Lo[i]; c=C[i]; ema=EMA[i]; nxo=O[i+1]; dt=DT[i]; ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                    "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(ep,2),
                    "exit_type":"SN","bars_held":bh,"pnl":round(pnl,2),
                    "pnl_pct":round(pnl/ACCOUNT*100,3)})
                lx=i; done=True
            if not done and 7<=bh<12:
                if c<=ema or c<=p["e"]*(1-0.01):
                    t_="ES" if c<=p["e"]*(1-0.01) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                        "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(c,2),
                        "exit_type":t_,"bars_held":bh,"pnl":round(pnl,2),
                        "pnl_pct":round(pnl/ACCOUNT*100,3)})
                    lx=i; done=True
            if not done and bh>=12 and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                    "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(c,2),
                    "exit_type":"Trail","bars_held":bh,"pnl":round(pnl,2),
                    "pnl_pct":round(pnl/ACCOUNT*100,3)})
                lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        ce=ment.get(ym,0)
        if ce>=cap: continue
        boc[i]=boc.get(i,0)+1; ment[ym]=ce+1
        tid+=1
        pos.append({"e":nxo,"ei":i,"tid":tid,"edt":dt})
    return pd.DataFrame(trades)

def bt_short(df, ind_mask, bl_col, tp_pct=0.015, max_hold=19,
             max_same=5, exit_cd=6, tag="S", tid_start=0):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    IND=ind_mask.values; BL=df[bl_col].fillna(False).values
    SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}
    tid = tid_start
    for i in range(WARMUP, n-1):
        h=H[i]; lo_=Lo[i]; c=C[i]; nxo=O[i+1]; dt=DT[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1+SAFENET_PCT)
            if h>=sn:
                ep=sn+(h-sn)*SN_PEN
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                    "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(ep,2),
                    "exit_type":"SN","bars_held":bh,"pnl":round(pnl,2),
                    "pnl_pct":round(pnl/ACCOUNT*100,3)})
                lx=i; done=True
            if not done:
                tp=p["e"]*(1-tp_pct)
                if lo_<=tp:
                    pnl=(p["e"]-tp)*NOTIONAL/p["e"]-FEE
                    trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                        "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(tp,2),
                        "exit_type":"TP","bars_held":bh,"pnl":round(pnl,2),
                        "pnl_pct":round(pnl/ACCOUNT*100,3)})
                    lx=i; done=True
            if not done and bh>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"id":p["tid"],"sub":tag,"entry_dt":p["edt"],
                    "exit_dt":dt,"entry_price":round(p["e"],2),"exit_price":round(c,2),
                    "exit_type":"MH","bars_held":bh,"pnl":round(pnl,2),
                    "pnl_pct":round(pnl/ACCOUNT*100,3)})
                lx=i; done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(IND[i]) or not _b(BL[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        tid+=1
        pos.append({"e":nxo,"ei":i,"tid":tid,"edt":dt})
    return pd.DataFrame(trades), tid

# ─── Run backtests ───
print("Running v7 canonical backtests...")
l_trades = bt_long_trail(df, ve60_mask)

s_subs = [("S1",0.02,10,19),("S2",0.015,10,19),("S3",0.02,12,19),("S4",0.015,10,12)]
s_all = []
tid_counter = 1000
for sn, tp, bl, mh in s_subs:
    st, tid_counter = bt_short(df, dd_mild, f"bl_dn_{bl}", tp_pct=tp,
                                max_hold=mh, tag=sn, tid_start=tid_counter)
    s_all.append(st)
s_trades = pd.concat(s_all, ignore_index=True)

# Filter OOS only
l_trades["exit_dt"] = pd.to_datetime(l_trades["exit_dt"])
s_trades["exit_dt"] = pd.to_datetime(s_trades["exit_dt"])
l_oos = l_trades[l_trades["exit_dt"] >= mid].reset_index(drop=True)
s_oos = s_trades[s_trades["exit_dt"] >= mid].reset_index(drop=True)

# ─── Print L trades ───
print("\n" + "=" * 120)
print(f"{'L 策略 OOS 逐筆交易明細':^120s}")
print(f"{'VE<60 + BL10 + EMA20 Trail | max_same=9, exit_cd=8, cap=15':^120s}")
print("=" * 120)
print(f"  {'#':>3s}  {'進場時間':>19s}  {'出場時間':>19s}  {'進場價':>10s}  {'出場價':>10s}  "
      f"{'出場類型':>6s}  {'持倉bars':>6s}  {'PnL($)':>8s}  {'PnL%':>7s}  {'累計PnL':>9s}")
print(f"  {'-'*112}")

cum = 0
wins = 0
for idx, r in l_oos.iterrows():
    cum += r["pnl"]
    wl = "W" if r["pnl"] > 0 else "L"
    if r["pnl"] > 0: wins += 1
    edt = pd.to_datetime(r["entry_dt"]).strftime("%Y-%m-%d %H:%M")
    xdt = pd.to_datetime(r["exit_dt"]).strftime("%Y-%m-%d %H:%M")
    print(f"  {idx+1:3d}  {edt:>19s}  {xdt:>19s}  {r['entry_price']:10.2f}  {r['exit_price']:10.2f}  "
          f"{r['exit_type']:>6s}  {r['bars_held']:6d}  {r['pnl']:+8.2f}  {r['pnl_pct']:+7.3f}  {cum:+9.2f}")

print(f"  {'-'*112}")
wr_l = wins/len(l_oos)*100 if len(l_oos)>0 else 0
w_sum = l_oos[l_oos["pnl"]>0]["pnl"].sum()
l_sum = abs(l_oos[l_oos["pnl"]<=0]["pnl"].sum())
pf_l = w_sum/l_sum if l_sum>0 else 999
print(f"  L 合計: {len(l_oos)} 筆 | PnL ${cum:+,.2f} | WR {wr_l:.1f}% | PF {pf_l:.2f} | "
      f"Avg ${cum/len(l_oos):+.2f}/trade" if len(l_oos)>0 else "  No L trades")

# Exit type breakdown
if len(l_oos) > 0:
    print(f"\n  出場分佈:")
    for et in ["Trail","ES","SN"]:
        sub = l_oos[l_oos["exit_type"]==et]
        if len(sub) > 0:
            print(f"    {et:>6s}: {len(sub):3d} 筆 ({len(sub)/len(l_oos)*100:5.1f}%)  "
                  f"avg PnL ${sub['pnl'].mean():+.2f}  total ${sub['pnl'].sum():+,.2f}")

# ─── Print S trades ───
print("\n\n" + "=" * 120)
print(f"{'S 策略 OOS 逐筆交易明細':^120s}")
print(f"{'DD Regime + CMP 4sub (S1/S2/S3/S4)':^120s}")
print("=" * 120)
print(f"  {'#':>3s}  {'Sub':>3s}  {'進場時間':>19s}  {'出場時間':>19s}  {'進場價':>10s}  {'出場價':>10s}  "
      f"{'出場':>4s}  {'bars':>4s}  {'PnL($)':>8s}  {'PnL%':>7s}  {'累計PnL':>9s}")
print(f"  {'-'*112}")

# Sort by exit datetime
s_oos_sorted = s_oos.sort_values("exit_dt").reset_index(drop=True)
cum_s = 0
wins_s = 0
for idx, r in s_oos_sorted.iterrows():
    cum_s += r["pnl"]
    if r["pnl"] > 0: wins_s += 1
    edt = pd.to_datetime(r["entry_dt"]).strftime("%Y-%m-%d %H:%M")
    xdt = pd.to_datetime(r["exit_dt"]).strftime("%Y-%m-%d %H:%M")
    print(f"  {idx+1:3d}  {r['sub']:>3s}  {edt:>19s}  {xdt:>19s}  {r['entry_price']:10.2f}  {r['exit_price']:10.2f}  "
          f"{r['exit_type']:>4s}  {r['bars_held']:4d}  {r['pnl']:+8.2f}  {r['pnl_pct']:+7.3f}  {cum_s:+9.2f}")

print(f"  {'-'*112}")
wr_s = wins_s/len(s_oos_sorted)*100 if len(s_oos_sorted)>0 else 0
w_sum_s = s_oos_sorted[s_oos_sorted["pnl"]>0]["pnl"].sum()
l_sum_s = abs(s_oos_sorted[s_oos_sorted["pnl"]<=0]["pnl"].sum())
pf_s = w_sum_s/l_sum_s if l_sum_s>0 else 999
print(f"  S 合計: {len(s_oos_sorted)} 筆 | PnL ${cum_s:+,.2f} | WR {wr_s:.1f}% | PF {pf_s:.2f} | "
      f"Avg ${cum_s/len(s_oos_sorted):+.2f}/trade" if len(s_oos_sorted)>0 else "  No S trades")

# Exit type breakdown for S
if len(s_oos_sorted) > 0:
    print(f"\n  出場分佈:")
    for et in ["TP","MH","SN"]:
        sub = s_oos_sorted[s_oos_sorted["exit_type"]==et]
        if len(sub) > 0:
            print(f"    {et:>4s}: {len(sub):3d} 筆 ({len(sub)/len(s_oos_sorted)*100:5.1f}%)  "
                  f"avg PnL ${sub['pnl'].mean():+.2f}  total ${sub['pnl'].sum():+,.2f}")

# Sub-strategy breakdown
print(f"\n  子策略明細:")
for sn in ["S1","S2","S3","S4"]:
    sub = s_oos_sorted[s_oos_sorted["sub"]==sn]
    if len(sub) > 0:
        sw = sub[sub["pnl"]>0]["pnl"].sum()
        sl = abs(sub[sub["pnl"]<=0]["pnl"].sum())
        spf = sw/sl if sl>0 else 999
        print(f"    {sn}: {len(sub):3d} 筆 | PnL ${sub['pnl'].sum():+,.2f} | "
              f"WR {(sub['pnl']>0).mean()*100:.1f}% | PF {spf:.2f}")

# ─── Combined summary ───
print("\n\n" + "=" * 120)
print(f"{'L+S 合併月度彙總':^120s}")
print("=" * 120)

all_oos = pd.concat([l_oos.assign(side="L"), s_oos_sorted.assign(side="S")], ignore_index=True)
all_oos["month"] = pd.to_datetime(all_oos["exit_dt"]).dt.to_period("M")
l_monthly = l_oos.copy()
l_monthly["month"] = pd.to_datetime(l_monthly["exit_dt"]).dt.to_period("M")
s_monthly = s_oos_sorted.copy()
s_monthly["month"] = pd.to_datetime(s_monthly["exit_dt"]).dt.to_period("M")

lm = l_monthly.groupby("month")["pnl"].agg(["sum","count"]).rename(columns={"sum":"L_PnL","count":"L_n"})
sm = s_monthly.groupby("month")["pnl"].agg(["sum","count"]).rename(columns={"sum":"S_PnL","count":"S_n"})
combined = lm.join(sm, how="outer").fillna(0)
combined["Total"] = combined["L_PnL"] + combined["S_PnL"]
combined["Cum"] = combined["Total"].cumsum()

print(f"  {'Month':>10s}  {'L_trades':>7s}  {'L_PnL':>9s}  {'S_trades':>7s}  {'S_PnL':>9s}  {'Total':>9s}  {'Cumulative':>10s}")
print(f"  {'-'*70}")
for m, r in combined.iterrows():
    status = "+" if r["Total"] > 0 else "-"
    print(f"  {str(m):>10s}  {int(r['L_n']):7d}  {r['L_PnL']:+9.0f}  {int(r['S_n']):7d}  {r['S_PnL']:+9.0f}  "
          f"{r['Total']:+9.0f}  {r['Cum']:+10.0f}  {status}")
print(f"  {'-'*70}")
print(f"  {'TOTAL':>10s}  {int(combined['L_n'].sum()):7d}  {combined['L_PnL'].sum():+9.0f}  "
      f"{int(combined['S_n'].sum()):7d}  {combined['S_PnL'].sum():+9.0f}  "
      f"{combined['Total'].sum():+9.0f}")

print(f"\n  Equity: $10,000 + ${combined['Total'].sum():,.0f} = ${10000+combined['Total'].sum():,.0f}")
print(f"  正月: {(combined['Total']>0).sum()}/{len(combined)}")
