"""
V22 R1: Ichimoku Cloud (一目均衡表)

指標:
  Tenkan = (9-bar high + 9-bar low) / 2
  Kijun  = (26-bar high + 26-bar low) / 2
  Senkou A = (Tenkan + Kijun) / 2     [plotted 26 bars ahead]
  Senkou B = (52-bar high + 52-bar low) / 2  [plotted 26 bars ahead]
  Chikou = close[i] compared against close[i-26]

防上帝視角:
  所有指標在 bar i 做決策時用 shift(1)（只用 i-1 以前的資料）
  Senkou 雲層是 26 bars ago 計算的 Tenkan/Kijun → 天然無 look-ahead
  進場 = O[i+1]

測試多種信號組合 × 多種出場：
  Signal S1: TK cross only (Tenkan crosses Kijun)
  Signal S2: Cloud breakout (close crosses above/below cloud top/bottom)
  Signal S3: Full 3-confirm (TK align + price vs cloud + cloud color)
  Signal S4: Full 4-confirm (S3 + Chikou align)

出場: SL 3.5% / TP {2, 3, 3.5, 4%} / MaxHold {6, 12, 24, 48}
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df = df.sort_values("datetime").reset_index(drop=True)

h = df["high"].values.astype(float)
l = df["low"].values.astype(float)
c = df["close"].values.astype(float)
o = df["open"].values.astype(float)
dt = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2
print(f"Bars={TOTAL}  IS[0:{IS_END}]  OOS[{IS_END}:{TOTAL}]", flush=True)

FEE = 4.0
NOTIONAL = 4000.0

# --- Ichimoku indicators ---
def rolling_max(arr, w):
    return pd.Series(arr).rolling(w).max().values

def rolling_min(arr, w):
    return pd.Series(arr).rolling(w).min().values

tenkan = (rolling_max(h, 9) + rolling_min(l, 9)) / 2
kijun  = (rolling_max(h, 26) + rolling_min(l, 26)) / 2
senkou_a_raw = (tenkan + kijun) / 2          # 當下計算值
senkou_b_raw = (rolling_max(h, 52) + rolling_min(l, 52)) / 2

# Senkou 雲層在 bar i = 26 bar 前計算的（無前瞻）
senkou_a = np.full(TOTAL, np.nan)
senkou_b = np.full(TOTAL, np.nan)
senkou_a[26:] = senkou_a_raw[:-26]
senkou_b[26:] = senkou_b_raw[:-26]

cloud_top = np.maximum(senkou_a, senkou_b)
cloud_bot = np.minimum(senkou_a, senkou_b)
cloud_bull = senkou_a > senkou_b  # 綠雲

# 上一根的值（避免用當前 bar）
def shift1(arr):
    out = np.full_like(arr, np.nan, dtype=float)
    out[1:] = arr[:-1]
    return out

tenkan_s1 = shift1(tenkan)
kijun_s1 = shift1(kijun)
cloud_top_s1 = shift1(cloud_top)
cloud_bot_s1 = shift1(cloud_bot)
cloud_bull_s1 = shift1(cloud_bull.astype(float)) > 0.5
c_s1 = shift1(c)
tenkan_s2 = shift1(tenkan_s1)
kijun_s2 = shift1(kijun_s1)
c_s2 = shift1(c_s1)
cloud_top_s2 = shift1(cloud_top_s1)
cloud_bot_s2 = shift1(cloud_bot_s1)

# TK cross (bullish): Tenkan_s1 > Kijun_s1 AND Tenkan_s2 <= Kijun_s2
tk_cross_up = (tenkan_s1 > kijun_s1) & (tenkan_s2 <= kijun_s2)
tk_cross_dn = (tenkan_s1 < kijun_s1) & (tenkan_s2 >= kijun_s2)

# Cloud breakout
cloud_brk_up = (c_s1 > cloud_top_s1) & (c_s2 <= cloud_top_s2)
cloud_brk_dn = (c_s1 < cloud_bot_s1) & (c_s2 >= cloud_bot_s2)

# Price above/below cloud
above_cloud_s1 = c_s1 > cloud_top_s1
below_cloud_s1 = c_s1 < cloud_bot_s1

# Chikou (momentum 26): close[i-1] > close[i-27]
chikou_bull = np.full(TOTAL, False)
chikou_bear = np.full(TOTAL, False)
for i in range(27, TOTAL):
    chikou_bull[i] = c[i-1] > c[i-27]
    chikou_bear[i] = c[i-1] < c[i-27]

# --- Signal definitions ---
def build_signals():
    sigs = {}
    # S1: TK cross only
    sigs["S1_TKcross_L"] = tk_cross_up.copy()
    sigs["S1_TKcross_S"] = tk_cross_dn.copy()
    # S2: Cloud breakout
    sigs["S2_CloudBrk_L"] = cloud_brk_up.copy()
    sigs["S2_CloudBrk_S"] = cloud_brk_dn.copy()
    # S3: TK align + above/below cloud + cloud color
    s3_L = (tenkan_s1 > kijun_s1) & above_cloud_s1 & cloud_bull_s1
    s3_S = (tenkan_s1 < kijun_s1) & below_cloud_s1 & (~cloud_bull_s1)
    # 取首次觸發（前一根不成立）
    s3_L_prev = np.roll(s3_L, 1); s3_L_prev[0] = False
    s3_S_prev = np.roll(s3_S, 1); s3_S_prev[0] = False
    sigs["S3_3Confirm_L"] = s3_L & ~s3_L_prev
    sigs["S3_3Confirm_S"] = s3_S & ~s3_S_prev
    # S4: S3 + Chikou
    s4_L = s3_L & chikou_bull
    s4_S = s3_S & chikou_bear
    s4_L_prev = np.roll(s4_L, 1); s4_L_prev[0] = False
    s4_S_prev = np.roll(s4_S, 1); s4_S_prev[0] = False
    sigs["S4_4Confirm_L"] = s4_L & ~s4_L_prev
    sigs["S4_4Confirm_S"] = s4_S & ~s4_S_prev
    return sigs

sigs = build_signals()
for k, v in sigs.items():
    print(f"  {k}: {int(v.sum())} raw signals", flush=True)

# --- Simulator ---
def simulate(signal_arr, side, start, end, tp_pct, sl_pct, maxhold, cooldown=6):
    trades = []
    in_pos = False
    last_exit = -999
    entry_price = 0.0
    entry_bar = -1
    for i in range(max(52, start), min(end, TOTAL - 1)):
        if in_pos:
            held = i - entry_bar
            ep = entry_price
            if side == "L":
                sl_level = ep * (1 - sl_pct)
                tp_level = ep * (1 + tp_pct)
                if l[i] <= sl_level:
                    ex_p = sl_level - (sl_level - l[i]) * 0.25
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ex_p-ep)/ep - FEE, "reason": "SL",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif h[i] >= tp_level:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(tp_level-ep)/ep - FEE, "reason": "TP",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif held >= maxhold:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(c[i]-ep)/ep - FEE, "reason": "MH",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
            else:
                sl_level = ep * (1 + sl_pct)
                tp_level = ep * (1 - tp_pct)
                if h[i] >= sl_level:
                    ex_p = sl_level + (h[i] - sl_level) * 0.25
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-ex_p)/ep - FEE, "reason": "SL",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif l[i] <= tp_level:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-tp_level)/ep - FEE, "reason": "TP",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif held >= maxhold:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-c[i])/ep - FEE, "reason": "MH",
                                   "entry_month": pd.Timestamp(dt[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
        if in_pos: continue
        if i - last_exit < cooldown: continue
        if signal_arr[i] and i + 1 < TOTAL:
            entry_price = o[i+1]; entry_bar = i; in_pos = True
    return trades

def stats(trades):
    if not trades: return dict(n=0, pnl=0, wr=0, pf=0, mdd=0, worst=0, pos=0, total=0)
    pnls = np.array([t["pnl"] for t in trades])
    total = pnls.sum()
    wr = (pnls>0).mean()*100
    gw = pnls[pnls>0].sum(); gl = abs(pnls[pnls<0].sum())
    pf = gw/gl if gl>0 else np.inf
    eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
    mdd = float((peak-eq).max()) if len(eq) else 0
    monthly = {}
    for t in trades:
        monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
    worst = min(monthly.values()) if monthly else 0
    pos = sum(1 for v in monthly.values() if v>0)
    return dict(n=len(pnls), pnl=float(total), wr=float(wr), pf=float(pf),
                mdd=mdd, worst=float(worst), pos=pos, total=len(monthly))

# --- Grid search IS ---
print("\n" + "="*110)
print("V22 R1 Ichimoku — IS grid (TP × MH, SL=3.5%, CD=6)")
print("="*110)

TP_GRID = [0.02, 0.025, 0.03, 0.035]
MH_GRID = [6, 12, 24, 48]

best = []
for sig_name, sig_arr in sigs.items():
    side = "L" if sig_name.endswith("_L") else "S"
    for tp in TP_GRID:
        for mh in MH_GRID:
            t_is = simulate(sig_arr, side, 0, IS_END, tp, 0.035, mh)
            s = stats(t_is)
            if s["n"] >= 10 and s["pnl"] > 0:
                best.append({"sig": sig_name, "side": side, "tp": tp, "mh": mh,
                             "is_n": s["n"], "is_pnl": s["pnl"], "is_wr": s["wr"],
                             "is_mdd": s["mdd"], "is_pf": s["pf"]})

print(f"\n{'Signal':<22} {'Side':<4} {'TP':>5} {'MH':>4} {'Nis':>4} {'PnL':>7} {'WR':>5} {'PF':>5} {'MDD':>6}")
print("-"*100)
for r in sorted(best, key=lambda x: -x["is_pnl"])[:30]:
    print(f"{r['sig']:<22} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} {r['is_n']:>4} ${r['is_pnl']:>6.0f} {r['is_wr']:>4.1f}% {r['is_pf']:>5.2f} ${r['is_mdd']:>5.0f}")

if not best:
    print("\nNo IS+ configs found. R1 REJECTED at stage 1.")
else:
    # Evaluate top IS on OOS
    print("\n" + "="*110)
    print("Top-10 IS candidates — OOS confirmation")
    print("="*110)
    print(f"{'Signal':<22} {'Side':<4} {'TP':>5} {'MH':>4} | {'IS $':>7} {'IS n':>4} | {'OOS $':>7} {'OOS n':>5} {'OOS WR':>6} {'OOS MDD':>7}")
    print("-"*110)
    for r in sorted(best, key=lambda x: -x["is_pnl"])[:10]:
        t_oos = simulate(sigs[r["sig"]], r["side"], IS_END, TOTAL, r["tp"], 0.035, r["mh"])
        s_oos = stats(t_oos)
        print(f"{r['sig']:<22} {r['side']:<4} {r['tp']:>5.3f} {r['mh']:>4} | "
              f"${r['is_pnl']:>6.0f} {r['is_n']:>4} | "
              f"${s_oos['pnl']:>6.0f} {s_oos['n']:>5} {s_oos['wr']:>5.1f}% ${s_oos['mdd']:>6.0f}")
