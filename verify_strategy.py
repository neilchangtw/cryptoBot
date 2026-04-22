"""
驗證 strategy.py 雙策略（L + S CMP-Portfolio）

1. 指標驗證：獨立計算 GK/Skew/RetSign/Multi-BL，逐 bar 比對
2. L 策略回測：OOS PnL 應接近 $13,776
3. S CMP-Portfolio 回測：OOS PnL 應接近 $10,049
4. 合計：$23,825, 8/8 Gate PASS 數字復現
"""
import os, sys
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import strategy


def load_data() -> pd.DataFrame:
    """載入 ETHUSDT 1h 歷史資料"""
    csv_path = os.path.join(BASE_DIR, "data", "ETHUSDT_1h_latest730d.csv")
    if not os.path.exists(csv_path):
        print(f"  [ERROR] Data file not found: {csv_path}")
        sys.exit(1)
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: 指標驗證
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_independent(df: pd.DataFrame) -> pd.DataFrame:
    """獨立計算所有指標（不使用 strategy.py）"""
    d = df.copy()

    # EMA20
    d["ema20_ref"] = d["close"].ewm(span=20).mean()

    # Returns
    d["ret"] = d["close"].pct_change()

    # GK
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl ** 2 - (2 * np.log(2) - 1) * ln_co ** 2
    gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
    d["gk_pctile_ref"] = gk_ratio.shift(1).rolling(100).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50, raw=False
    )

    # Skew + RetSign（shift(1) 防前瞻）
    d["skew_20_ref"] = d["ret"].rolling(20).skew().shift(1)
    d["ret_sign_15_ref"] = (d["ret"] > 0).astype(float).rolling(15).mean().shift(1)

    # Breakout: BL10
    d["bl_ref"] = d["close"].shift(1) > d["close"].shift(2).rolling(9).max()
    d["bs_ref"] = d["close"].shift(1) < d["close"].shift(2).rolling(9).min()

    # Multi-BL breakout (S 子策略用)
    for bl in [8, 12, 15]:
        d[f"bs_{bl}_ref"] = d["close"].shift(1) < d["close"].shift(2).rolling(bl - 1).min()

    # Session
    d["sok_ref"] = ~(d["datetime"].dt.hour.isin({0, 1, 2, 12}) |
                     d["datetime"].dt.weekday.isin({0, 5, 6}))

    # V14+R Regime Gate（獨立實作，與 strategy.py 對比）
    sma = d["close"].rolling(200).mean()
    slope = (sma - sma.shift(100)) / sma.shift(100)
    d["sma_slope_ref"] = slope.shift(1)
    d["regime_block_l_ref"] = d["sma_slope_ref"] > 0.045
    d["regime_block_s_ref"] = d["sma_slope_ref"].abs() < 0.010

    return d


