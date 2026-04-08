"""
ETH Compression Range Breakout (CRB) Strategy
==============================================
Donchian 通道壓縮 → 價格突破壓縮區間 → 結構止損在區間邊界

進場（3 條件）：
  1. Donchian Width(24h) 百分位 < 25 → Close 突破壓縮區間
  2. ETH/BTC ratio Z-score > 1.0 做多 / < -1.0 做空
  3. Session: Block hours {0,1,2,12} UTC+8 + Block days {Mon,Sat,Sun}

止損：壓縮區間對面邊界 + 0.1% buffer
後備：SafeNet ±3.5%
出場：EMA20 trailing（min hold 12h）
倉位：$10,000，2% risk/trade，動態倉位
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ============================================================
# PARAMETERS
# ============================================================
ACCOUNT     = 10_000
RISK_PCT    = 0.02
FEE_RATE    = 0.0004    # taker per side
SLIPPAGE    = 0.0001    # per side
SAFENET_PCT = 0.035
MAX_LEV     = 5

DC_WINDOW   = 24
PCTILE_LB   = 120
COMP_THRESH = 25
MIN_COMP    = 4
MAX_STALE   = 8
MAX_WIDTH   = 0.030
MIN_STOP    = 0.005

RZ_THRESH   = 1.0
RZ_WINDOW   = 50

BLOCK_HOURS = {0, 1, 2, 12}
BLOCK_DAYS  = {0, 5, 6}

EMA_SPAN    = 20
MIN_HOLD    = 12

# ============================================================
# DATA
# ============================================================
def fetch(symbol, interval, start_date, end_date):
    all_d = []
    cur = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
    end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
    while cur < end_ts:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "endTime": end_ts, "limit": 1000}, timeout=10)
            d = r.json()
            if not d or isinstance(d, dict): break
            all_d.extend(d); cur = d[-1][0] + 1; _time.sleep(0.1)
        except: break
    if not all_d: return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume",
                                       "ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    return df

# ============================================================
# INDICATORS
# ============================================================
def add_indicators(df, btc_df):
    df["dc_high"] = df["high"].rolling(DC_WINDOW).max()
    df["dc_low"]  = df["low"].rolling(DC_WINDOW).min()
    df["dc_width"] = (df["dc_high"] - df["dc_low"]) / df["close"] * 100

    roll_min = df["dc_width"].rolling(PCTILE_LB).min()
    roll_max = df["dc_width"].rolling(PCTILE_LB).max()
    denom = roll_max - roll_min
    df["dc_pctile"] = np.where(denom > 0, (df["dc_width"] - roll_min) / denom * 100, 50)

    btc_map = btc_df.set_index("ot")["close"].to_dict()
    df["btc_close"] = df["ot"].map(btc_map).ffill()
    df["ratio"] = df["close"] / df["btc_close"]
    r_mean = df["ratio"].rolling(RZ_WINDOW).mean()
    r_std  = df["ratio"].rolling(RZ_WINDOW).std()
    df["ratio_z"] = (df["ratio"] - r_mean) / r_std

    df["ema20"] = df["close"].ewm(span=EMA_SPAN).mean()
    df["hour"]    = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday
    df["month"]   = df["datetime"].dt.to_period("M")
    return df.dropna().reset_index(drop=True)

# ============================================================
# BACKTEST ENGINE
# ============================================================
def run(data, cfg=None):
    if cfg is None: cfg = {}
    comp_thresh = cfg.get("comp_thresh", COMP_THRESH)
    min_comp    = cfg.get("min_comp", MIN_COMP)
    max_stale   = cfg.get("max_stale", MAX_STALE)
    max_width   = cfg.get("max_width", MAX_WIDTH)
    min_stop    = cfg.get("min_stop", MIN_STOP)
    rz_thresh   = cfg.get("rz_thresh", RZ_THRESH)
    min_hold    = cfg.get("min_hold", MIN_HOLD)
    safenet_pct = cfg.get("safenet_pct", SAFENET_PCT)
    max_lev     = cfg.get("max_lev", MAX_LEV)
    use_rz      = cfg.get("use_rz", True)
    no_sess     = cfg.get("no_session", False)

    cs_start = -1; cs_high = 0.0; cs_low = 1e9; cs_last = -999; cs_used = False
    pos = None; trades = []; equity = ACCOUNT

    for i in range(1, len(data) - 1):
        row = data.iloc[i]; nxt = data.iloc[i + 1]
        hi = row["high"]; lo = row["low"]; close = row["close"]

        # ── EXIT ──
        if pos is not None:
            bars  = i - pos["ei"]
            side  = pos["side"]; entry = pos["entry"]; size = pos["size"]
            if side == "long":
                pos["mf"] = max(pos.get("mf",0), (hi - entry) * size)
            else:
                pos["mf"] = max(pos.get("mf",0), (entry - lo) * size)

            exited = False; exit_price = None; exit_type = None

            # 1) Structure Stop
            sl = pos["struct_sl"]
            if side == "long" and lo <= sl:
                exit_price = sl - (sl - lo) * 0.25 if lo < sl else sl
                exit_type = "StructStop"; exited = True
            elif side == "short" and hi >= sl:
                exit_price = sl + (hi - sl) * 0.25 if hi > sl else sl
                exit_type = "StructStop"; exited = True

            # 2) SafeNet
            if not exited:
                if side == "long":
                    sn = entry * (1 - safenet_pct)
                    if lo <= sn:
                        exit_price = sn - (sn - lo) * 0.25 if lo < sn else sn
                        exit_type = "SafeNet"; exited = True
                else:
                    sn = entry * (1 + safenet_pct)
                    if hi >= sn:
                        exit_price = sn + (hi - sn) * 0.25 if hi > sn else sn
                        exit_type = "SafeNet"; exited = True

            # 3) EMA Trail after min_hold
            if not exited and bars >= min_hold:
                ema_val = row["ema20"]
                if side == "long" and close < ema_val:
                    exit_price = close * (1 - SLIPPAGE); exit_type = "Trail"; exited = True
                elif side == "short" and close > ema_val:
                    exit_price = close * (1 + SLIPPAGE); exit_type = "Trail"; exited = True

            if exited:
                gross = (exit_price - entry) * size if side == "long" else (entry - exit_price) * size
                fee = (entry * size + exit_price * size) * FEE_RATE
                if exit_type == "Trail":
                    fee += exit_price * size * SLIPPAGE
                net = gross - fee; equity += net
                trades.append({
                    "entry_dt": pos["entry_dt"], "exit_dt": str(row["datetime"]),
                    "side": side, "entry": round(entry,2), "exit": round(exit_price,2),
                    "size": round(size,4), "notional": round(entry*size,0),
                    "pnl": round(net,2), "gross": round(gross,2), "fee": round(fee,2),
                    "type": exit_type, "bars": bars, "mf": round(pos.get("mf",0),2),
                    "equity": round(equity,2),
                    "stop_pct": pos.get("stop_pct",0), "comp_w": pos.get("comp_w",0),
                })
                pos = None

        # ── ENTRY CHECK (before compression update) ──
        comp_dur = (cs_last - cs_start + 1) if cs_start >= 0 else 0
        stale    = (i - cs_last) if cs_last >= 0 else 999
        has_range = (cs_start >= 0 and comp_dur >= min_comp
                     and stale <= max_stale and not cs_used and pos is None)

        if has_range:
            cw = (cs_high - cs_low) / close
            lb = close > cs_high; sb = close < cs_low

            if (lb or sb) and min_stop <= cw <= max_width:
                side = "long" if lb else "short"
                rz = row["ratio_z"]
                if not pd.isna(rz):
                    dir_ok = (not use_rz) or \
                             (side == "long" and rz > rz_thresh) or \
                             (side == "short" and rz < -rz_thresh)
                    h = row["hour"]; wd = row["weekday"]
                    sess_ok = no_sess or (h not in BLOCK_HOURS and wd not in BLOCK_DAYS)

                    if dir_ok and sess_ok:
                        ep = nxt["open"]
                        if side == "long":
                            ep *= (1 + SLIPPAGE); ssl = cs_low * 0.999
                        else:
                            ep *= (1 - SLIPPAGE); ssl = cs_high * 1.001
                        valid = (side == "long" and ep > ssl) or (side == "short" and ep < ssl)
                        if valid:
                            sd = abs(ep - ssl); sd_pct = sd / ep
                            if sd_pct >= min_stop:
                                sz = (equity * RISK_PCT) / sd
                                if sz * ep > equity * max_lev:
                                    sz = equity * max_lev / ep
                                if sz * ep >= 100:
                                    pos = {"entry": ep, "struct_sl": ssl, "side": side,
                                           "size": sz, "ei": i, "mf": 0,
                                           "entry_dt": str(nxt["datetime"]),
                                           "stop_pct": round(sd_pct*100,2),
                                           "comp_w": round(cw*100,2)}
                                    cs_used = True

        # ── UPDATE COMPRESSION STATE ──
        pctile = row["dc_pctile"]
        is_comp = (not pd.isna(pctile)) and (pctile < comp_thresh)
        if is_comp:
            cur_stale = (i - cs_last) if cs_last >= 0 else 999
            if cs_start < 0 or cur_stale > max_stale:
                cs_start = i; cs_high = row["high"]; cs_low = row["low"]; cs_used = False
            else:
                cs_high = max(cs_high, row["high"]); cs_low = min(cs_low, row["low"])
            cs_last = i
        if cs_start >= 0 and (i - cs_last) > max_stale:
            cs_start = -1
        if equity <= 0: break

    # Close remaining
    if pos is not None:
        row = data.iloc[-1]; side = pos["side"]; entry = pos["entry"]; size = pos["size"]
        ep = row["close"]
        gross = (ep - entry)*size if side == "long" else (entry - ep)*size
        fee = (entry*size + ep*size) * (FEE_RATE + SLIPPAGE); net = gross - fee; equity += net
        trades.append({"entry_dt": pos["entry_dt"], "exit_dt": str(row["datetime"]),
            "side": side, "entry": round(entry,2), "exit": round(ep,2),
            "size": round(size,4), "notional": round(entry*size,0),
            "pnl": round(net,2), "gross": round(gross,2), "fee": round(fee,2),
            "type": "EOD", "bars": len(data)-1-pos["ei"], "mf": round(pos.get("mf",0),2),
            "equity": round(equity,2), "stop_pct": pos.get("stop_pct",0), "comp_w": pos.get("comp_w",0)})
    return pd.DataFrame(trades) if trades else pd.DataFrame()

# ============================================================
# STATISTICS
# ============================================================
def calc(tdf, years=2.0):
    if len(tdf) == 0:
        return {k: 0 for k in ["n","pnl","annual","wr","pf","mdd","sharpe","avg_bars",
                "fee_total","struct_n","struct_pnl","sn_n","sn_pnl","trail_n","trail_pnl",
                "lt12_n","lt12_pnl","ge12_n","ge12_pnl","avg_w","avg_l"]}
    pnl = tdf["pnl"].sum(); annual = pnl / years
    wr = (tdf["pnl"] > 0).mean() * 100
    w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 99.9
    avg_bars = tdf["bars"].mean(); fee_total = tdf["fee"].sum()

    eq = np.concatenate([[ACCOUNT], tdf["equity"].values])
    peak = np.maximum.accumulate(eq); dd = (eq - peak) / peak * 100; mdd = dd.min()

    daily = tdf.groupby(tdf["exit_dt"].str[:10])["pnl"].sum() if "exit_dt" in tdf.columns else pd.Series([pnl])
    sharpe = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0

    struct = tdf[tdf["type"]=="StructStop"]; sn = tdf[tdf["type"]=="SafeNet"]; trail = tdf[tdf["type"]=="Trail"]
    lt12 = tdf[tdf["bars"]<12]; ge12 = tdf[tdf["bars"]>=12]

    return {"n": len(tdf), "pnl": round(pnl,2), "annual": round(annual,2),
            "wr": round(wr,1), "pf": round(pf,2), "mdd": round(mdd,1),
            "sharpe": round(sharpe,2), "avg_bars": round(avg_bars,1),
            "fee_total": round(fee_total,2),
            "struct_n": len(struct), "struct_pnl": round(struct["pnl"].sum(),2),
            "sn_n": len(sn), "sn_pnl": round(sn["pnl"].sum(),2),
            "trail_n": len(trail), "trail_pnl": round(trail["pnl"].sum(),2),
            "lt12_n": len(lt12), "lt12_pnl": round(lt12["pnl"].sum(),2),
            "ge12_n": len(ge12), "ge12_pnl": round(ge12["pnl"].sum(),2),
            "avg_w": round(w["pnl"].mean(),2) if len(w)>0 else 0,
            "avg_l": round(l["pnl"].mean(),2) if len(l)>0 else 0}

def print_box(s, name, period, months=24):
    mo = s["n"]/months if months>0 else 0
    rr = abs(s["avg_w"]/s["avg_l"]) if s["avg_l"]!=0 else 0
    print(f"""
  +{'='*58}+
  | Strategy: {name}
  | Period:   {period}
  | Trades:   {s['n']} ({mo:.1f}/month)
  | Win Rate: {s['wr']:.1f}%    RR: {rr:.2f}
  | PF:       {s['pf']:.2f}
  | Annual:   ${s['annual']:+,.0f} USDT
  | Max DD:   {s['mdd']:.1f}%
  | Sharpe:   {s['sharpe']:.2f}
  | Avg Hold: {s['avg_bars']:.1f} hours
  | Avg W/L:  ${s['avg_w']:+,.0f} / ${s['avg_l']:+,.0f}
  | Fee+Slip: -${abs(s['fee_total']):,.0f} USDT
  | StructSL: {s['struct_n']}x (${s['struct_pnl']:+,.0f})
  | SafeNet:  {s['sn_n']}x (${s['sn_pnl']:+,.0f})
  | Trail:    {s['trail_n']}x (${s['trail_pnl']:+,.0f})
  | <12h: {s['lt12_n']}t ${s['lt12_pnl']:+,.0f} | >=12h: {s['ge12_n']}t ${s['ge12_pnl']:+,.0f}
  +{'='*58}+""")

# ============================================================
# MAIN
# ============================================================
print("=" * 80)
print("  ETH Compression Range Breakout (CRB) — 2-Year Backtest")
print("=" * 80)

print("\n  Fetching ETHUSDT 1h...", end=" ", flush=True)
eth = fetch("ETHUSDT", "1h", "2022-10-01", "2025-01-01")
print(f"{len(eth)} bars")
print("  Fetching BTCUSDT 1h...", end=" ", flush=True)
btc = fetch("BTCUSDT", "1h", "2022-10-01", "2025-01-01")
print(f"{len(btc)} bars")

print("  Computing indicators...", flush=True)
df = add_indicators(eth, btc)
print(f"  Ready: {len(df)} bars ({df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]})")

bt_start = pd.Timestamp("2023-01-01")
bt_end   = pd.Timestamp("2025-01-01")
bt_data  = df[(df["datetime"] >= bt_start) & (df["datetime"] <= bt_end)].reset_index(drop=True)
print(f"  Backtest: {len(bt_data)} bars ({bt_data['datetime'].iloc[0]} ~ {bt_data['datetime'].iloc[-1]})")

# ── BASE BACKTEST ──
print("\n" + "=" * 80)
print("  BASE BACKTEST")
print("=" * 80)

tdf = run(bt_data)
s = calc(tdf)
print_box(s, "CRB Base (DC24 < p25, RZ1.0, Sess)", "2023-01-01 ~ 2024-12-31")

# ── WALK-FORWARD ──
print("\n  --- Walk-Forward (Y1 IS / Y2 OOS) ---")
y1 = bt_data[bt_data["datetime"] < pd.Timestamp("2024-01-01")].reset_index(drop=True)
y2 = bt_data[bt_data["datetime"] >= pd.Timestamp("2024-01-01")].reset_index(drop=True)
t1 = run(y1); s1 = calc(t1, 1.0)
t2 = run(y2); s2 = calc(t2, 1.0)
print(f"  Y1 (IS):  {s1['n']}t  ${s1['pnl']:+,.0f}  WR {s1['wr']:.1f}%  PF {s1['pf']:.2f}  DD {s1['mdd']:.1f}%")
print(f"  Y2 (OOS): {s2['n']}t  ${s2['pnl']:+,.0f}  WR {s2['wr']:.1f}%  PF {s2['pf']:.2f}  DD {s2['mdd']:.1f}%")

# ── MONTHLY ──
if len(tdf) > 0:
    print(f"\n  --- Monthly Breakdown ---")
    print(f"  {'Month':<10s} {'N':>4s}  {'PnL':>10s}  {'WR':>6s}")
    print(f"  {'-'*36}")
    mp = []
    for m in sorted(bt_data["month"].unique()):
        ms = str(m)
        mt = tdf[tdf["exit_dt"].str[:7] == ms] if "exit_dt" in tdf.columns else pd.DataFrame()
        if len(mt) > 0:
            mpnl = mt["pnl"].sum(); mwr = (mt["pnl"]>0).mean()*100; mp.append(mpnl)
            print(f"  {ms:<10s} {len(mt):>4d}  ${mpnl:>+9,.0f}  {mwr:>5.1f}%")
        else:
            mp.append(0); print(f"  {ms:<10s}    0  $       +0")
    prof = sum(1 for x in mp if x > 0)
    print(f"\n  Profitable months: {prof}/{len(mp)}")

# ── LONG/SHORT ──
if len(tdf) > 0:
    lg = tdf[tdf["side"]=="long"]; sh = tdf[tdf["side"]=="short"]
    print(f"\n  --- Long/Short ---")
    print(f"  Long:  {len(lg)}t ${lg['pnl'].sum():+,.0f} WR {(lg['pnl']>0).mean()*100:.1f}%")
    print(f"  Short: {len(sh)}t ${sh['pnl'].sum():+,.0f} WR {(sh['pnl']>0).mean()*100:.1f}%")

# ── HOLD TIME ──
if len(tdf) > 0:
    print(f"\n  --- Hold Time ---")
    print(f"  {'Dur':<10s} {'N':>4s}  {'PnL':>10s}  {'WR':>6s}")
    print(f"  {'-'*36}")
    for lo_h,hi_h,lab in [(0,3,"<3h"),(3,6,"3-6h"),(6,12,"6-12h"),(12,24,"12-24h"),
                           (24,48,"24-48h"),(48,96,"48-96h"),(96,9999,"96h+")]:
        ht = tdf[(tdf["bars"]>=lo_h)&(tdf["bars"]<hi_h)]
        if len(ht)==0: continue
        print(f"  {lab:<10s} {len(ht):>4d}  ${ht['pnl'].sum():>+9,.0f}  {(ht['pnl']>0).mean()*100:>5.1f}%")

# ── COMPONENT ANALYSIS ──
print(f"\n  --- Component Contribution ---")
print(f"  {'Config':<35s} {'N':>4s}  {'PnL':>10s}  {'Annual':>10s}  {'WR':>6s}  {'PF':>6s}  {'MDD':>6s}")
print(f"  {'-'*80}")
for name, cfg in [
    ("Full (base)", {}),
    ("No Z-score filter", {"use_rz": False}),
    ("No session filter", {"no_session": True}),
    ("No Z + No session", {"use_rz": False, "no_session": True}),
]:
    t = run(bt_data, cfg); sc = calc(t)
    print(f"  {name:<35s} {sc['n']:>4d}  ${sc['pnl']:>+9,.0f}  ${sc['annual']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['mdd']:>5.1f}%")

# ── ASSESSMENT ──
print("\n" + "=" * 80)
print("  ASSESSMENT")
print("=" * 80)

p_pnl = s["annual"] >= 5000
p_trd = (s["n"] / 2.0) >= 120
p_mdd = abs(s["mdd"]) <= 25
p_oos = s2["pnl"] > 0

print(f"""
  Annual >= $5,000?   {'PASS' if p_pnl else 'FAIL'}  (${s['annual']:+,.0f})
  Trades >= 120/yr?   {'PASS' if p_trd else 'FAIL'}  ({s['n']} in 2yr = {s['n']/2:.0f}/yr)
  MDD <= 25%?         {'PASS' if p_mdd else 'FAIL'}  ({s['mdd']:.1f}%)
  OOS profitable?     {'PASS' if p_oos else 'FAIL'}  (${s2['pnl']:+,.0f})
