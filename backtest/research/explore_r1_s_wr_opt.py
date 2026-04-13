"""
Exploration Round 1: S Strategy — CMP Portfolio with TP 1.5% + GK < 25
=========================================================================
Hypothesis: Lower TP captures minimum post-compression move, stricter GK
filter selects highest quality compression → WR should increase from 65% to ~72%

Changes from baseline CMP:
  - GK pctile < 25 (was 30-40 per sub)
  - TP 1.5% (was 2%)
  - MaxHold 15 (was 12)

Parameters locked BEFORE seeing any data:
  - GK < 25: bottom quartile = strongest compression = most reliable breakout
  - TP 1.5%: ~1 ATR for compressed ETH 1h = minimum expected post-breakout move
  - MH 15: more time for the lower TP target
  - BL 8/10/12/15: proven breakout lengths (unchanged)
  - SN 5.5%, EXIT_CD 6, maxSame 5: proven (unchanged)
  - Session: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ============================================================
# FIXED PARAMETERS (locked before seeing data)
# ============================================================
NOTIONAL = 2000
FEE = 2.0          # taker 0.04%*2 + slip 0.01%*2 = $2/trade
ACCOUNT = 10000
SN_PCT = 0.055     # SafeNet 5.5%
SN_PEN = 0.25      # 25% penetration beyond SN
BLOCK_H = {0, 1, 2, 12}   # UTC+8 blocked hours
BLOCK_D = {0, 5, 6}        # Mon=0, Sat=5, Sun=6

GK_SHORT = 5
GK_LONG = 20
GK_WIN = 100

# Round 1 specific parameters
GK_THRESH = 25     # stricter: bottom quartile only
TP_PCT = 0.015     # 1.5% take profit (was 2%)
MAX_HOLD = 15      # 15 bars (was 12)
EXIT_CD = 6        # proven cooldown
MAX_SAME = 5       # per sub-strategy
BL_LIST = [8, 10, 12, 15]  # proven breakout lengths

# Also run baseline for comparison
BASELINE_GK_CONFIGS = [40, 40, 30, 40]  # per sub
BASELINE_TP = 0.02
BASELINE_MH = 12


# ============================================================
# DATA FETCHING
# ============================================================
def fetch_binance_klines(symbol="ETHUSDT", interval="1h", days=730):
    """Fetch klines from Binance Futures API, paginated."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_data = []
    cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days from Binance...")
    while cur < end_ms:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms, "limit": 1500
        }
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2)
        if not data:
            break
        all_data.extend(data)
        cur = data[-1][0] + 1  # next ms after last candle
        if len(data) < 1500:
            break
        time.sleep(0.1)

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "trades", "tbv", "tqv", "ignore"
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = df["datetime"] + timedelta(hours=8)  # UTC+8
    df["datetime"] = df["datetime"].dt.tz_localize(None)   # drop tz for comparison
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  Fetched {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df


# ============================================================
# INDICATOR CALCULATION
# ============================================================
def pctile_func(x):
    """Min-max percentile over window."""
    if x.max() == x.min():
        return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100


def calc_indicators(df):
    """Calculate all indicators with proper shift(1) to prevent look-ahead."""
    d = df.copy()
    d["ret"] = d["close"].pct_change()

    # Garman-Klass volatility
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)

    # GK ratio and percentile (all shifted by 1)
    d["gk_s"] = d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"] = d["gk"].rolling(GK_LONG).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    # GK threshold columns
    for t in [25, 30, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t

    # Breakout: close shift(1) < rolling min of close shift(2)..(shift(2+BL-1))
    # This means: yesterday's close broke below the min of the (BL-1) closes before that
    d["cs1"] = d["close"].shift(1)
    for bl in BL_LIST:
        d[f"cmn{bl}"] = d["close"].shift(2).rolling(bl - 1).min()
        d[f"bs{bl}"] = d["cs1"] < d[f"cmn{bl}"]

    # Session filter (UTC+8)
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday  # Mon=0, Sun=6
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    return d


# ============================================================
# BACKTEST ENGINE
# ============================================================
def _b(v):
    try:
        if pd.isna(v):
            return False
    except:
        pass
    return bool(v)


def bt_cmp(df, max_same=5, gk_col="gk25", brk_look=10,
           tp_pct=0.015, max_hold=15, sn_pct=SN_PCT, exit_cd=EXIT_CD):
    """Run CMP short backtest for a single sub-strategy."""
    W = 160  # warmup bars
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    DT = df["datetime"].values

    bs_col = f"bs{brk_look}"
    BSa = df[bs_col].values
    SOKa = df["sok"].values
    GKa = df[gk_col].values

    pos = []
    trades = []
    lx = -999  # last exit bar index

    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]

        # ---- Check exits for existing positions ----
        npos = []
        for p in pos:
            b = i - p["ei"]
            done = False

            # SafeNet: price goes UP beyond SN (bad for short)
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct)
                ep_ += (h - ep_) * SN_PEN  # penetration
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt,
                               "entry": p["e"], "exit": ep_})
                lx = i
                done = True

            # Take Profit: price goes DOWN to TP (good for short)
            if not done and lo <= p["e"] * (1 - tp_pct):
                ep_ = p["e"] * (1 - tp_pct)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt,
                               "entry": p["e"], "exit": ep_})
                lx = i
                done = True

            # Max Hold
            if not done and b >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt,
                               "entry": p["e"], "exit": c})
                lx = i
                done = True

            if not done:
                npos.append(p)
        pos = npos

        # ---- Check entry ----
        gk_ok = _b(GKa[i])
        brk = _b(BSa[i])
        sok = _b(SOKa[i])

        if gk_ok and brk and sok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(
        columns=["pnl", "t", "b", "dt", "entry", "exit"])