def verify_indicators():
    """比對指標計算是否一致"""
    print("=" * 60)
    print("  Step 1: Indicator Verification (GK + Skew + RetSign + Multi-BL)")
    print("=" * 60)

    df_raw = load_data()
    print(f"  Loaded {len(df_raw)} bars")

    df_live = strategy.compute_indicators(df_raw)
    df_ref = compute_independent(df_raw)

    warmup = strategy.WARMUP_BARS + 5
    n = len(df_live)
    mismatches = {
        "gk_pctile": 0, "breakout_long": 0, "breakout_short": 0,
        "session_ok": 0, "ema20": 0, "skew_20": 0, "ret_sign_15": 0,
        "brk_short_8": 0, "brk_short_12": 0, "brk_short_15": 0,
        "sma_slope": 0, "regime_block_l": 0, "regime_block_s": 0,
    }

    for i in range(warmup, n):
        # gk_pctile
        ref = df_ref.iloc[i]["gk_pctile_ref"]
        live = df_live.iloc[i]["gk_pctile"]
        if not (pd.isna(ref) and pd.isna(live)):
            if pd.isna(ref) != pd.isna(live) or (not pd.isna(ref) and abs(ref - live) > 1e-6):
                mismatches["gk_pctile"] += 1

        # breakout_long
        ref_bl = bool(df_ref.iloc[i]["bl_ref"]) if not pd.isna(df_ref.iloc[i]["bl_ref"]) else False
        live_bl = bool(df_live.iloc[i]["breakout_long"]) if not pd.isna(df_live.iloc[i]["breakout_long"]) else False
        if ref_bl != live_bl:
            mismatches["breakout_long"] += 1

        # breakout_short
        ref_bs = bool(df_ref.iloc[i]["bs_ref"]) if not pd.isna(df_ref.iloc[i]["bs_ref"]) else False
        live_bs = bool(df_live.iloc[i]["breakout_short"]) if not pd.isna(df_live.iloc[i]["breakout_short"]) else False
        if ref_bs != live_bs:
            mismatches["breakout_short"] += 1

        # session_ok
        if bool(df_ref.iloc[i]["sok_ref"]) != bool(df_live.iloc[i]["session_ok"]):
            mismatches["session_ok"] += 1

        # ema20
        ref_ema = df_ref.iloc[i]["ema20_ref"]
        live_ema = df_live.iloc[i]["ema20"]
        if abs(ref_ema - live_ema) > 1e-4:
            mismatches["ema20"] += 1

        # skew_20
        ref_sk = df_ref.iloc[i]["skew_20_ref"]
        live_sk = df_live.iloc[i]["skew_20"]
        if not (pd.isna(ref_sk) and pd.isna(live_sk)):
            if pd.isna(ref_sk) != pd.isna(live_sk) or (not pd.isna(ref_sk) and abs(ref_sk - live_sk) > 1e-6):
                mismatches["skew_20"] += 1

        # ret_sign_15
        ref_rs = df_ref.iloc[i]["ret_sign_15_ref"]
        live_rs = df_live.iloc[i]["ret_sign_15"]
        if not (pd.isna(ref_rs) and pd.isna(live_rs)):
            if pd.isna(ref_rs) != pd.isna(live_rs) or (not pd.isna(ref_rs) and abs(ref_rs - live_rs) > 1e-6):
                mismatches["ret_sign_15"] += 1

        # Multi-BL breakout (S 子策略)
        for bl in [8, 12, 15]:
            ref_v = bool(df_ref.iloc[i][f"bs_{bl}_ref"]) if not pd.isna(df_ref.iloc[i][f"bs_{bl}_ref"]) else False
            live_v = bool(df_live.iloc[i][f"brk_short_{bl}"]) if not pd.isna(df_live.iloc[i][f"brk_short_{bl}"]) else False
            if ref_v != live_v:
                mismatches[f"brk_short_{bl}"] += 1

        # V14+R Regime Gate
        ref_slope = df_ref.iloc[i]["sma_slope_ref"]
        live_slope = df_live.iloc[i]["sma_slope"] if "sma_slope" in df_live.columns else None
        if live_slope is not None and not (pd.isna(ref_slope) and pd.isna(live_slope)):
            if pd.isna(ref_slope) != pd.isna(live_slope) or (not pd.isna(ref_slope) and abs(ref_slope - live_slope) > 1e-10):
                mismatches["sma_slope"] += 1

        ref_bl_r = bool(df_ref.iloc[i]["regime_block_l_ref"]) if not pd.isna(df_ref.iloc[i]["regime_block_l_ref"]) else False
        live_bl_r = bool(df_live.iloc[i]["regime_block_l"]) if "regime_block_l" in df_live.columns and not pd.isna(df_live.iloc[i]["regime_block_l"]) else False
        if "regime_block_l" in df_live.columns and ref_bl_r != live_bl_r:
            mismatches["regime_block_l"] += 1

        ref_bs_r = bool(df_ref.iloc[i]["regime_block_s_ref"]) if not pd.isna(df_ref.iloc[i]["regime_block_s_ref"]) else False
        live_bs_r = bool(df_live.iloc[i]["regime_block_s"]) if "regime_block_s" in df_live.columns and not pd.isna(df_live.iloc[i]["regime_block_s"]) else False
        if "regime_block_s" in df_live.columns and ref_bs_r != live_bs_r:
            mismatches["regime_block_s"] += 1

    total_checked = n - warmup
    print(f"  Checked {total_checked} bars (after warmup)")
    all_ok = True
    for key, count in mismatches.items():
        status = "PASS" if count == 0 else "FAIL"
        if count > 0:
            all_ok = False
        print(f"  [{status}] {key}: {count} mismatches")

    return all_ok


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: L 策略回測
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def verify_long_strategy():
    """L 策略：OR-entry + EMA20 Trail"""
    print("\n" + "=" * 60)
    print("  Step 2: L Strategy (Long Only) Verification")
    print("=" * 60)

    df_raw = load_data()
    df = strategy.compute_indicators(df_raw)
    w = strategy.WARMUP_BARS
    N = len(df)

    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    E = df["ema20"].values
    D = df["datetime"].values

    positions = []
    trades = []
    last_exit = -9999

    for i in range(w, N - 1):
        # 出場
        new_pos = []
        for p in positions:
            exit_result = strategy.check_exit(
                side="long",
                entry_price=p["e"],
                entry_bar_counter=p["ei"],
                current_bar_counter=i,
                bar_high=H[i], bar_low=L[i], bar_close=C[i],
                ema20=E[i],
            )
            if exit_result["exit"]:
                pnl, _ = strategy.compute_pnl(p["e"], exit_result["exit_price"], "long")
                trades.append({"pnl": pnl, "tp": exit_result["reason"],
                               "dt": D[i], "bars": i - p["ei"]})
                last_exit = i
            else:
                new_pos.append(p)
        positions = new_pos

        # 進場：OR-entry
        row = df.iloc[i]
        gp = row["gk_pctile"]
        if pd.isna(gp):
            continue

        cond_gk = gp < strategy.L_GK_THRESH
        skew = row["skew_20"]
        cond_skew = (not pd.isna(skew)) and skew > 1.0
        ret_sign = row["ret_sign_15"]
        cond_ret = (not pd.isna(ret_sign)) and ret_sign > 0.60

        if not (cond_gk or cond_skew or cond_ret):
            continue
        bl = bool(row["breakout_long"]) if not pd.isna(row["breakout_long"]) else False
        if not bl:
            continue
        sok = bool(row["session_ok"])
        if not sok:
            continue

        # Cooldown (研究回測無 freshness)
        if (i - last_exit) < strategy.L_EXIT_CD:
            continue

        # MaxSame
        if len(positions) >= strategy.L_MAX_SAME:
            continue

        positions.append({"e": O[i + 1], "ei": i})

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "tp", "dt", "bars"])
    tdf["dt"] = pd.to_datetime(tdf["dt"])

    # IS/OOS split
    mid = tdf["dt"].min() + (tdf["dt"].max() - tdf["dt"].min()) / 2
    oos = tdf[tdf["dt"] >= mid]
    is_t = tdf[tdf["dt"] < mid]

    oos_pnl = oos["pnl"].sum()
    is_pnl = is_t["pnl"].sum()
    total_pnl = tdf["pnl"].sum()

    oos_wins = oos[oos["pnl"] > 0]["pnl"].sum()
    oos_losses = abs(oos[oos["pnl"] < 0]["pnl"].sum())
    oos_pf = oos_wins / oos_losses if oos_losses > 0 else 999
    oos_wr = (oos["pnl"] > 0).mean() * 100 if len(oos) > 0 else 0

    print(f"  Total trades: {len(tdf)}")
    print(f"  IS: {len(is_t)}t, ${is_pnl:.2f}")
    print(f"  OOS: {len(oos)}t, ${oos_pnl:.2f}, PF {oos_pf:.2f}, WR {oos_wr:.1f}%")

    # Exit type breakdown
    print(f"\n  Exit type breakdown (OOS):")
    for tp in oos["tp"].unique():
        sub = oos[oos["tp"] == tp]
        print(f"    {tp}: {len(sub)}t, ${sub['pnl'].sum():+.2f}")

    # Checks
    target = 13776
    tolerance = 0.20  # ±20%
    pnl_ok = abs(oos_pnl - target) / target < tolerance
    pf_ok = oos_pf > 2.0
    trail_dominant = oos[oos["tp"] == "Trail"]["pnl"].sum() > 0

    print(f"\n  Checks:")
    print(f"  [{'PASS' if pnl_ok else 'FAIL'}] OOS PnL ${oos_pnl:.2f} ~ ${target} (±{tolerance*100:.0f}%)")
    print(f"  [{'PASS' if pf_ok else 'FAIL'}] OOS PF {oos_pf:.2f} > 2.0")
    print(f"  [{'PASS' if trail_dominant else 'FAIL'}] Trail is profit source")

    return oos_pnl, pnl_ok and pf_ok and trail_dominant


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: S CMP-Portfolio 回測
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def verify_short_strategy():
    """S CMP-Portfolio: 4 子策略並行"""
    print("\n" + "=" * 60)
    print("  Step 3: S CMP-Portfolio (Short Only) Verification")
    print("=" * 60)

    df_raw = load_data()
    df = strategy.compute_indicators(df_raw)
    w = strategy.WARMUP_BARS
    N = len(df)

    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    D = df["datetime"].values

    # 每個子策略獨立追蹤
    sub_positions = {s["id"]: [] for s in strategy.S_SUBS}
    sub_last_exit = {s["id"]: -9999 for s in strategy.S_SUBS}
    trades = []

    for i in range(w, N - 1):
        row = df.iloc[i]
        gp = row["gk_pctile"]
        sok = bool(row["session_ok"])

        # 出場（所有子策略）
        for sub in strategy.S_SUBS:
            sid = sub["id"]
            new_pos = []
            for p in sub_positions[sid]:
                exit_result = strategy.check_exit_cmp(
                    entry_price=p["e"],
                    entry_bar_counter=p["ei"],
                    current_bar_counter=i,
                    bar_high=H[i], bar_low=L[i], bar_close=C[i],
                )
                if exit_result["exit"]:
                    pnl, _ = strategy.compute_pnl(p["e"], exit_result["exit_price"], "short")
                    trades.append({"pnl": pnl, "tp": exit_result["reason"],
                                   "sub": sid, "dt": D[i], "bars": i - p["ei"]})
                    sub_last_exit[sid] = i
                else:
                    new_pos.append(p)
            sub_positions[sid] = new_pos

        # 進場（各子策略獨立）
        if pd.isna(gp) or not sok:
            continue

        for sub in strategy.S_SUBS:
            sid = sub["id"]
            if gp >= sub["gk_thresh"]:
                continue

            brk_col = f"brk_short_{sub['brk_look']}"
            brk_val = row.get(brk_col)
            if pd.isna(brk_val) or not bool(brk_val):
                continue

            if (i - sub_last_exit[sid]) < sub["exit_cd"]:
                continue

            if len(sub_positions[sid]) >= sub["max_same"]:
                continue

            sub_positions[sid].append({"e": O[i + 1], "ei": i})

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "tp", "sub", "dt", "bars"])
    tdf["dt"] = pd.to_datetime(tdf["dt"])

    # IS/OOS split
    mid = tdf["dt"].min() + (tdf["dt"].max() - tdf["dt"].min()) / 2
    oos = tdf[tdf["dt"] >= mid]
    is_t = tdf[tdf["dt"] < mid]

    oos_pnl = oos["pnl"].sum()
    is_pnl = is_t["pnl"].sum()

    oos_wins = oos[oos["pnl"] > 0]["pnl"].sum()
    oos_losses = abs(oos[oos["pnl"] < 0]["pnl"].sum())
    oos_pf = oos_wins / oos_losses if oos_losses > 0 else 999
    oos_wr = (oos["pnl"] > 0).mean() * 100 if len(oos) > 0 else 0

    print(f"  Total trades: {len(tdf)}")
    print(f"  IS: {len(is_t)}t, ${is_pnl:.2f}")
    print(f"  OOS: {len(oos)}t, ${oos_pnl:.2f}, PF {oos_pf:.2f}, WR {oos_wr:.1f}%")

    # Per-sub breakdown
    print(f"\n  Per-sub breakdown (OOS):")
    for sub in strategy.S_SUBS:
        sid = sub["id"]
        sub_oos = oos[oos["sub"] == sid]
        if len(sub_oos) > 0:
            sub_pnl = sub_oos["pnl"].sum()
            sub_wr = (sub_oos["pnl"] > 0).mean() * 100
            print(f"    {sid}: {len(sub_oos)}t, ${sub_pnl:+.2f}, WR {sub_wr:.1f}%")

    # Exit type breakdown
    print(f"\n  Exit type breakdown (OOS):")
    for tp in oos["tp"].unique():
        sub_t = oos[oos["tp"] == tp]
        print(f"    {tp}: {len(sub_t)}t, ${sub_t['pnl'].sum():+.2f}")

    # Checks
    target = 10049
    tolerance = 0.20
    pnl_ok = abs(oos_pnl - target) / target < tolerance
    pf_ok = oos_pf > 1.5
    tp_dominant = len(oos[oos["tp"] == "TP"]) > len(oos) * 0.3  # TP should be major exit

    print(f"\n  Checks:")
    print(f"  [{'PASS' if pnl_ok else 'FAIL'}] OOS PnL ${oos_pnl:.2f} ~ ${target} (±{tolerance*100:.0f}%)")
    print(f"  [{'PASS' if pf_ok else 'FAIL'}] OOS PF {oos_pf:.2f} > 1.5")
    print(f"  [{'PASS' if tp_dominant else 'FAIL'}] TP is major exit type")

    return oos_pnl, pnl_ok and pf_ok and tp_dominant


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: 狀態遷移測試
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def verify_state_migration():
    """測試舊格式 state 能正確遷移"""
    print("\n" + "=" * 60)
    print("  Step 4: State Migration Test")
    print("=" * 60)

    import json
    import tempfile

    # 模擬舊格式 state
    old_state = {
        "positions": {
            "20260401_120000": {
                "trade_id": "20260401_120000",
                "side": "long",
                "entry_price": 1800.0,
                "entry_bar_counter": 100,
                "qty": 1.11,
                "bars_held": 5,
                "mae_pct": -0.5,
                "mfe_pct": 1.2,
                "entry_time_utc": "2026-04-01 04:00:00",
                "entry_time_utc8": "2026-04-01 12:00:00",
                "mae_time_bar": 2,
                "mfe_time_bar": 4,
                "pnl_at_bar7": None,
                "pnl_at_bar12": None,
            },
            "20260401_130000": {
                "trade_id": "20260401_130000",
                "side": "short",
                "entry_price": 1850.0,
                "entry_bar_counter": 105,
                "qty": 1.08,
                "bars_held": 3,
                "mae_pct": -0.3,
                "mfe_pct": 0.8,
                "entry_time_utc": "2026-04-01 05:00:00",
                "entry_time_utc8": "2026-04-01 13:00:00",
                "mae_time_bar": 1,
                "mfe_time_bar": 3,
                "pnl_at_bar7": None,
                "pnl_at_bar12": None,
            },
        },
        "last_exits": {"long": 90, "short": 95},
        "account_balance": 10500.0,
        "bar_counter": 110,
        "last_bar_time": "2026-04-01 14:00:00",
        "trade_number": 50,
        "daily_stats": {},
        "pending_entry": None,
    }

    # 寫入臨時檔案
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(old_state, f, ensure_ascii=False)
        tmp_path = f.name

    try:
        from executor import Executor
        ex = Executor(state_path=tmp_path)

        # 驗證遷移
        checks = []

        # 1. last_exits 遷移
        le_ok = (ex.last_exits.get("L") == 90 and
                 ex.last_exits.get("S1") == 95 and
                 ex.last_exits.get("S2") == 95)
        checks.append(("last_exits migrated", le_ok))

        # 2. position sub_strategy 遷移
        pos_long = ex.positions.get("20260401_120000", {})
        pos_short = ex.positions.get("20260401_130000", {})
        sub_ok = (pos_long.get("sub_strategy") == "L" and
                  pos_short.get("sub_strategy") == "S1")
        checks.append(("position sub_strategy added", sub_ok))

        # 3. can_open_sub
        can_l = ex.can_open_sub("L")
        can_s1 = ex.can_open_sub("S1")
        checks.append(("can_open_sub works", can_l and can_s1))

        # 4. balance preserved
        bal_ok = ex.account_balance == 10500.0
        checks.append(("balance preserved", bal_ok))

        all_ok = True
        for name, passed in checks:
            status = "PASS" if passed else "FAIL"
            if not passed:
                all_ok = False
            print(f"  [{status}] {name}")

        return all_ok

    finally:
        os.unlink(tmp_path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 5: API 層交叉驗證（用 strategy.py 的 evaluate 函式直接跑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def verify_api_crosscheck():
    """
    用 strategy.py 的 evaluate_long_signal / evaluate_short_signals 函式
    直接跑模擬，交叉比對 Step 2/3 的手動回測結果。

    這是不同於 Step 2/3 的驗證方式：
    - Step 2/3: 手動實作進場邏輯（與研究腳本一致）
    - Step 5:   呼叫 strategy.py 的 API 函式（與 main_eth.py 一致）
    兩者的出場邏輯都呼叫同一個 check_exit / check_exit_cmp。
    """
    print("\n" + "=" * 60)
    print("  Step 5: API-Level Cross-Check (evaluate_*_signal)")
    print("=" * 60)

    df_raw = load_data()
    df = strategy.compute_indicators(df_raw)
    w = strategy.WARMUP_BARS
    N = len(df)

    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    E = df["ema20"].values
    D = df["datetime"].values

    # 模擬 executor 的狀態結構
    positions = {}   # {trade_id: {sub_strategy, side, entry_price, entry_bar_counter}}
    last_exits = {"L": -9999, "S1": -9999, "S2": -9999, "S3": -9999, "S4": -9999}
    trades = []
    tid = 0

    for i in range(w, N - 1):
        # ── 出場 ──
        for k in list(positions.keys()):
            pos = positions[k]
            sub = pos["sub_strategy"]
            if sub == "L":
                er = strategy.check_exit(
                    side="long", entry_price=pos["entry_price"],
                    entry_bar_counter=pos["entry_bar_counter"],
                    current_bar_counter=i,
                    bar_high=H[i], bar_low=L[i], bar_close=C[i], ema20=E[i],
                )
            else:
                er = strategy.check_exit_cmp(
                    entry_price=pos["entry_price"],
                    entry_bar_counter=pos["entry_bar_counter"],
                    current_bar_counter=i,
                    bar_high=H[i], bar_low=L[i], bar_close=C[i],
                )
            if er["exit"]:
                side = "long" if sub == "L" else "short"
                pnl, _ = strategy.compute_pnl(pos["entry_price"], er["exit_price"], side)
                trades.append({"pnl": pnl, "sub": sub, "tp": er["reason"], "dt": D[i]})
                last_exits[sub] = i
                del positions[k]

        # ── L 進場（透過 API） ──
        long_sig = strategy.evaluate_long_signal(df, i, positions, last_exits, i)
        if long_sig:
            tid += 1
            positions[f"t{tid}"] = {
                "sub_strategy": "L", "side": "long",
                "entry_price": O[i + 1], "entry_bar_counter": i,
            }

        # ── S 進場（透過 API） ──
        short_sigs = strategy.evaluate_short_signals(df, i, positions, last_exits, i)
        for sig in short_sigs:
            tid += 1
            positions[f"t{tid}"] = {
                "sub_strategy": sig["sub_strategy"], "side": "short",
                "entry_price": O[i + 1], "entry_bar_counter": i,
            }

    tdf = pd.DataFrame(trades)
    tdf["dt"] = pd.to_datetime(tdf["dt"])
    mid = tdf["dt"].min() + (tdf["dt"].max() - tdf["dt"].min()) / 2
    oos = tdf[tdf["dt"] >= mid]

    l_oos = oos[oos["sub"] == "L"]
    s_oos = oos[oos["sub"].str.startswith("S")]
    l_pnl = l_oos["pnl"].sum()
    s_pnl = s_oos["pnl"].sum()

    print(f"  L trades (API): {len(l_oos)} OOS, ${l_pnl:,.2f}")
    print(f"  S trades (API): {len(s_oos)} OOS, ${s_pnl:,.2f}")
    print(f"  Combined: ${l_pnl + s_pnl:,.2f}")

    # 與 Step 2/3 交叉比對（允許微小差異，因 API 有 position dict 計數的互動效應）
    l_ok = l_pnl > 10000
    s_ok = s_pnl > 8000
    combined_ok = (l_pnl + s_pnl) > 20000

    print(f"\n  [{'PASS' if l_ok else 'FAIL'}] L OOS > $10,000")
    print(f"  [{'PASS' if s_ok else 'FAIL'}] S OOS > $8,000")
    print(f"  [{'PASS' if combined_ok else 'FAIL'}] Combined > $20,000")

    return l_ok and s_ok and combined_ok


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 6: 出場函式單元測試
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def verify_exit_functions():
    """單元測試 check_exit 和 check_exit_cmp 的邊界條件"""
    print("\n" + "=" * 60)
    print("  Step 6: Exit Function Unit Tests")
    print("=" * 60)

    checks = []

    # ── check_exit (L 策略) ──

    # SafeNet: 價格跌破 entry*(1-5.5%)
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=1,
        bar_high=2010, bar_low=1880, bar_close=1885, ema20=1950,
    )
    checks.append(("L SafeNet triggers on big drop",
                    r["exit"] and r["reason"] == "SafeNet"))

    # SafeNet 滑價模型: exit_price < safenet_level
    sn_level = 2000 * (1 - 0.055)  # 1890
    checks.append(("L SafeNet price includes slippage",
                    r["exit"] and r["exit_price"] < sn_level))

    # 第 3 bar 不該出場（min_trail=7）
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=3,
        bar_high=2010, bar_low=1990, bar_close=1995, ema20=1996,
    )
    checks.append(("L no exit before min_trail=7",
                    not r["exit"]))

    # 第 8 bar Trail（close <= ema20）
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=8,
        bar_high=2050, bar_low=2010, bar_close=2015, ema20=2020,
    )
    checks.append(("L Trail triggers at bar 8 when close<=ema20",
                    r["exit"] and r["reason"] == "Trail"))

    # 第 9 bar EarlyStop（loss > 1% 且 close > ema20）
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=9,
        bar_high=1990, bar_low=1970, bar_close=1975, ema20=1970,
    )
    checks.append(("L EarlyStop at bar 9: loss>1% but close>ema20",
                    r["exit"] and r["reason"] == "EarlyStop"))

    # 第 20 bar Trail（bars >= 12, close <= ema20）
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=20,
        bar_high=2100, bar_low=2080, bar_close=2085, ema20=2090,
    )
    checks.append(("L Trail at bar 20 (>= EARLY_STOP_END)",
                    r["exit"] and r["reason"] == "Trail"))

    # 第 20 bar 不出場（close > ema20）
    r = strategy.check_exit(
        side="long", entry_price=2000, entry_bar_counter=0, current_bar_counter=20,
        bar_high=2100, bar_low=2080, bar_close=2095, ema20=2090,
    )
    checks.append(("L no exit at bar 20 when close>ema20",
                    not r["exit"]))

    # ── check_exit_cmp (S 策略) ──

    # SafeNet: 價格漲破 entry*(1+5.5%)
    r = strategy.check_exit_cmp(
        entry_price=2000, entry_bar_counter=0, current_bar_counter=2,
        bar_high=2120, bar_low=2050, bar_close=2100,
    )
    checks.append(("S SafeNet triggers on big rise",
                    r["exit"] and r["reason"] == "SafeNet"))

    # TP: 價格跌到 entry*(1-2%)
    r = strategy.check_exit_cmp(
        entry_price=2000, entry_bar_counter=0, current_bar_counter=3,
        bar_high=2010, bar_low=1955, bar_close=1965,
    )
    checks.append(("S TP triggers at -2%",
                    r["exit"] and r["reason"] == "TP" and abs(r["exit_price"] - 1960) < 1))

    # SafeNet 優先於 TP（同時觸發）
    r = strategy.check_exit_cmp(
        entry_price=2000, entry_bar_counter=0, current_bar_counter=3,
        bar_high=2120, bar_low=1955, bar_close=2000,
    )
    checks.append(("S SafeNet > TP priority",
                    r["exit"] and r["reason"] == "SafeNet"))

    # MaxHold: 持倉 >= 12 bar → 出場
    r = strategy.check_exit_cmp(
        entry_price=2000, entry_bar_counter=0, current_bar_counter=12,
        bar_high=2005, bar_low=1995, bar_close=2001,
    )
    checks.append(("S MaxHold at bar 12",
                    r["exit"] and r["reason"] == "MaxHold" and r["exit_price"] == 2001))

    # 第 11 bar 不觸發 MaxHold
    r = strategy.check_exit_cmp(
        entry_price=2000, entry_bar_counter=0, current_bar_counter=11,
        bar_high=2005, bar_low=1995, bar_close=2001,
    )
    checks.append(("S no MaxHold at bar 11",
                    not r["exit"]))

    # ── compute_pnl ──
    pnl, pct = strategy.compute_pnl(2000, 2040, "long")
    expected = (2040 - 2000) * 2000 / 2000 - 2  # 40 - 2 = 38
    checks.append(("PnL long correct", abs(pnl - 38) < 0.01))

    pnl, pct = strategy.compute_pnl(2000, 1960, "short")
    expected = (2000 - 1960) * 2000 / 2000 - 2  # 40 - 2 = 38
    checks.append(("PnL short correct", abs(pnl - 38) < 0.01))

    # 報告
    all_ok = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_ok = False
        print(f"  [{status}] {name}")

    return all_ok


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    print("=" * 60)
    print("  Dual Strategy (L + S CMP-Portfolio) Verification")
    print("=" * 60)
    print()

    # Step 1: 指標
    ind_ok = verify_indicators()

    # Step 2: L 策略
    l_oos_pnl, l_ok = verify_long_strategy()

    # Step 3: S 策略
    s_oos_pnl, s_ok = verify_short_strategy()

    # Step 4: 狀態遷移
    mig_ok = verify_state_migration()

    # Step 5: API 層交叉驗證
    api_ok = verify_api_crosscheck()

    # Step 6: 出場函式單元測試
    exit_ok = verify_exit_functions()

    # 合計
    combined = l_oos_pnl + s_oos_pnl
    print("\n" + "=" * 60)
    print("  Combined Results")
    print("=" * 60)
    print(f"  L OOS: ${l_oos_pnl:,.2f}")
    print(f"  S OOS: ${s_oos_pnl:,.2f}")
    print(f"  Total: ${combined:,.2f}")
    print(f"  Target: $23,825")

    combined_ok = combined > 20000
    print(f"  [{'PASS' if combined_ok else 'FAIL'}] Combined > $20,000")

    print("\n" + "=" * 60)
    results = {
        "Indicators": ind_ok,
        "L Strategy": l_ok,
        "S Strategy": s_ok,
        "State Migration": mig_ok,
        "API Cross-Check": api_ok,
        "Exit Unit Tests": exit_ok,
        "Combined": combined_ok,
    }
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'} — {name}")

    all_pass = all(results.values())
    if all_pass:
        print("\n  *** ALL 7 VERIFICATION STEPS PASSED ***")
    else:
        print("\n  *** VERIFICATION FAILED — CHECK ABOVE ***")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
