"""
Strategy: Trade Granularity Breakout (TGB) — ETHUSDT 1h
Edge: 壓縮期大單累積（Average Trade Size 上升）→ 突破有 follow-through

原始設計是 OI Fuel Breakout，但 Binance Futures 數據 API（OI、L/S ratio）
只提供最近 ~30 天歷史，無法做 2 年回測。
轉用 klines 內建的 number_of_trades 欄位（從未被任何研究腳本使用）。

Average Trade Size (ATS) = volume / number_of_trades
ATS 升高 = 單筆成交變大 = 大戶/機構在操作
ATS 在壓縮期升高 = 靜悄悄大額建倉 = 突破的「彈藥」

Entry (3 conditions, bar close 確認):
  1. Parkinson HV percentile < 40 (intrabar range compression, previous bar)
  2. ATS 24h change rate percentile > 60 (trade size growing = big player accumulation)
  3. Close > prev 20-bar high (LONG) or Close < prev 20-bar low (SHORT)

Exit:
  SafeNet ±3.5% (25% overshoot slippage)
  EMA20 trail after min hold 12h

Risk: $10K account, 2%/trade, dynamic position sizing
Cost: 0.10% round trip (taker 0.04%×2 + slip 0.01%×2)
Period: 2023-01-01 ~ 2024-12-31 (2 years)
"""
import os, sys, requests, warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import time as _time

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
# CONFIG — parameters chosen BEFORE seeing results
# ══════════════════════════════════════════════════════════════
ACCOUNT = 10000
RISK_PCT = 0.02
SAFENET_PCT = 0.035
FEE_RATE = 0.0010       # 0.10% round trip
MAX_SAME = 2
MIN_HOLD = 12            # bars = hours on 1h

PARK_WIN = 20            # Parkinson HV rolling window
PCTILE_WIN = 100         # percentile rolling window
PARK_THRESH = 40         # compression: Parkinson pctile < 40
ATS_BARS = 24            # ATS change lookback (24h)
ATS_THRESH = 60          # accumulation: ATS change pctile > 60
DONCH_WIN = 20           # Donchian channel window