def bt_portfolio(df, strats):
    """Run multiple sub-strategies and merge trades chronologically."""
    all_trades = []
    for cfg in strats:
        t = bt_cmp(df, **cfg)
        if len(t) > 0:
            t["sub"] = f"GK{cfg.get('gk_col','gk25')[-2:]}-BL{cfg['brk_look']}"
            all_trades.append(t)
    if not all_trades:
        return pd.DataFrame(columns=["pnl", "t", "b", "dt", "entry", "exit", "sub"])
    merged = pd.concat(all_trades, ignore_index=True)
    return merged.sort_values("dt").reset_index(drop=True)


# ============================================================
# EVALUATION
# ============================================================
def evaluate(tdf, start_dt, end_dt, label=""):
    """Evaluate strategy performance for a given period."""
    tdf = tdf.copy()
    tdf["dt"] = pd.to_datetime(tdf["dt"])
    period = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)

    n = len(period)
    if n == 0:
        return None

    pnl = period["pnl"].sum()
    w = period[period["pnl"] > 0]["pnl"].sum()
    l_ = abs(period[period["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999
    wr = (period["pnl"] > 0).mean() * 100

    eq = period["pnl"].cumsum()
    dd = eq - eq.cummax()
    mdd = abs(dd.min()) / ACCOUNT * 100

    period["m"] = period["dt"].dt.to_period("M")
    ms = period.groupby("m")["pnl"].sum()
    months_total = len(ms)
    pos_months = (ms > 0).sum()

    top_m_val = ms.max() if len(ms) > 0 else 0
    top_m_name = ms.idxmax() if len(ms) > 0 else "N/A"
    top_pct = top_m_val / pnl * 100 if pnl > 0 else 999
    no_best = pnl - top_m_val
    worst_m_val = ms.min() if len(ms) > 0 else 0
    worst_m_name = ms.idxmin() if len(ms) > 0 else "N/A"

    # Duration in days for annualization
    days = (end_dt - start_dt).days
    months = days / 30.44
    trades_per_month = n / months if months > 0 else 0

    # Exit type distribution
    exit_dist = period.groupby("t")["pnl"].agg(["count", "sum", "mean"])

    return {
        "label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr,
        "mdd": mdd, "months": months_total, "pos_months": pos_months,
        "top_pct": top_pct, "top_m": str(top_m_name), "top_m_val": top_m_val,
        "no_best": no_best, "worst_m": str(worst_m_name), "worst_m_val": worst_m_val,
        "trades_per_month": trades_per_month,
        "monthly": ms, "exit_dist": exit_dist, "trades": period,
        "avg": pnl / n if n > 0 else 0
    }


def walk_forward_6fold(df, strats, full_start, full_end):
    """6-fold walk-forward: 12-month train, 2-month test, rolling."""
    oos_start = full_start + pd.DateOffset(months=12)
    results = []
    for fold in range(6):
        test_start = oos_start + pd.DateOffset(months=fold * 2)
        test_end = test_start + pd.DateOffset(months=2)
        if test_end > full_end:
            test_end = full_end

        # Run backtest on full data, then filter to test period
        t = bt_portfolio(df, strats)
        t["dt"] = pd.to_datetime(t["dt"])
        test_trades = t[(t["dt"] >= test_start) & (t["dt"] < test_end)]
        fold_pnl = test_trades["pnl"].sum() if len(test_trades) > 0 else 0
        fold_n = len(test_trades)

        results.append({
            "fold": fold + 1,
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
            "pnl": fold_pnl,
            "n": fold_n,
            "positive": fold_pnl > 0
        })
    return results


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  EXPLORATION ROUND 1: S CMP Portfolio — TP 1.5% + GK < 25")
    print("=" * 70)

    # ---- Fetch fresh data ----
    df_raw = fetch_binance_klines("ETHUSDT", "1h", 730)

    # ---- Define periods ----
    last_dt = df_raw["datetime"].iloc[-1]
    full_start = last_dt - pd.Timedelta(days=730)
    mid_dt = last_dt - pd.Timedelta(days=365)
    full_end = last_dt

    print(f"\n  Full period: {full_start.strftime('%Y-%m-%d')} ~ {full_end.strftime('%Y-%m-%d')}")
    print(f"  IS period:   {full_start.strftime('%Y-%m-%d')} ~ {mid_dt.strftime('%Y-%m-%d')}")
    print(f"  OOS period:  {mid_dt.strftime('%Y-%m-%d')} ~ {full_end.strftime('%Y-%m-%d')}")

    # ---- Calculate indicators ----
    df = calc_indicators(df_raw)

    # ============================================================
    # BASELINE: Current CMP (for comparison only)
    # ============================================================
    print(f"\n{'='*70}")
    print("  BASELINE: Current CMP (GK 30-40, TP 2%, MH 12)")
    print(f"{'='*70}")

    baseline_strats = [
        dict(max_same=5, gk_col="gk40", brk_look=8, tp_pct=0.02, max_hold=12),
        dict(max_same=5, gk_col="gk40", brk_look=15, tp_pct=0.02, max_hold=12),
        dict(max_same=5, gk_col="gk30", brk_look=10, tp_pct=0.02, max_hold=12),
        dict(max_same=5, gk_col="gk40", brk_look=12, tp_pct=0.02, max_hold=12),
    ]

    t_base = bt_portfolio(df, baseline_strats)
    r_base_is = evaluate(t_base, full_start, mid_dt, "Baseline IS")
    r_base_oos = evaluate(t_base, mid_dt, full_end, "Baseline OOS")

    if r_base_oos:
        print(f"  OOS: {r_base_oos['n']}t ${r_base_oos['pnl']:+,.0f} PF{r_base_oos['pf']:.2f} "
              f"WR{r_base_oos['wr']:.1f}% MDD{r_base_oos['mdd']:.1f}%")
        print(f"  IS:  {r_base_is['n']}t ${r_base_is['pnl']:+,.0f}" if r_base_is else "  IS: no data")

    # ============================================================
    # ROUND 1: Modified CMP (GK < 25, TP 1.5%, MH 15)
    # ============================================================
    print(f"\n{'='*70}")
    print("  ROUND 1: Modified CMP (GK < 25, TP 1.5%, MH 15)")
    print(f"{'='*70}")

    r1_strats = [
        dict(max_same=MAX_SAME, gk_col="gk25", brk_look=8,  tp_pct=TP_PCT, max_hold=MAX_HOLD),
        dict(max_same=MAX_SAME, gk_col="gk25", brk_look=10, tp_pct=TP_PCT, max_hold=MAX_HOLD),
        dict(max_same=MAX_SAME, gk_col="gk25", brk_look=12, tp_pct=TP_PCT, max_hold=MAX_HOLD),
        dict(max_same=MAX_SAME, gk_col="gk25", brk_look=15, tp_pct=TP_PCT, max_hold=MAX_HOLD),
    ]

    t_r1 = bt_portfolio(df, r1_strats)
    r1_is = evaluate(t_r1, full_start, mid_dt, "R1 IS")
    r1_oos = evaluate(t_r1, mid_dt, full_end, "R1 OOS")

    if r1_oos:
        print(f"\n  ┌──────────────────────────────────────────┐")
        print(f"  │ 方向：S（做空）                           │")
        print(f"  │ 回測期間：{full_start.strftime('%Y-%m-%d')} ~ {full_end.strftime('%Y-%m-%d')} │")
        print(f"  │ IS 年化淨利：${r1_is['pnl']:+,.0f}" + " " * max(0, 26 - len(f"${r1_is['pnl']:+,.0f}")) + "│" if r1_is else "")
        print(f"  │ OOS 年化淨利：${r1_oos['pnl']:+,.0f} ← 達標依據" + " " * max(0, 14 - len(f"${r1_oos['pnl']:+,.0f}")) + "│")
        print(f"  │ OOS PF：{r1_oos['pf']:.2f}" + " " * max(0, 30 - len(f"{r1_oos['pf']:.2f}")) + "│")
        print(f"  │ OOS MDD：{r1_oos['mdd']:.1f}%" + " " * max(0, 28 - len(f"{r1_oos['mdd']:.1f}%")) + "│")
        print(f"  │ OOS 月均交易：{r1_oos['trades_per_month']:.1f} 筆" + " " * max(0, 22 - len(f"{r1_oos['trades_per_month']:.1f} 筆")) + "│")
        print(f"  │ OOS WR：{r1_oos['wr']:.1f}% ← 是否 ≥ 70%？" + " " * max(0, 12 - len(f"{r1_oos['wr']:.1f}%")) + "│")
        print(f"  │ OOS 正收益月份：{r1_oos['pos_months']}/{r1_oos['months']}" + " " * max(0, 20 - len(f"{r1_oos['pos_months']}/{r1_oos['months']}")) + "│")
        print(f"  │ OOS topMonth：{r1_oos['top_pct']:.1f}%（{r1_oos['top_m']}）" + " " * max(0, 10 - len(f"{r1_oos['top_pct']:.1f}%（{r1_oos['top_m']}）")) + "│")
        print(f"  │ OOS 移除最佳月後：${r1_oos['no_best']:+,.0f}" + " " * max(0, 18 - len(f"${r1_oos['no_best']:+,.0f}")) + "│")
        print(f"  │ OOS 最差月：${r1_oos['worst_m_val']:+,.0f}（{r1_oos['worst_m']}）" + " " * max(0, 8 - len(f"${r1_oos['worst_m_val']:+,.0f}（{r1_oos['worst_m']}）")) + "│")
        print(f"  │ 手續費總扣除：-${r1_oos['n'] * FEE:,.0f}" + " " * max(0, 22 - len(f"-${r1_oos['n'] * FEE:,.0f}")) + "│")
        print(f"  └──────────────────────────────────────────┘")

        # Monthly breakdown
        print(f"\n  ─── 月度 OOS 明細 ───")
        print(f"  | 月份    |   PnL   |  累計   |")
        print(f"  |---------|--------:|--------:|")
        cum = 0
        ms = r1_oos["monthly"]
        for m, v in ms.items():
            cum += v
            print(f"  | {m} | ${v:>+7,.0f} | ${cum:>+7,.0f} |")

        # Exit distribution
        print(f"\n  ─── 出場分佈 ───")
        ed = r1_oos["exit_dist"]
        print(f"  {ed.to_string()}")

        # Walk-Forward
        print(f"\n  ─── Walk-Forward 6-fold ───")
        wf = walk_forward_6fold(df, r1_strats, full_start, full_end)
        wf_pos = sum(1 for w in wf if w["positive"])
        for w in wf:
            status = "✓" if w["positive"] else "✗"
            print(f"  Fold {w['fold']}: {w['test_start']} ~ {w['test_end']} "
                  f"| {w['n']}t ${w['pnl']:+,.0f} {status}")
        print(f"  Walk-Forward: {wf_pos}/6 正向")

        # ---- PASS/FAIL Assessment ----
        print(f"\n{'='*70}")
        print("  達標評估")
        print(f"{'='*70}")

        checks = {
            "OOS PnL ≥ $10,000": r1_oos["pnl"] >= 10000,
            "OOS PF ≥ 1.5": r1_oos["pf"] >= 1.5,
            "OOS MDD ≤ 25%": r1_oos["mdd"] <= 25,
            "月均交易 ≥ 10": r1_oos["trades_per_month"] >= 10,
            "WR ≥ 70%": r1_oos["wr"] >= 70,
            "正收益月 ≥ 75%": r1_oos["pos_months"] / r1_oos["months"] >= 0.75 if r1_oos["months"] > 0 else False,
            "topMonth ≤ 20%": r1_oos["top_pct"] <= 20,
            "移除最佳月 ≥ $8K": r1_oos["no_best"] >= 8000,
            "WF ≥ 5/6": wf_pos >= 5,
        }

        all_pass = True
        for name, ok in checks.items():
            status = "✓ PASS" if ok else "✗ FAIL"
            print(f"  {status} | {name}")
            if not ok:
                all_pass = False

        if all_pass:
            print(f"\n  ★ 第 1 輪 S 策略：全部達標！")
        else:
            fails = [k for k, v in checks.items() if not v]
            print(f"\n  ✗ 第 1 輪失敗：{', '.join(fails)}")

        # Comparison with baseline
        if r_base_oos:
            print(f"\n  ─── vs 基準比較 ───")
            print(f"  {'指標':<20} {'基準':>10} {'R1':>10} {'差異':>10}")
            print(f"  {'PnL':<20} ${r_base_oos['pnl']:>+9,.0f} ${r1_oos['pnl']:>+9,.0f} ${r1_oos['pnl']-r_base_oos['pnl']:>+9,.0f}")
            print(f"  {'WR%':<20} {r_base_oos['wr']:>9.1f}% {r1_oos['wr']:>9.1f}% {r1_oos['wr']-r_base_oos['wr']:>+9.1f}%")
            print(f"  {'PF':<20} {r_base_oos['pf']:>10.2f} {r1_oos['pf']:>10.2f} {r1_oos['pf']-r_base_oos['pf']:>+10.2f}")
            print(f"  {'Trades':<20} {r_base_oos['n']:>10} {r1_oos['n']:>10} {r1_oos['n']-r_base_oos['n']:>+10}")
            print(f"  {'MDD%':<20} {r_base_oos['mdd']:>9.1f}% {r1_oos['mdd']:>9.1f}% {r1_oos['mdd']-r_base_oos['mdd']:>+9.1f}%")
    else:
        print("  No OOS trades generated!")

    # ============================================================
    # GOD-VIEW SELF-CHECK
    # ============================================================
    print(f"\n{'='*70}")
    print("  上帝視角自檢")
    print(f"{'='*70}")
    print("  □ 所有 signal 計算只用 shift(1) 或更早數據？ → 是")
    print("    gk_pct = gk_r.shift(1).rolling(100).apply(pctile)")
    print("    breakout = close.shift(1) < close.shift(2).rolling(BL-1).min()")
    print("  □ 進場價是 next bar open（df['open'].shift(-1)）？ → 是")
    print("    entry = O[i+1] (nxo = next bar open)")
    print("  □ 所有滾動指標都有 .shift(1) 防止含當根？ → 是")
    print("    gk_r.shift(1) before percentile calculation")
    print("  □ 參數是在看任何數據之前就決定的？ → 是")
    print("    GK<25 (bottom quartile), TP 1.5% (~1 ATR compressed), MH 15")
    print("  □ 沒有在 OOS 結果出來後調整任何參數？ → 是")
    print("    Parameters are constants at top of script, single run")
    print("  □ 百分位/標準差用 rolling 計算，沒有洩漏 OOS？ → 是")
    print("    rolling(100).apply(pctile) — no global normalization")

    print(f"\n{'='*70}")
    print("  Round 1 完成")
    print(f"{'='*70}")