""")

if p_pnl and p_trd and p_mdd and p_oos:
    print("  * ALL PASSED -- Sensitivity +/-20%")
    print(f"  {'Param':<18s} {'Val':<8s} {'N':>4s} {'PnL':>10s} {'Annual':>10s} {'WR':>6s} {'PF':>6s} {'MDD':>6s}")
    print(f"  {'-'*72}")
    for param, base in [("comp_thresh",COMP_THRESH),("min_comp",MIN_COMP),("max_stale",MAX_STALE),
                         ("rz_thresh",RZ_THRESH),("min_hold",MIN_HOLD),("safenet_pct",SAFENET_PCT)]:
        for mult in [0.8, 1.0, 1.2]:
            v = round(base * mult, 4)
            t = run(bt_data, {param: v}); sc = calc(t)
            tag = " BASE" if mult == 1.0 else ""
            print(f"  {param:<18s} {v:<8.3f} {sc['n']:>4d} ${sc['pnl']:>+9,.0f} ${sc['annual']:>+9,.0f} {sc['wr']:>5.1f}% {sc['pf']:>5.2f} {sc['mdd']:>5.1f}%{tag}")
else:
    print("  X TARGETS NOT MET -- Exploratory Sweep")
    print(f"\n  {'Config':<35s} {'N':>4s}  {'PnL':>10s}  {'Annual':>10s}  {'WR':>6s}  {'PF':>6s}  {'MDD':>6s}")
    print(f"  {'-'*80}")
    for name, cfg in [
        ("Base", {}),
        ("comp<15", {"comp_thresh": 15}), ("comp<20", {"comp_thresh": 20}),
        ("comp<30", {"comp_thresh": 30}), ("comp<35", {"comp_thresh": 35}),
        ("comp<40", {"comp_thresh": 40}),
        ("min_comp=2", {"min_comp": 2}), ("min_comp=6", {"min_comp": 6}),
        ("max_stale=4", {"max_stale": 4}), ("max_stale=12", {"max_stale": 12}),
        ("rz=0.5", {"rz_thresh": 0.5}), ("rz=0.8", {"rz_thresh": 0.8}),
        ("rz=1.2", {"rz_thresh": 1.2}), ("rz=0 (no dir)", {"rz_thresh": 0.01}),
        ("min_hold=6", {"min_hold": 6}), ("min_hold=18", {"min_hold": 18}),
        ("min_hold=24", {"min_hold": 24}),
        ("max_w=4%", {"max_width": 0.04}), ("max_w=5%", {"max_width": 0.05}),
        ("DC_WIN=12", {"comp_thresh": 25}),  # need different handling
        ("safenet=3%", {"safenet_pct": 0.03}), ("safenet=4%", {"safenet_pct": 0.04}),
        ("No session", {"no_session": True}),
        ("No Z-score", {"use_rz": False}),
        ("No Z + No sess", {"use_rz": False, "no_session": True}),
        ("Loose: c35+rz0.8", {"comp_thresh": 35, "rz_thresh": 0.8}),
        ("Loose: c35+rz0.5", {"comp_thresh": 35, "rz_thresh": 0.5}),
        ("Wide: c30+w5%", {"comp_thresh": 30, "max_width": 0.05}),
        ("Wide: c35+w5%+rz0.8", {"comp_thresh": 35, "max_width": 0.05, "rz_thresh": 0.8}),
    ]:
        t = run(bt_data, cfg); sc = calc(t)
        print(f"  {name:<35s} {sc['n']:>4d}  ${sc['pnl']:>+9,.0f}  ${sc['annual']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['mdd']:>5.1f}%")

    # Diagnosis
    print(f"\n  --- Diagnosis ---")
    if s["n"] < 20:
        print("  極少交易 → 壓縮條件或方向過濾太嚴格")
    if s["struct_n"] > s["n"] * 0.5 and s["n"] > 0:
        print(f"  結構止損佔 {s['struct_n']}/{s['n']} ({s['struct_n']/s['n']*100:.0f}%) → 區間可能太窄")
    if s["lt12_pnl"] < -500:
        print(f"  <12h 虧損 ${s['lt12_pnl']:+,.0f} → 初期止損保護不足或假突破過多")
    if s["n"] > 0 and s["wr"] < 30:
        print(f"  WR {s['wr']:.1f}% 太低 → 信號品質不足")
    if s["n"] > 0 and abs(s["avg_l"]) > s["avg_w"] * 2:
        print(f"  虧損筆均 ${s['avg_l']:+,.0f} >> 獲利筆均 ${s['avg_w']:+,.0f} → 止損太寬")

print("\n" + "=" * 80)
print("  Complete.")
print("=" * 80)