# Fetch period: extra warmup before 2023-01-01
FETCH_START = int(datetime(2022, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
FETCH_END = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
BT_START = datetime(2023, 1, 1)   # UTC+8 (matches datetime column)
BT_END = datetime(2025, 1, 1)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════
# DATA FETCHING — klines with ALL fields (including trades)
# ══════════════════════════════════════════════════════════════

def fetch_klines(symbol, interval):
    """Fetch OHLCV + trades from Binance Spot API, cached"""
    tag = f"{symbol}_{interval}_{FETCH_START}_{FETCH_END}_full"
    cache = os.path.join(DATA_DIR, f"{tag}.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache)
        print(f"  [Cache] {len(df)} bars: {symbol} {interval}")
        return df

    print(f"  Fetching {symbol} {interval}...", end=" ", flush=True)
    rows = []
    cur = FETCH_START
    while cur < FETCH_END:
        try:
            r = requests.get("https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval,
                        "startTime": cur, "limit": 1000}, timeout=10)
            d = r.json()
            if not d: break
            rows.extend(d)
            cur = d[-1][0] + 1
            if len(d) < 1000: break
            _time.sleep(0.1)
        except Exception as e:
            print(f"\n  Error: {e}")
            break

    if not rows:
        print("FAILED")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=[
        "ot", "open", "high", "low", "close", "volume",
        "ct", "qv", "trades", "tbv", "tbqv", "ig"])
    for c in ["open", "high", "low", "close", "volume", "qv", "tbv", "tbqv"]:
        df[c] = pd.to_numeric(df[c])
    df["trades"] = pd.to_numeric(df["trades"])
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df.to_csv(cache, index=False)
    print(f"{len(df)} bars")
    return df


# ══════════════════════════════════════════════════════════════
# INDICATORS
# ══════════════════════════════════════════════════════════════

def pctile_fn(x):
    """Min-max percentile (consistent with existing scripts)"""
    if x.max() != x.min():
        return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
    return 50


def compute_indicators(df):
    """Compute Parkinson HV, ATS change, Donchian, EMA20"""
    df = df.copy()

    # ── Parkinson Historical Volatility ──
    # σ_P = sqrt(mean(ln(H/L)²) / (4·ln2))
    log_hl_sq = np.log(df["high"] / df["low"]) ** 2
    park_mean = log_hl_sq.rolling(PARK_WIN).mean()
    df["parkinson"] = np.sqrt(park_mean / (4 * np.log(2)))

    df["park_pctile"] = df["parkinson"].rolling(PCTILE_WIN).apply(
        pctile_fn, raw=False)

    # ── Average Trade Size (ATS) = volume / number_of_trades ──
    # Novel metric: measures trade granularity (who is trading)
    df["ats"] = df["volume"] / df["trades"].replace(0, np.nan)

    # ATS 24h change rate (%)
    df["ats_chg"] = df["ats"].pct_change(ATS_BARS) * 100

    # ATS change percentile
    df["ats_chg_pctile"] = df["ats_chg"].rolling(PCTILE_WIN).apply(
        pctile_fn, raw=False)

    # ── Donchian Channel (use PREVIOUS 20 bars, shift(1)) ──
    df["donch_hi"] = df["high"].rolling(DONCH_WIN).max().shift(1)
    df["donch_lo"] = df["low"].rolling(DONCH_WIN).min().shift(1)

    # ── EMA20 for trail exit ──
    df["ema20"] = df["close"].ewm(span=20).mean()

    return df


# ══════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════

def run_backtest(data, park_t=PARK_THRESH, ats_t=ATS_THRESH,
                 donch_w=DONCH_WIN, min_h=MIN_HOLD):
    """Dynamic-sizing backtest. Entry on next bar open."""
    equity = ACCOUNT
    peak = ACCOUNT
    mdd = 0.0

    lpos = []
    spos = []
    trades = []
    fees_total = 0.0

    n = len(data)
    warmup = PCTILE_WIN + ATS_BARS + PARK_WIN + 10

    for i in range(warmup, n - 1):
        row = data.iloc[i]
        nxt = data.iloc[i + 1]
        close = row["close"]
        hi = row["high"]
        lo = row["low"]

        # ── EXITS ──────────────────────────────────────────

        new_lpos = []
        for p in lpos:
            bars = i - p["ei"]
            p["mf"] = max(p.get("mf", 0), (hi - p["entry"]) * p["qty"])

            closed = False
            sn_price = p["entry"] * (1 - SAFENET_PCT)
            if lo <= sn_price:
                overshoot = sn_price - lo
                exit_p = sn_price - overshoot * 0.25
                pnl = (exit_p - p["entry"]) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "long", "type": "SafeNet", "entry": p["entry"],
                    "exit": exit_p, "qty": p["qty"], "notional": p["notional"],
                    "pnl": pnl, "fee": fee, "bars": bars, "mf": p["mf"]})
                equity += pnl; closed = True
            elif bars >= min_h and close <= row["ema20"]:
                exit_p = close
                pnl = (exit_p - p["entry"]) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "long", "type": "Trail", "entry": p["entry"],
                    "exit": exit_p, "qty": p["qty"], "notional": p["notional"],
                    "pnl": pnl, "fee": fee, "bars": bars, "mf": p["mf"]})
                equity += pnl; closed = True

            if not closed:
                new_lpos.append(p)
        lpos = new_lpos

        new_spos = []
        for p in spos:
            bars = i - p["ei"]
            p["mf"] = max(p.get("mf", 0), (p["entry"] - lo) * p["qty"])

            closed = False
            sn_price = p["entry"] * (1 + SAFENET_PCT)
            if hi >= sn_price:
                overshoot = hi - sn_price
                exit_p = sn_price + overshoot * 0.25
                pnl = (p["entry"] - exit_p) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "short", "type": "SafeNet", "entry": p["entry"],
                    "exit": exit_p, "qty": p["qty"], "notional": p["notional"],
                    "pnl": pnl, "fee": fee, "bars": bars, "mf": p["mf"]})
                equity += pnl; closed = True
            elif bars >= min_h and close >= row["ema20"]:
                exit_p = close
                pnl = (p["entry"] - exit_p) * p["qty"]
                fee = p["notional"] * FEE_RATE
                pnl -= fee; fees_total += fee
                trades.append({"entry_dt": p["entry_dt"], "exit_dt": row["datetime"],
                    "side": "short", "type": "Trail", "entry": p["entry"],
                    "exit": exit_p, "qty": p["qty"], "notional": p["notional"],
                    "pnl": pnl, "fee": fee, "bars": bars, "mf": p["mf"]})
                equity += pnl; closed = True

            if not closed:
                new_spos.append(p)
        spos = new_spos

        # Equity tracking
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak * 100)

        # ── ENTRIES (only during BT period) ──────────────────

        if row["datetime"] < BT_START or row["datetime"] >= BT_END:
            continue
        if equity < 100:
            continue

        prev = data.iloc[i - 1]
        park_p = prev["park_pctile"]
        ats_p = row["ats_chg_pctile"]

        if pd.isna(park_p) or pd.isna(ats_p):
            continue

        # Condition 1: Parkinson compression (previous bar)
        if park_p >= park_t:
            continue

        # Condition 2: ATS accumulation (current bar)
        if ats_p <= ats_t:
            continue

        # Condition 3: Donchian breakout
        dh = row["donch_hi"]
        dl = row["donch_lo"]
        if pd.isna(dh) or pd.isna(dl):
            continue

        sig_long = close > dh
        sig_short = close < dl

        if not sig_long and not sig_short:
            continue

        # Dynamic position sizing
        risk_amt = equity * RISK_PCT
        entry_p = nxt["open"]
        notional = risk_amt / SAFENET_PCT
        qty = notional / entry_p

        pos = {"entry": entry_p, "ei": i + 1, "mf": 0,
               "qty": qty, "notional": notional,
               "entry_dt": nxt["datetime"]}

        if sig_long and len(lpos) < MAX_SAME:
            lpos.append(pos.copy())
        if sig_short and len(spos) < MAX_SAME:
            spos.append(pos.copy())

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()
    return tdf, equity, mdd, fees_total


