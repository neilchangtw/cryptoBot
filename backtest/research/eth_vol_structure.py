"""
ETH Volatility Structure Strategy (VSS)
========================================
核心邏輯：波動率壓縮 → 成交量爆發確認 → 騎乘擴張 → 動量耗盡出場

進場（2 條件，無方向過濾）：
  1. BB Width 百分位 < 壓縮閾值（前一 bar）
  2. Close 突破 BB Upper/Lower（當前 bar）+ Volume > vol_mult × MA20

停損：BB 中線（結構止損）
出場：BB Width 從擴張轉收縮（寬度 < 近 N bar 最高寬度 × 收縮比）

禁用元素：
  ✗ RSI 50-cross / EMA趨勢過濾 / 多條件並聯(4+) / Trail stop
  ✗ Session filter / Volume ratio篩選 / Time stop / Pullback depth
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

# ============================================================
# Data Fetch — 2 years of data
# ============================================================
def fetch(symbol, interval, days=730):
    all_d = []
    end = int(datetime.now().timestamp() * 1000)
    cur = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    while cur < end:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                           params={"symbol": symbol, "interval": interval,
                                   "startTime": cur, "limit": 1000}, timeout=10)
            d = r.json()
            if not d: break
            all_d.extend(d)
            cur = d[-1][0] + 1
            _time.sleep(0.1)
        except:
            break
    if not all_d: return pd.DataFrame()
    df = pd.DataFrame(all_d, columns=["ot","open","high","low","close","volume",
                                       "ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume","tbv"]:
        df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    return df

print("=" * 100)
print("  ETH Volatility Structure Strategy (VSS) — 2-Year Backtest")
print("=" * 100)

print("\nFetching ETH 1h (2 years)...", end=" ", flush=True)
df = fetch("ETHUSDT", "1h", 730)
print(f"{len(df)} bars")

# ============================================================
# Indicators
# ============================================================
print("Computing indicators...", flush=True)

# Bollinger Bands (20, 2)
df["bb_mid"] = df["close"].rolling(20).mean()
bb_std = df["close"].rolling(20).std()
df["bb_upper"] = df["bb_mid"] + 2 * bb_std
df["bb_lower"] = df["bb_mid"] - 2 * bb_std
df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"] * 100

# BB Width Percentile (rolling 100 bars)
df["bbw_pctile"] = df["bb_width"].rolling(100).apply(
    lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    if x.max() != x.min() else 50, raw=False)

# Volume MA
df["vol_ma20"] = df["volume"].rolling(20).mean()
df["vol_ratio"] = df["volume"] / df["vol_ma20"]

# ATR for reference
tr = pd.DataFrame({
    "hl": df["high"] - df["low"],
    "hc": abs(df["high"] - df["close"].shift(1)),
    "lc": abs(df["low"] - df["close"].shift(1))
}).max(axis=1)
df["atr"] = tr.rolling(14).mean()

# BB Width rolling max (for contraction exit)
for n in [3, 5, 7]:
    df[f"bbw_max{n}"] = df["bb_width"].rolling(n).max()

df = df.dropna().reset_index(drop=True)
df["month"] = df["datetime"].dt.to_period("M")
df["hour"] = df["datetime"].dt.hour
df["weekday"] = df["datetime"].dt.weekday
print(f"Ready: {len(df)} bars")
print(f"Date range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

# ============================================================
# Parameterized Backtest Engine
# ============================================================
ACCOUNT = 10000       # $10,000 initial capital
RISK_PCT = 0.02       # 2% risk per trade
FEE_RATE = 0.0004     # 0.04% taker fee per side
MAX_POS = 1           # 1 position at a time (focus)

def run(data, cfg):
    """
    cfg keys:
      squeeze_pctile: float — BB width percentile threshold (e.g. 15)
      vol_mult: float — volume multiplier for breakout confirmation (e.g. 2.0)
      exit_contract_ratio: float — BB width contraction ratio for exit (e.g. 0.80)
      exit_lookback: int — bars to look back for BB width max (e.g. 3)
      use_atr_sl: bool — if True, use ATR-based SL instead of BB mid
      atr_sl_mult: float — ATR multiplier for SL (e.g. 2.0)
    """
    sq_pct = cfg.get("squeeze_pctile", 15)
    vol_mult = cfg.get("vol_mult", 2.0)
    exit_ratio = cfg.get("exit_contract_ratio", 0.80)
    exit_lb = cfg.get("exit_lookback", 3)
    use_atr_sl = cfg.get("use_atr_sl", False)
    atr_sl_mult = cfg.get("atr_sl_mult", 2.0)

    bbw_max_col = f"bbw_max{exit_lb}" if f"bbw_max{exit_lb}" in data.columns else "bbw_max3"

    pos = None  # current position dict or None
    trades = []
    equity = ACCOUNT

    for i in range(1, len(data) - 1):
        row = data.iloc[i]
        prev = data.iloc[i - 1]
        nxt = data.iloc[i + 1]

        hi = row["high"]; lo = row["low"]; close = row["close"]

        # --- EXIT current position ---
        if pos is not None:
            bars = i - pos["ei"]
            side = pos["side"]
            entry = pos["entry"]
            size = pos["size"]
            sl = pos["sl"]

            # Track max float
            if side == "long":
                pos["mf"] = max(pos.get("mf", 0), (hi - entry) * size)
            else:
                pos["mf"] = max(pos.get("mf", 0), (entry - lo) * size)

            exited = False
            exit_price = None
            exit_type = None

            # 1. Structure Stop Loss — BB mid or ATR-based
            if side == "long" and lo <= sl:
                # Slippage: exit at SL level (or worse by 25% of overshoot)
                exit_price = sl - (sl - lo) * 0.25 if lo < sl else sl
                exit_type = "StopLoss"
                exited = True
            elif side == "short" and hi >= sl:
                exit_price = sl + (hi - sl) * 0.25 if hi > sl else sl
                exit_type = "StopLoss"
                exited = True

            # 2. BB Width Contraction Exit (momentum exhausted)
            if not exited and bars >= 2:
                bbw_peak = row[bbw_max_col] if not pd.isna(row[bbw_max_col]) else row["bb_width"]
                if row["bb_width"] < bbw_peak * exit_ratio:
                    exit_price = close
                    exit_type = "Contraction"
                    exited = True

            # 3. SL update: move SL to BB mid if it's more favorable
            if not exited and bars >= 3:
                if side == "long":
                    new_sl = row["bb_mid"]
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                elif side == "short":
                    new_sl = row["bb_mid"]
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl

            if exited:
                if side == "long":
                    gross_pnl = (exit_price - entry) * size
                else:
                    gross_pnl = (entry - exit_price) * size
                fee = (entry * size + exit_price * size) * FEE_RATE
                net_pnl = gross_pnl - fee
                equity += net_pnl

                trades.append({
                    "dt": str(row["datetime"]),
                    "entry_dt": pos["entry_dt"],
                    "side": side,
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "size": round(size, 4),
                    "pnl": round(net_pnl, 2),
                    "gross_pnl": round(gross_pnl, 2),
                    "fee": round(fee, 2),
                    "type": exit_type,
                    "bars": bars,
                    "mf": round(pos.get("mf", 0), 2),
                    "entry_hour": pos.get("entry_hour", 0),
                    "entry_wd": pos.get("entry_wd", 0),
                    "equity": round(equity, 2),
                })
                pos = None

        # --- ENTRY ---
        if pos is not None:
            continue  # already in a position

        # Condition 1: Previous bar was in squeeze (BB width percentile < threshold)
        if pd.isna(prev["bbw_pctile"]):
            continue
        was_squeeze = prev["bbw_pctile"] < sq_pct

        if not was_squeeze:
            continue

        # Condition 2: Current bar breakout + volume surge
        bb_long = close > row["bb_upper"]
        bb_short = close < row["bb_lower"]

        if not bb_long and not bb_short:
            continue

        vol_ok = row["vol_ratio"] > vol_mult
        if not vol_ok:
            continue

        # Direction determined by breakout
        side = "long" if bb_long else "short"

        # Entry at next bar open
        entry_price = nxt["open"]

        # Stop loss: BB mid (structure-based)
        if use_atr_sl:
            if side == "long":
                sl = entry_price - atr_sl_mult * row["atr"]
            else:
                sl = entry_price + atr_sl_mult * row["atr"]
        else:
            sl = row["bb_mid"]

        # Position sizing: risk $ACCOUNT * RISK_PCT per trade
        risk_per_unit = abs(entry_price - sl)
        if risk_per_unit < entry_price * 0.001:
            continue  # SL too close, skip

        risk_dollars = equity * RISK_PCT
        size = risk_dollars / risk_per_unit  # number of ETH units
        notional = size * entry_price

        # Cap notional at 5x equity (reasonable leverage limit)
        max_notional = equity * 5
        if notional > max_notional:
            size = max_notional / entry_price

        pos = {
            "entry": entry_price,
            "sl": sl,
            "side": side,
            "size": size,
            "ei": i,
            "mf": 0,
            "entry_dt": str(row["datetime"]),
            "entry_hour": row["hour"],
            "entry_wd": row["weekday"],
        }

    # Close any remaining position at last bar
    if pos is not None:
        row = data.iloc[-1]
        side = pos["side"]
        entry = pos["entry"]
        size = pos["size"]
        exit_price = row["close"]
        if side == "long":
            gross_pnl = (exit_price - entry) * size
        else:
            gross_pnl = (entry - exit_price) * size
        fee = (entry * size + exit_price * size) * FEE_RATE
        net_pnl = gross_pnl - fee
        equity += net_pnl
        trades.append({
            "dt": str(row["datetime"]), "entry_dt": pos["entry_dt"],
            "side": side, "entry_price": round(entry, 2),
            "exit_price": round(exit_price, 2), "size": round(size, 4),
            "pnl": round(net_pnl, 2), "gross_pnl": round(gross_pnl, 2),
            "fee": round(fee, 2), "type": "EOD", "bars": len(data) - 1 - pos["ei"],
            "mf": round(pos.get("mf", 0), 2), "entry_hour": pos.get("entry_hour", 0),
            "entry_wd": pos.get("entry_wd", 0), "equity": round(equity, 2),
        })

    return pd.DataFrame(trades) if trades else pd.DataFrame()

# ============================================================
# Metrics Calculator
# ============================================================
def calc(tdf, label=""):
    if len(tdf) == 0:
        return {"n": 0, "pnl": 0, "wr": 0, "pf": 0, "dd_pct": 0, "sharpe": 0,
                "avg_bars": 0, "sl_n": 0, "ct_n": 0}

    pnl = tdf["pnl"].sum()
    wr = (tdf["pnl"] > 0).mean() * 100
    w = tdf[tdf["pnl"] > 0]; l = tdf[tdf["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 99.9
    avg_bars = tdf["bars"].mean()

    # Max drawdown on equity curve
    eq = tdf["equity"].values
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak * 100
    max_dd = dd.min()

    # Sharpe (annualized, assuming ~250 trading days)
    daily_pnl = tdf.groupby(tdf["dt"].str[:10])["pnl"].sum()
    sharpe = daily_pnl.mean() / daily_pnl.std() * np.sqrt(365) if daily_pnl.std() > 0 else 0

    sl_n = len(tdf[tdf["type"] == "StopLoss"])
    ct_n = len(tdf[tdf["type"] == "Contraction"])

    # Consecutive losses
    is_loss = (tdf["pnl"] <= 0).values
    mcl = 0; cur = 0; mcl_pnl = 0; cur_pnl = 0
    for j in range(len(is_loss)):
        if is_loss[j]:
            cur += 1; cur_pnl += tdf.iloc[j]["pnl"]
            if cur > mcl: mcl = cur; mcl_pnl = cur_pnl
        else:
            cur = 0; cur_pnl = 0

    return {"n": len(tdf), "pnl": round(pnl, 2), "wr": round(wr, 1), "pf": round(pf, 2),
            "dd_pct": round(max_dd, 1), "sharpe": round(sharpe, 2), "avg_bars": round(avg_bars, 1),
            "sl_n": sl_n, "ct_n": ct_n, "mcl": mcl, "mcl_pnl": round(mcl_pnl, 2),
            "avg_w": round(w["pnl"].mean(), 2) if len(w) > 0 else 0,
            "avg_l": round(l["pnl"].mean(), 2) if len(l) > 0 else 0}

def print_box(s, cfg_name="VSS"):
    years = 2.0  # approximate
    annual = s["pnl"] / years
    rr = abs(s["avg_w"] / s["avg_l"]) if s["avg_l"] != 0 else 0
    print(f"""
  +{'='*50}+
  | Strategy: {cfg_name:<38s} |
  | Period:   {str(df['datetime'].iloc[0])[:10]} ~ {str(df['datetime'].iloc[-1])[:10]:<17s} |
  | Trades:   {s['n']:<38d} |
  | Win Rate: {s['wr']:<37.1f}% |
  | PF:       {s['pf']:<38.2f} |
  | Annual:   ${annual:<+37,.0f} |
  | Max DD:   {s['dd_pct']:<37.1f}% |
  | Sharpe:   {s['sharpe']:<38.2f} |
  | Avg Hold: {s['avg_bars']:<35.1f} hr |
  | Avg W/L:  ${s['avg_w']:+.0f} / ${s['avg_l']:+.0f} (RR {rr:.2f}){' '*(17-len(f'${s["avg_w"]:+.0f} / ${s["avg_l"]:+.0f} (RR {rr:.2f})'))}|
  | Max CLoss:{s['mcl']} (${s['mcl_pnl']:.0f}){' '*(30-len(f'{s["mcl"]} (${s["mcl_pnl"]:.0f})'))}|
  | SL/Contr: {s['sl_n']}/{s['ct_n']}{' '*(33-len(f'{s["sl_n"]}/{s["ct_n"]}'))}|
  +{'='*50}+""")

# ============================================================
# BASE CONFIG
# ============================================================
BASE_CFG = {
    "squeeze_pctile": 15,
    "vol_mult": 2.0,
    "exit_contract_ratio": 0.80,
    "exit_lookback": 3,
    "use_atr_sl": False,
    "atr_sl_mult": 2.0,
}

# ============================================================
# STEP 3: Run Base Backtest
# ============================================================
print("\n" + "=" * 100)
print("  STEP 3: Base Strategy Backtest")
print("=" * 100)

tdf = run(df, BASE_CFG)
s = calc(tdf)
print_box(s, "VSS Base (BBW<15 + Vol>2x)")

# ============================================================
# Sensitivity: Squeeze threshold
# ============================================================
print("\n" + "=" * 100)
print("  Sensitivity: Squeeze Percentile Threshold")
print("=" * 100)
print(f"  {'Squeeze':<10s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}  {'AvgH':>5s}  {'SL':>4s}  {'CT':>4s}")
print("  " + "-" * 75)
for sq in [5, 10, 15, 20, 25, 30]:
    cfg = {**BASE_CFG, "squeeze_pctile": sq}
    t = run(df, cfg); sc = calc(t)
    tag = " ***" if sq == 15 else ""
    print(f"  <{sq:<8d} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}  {sc['avg_bars']:>5.1f}  {sc['sl_n']:>4d}  {sc['ct_n']:>4d}{tag}")

# ============================================================
# Sensitivity: Volume multiplier
# ============================================================
print("\n" + "=" * 100)
print("  Sensitivity: Volume Multiplier")
print("=" * 100)
print(f"  {'VolMult':<10s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}  {'AvgH':>5s}")
print("  " + "-" * 65)
for vm in [1.0, 1.5, 2.0, 2.5, 3.0]:
    cfg = {**BASE_CFG, "vol_mult": vm}
    t = run(df, cfg); sc = calc(t)
    tag = " ***" if vm == 2.0 else ""
    print(f"  >{vm:<8.1f} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}  {sc['avg_bars']:>5.1f}{tag}")

# ============================================================
# Sensitivity: Exit contraction ratio
# ============================================================
print("\n" + "=" * 100)
print("  Sensitivity: Exit Contraction Ratio")
print("=" * 100)
print(f"  {'ExitR':<10s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}  {'AvgH':>5s}")
print("  " + "-" * 65)
for er in [0.60, 0.70, 0.80, 0.85, 0.90, 0.95]:
    cfg = {**BASE_CFG, "exit_contract_ratio": er}
    t = run(df, cfg); sc = calc(t)
    tag = " ***" if er == 0.80 else ""
    print(f"  {er:<10.2f} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}  {sc['avg_bars']:>5.1f}{tag}")

# ============================================================
# Sensitivity: Exit lookback
# ============================================================
print("\n" + "=" * 100)
print("  Sensitivity: Exit Lookback")
print("=" * 100)
print(f"  {'Lookback':<10s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}  {'AvgH':>5s}")
print("  " + "-" * 65)
for lb in [3, 5, 7]:
    cfg = {**BASE_CFG, "exit_lookback": lb}
    t = run(df, cfg); sc = calc(t)
    tag = " ***" if lb == 3 else ""
    print(f"  {lb:<10d} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}  {sc['avg_bars']:>5.1f}{tag}")

# ============================================================
# Sensitivity: SL type (BB mid vs ATR)
# ============================================================
print("\n" + "=" * 100)
print("  Sensitivity: Stop Loss Type")
print("=" * 100)
print(f"  {'SL Type':<20s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}")
print("  " + "-" * 65)
for sl_type, atr_m in [("BB Mid", False), ("ATR 1.5x", True), ("ATR 2.0x", True), ("ATR 2.5x", True)]:
    cfg = {**BASE_CFG, "use_atr_sl": atr_m}
    if atr_m:
        cfg["atr_sl_mult"] = float(sl_type.split(" ")[1].replace("x", ""))
    t = run(df, cfg); sc = calc(t)
    print(f"  {sl_type:<20s} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}")

# ============================================================
# Find best config from sensitivity
# ============================================================
print("\n" + "=" * 100)
print("  Grid Search: Top Configs")
print("=" * 100)

results = []
for sq in [10, 15, 20, 25]:
    for vm in [1.5, 2.0, 2.5]:
        for er in [0.70, 0.80, 0.90]:
            for lb in [3, 5]:
                cfg = {"squeeze_pctile": sq, "vol_mult": vm,
                       "exit_contract_ratio": er, "exit_lookback": lb,
                       "use_atr_sl": False}
                t = run(df, cfg)
                sc = calc(t)
                if sc["n"] >= 20:  # minimum trade requirement
                    results.append((cfg, sc))

# Sort by PnL
results.sort(key=lambda x: x[1]["pnl"], reverse=True)
print(f"\n  Top 10 configs (min 20 trades):")
print(f"  {'#':>3s}  {'Sq':<4s} {'VM':<5s} {'ER':<5s} {'LB':<4s} {'N':>5s}  {'PnL':>10s}  {'WR':>6s}  {'PF':>6s}  {'DD%':>6s}  {'Sharpe':>7s}")
print("  " + "-" * 70)
for idx, (cfg, sc) in enumerate(results[:10]):
    print(f"  {idx+1:>3d}  <{cfg['squeeze_pctile']:<3d} >{cfg['vol_mult']:<4.1f} {cfg['exit_contract_ratio']:<5.2f} {cfg['exit_lookback']:<4d} {sc['n']:>5d}  ${sc['pnl']:>+9,.0f}  {sc['wr']:>5.1f}%  {sc['pf']:>5.2f}  {sc['dd_pct']:>5.1f}%  {sc['sharpe']:>7.2f}")

# ============================================================
# Best config deep analysis
# ============================================================
if results:
    BEST_CFG = results[0][0]
    print(f"\n  Best config: squeeze<{BEST_CFG['squeeze_pctile']}, vol>{BEST_CFG['vol_mult']}, "
          f"exit_ratio={BEST_CFG['exit_contract_ratio']}, lookback={BEST_CFG['exit_lookback']}")

    print("\n" + "=" * 100)
    print("  Deep Analysis: Best Config")
    print("=" * 100)

    tdf_best = run(df, BEST_CFG)
    s_best = calc(tdf_best)
    print_box(s_best, f"VSS Best (sq<{BEST_CFG['squeeze_pctile']} vm>{BEST_CFG['vol_mult']})")

    # Walk-Forward 1y/1y
    print("\n--- Walk-Forward (Year 1 train / Year 2 test) ---")
    split = len(df) // 2
    df_y1 = df.iloc[:split].reset_index(drop=True)
    df_y2 = df.iloc[split:].reset_index(drop=True)
    t_y1 = run(df_y1, BEST_CFG); s_y1 = calc(t_y1)
    t_y2 = run(df_y2, BEST_CFG); s_y2 = calc(t_y2)
    print(f"  Year 1 (IS):  {s_y1['n']} trades, ${s_y1['pnl']:+,.0f}, WR {s_y1['wr']:.1f}%, PF {s_y1['pf']:.2f}")
    print(f"  Year 2 (OOS): {s_y2['n']} trades, ${s_y2['pnl']:+,.0f}, WR {s_y2['wr']:.1f}%, PF {s_y2['pf']:.2f}")

    # Rolling 3m/1m
    print("\n--- Rolling Validation (3m/1m) ---")
    months = sorted(df["month"].unique())
    fold_pnls = []
    print(f"  {'Fold':<6s} {'Test':<12s} {'N':>4s}  {'PnL':>10s}")
    print("  " + "-" * 40)
    for fi in range(len(months) - 4):
        test_m = months[fi + 3]
        ft = df[df["month"] == test_m].reset_index(drop=True)
        if len(ft) < 20:
            fold_pnls.append(0); continue
        t = run(ft, BEST_CFG)
        fp = t["pnl"].sum() if len(t) > 0 else 0
        fold_pnls.append(fp)
        n_t = len(t) if len(t) > 0 else 0
        print(f"  {fi+1:<6d} {str(test_m):<12s} {n_t:>4d}  ${fp:>+9,.0f}")
    prof = sum(1 for x in fold_pnls if x > 0)
    print(f"\n  Rolling: ${sum(fold_pnls):+,.0f} ({prof}/{len(fold_pnls)} folds profitable)")

    # Monthly breakdown
    print("\n--- Monthly Breakdown ---")
    if len(tdf_best) > 0:
        tdf_best["month_str"] = tdf_best["dt"].str[:7]
        cum_eq = ACCOUNT
        print(f"  {'Month':<10s} {'N':>4s}  {'PnL':>10s}  {'WR':>6s}  {'SL':>3s}  {'CT':>3s}  {'Equity':>10s}")
        print("  " + "-" * 60)
        for m in sorted(tdf_best["month_str"].unique()):
            mt = tdf_best[tdf_best["month_str"] == m]
            mpnl = mt["pnl"].sum()
            mwr = (mt["pnl"] > 0).mean() * 100 if len(mt) > 0 else 0
            msl = len(mt[mt["type"] == "StopLoss"])
            mct = len(mt[mt["type"] == "Contraction"])
            cum_eq += mpnl
            print(f"  {m:<10s} {len(mt):>4d}  ${mpnl:>+9,.0f}  {mwr:>5.1f}%  {msl:>3d}  {mct:>3d}  ${cum_eq:>9,.0f}")

    # Long/Short
    if len(tdf_best) > 0:
        longs = tdf_best[tdf_best["side"] == "long"]
        shorts = tdf_best[tdf_best["side"] == "short"]
        print(f"\n  Long:  {len(longs)} trades, ${longs['pnl'].sum():+,.0f}, WR {(longs['pnl']>0).mean()*100:.1f}%")
        print(f"  Short: {len(shorts)} trades, ${shorts['pnl'].sum():+,.0f}, WR {(shorts['pnl']>0).mean()*100:.1f}%")

    # Hold time
    if len(tdf_best) > 0:
        print(f"\n  --- Hold Time vs PnL ---")
        print(f"  {'Duration':<12s} {'N':>4s}  {'PnL':>10s}  {'WR':>6s}")
        print("  " + "-" * 40)
        for lo_h, hi_h, label in [(0,3,"<3h"),(3,6,"3-6h"),(6,12,"6-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,999,"48h+")]:
            ht = tdf_best[(tdf_best["bars"]>=lo_h)&(tdf_best["bars"]<hi_h)]
            if len(ht) == 0: continue
            hpnl = ht["pnl"].sum(); hwr = (ht["pnl"]>0).mean()*100
            print(f"  {label:<12s} {len(ht):>4d}  ${hpnl:>+9,.0f}  {hwr:>5.1f}%")

    # Full trade list
    print(f"\n  --- Full Trade List ---")
    if len(tdf_best) > 0:
        print(f"  {'#':>4s}  {'Entry Date':<20s} {'Side':<6s} {'Entry':>9s} {'Exit':>9s}  {'PnL':>10s}  {'Fee':>6s}  {'Bars':>5s}  {'Type':<12s} {'Equity':>10s}")
        print("  " + "-" * 110)
        for idx, t in tdf_best.iterrows():
            print(f"  {idx+1:>4d}  {t.get('entry_dt',''):<20s} {t['side']:<6s} ${t['entry_price']:>8.1f} ${t['exit_price']:>8.1f}  ${t['pnl']:>+9.2f}  ${t['fee']:>5.2f}  {t['bars']:>5.0f}  {t['type']:<12s} ${t['equity']:>9,.0f}")

# ============================================================
# STEP 4: Honest Assessment
# ============================================================
print("\n" + "=" * 100)
print("  STEP 4: Honest Professional Assessment")
print("=" * 100)

if results:
    s = results[0][1]
    years = 2.0
    annual = s["pnl"] / years
    target_trades = 120
    target_pnl = 5000

    print(f"""
  Target check:
    Annual PnL >= $5,000?  {'YES' if annual >= target_pnl else 'NO'} (actual: ${annual:+,.0f})
    Trades >= 120/year?    {'YES' if s['n']/years >= target_trades/2 else 'NO'} (actual: {s['n']:.0f} in {years:.0f}y = {s['n']/years:.0f}/year)
    Max DD <= 25%?         {'YES' if abs(s['dd_pct']) <= 25 else 'NO'} (actual: {s['dd_pct']:.1f}%)
    OOS profitable?        {'YES' if s_y2['pnl'] > 0 else 'NO'} (OOS: ${s_y2['pnl']:+,.0f})

  Verdict: {'PASS - Proceed to sensitivity test' if annual >= target_pnl and s['n']/years >= 60 else 'FAIL - Hypothesis needs revision'}
""")

print("=" * 100)
print("  Backtest complete.")
print("=" * 100)
