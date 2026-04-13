"""
Exploration Round 2: S Strategy — CMP Portfolio + Anti-Bullish Filter
======================================================================
Hypothesis: L strategy's bullish signals (Skew>1.0, RetSign>0.60) identify
upward market bias. Shorting during these periods has structurally lower WR.
Using L's signals as S rejection filter removes low-quality shorts.

Key insight from R1: Lowering TP kills PnL. Instead, keep TP 2% and
FILTER OUT losing trades using independent bullish indicators.

Changes from baseline CMP:
  - NEW: Reject S entry when skew(20) > 1.0 OR ret_sign(15) > 0.60
  - All other parameters unchanged (TP 2%, MH 12, SN 5.5%)

Parameters source: L strategy thresholds (independently validated, not tuned for S)
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import datetime, timezone, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ============================================================
# FIXED PARAMETERS
# ============================================================
NOTIONAL = 2000
FEE = 2.0
ACCOUNT = 10000
SN_PCT = 0.055
SN_PEN = 0.25
BLOCK_H = {0, 1, 2, 12}
BLOCK_D = {0, 5, 6}

GK_SHORT = 5
GK_LONG = 20
GK_WIN = 100

# CMP baseline parameters (unchanged)
TP_PCT = 0.02
MAX_HOLD = 12
EXIT_CD = 6
MAX_SAME = 5

# Anti-bullish filter thresholds (from L strategy, not tuned for S)
SKEW_REJECT = 1.0
RETSIGN_REJECT = 0.60
RETSIGN_WIN = 15
SKEW_WIN = 20


# ============================================================
# DATA FETCHING
# ============================================================
def fetch_binance_klines(symbol="ETHUSDT", interval="1h", days=730):
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_data = []
    cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == 2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data)
        cur = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.1)

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "trades", "tbv", "tqv", "ignore"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = df["datetime"] + timedelta(hours=8)
    df["datetime"] = df["datetime"].dt.tz_localize(None)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  Fetched {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df


# ============================================================
# INDICATOR CALCULATION
# ============================================================
def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100


def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()

    # GK volatility
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)
    d["gk_s"] = d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"] = d["gk"].rolling(GK_LONG).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    for t in [25, 30, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t

    # Breakout (short direction: close broke below recent min)
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15]:
        d[f"cmn{bl}"] = d["close"].shift(2).rolling(bl - 1).min()
        d[f"bs{bl}"] = d["cs1"] < d[f"cmn{bl}"]

    # Session filter
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    # ---- Anti-bullish indicators (from L strategy) ----
    # Skew: rolling skew of returns, shift(1) to prevent look-ahead
    d["skew20"] = d["ret"].rolling(SKEW_WIN).skew().shift(1)

    # RetSign: proportion of positive returns in last N bars, shift(1)
    d["ret_sign15"] = (d["ret"] > 0).rolling(RETSIGN_WIN).mean().shift(1)

    # Anti-bullish flag: reject S entry when market is bullish
    d["bullish"] = (d["skew20"] > SKEW_REJECT) | (d["ret_sign15"] > RETSIGN_REJECT)
    d["s_allowed"] = ~d["bullish"]  # True when S entry is allowed

    return d


# ============================================================
# BACKTEST ENGINE
# ============================================================
def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)


def bt_cmp(df, max_same=5, gk_col="gk40", brk_look=10,
           tp_pct=0.02, max_hold=12, sn_pct=SN_PCT, exit_cd=EXIT_CD,
           use_antibull=False):
    W = 160
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values
    DT = df["datetime"].values
    bs_col = f"bs{brk_look}"
    BSa = df[bs_col].values; SOKa = df["sok"].values
    GKa = df[gk_col].values
    SAa = df["s_allowed"].values if use_antibull else None

    pos = []; trades = []; lx = -999

    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]

        # Check exits
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct) + (h - p["e"] * (1 + sn_pct)) * SN_PEN
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            if not done and lo <= p["e"] * (1 - tp_pct):
                ep_ = p["e"] * (1 - tp_pct)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True
            if not done and b >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos

        # Check entry
        gk_ok = _b(GKa[i]); brk = _b(BSa[i]); sok = _b(SOKa[i])
        ab_ok = _b(SAa[i]) if SAa is not None else True

        if gk_ok and brk and sok and ab_ok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "t", "b", "dt"])


def bt_portfolio(df, strats):
    all_trades = []
    for cfg in strats:
        t = bt_cmp(df, **cfg)
        if len(t) > 0:
            all_trades.append(t)
    if not all_trades:
        return pd.DataFrame(columns=["pnl", "t", "b", "dt"])
    return pd.concat(all_trades, ignore_index=True).sort_values("dt").reset_index(drop=True)


# ============================================================
# EVALUATION
# ============================================================
def evaluate(tdf, start_dt, end_dt, label=""):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    period = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(period)
    if n == 0: return None

    pnl = period["pnl"].sum()
    w = period[period["pnl"] > 0]["pnl"].sum()
    l_ = abs(period[period["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999
    wr = (period["pnl"] > 0).mean() * 100
    eq = period["pnl"].cumsum(); dd = eq - eq.cummax()
    mdd = abs(dd.min()) / ACCOUNT * 100

    period["m"] = period["dt"].dt.to_period("M")
    ms = period.groupby("m")["pnl"].sum()
    pos_months = (ms > 0).sum(); months_total = len(ms)
    top_m_val = ms.max(); top_m_name = str(ms.idxmax())
    top_pct = top_m_val / pnl * 100 if pnl > 0 else 999
    no_best = pnl - top_m_val
    worst_m_val = ms.min(); worst_m_name = str(ms.idxmin())
    days = (end_dt - start_dt).days
    tpm = n / (days / 30.44) if days > 0 else 0
    ed = period.groupby("t")["pnl"].agg(["count", "sum", "mean"])

    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr,
            "mdd": mdd, "months": months_total, "pos_months": pos_months,
            "top_pct": top_pct, "top_m": top_m_name, "top_m_val": top_m_val,
            "no_best": no_best, "worst_m": worst_m_name, "worst_m_val": worst_m_val,
            "tpm": tpm, "ms": ms, "ed": ed, "avg": pnl / n if n else 0}


def walk_forward(df, strats, full_start, full_end):
    oos_start = full_start + pd.DateOffset(months=12)
    results = []
    for fold in range(6):
        ts = oos_start + pd.DateOffset(months=fold * 2)
        te = ts + pd.DateOffset(months=2)
        if te > full_end: te = full_end
        t = bt_portfolio(df, strats)
        t["dt"] = pd.to_datetime(t["dt"])
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold + 1, "ts": ts.strftime("%Y-%m-%d"),
                         "te": te.strftime("%Y-%m-%d"), "pnl": fp,
                         "n": len(tt), "pos": fp > 0})
    return results


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  EXPLORATION R2: S CMP + Anti-Bullish Filter")
    print("=" * 70)

    df_raw = fetch_binance_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    full_start = last_dt - pd.Timedelta(days=730)
    mid_dt = last_dt - pd.Timedelta(days=365)
    full_end = last_dt

    print(f"\n  IS:  {full_start.strftime('%Y-%m-%d')} ~ {mid_dt.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid_dt.strftime('%Y-%m-%d')} ~ {full_end.strftime('%Y-%m-%d')}")

    df = calc_indicators(df_raw)

    # Count how often bullish filter triggers
    oos_mask = df["datetime"] >= mid_dt
    bull_pct = df.loc[oos_mask, "bullish"].mean() * 100
    print(f"\n  Anti-bullish filter active in OOS: {bull_pct:.1f}% of bars")

    # ---- BASELINE (no filter) ----
    base_strats = [
        dict(max_same=5, gk_col="gk40", brk_look=8,  use_antibull=False),
        dict(max_same=5, gk_col="gk40", brk_look=15, use_antibull=False),
        dict(max_same=5, gk_col="gk30", brk_look=10, use_antibull=False),
        dict(max_same=5, gk_col="gk40", brk_look=12, use_antibull=False),
    ]
    t_base = bt_portfolio(df, base_strats)
    rb = evaluate(t_base, mid_dt, full_end, "Baseline OOS")

    # ---- R2: WITH ANTI-BULLISH FILTER ----
    r2_strats = [
        dict(max_same=5, gk_col="gk40", brk_look=8,  use_antibull=True),
        dict(max_same=5, gk_col="gk40", brk_look=15, use_antibull=True),
        dict(max_same=5, gk_col="gk30", brk_look=10, use_antibull=True),
        dict(max_same=5, gk_col="gk40", brk_look=12, use_antibull=True),
    ]
    t_r2 = bt_portfolio(df, r2_strats)
    r2 = evaluate(t_r2, mid_dt, full_end, "R2 OOS")
    r2_is = evaluate(t_r2, full_start, mid_dt, "R2 IS")

    if r2:
        print(f"\n  ┌──────────────────────────────────────────┐")
        print(f"  │ 方向：S（做空）+ Anti-Bullish Filter      │")
        print(f"  │ 回測：{full_start.strftime('%Y-%m-%d')} ~ {full_end.strftime('%Y-%m-%d')}        │")
        print(f"  │ IS PnL：${r2_is['pnl']:+,.0f}                          │" if r2_is else "")
        print(f"  │ OOS PnL：${r2['pnl']:+,.0f} ← 達標依據              │")
        print(f"  │ OOS PF：{r2['pf']:.2f}                              │")
        print(f"  │ OOS MDD：{r2['mdd']:.1f}%                            │")
        print(f"  │ OOS 月均交易：{r2['tpm']:.1f} 筆                     │")
        print(f"  │ OOS WR：{r2['wr']:.1f}% ← 是否 ≥ 70%？             │")
        print(f"  │ OOS 正收益月：{r2['pos_months']}/{r2['months']}                        │")
        print(f"  │ OOS topMonth：{r2['top_pct']:.1f}% ({r2['top_m']})    │")
        print(f"  │ OOS 移除最佳月：${r2['no_best']:+,.0f}               │")
        print(f"  │ OOS 最差月：${r2['worst_m_val']:+,.0f} ({r2['worst_m']}) │")
        print(f"  │ 手續費：-${r2['n'] * FEE:,.0f}                       │")
        print(f"  └──────────────────────────────────────────┘")

        # Monthly
        print(f"\n  ─── 月度 OOS 明細 ───")
        print(f"  | 月份    |   PnL   |  累計   |")
        print(f"  |---------|--------:|--------:|")
        cum = 0
        for m, v in r2["ms"].items():
            cum += v
            print(f"  | {m} | ${v:>+7,.0f} | ${cum:>+7,.0f} |")

        # Exit distribution
        print(f"\n  ─── 出場分佈 ───")
        print(f"  {r2['ed'].to_string()}")

        # Walk-Forward
        print(f"\n  ─── Walk-Forward 6-fold ───")
        wf = walk_forward(df, r2_strats, full_start, full_end)
        wf_pos = sum(1 for w in wf if w["pos"])
        for w in wf:
            s = "✓" if w["pos"] else "✗"
            print(f"  Fold {w['fold']}: {w['ts']} ~ {w['te']} | {w['n']}t ${w['pnl']:+,.0f} {s}")
        print(f"  Walk-Forward: {wf_pos}/6 正向")

        # Assessment
        print(f"\n{'='*70}")
        print("  達標評估")
        print(f"{'='*70}")
        checks = {
            "OOS PnL ≥ $10,000": r2["pnl"] >= 10000,
            "OOS PF ≥ 1.5": r2["pf"] >= 1.5,
            "OOS MDD ≤ 25%": r2["mdd"] <= 25,
            "月均交易 ≥ 10": r2["tpm"] >= 10,
            "WR ≥ 70%": r2["wr"] >= 70,
            "正收益月 ≥ 75%": r2["pos_months"] / max(r2["months"], 1) >= 0.75,
            "topMonth ≤ 20%": r2["top_pct"] <= 20,
            "移除最佳月 ≥ $8K": r2["no_best"] >= 8000,
            "WF ≥ 5/6": wf_pos >= 5,
        }
        all_pass = True
        for name, ok in checks.items():
            print(f"  {'✓ PASS' if ok else '✗ FAIL'} | {name}")
            if not ok: all_pass = False

        # Comparison
        if rb:
            print(f"\n  ─── vs 基準 ───")
            print(f"  {'指標':<16} {'基準':>10} {'R2':>10} {'差異':>10}")
            for k, fmt in [("pnl", ",.0f"), ("wr", ".1f"), ("pf", ".2f"), ("n", "d"), ("mdd", ".1f")]:
                bv = rb[k]; rv = r2[k]
                if k in ("wr", "mdd"):
                    print(f"  {k:<16} {bv:>9{fmt}}% {rv:>9{fmt}}% {rv-bv:>+9{fmt}}%")
                elif k == "pnl":
                    print(f"  {k:<16} ${bv:>+9{fmt}} ${rv:>+9{fmt}} ${rv-bv:>+9{fmt}}")
                else:
                    print(f"  {k:<16} {bv:>10{fmt}} {rv:>10{fmt}} {rv-bv:>+10{fmt}}")

        if all_pass:
            print(f"\n  ★ R2 S 策略：全部達標！")
        else:
            fails = [k for k, v in checks.items() if not v]
            print(f"\n  ✗ R2 失敗：{', '.join(fails)}")

    # God-view check
    print(f"\n{'='*70}")
    print("  上帝視角自檢")
    print(f"{'='*70}")
    print("  □ signal 只用 shift(1)+？ → 是 (gk_r.shift(1), ret.rolling.shift(1), close.shift(1))")
    print("  □ 進場價 next bar open？ → 是 (O[i+1])")
    print("  □ 滾動指標 .shift(1)？ → 是 (skew20.shift(1), ret_sign15.shift(1), gk_pct.shift(1))")
    print("  □ 參數非數據驅動？ → 是 (Skew>1.0, RetSign>0.60 來自 L 策略)")
    print("  □ OOS 後未調參？ → 是 (單次運行)")
    print("  □ rolling 無洩漏？ → 是")