# ══════════════════════════════════════════════════════════════
# STATISTICS OUTPUT
# ══════════════════════════════════════════════════════════════

def print_results(tdf, final_eq, mdd, fees, label="TGB"):
    """Print results in required format"""

    print(f"\n{'═'*60}")
    print(f"  策略名稱：Trade Granularity Breakout ({label})")
    print(f"  回測期間：2023-01-01 ~ 2024-12-31")
    print(f"{'═'*60}")

    if len(tdf) == 0:
        print(f"  *** 零交易 — 條件組合無匹配 ***")
        return None

    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    tdf["exit_dt"] = pd.to_datetime(tdf["exit_dt"])
    bt = tdf[(tdf["entry_dt"] >= BT_START) & (tdf["entry_dt"] < BT_END)].copy()

    if len(bt) == 0:
        print(f"  *** 回測期間內零交易 ***")
        return None

    n = len(bt)
    months = 24
    m_trades = n / months
    total_pnl = bt["pnl"].sum()
    annual_pnl = total_pnl / 2
    wr = (bt["pnl"] > 0).mean() * 100

    w = bt[bt["pnl"] > 0]
    l = bt[bt["pnl"] <= 0]
    pf = w["pnl"].sum() / abs(l["pnl"].sum()) if len(l) > 0 and l["pnl"].sum() != 0 else 999
    avg_w = w["pnl"].mean() if len(w) > 0 else 0
    avg_l = abs(l["pnl"].mean()) if len(l) > 0 else 1
    rr = avg_w / avg_l if avg_l > 0 else 0

    avg_bars = bt["bars"].mean()

    bt["month"] = bt["exit_dt"].dt.to_period("M")
    mp = bt.groupby("month")["pnl"].sum()
    sharpe = mp.mean() / mp.std() * np.sqrt(12) if len(mp) > 1 and mp.std() > 0 else 0

    sn = bt[bt["type"] == "SafeNet"]
    tr = bt[bt["type"] == "Trail"]

    lt12 = bt[bt["bars"] < 12]
    h12_24 = bt[(bt["bars"] >= 12) & (bt["bars"] < 24)]
    gt24 = bt[bt["bars"] >= 24]

    lo_t = bt[bt["side"] == "long"]
    sh_t = bt[bt["side"] == "short"]

    print(f"  總交易筆數：{n} 筆（月均 {m_trades:.1f} 筆）")
    print(f"  勝率：{wr:.1f}%")
    print(f"  盈虧比（PF）：{pf:.2f}")
    print(f"  年化淨利潤：${annual_pnl:+,.0f} USDT")
    print(f"  2年總 PnL：${total_pnl:+,.0f} USDT")
    print(f"  最大回撤（MDD）：-{mdd:.1f}%")
    print(f"  夏普比率：{sharpe:.2f}")
    print(f"  平均持倉時間：{avg_bars:.1f} 小時")
    print(f"  Avg Win/Loss：${avg_w:+.1f} / $-{avg_l:.1f}（RR {rr:.2f}:1）")
    print(f"  手續費+滑點總扣除：-${fees:,.0f} USDT")
    print(f"  SafeNet 觸發：{len(sn)} 次（${sn['pnl'].sum():+,.0f}）")
    print(f"  Trail 出場：{len(tr)} 次（${tr['pnl'].sum():+,.0f}）")

    print(f"{'─'*60}")

    def fmt(subset, label):
        if len(subset) == 0: return f"    {label}：0 筆"
        return (f"    {label}：{len(subset)} 筆  PnL ${subset['pnl'].sum():+,.0f}  "
                f"WR {(subset['pnl']>0).mean()*100:.0f}%")

    print(f"  持倉時間拆解：")
    print(fmt(lt12, "<12h  "))
    print(fmt(h12_24, "12-24h"))
    print(fmt(gt24, "24h+  "))

    print(f"{'─'*60}")
    print(f"  多空拆解：")
    print(fmt(lo_t, "Long "))
    print(fmt(sh_t, "Short"))

    print(f"{'═'*60}")

    # Target check
    t1 = annual_pnl >= 5000
    t2 = m_trades >= 10
    t3 = mdd <= 25

    print(f"\n  目標檢查：")
    print(f"    年淨利 >=$5,000：{'PASS' if t1 else 'FAIL'} (${annual_pnl:+,.0f})")
    print(f"    月均 >=10 筆：   {'PASS' if t2 else 'FAIL'} ({m_trades:.1f})")
    print(f"    MDD <=25%：      {'PASS' if t3 else 'FAIL'} ({mdd:.1f}%)")

    passed = t1 and t2 and t3
    if passed:
        print(f"\n  *** ALL PASS ***")
    else:
        print(f"\n  *** FAIL ***")

    return {"n": n, "pnl": total_pnl, "annual": annual_pnl, "wr": wr,
            "pf": pf, "mdd": mdd, "sharpe": sharpe, "monthly": m_trades,
            "passed": passed, "bt": bt}


# ══════════════════════════════════════════════════════════════
# SENSITIVITY TEST
# ══════════════════════════════════════════════════════════════

def sensitivity_test(data):
    """±20% parameter sensitivity"""
    print(f"\n{'='*70}")
    print(f"  參數敏感度測試（±20%）")
    print(f"{'='*70}")

    params = [
        ("PARK_THRESH", [32, 36, 40, 44, 48]),
        ("ATS_THRESH",  [48, 54, 60, 66, 72]),
        ("DONCH_WIN",   [16, 18, 20, 22, 24]),
        ("MIN_HOLD",    [10, 11, 12, 13, 14]),
    ]

    orig = (PARK_THRESH, ATS_THRESH, DONCH_WIN, MIN_HOLD)

    print(f"\n  {'Param':<15s} {'Value':>6s} {'N':>5s} {'年PnL':>9s} {'WR':>6s} "
          f"{'PF':>6s} {'MDD':>6s} {'月均':>5s}")
    print(f"  {'─'*60}")

    for param_name, values in params:
        for v in values:
            pt, at, dw, mh = orig

            if param_name == "PARK_THRESH": pt = v
            elif param_name == "ATS_THRESH": at = v
            elif param_name == "DONCH_WIN": dw = v
            elif param_name == "MIN_HOLD": mh = v

            # Recompute Donchian if window changed
            if param_name == "DONCH_WIN":
                data["donch_hi"] = data["high"].rolling(v).max().shift(1)
                data["donch_lo"] = data["low"].rolling(v).min().shift(1)

            tdf, eq, mdd_v, fees = run_backtest(data, park_t=pt, ats_t=at,
                                                 donch_w=dw, min_h=mh)
            if len(tdf) > 0:
                tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                bt = tdf[(tdf["entry_dt"] >= BT_START) & (tdf["entry_dt"] < BT_END)]
                n = len(bt)
                pnl = bt["pnl"].sum()
                wr = (bt["pnl"] > 0).mean() * 100 if n > 0 else 0
                ww = bt[bt["pnl"] > 0]; ll = bt[bt["pnl"] <= 0]
                pf = ww["pnl"].sum() / abs(ll["pnl"].sum()) if len(ll) > 0 and ll["pnl"].sum() != 0 else 999
                idx = ["PARK_THRESH", "ATS_THRESH", "DONCH_WIN", "MIN_HOLD"].index(param_name)
                mark = " *" if v == orig[idx] else ""
                print(f"  {param_name:<15s} {v:>6} {n:>5d} ${pnl/2:>+8,.0f} "
                      f"{wr:>5.1f}% {pf:>5.2f} {mdd_v:>5.1f}% {n/24:>5.1f}{mark}")
            else:
                print(f"  {param_name:<15s} {v:>6}   --- no trades ---")

        # Restore Donchian
        if param_name == "DONCH_WIN":
            data["donch_hi"] = data["high"].rolling(orig[2]).max().shift(1)
            data["donch_lo"] = data["low"].rolling(orig[2]).min().shift(1)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  Trade Granularity Breakout (TGB) — ETHUSDT 1h")
    print("  Backtest: 2023-01-01 ~ 2024-12-31")
    print(f"  Novel: Average Trade Size (volume/trades) — never tested before")
    print(f"  Params: ParkHV<{PARK_THRESH}pctile, ATS24h>{ATS_THRESH}pctile, "
          f"Donchian{DONCH_WIN}")
    print(f"  Risk: {RISK_PCT*100:.0f}%/trade, SafeNet ±{SAFENET_PCT*100:.1f}%, "
          f"EMA20 trail (min {MIN_HOLD}h)")
    print(f"  Cost: {FEE_RATE*100:.2f}% round trip")
    print("=" * 70)

    # ── 1. Fetch data ──
    print("\n[1] Fetching data...")
    df = fetch_klines("ETHUSDT", "1h")

    if len(df) == 0:
        print("FATAL: No data"); sys.exit(1)

    # Ensure datetime is proper type (CSV cache stores as string)
    df["datetime"] = pd.to_datetime(df["datetime"])

    # ── 2. Compute indicators ──
    print("\n[2] Computing indicators...")
    df = compute_indicators(df)
    df = df.reset_index(drop=True)

    # Diagnostics
    bt_mask = (pd.to_datetime(df["datetime"]) >= BT_START) & \
              (pd.to_datetime(df["datetime"]) < BT_END)
    bt_data = df[bt_mask]

    print(f"  BT bars: {len(bt_data)}")

    park_below = (bt_data["park_pctile"] < PARK_THRESH).sum()
    ats_above = (bt_data["ats_chg_pctile"] > ATS_THRESH).sum()
    print(f"  Parkinson <{PARK_THRESH}: {park_below} bars "
          f"({park_below/len(bt_data)*100:.1f}%)")
    print(f"  ATS chg >{ATS_THRESH}: {ats_above} bars "
          f"({ats_above/len(bt_data)*100:.1f}%)")

    both = bt_data[(bt_data["park_pctile"] < PARK_THRESH) &
                   (bt_data["ats_chg_pctile"] > ATS_THRESH)]
    print(f"  Both met: {len(both)} bars ({len(both)/len(bt_data)*100:.1f}%)")

    # ATS correlation with volume
    valid = bt_data[bt_data["ats"].notna() & bt_data["volume"].notna()]
    if len(valid) > 0:
        corr = valid["ats"].corr(valid["volume"])
        print(f"  ATS vs Volume correlation: {corr:.3f} "
              f"({'low — good' if abs(corr) < 0.5 else 'moderate' if abs(corr) < 0.7 else 'high — overlap risk'})")

    # Sample ATS data
    sample = bt_data[bt_data["ats"].notna()].iloc[::1000]
    if len(sample) > 0:
        print(f"\n  ATS samples (every 1000th bar):")
        for _, r in sample.head(5).iterrows():
            print(f"    {r['datetime']}  price={r['close']:.2f}  "
                  f"vol={r['volume']:.0f}  trades={r['trades']:.0f}  "
                  f"ATS={r['ats']:.2f}  chg24h={r['ats_chg']:+.1f}%  "
                  f"pctile={r['ats_chg_pctile']:.0f}")

    # ── 3. Run backtest ──
    print(f"\n[3] Running backtest...")
    tdf, final_eq, mdd, fees = run_backtest(df)

    # ── 4. Results ──
    stats = print_results(tdf, final_eq, mdd, fees)

    if stats and stats["passed"]:
        sensitivity_test(df)
    elif stats:
        bt = stats["bt"]
        print(f"\n\n[4] 失敗分析")

        if len(bt) > 0:
            print(f"\n  前 15 筆交易：")
            print(f"  {'Entry Date':<20s} {'Side':>5s} {'Entry':>9s} {'Exit':>9s} "
                  f"{'Bars':>4s} {'PnL':>9s} {'Type':<8s}")
            print(f"  {'─'*65}")
            for _, t in bt.head(15).iterrows():
                print(f"  {str(t['entry_dt'])[:19]:<20s} {t['side']:>5s} "
                      f"${t['entry']:>8,.2f} ${t['exit']:>8,.2f} "
                      f"{t['bars']:>4.0f} ${t['pnl']:>+8.1f} {t['type']:<8s}")

            # Monthly PnL
            bt_m = bt.copy()
            bt_m["month"] = bt_m["exit_dt"].dt.to_period("M")
            mp = bt_m.groupby("month")["pnl"].sum()
            print(f"\n  月度 PnL：")
            for m, p in mp.items():
                bar = "+" * min(int(abs(p) / 20), 30) if p > 0 else "-" * min(int(abs(p) / 20), 30)
                print(f"    {m}: ${p:>+8.1f}  {bar}")

            # ATS diagnostic: winners vs losers
            if len(bt) >= 5:
                print(f"\n  ATS 診斷（贏家 vs 輸家）：")
                # Can't check ATS at entry since we don't store it, but we can check bars
                ww = bt[bt["pnl"] > 0]
                ll = bt[bt["pnl"] <= 0]
                if len(ww) > 0 and len(ll) > 0:
                    print(f"    贏家 avg bars: {ww['bars'].mean():.1f}  "
                          f"avg pnl: ${ww['pnl'].mean():+.1f}")
                    print(f"    輸家 avg bars: {ll['bars'].mean():.1f}  "
                          f"avg pnl: ${ll['pnl'].mean():+.1f}")
    else:
        print(f"\n[4] 零交易。")
        print(f"    可能原因：")
        print(f"    - Parkinson 壓縮 + ATS 上升 + Donchian 突破同時成立太罕見")
        print(f"    - 試試放寬 PARK_THRESH 至 50 或 ATS_THRESH 至 50")
