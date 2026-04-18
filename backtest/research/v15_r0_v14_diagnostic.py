"""
V15 R0: V14 完整回測重建 + 弱月深度診斷

目標：
1. 精確重建 V14 L+S 回測，驗證 OOS $4,549
2. 深度分析弱月特徵：
   - 每月交易明細（進場時間、GK、持倉天數、出場原因、PnL）
   - 弱月 vs 強月的信號品質差異
   - 假信號率（breakout 後回撤比例）
   - 進場時的 BTC 狀態、volume 狀態
   - GK percentile 分佈差異
3. 尋找 V14 的結構性弱點

Anti-lookahead: 所有指標 shift(1)，entry at close price
"""
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# === 帳戶設定 ===
MARGIN = 200
LEVERAGE = 20
NOTIONAL = MARGIN * LEVERAGE  # $4,000
FEE = 4.0

# === L 策略參數 ===
L_GK_SHORT, L_GK_LONG = 5, 20
L_GK_THRESH = 25
L_SAFENET = 0.035
L_TP = 0.035
L_MH = 6
L_EXT = 2
L_CD = 6
L_CAP = 20
L_MONTHLY_LOSS = -75
# V14 MFE Trailing
L_MFE_ACT = 0.010
L_MFE_TRAIL_DD = 0.008
L_MFE_MIN_BAR = 1
# V14 Conditional MH
L_COND_BAR = 2
L_COND_THRESH = -0.01
L_COND_MH = 5

# === S 策略參數 ===
S_GK_SHORT, S_GK_LONG = 10, 30
S_GK_THRESH = 35
S_SAFENET = 0.04
S_TP = 0.02
S_MH = 10
S_EXT = 2
S_CD = 8
S_CAP = 20
S_MONTHLY_LOSS = -150

# === 共用 ===
BRK = 15
GK_WIN = 100
BLOCK_H = {0, 1, 2, 12}
L_BLOCK_D = {5, 6}
S_BLOCK_D = {0, 5, 6}
DAILY_LOSS = -200
CONSEC_PAUSE = 4
CONSEC_CD = 24
WARMUP = GK_WIN + S_GK_LONG + 20  # 150


def load_data():
    eth = pd.read_csv(DATA_DIR / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    btc = pd.read_csv(DATA_DIR / "BTCUSDT_1h_latest730d.csv", parse_dates=["datetime"])
    return eth, btc


def compute_indicators(df):
    d = df.copy()
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2

    # L: 5/20
    d["gk_ratio"] = gk.rolling(L_GK_SHORT).mean() / gk.rolling(L_GK_LONG).mean()
    # S: 10/30
    d["gk_ratio_s"] = gk.rolling(S_GK_SHORT).mean() / gk.rolling(S_GK_LONG).mean()

    def rank_pctile(s):
        if len(s) <= 1:
            return 50
        return (s.iloc[:-1] < s.iloc[-1]).sum() / (len(s) - 1) * 100

    d["gk_pctile"] = d["gk_ratio"].shift(1).rolling(GK_WIN).apply(rank_pctile, raw=False)
    d["gk_pctile_s"] = d["gk_ratio_s"].shift(1).rolling(GK_WIN).apply(rank_pctile, raw=False)

    d["brk_max"] = d["close"].shift(1).rolling(BRK).max()
    d["brk_min"] = d["close"].shift(1).rolling(BRK).min()
    d["brk_long"] = d["close"] > d["brk_max"]
    d["brk_short"] = d["close"] < d["brk_min"]

    d["hour"] = d["datetime"].dt.hour
    d["weekday"] = d["datetime"].dt.weekday
    d["session_l"] = ~(d["hour"].isin(BLOCK_H) | d["weekday"].isin(L_BLOCK_D))
    d["session_s"] = ~(d["hour"].isin(BLOCK_H) | d["weekday"].isin(S_BLOCK_D))

    d["gk_raw"] = gk
    d["atr14"] = (d["high"] - d["low"]).rolling(14).mean()

    return d


def run_v14_backtest(df, btc_df=None, start_bar=0, end_bar=None):
    """Complete V14 backtest with full trade lifecycle tracking."""
    if end_bar is None:
        end_bar = len(df)

    trades = []  # all completed trades
    pos_l = None
    pos_s = None
    last_exit_l = -9999
    last_exit_s = -9999
    consec_losses = 0
    consec_cd_until = -9999

    # monthly/daily tracking
    daily_pnl = 0.0
    current_day = None
    monthly_pnl_l = 0.0
    monthly_pnl_s = 0.0
    monthly_entries_l = 0
    monthly_entries_s = 0
    current_month = None

    for i in range(start_bar, end_bar):
        row = df.iloc[i]
        dt = row["datetime"]
        day = dt.date()
        month = dt.strftime("%Y-%m")

        # Day rollover
        if day != current_day:
            daily_pnl = 0.0
            current_day = day

        # Month rollover
        if month != current_month:
            monthly_pnl_l = 0.0
            monthly_pnl_s = 0.0
            monthly_entries_l = 0
            monthly_entries_s = 0
            current_month = month

        # --- EXIT CHECK ---
        # L exit
        if pos_l is not None:
            bars_held = i - pos_l["entry_bar"]
            ep = pos_l["entry_price"]
            running_mfe = pos_l["running_mfe"]
            mh_reduced = pos_l["mh_reduced"]
            eff_mh = L_COND_MH if mh_reduced else L_MH

            bar_mfe = (row["high"] - ep) / ep
            running_mfe = max(running_mfe, bar_mfe)
            pos_l["running_mfe"] = running_mfe

            exit_reason = None
            exit_price = None

            # SafeNet
            sl_level = ep * (1 - L_SAFENET)
            if row["low"] <= sl_level:
                exit_reason = "SafeNet"
                exit_price = sl_level - (sl_level - row["low"]) * 0.25

            # TP
            if exit_reason is None:
                tp_level = ep * (1 + L_TP)
                if row["high"] >= tp_level:
                    exit_reason = "TP"
                    exit_price = tp_level

            # MFE trail (V14)
            if exit_reason is None and bars_held >= L_MFE_MIN_BAR and running_mfe >= L_MFE_ACT:
                cpnl = (row["close"] - ep) / ep
                dd = running_mfe - cpnl
                if dd >= L_MFE_TRAIL_DD:
                    exit_reason = "MFE-trail"
                    exit_price = row["close"]

            # Extension
            if exit_reason is None and pos_l.get("ext_active"):
                ext_bars = i - pos_l["ext_start"]
                if row["low"] <= ep:
                    exit_reason = "BE"
                    exit_price = ep
                elif ext_bars >= L_EXT:
                    exit_reason = "MH-ext"
                    exit_price = row["close"]

            # Conditional MH check (V14)
            if exit_reason is None and not pos_l.get("ext_active"):
                if not mh_reduced and bars_held == L_COND_BAR:
                    pnl_pct = (row["close"] - ep) / ep
                    if pnl_pct <= L_COND_THRESH:
                        pos_l["mh_reduced"] = True
                        mh_reduced = True
                        eff_mh = L_COND_MH

                # MaxHold
                if exit_reason is None and bars_held >= eff_mh and not pos_l.get("ext_active"):
                    pnl_pct = (row["close"] - ep) / ep
                    if pnl_pct > 0:
                        pos_l["ext_active"] = True
                        pos_l["ext_start"] = i
                    else:
                        exit_reason = "MaxHold"
                        exit_price = row["close"]

            if exit_reason:
                gross = (exit_price - ep) * NOTIONAL / ep
                net = gross - FEE
                # track per-bar PnL for lifecycle
                bar_pnls = []
                for b in range(pos_l["entry_bar"] + 1, i + 1):
                    brow = df.iloc[b]
                    bp = (brow["close"] - ep) / ep * 100
                    bar_pnls.append(bp)

                trade = {
                    "side": "L", "entry_bar": pos_l["entry_bar"],
                    "entry_dt": pos_l["entry_dt"], "entry_price": ep,
                    "exit_bar": i, "exit_dt": dt, "exit_price": exit_price,
                    "exit_reason": exit_reason, "pnl": net,
                    "bars_held": bars_held, "running_mfe_pct": running_mfe * 100,
                    "mh_reduced": mh_reduced,
                    "gk_at_entry": pos_l["gk_at_entry"],
                    "month": pos_l["entry_dt"].strftime("%Y-%m"),
                    "entry_hour": pos_l["entry_dt"].hour,
                    "bar_pnls": bar_pnls,
                    # context at entry
                    "entry_atr14": pos_l.get("entry_atr14", 0),
                    "entry_volume": pos_l.get("entry_volume", 0),
                    "btc_ret_24h": pos_l.get("btc_ret_24h", 0),
                    "btc_above_ema20": pos_l.get("btc_above_ema20", False),
                    "eth_ret_6h": pos_l.get("eth_ret_6h", 0),
                }
                trades.append(trade)

                daily_pnl += net
                monthly_pnl_l += net
                last_exit_l = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE:
                        consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_l = None

        # S exit
        if pos_s is not None:
            bars_held = i - pos_s["entry_bar"]
            ep = pos_s["entry_price"]

            exit_reason = None
            exit_price = None

            # SafeNet
            sl_level = ep * (1 + S_SAFENET)
            if row["high"] >= sl_level:
                exit_reason = "SafeNet"
                exit_price = sl_level + (row["high"] - sl_level) * 0.25

            # TP
            if exit_reason is None:
                tp_level = ep * (1 - S_TP)
                if row["low"] <= tp_level:
                    exit_reason = "TP"
                    exit_price = tp_level

            # Extension
            if exit_reason is None and pos_s.get("ext_active"):
                ext_bars = i - pos_s["ext_start"]
                if row["high"] >= ep:
                    exit_reason = "BE"
                    exit_price = ep
                elif ext_bars >= S_EXT:
                    exit_reason = "MH-ext"
                    exit_price = row["close"]

            # MaxHold
            if exit_reason is None and bars_held >= S_MH and not pos_s.get("ext_active"):
                pnl_pct = (ep - row["close"]) / ep
                if pnl_pct > 0:
                    pos_s["ext_active"] = True
                    pos_s["ext_start"] = i
                else:
                    exit_reason = "MaxHold"
                    exit_price = row["close"]

            if exit_reason:
                gross = (ep - exit_price) * NOTIONAL / ep
                net = gross - FEE
                bar_pnls = []
                for b in range(pos_s["entry_bar"] + 1, i + 1):
                    brow = df.iloc[b]
                    bp = (ep - brow["close"]) / ep * 100
                    bar_pnls.append(bp)

                trade = {
                    "side": "S", "entry_bar": pos_s["entry_bar"],
                    "entry_dt": pos_s["entry_dt"], "entry_price": ep,
                    "exit_bar": i, "exit_dt": dt, "exit_price": exit_price,
                    "exit_reason": exit_reason, "pnl": net,
                    "bars_held": bars_held, "running_mfe_pct": 0,
                    "mh_reduced": False,
                    "gk_at_entry": pos_s["gk_at_entry"],
                    "month": pos_s["entry_dt"].strftime("%Y-%m"),
                    "entry_hour": pos_s["entry_dt"].hour,
                    "bar_pnls": bar_pnls,
                    "entry_atr14": pos_s.get("entry_atr14", 0),
                    "entry_volume": pos_s.get("entry_volume", 0),
                    "btc_ret_24h": pos_s.get("btc_ret_24h", 0),
                    "btc_above_ema20": pos_s.get("btc_above_ema20", False),
                    "eth_ret_6h": pos_s.get("eth_ret_6h", 0),
                }
                trades.append(trade)

                daily_pnl += net
                monthly_pnl_s += net
                last_exit_s = i
                if net < 0:
                    consec_losses += 1
                    if consec_losses >= CONSEC_PAUSE:
                        consec_cd_until = i + CONSEC_CD
                else:
                    consec_losses = 0
                pos_s = None

        # --- ENTRY CHECK ---
        gk_l = row.get("gk_pctile", np.nan)
        gk_s = row.get("gk_pctile_s", np.nan)
        if pd.isna(gk_l) or pd.isna(gk_s):
            continue

        # Circuit breaker
        if daily_pnl <= DAILY_LOSS:
            continue
        if i < consec_cd_until:
            continue

        # BTC context
        btc_ret_24h = 0.0
        btc_above_ema20 = False
        if btc_df is not None and i < len(btc_df):
            if i >= 24:
                btc_ret_24h = (btc_df.iloc[i]["close"] - btc_df.iloc[i - 24]["close"]) / btc_df.iloc[i - 24]["close"] * 100
            if "ema20" in btc_df.columns:
                btc_above_ema20 = btc_df.iloc[i]["close"] > btc_df.iloc[i]["ema20"]

        # ETH 6h return
        eth_ret_6h = 0.0
        if i >= 6:
            eth_ret_6h = (row["close"] - df.iloc[i - 6]["close"]) / df.iloc[i - 6]["close"] * 100

        # L entry
        if pos_l is None:
            if (gk_l < L_GK_THRESH and row["brk_long"]
                    and row["session_l"]
                    and (i - last_exit_l) >= L_CD
                    and monthly_entries_l < L_CAP
                    and monthly_pnl_l > L_MONTHLY_LOSS):
                pos_l = {
                    "entry_bar": i, "entry_dt": dt, "entry_price": row["close"],
                    "running_mfe": 0.0, "mh_reduced": False,
                    "ext_active": False, "ext_start": 0,
                    "gk_at_entry": gk_l,
                    "entry_atr14": row.get("atr14", 0),
                    "entry_volume": row.get("volume", 0),
                    "btc_ret_24h": btc_ret_24h,
                    "btc_above_ema20": btc_above_ema20,
                    "eth_ret_6h": eth_ret_6h,
                }
                monthly_entries_l += 1

        # S entry
        if pos_s is None:
            if (gk_s < S_GK_THRESH and row["brk_short"]
                    and row["session_s"]
                    and (i - last_exit_s) >= S_CD
                    and monthly_entries_s < S_CAP
                    and monthly_pnl_s > S_MONTHLY_LOSS):
                pos_s = {
                    "entry_bar": i, "entry_dt": dt, "entry_price": row["close"],
                    "ext_active": False, "ext_start": 0,
                    "gk_at_entry": gk_s,
                    "entry_atr14": row.get("atr14", 0),
                    "entry_volume": row.get("volume", 0),
                    "btc_ret_24h": btc_ret_24h,
                    "btc_above_ema20": btc_above_ema20,
                    "eth_ret_6h": eth_ret_6h,
                }
                monthly_entries_s += 1

    return trades


def analyze(trades, df, btc_df, split_bar):
    """Deep diagnostic analysis."""
    tdf = pd.DataFrame(trades)
    if tdf.empty:
        print("No trades!")
        return

    oos = tdf[tdf["entry_bar"] >= split_bar].copy()
    oos_l = oos[oos["side"] == "L"]
    oos_s = oos[oos["side"] == "S"]

    print("=" * 80)
    print("V14 OOS 回測驗證")
    print("=" * 80)
    print(f"L: {len(oos_l)}t, PnL ${oos_l['pnl'].sum():.0f}, WR {(oos_l['pnl']>0).mean()*100:.0f}%")
    print(f"S: {len(oos_s)}t, PnL ${oos_s['pnl'].sum():.0f}, WR {(oos_s['pnl']>0).mean()*100:.0f}%")
    print(f"L+S: PnL ${oos['pnl'].sum():.0f}")

    # Exit distribution
    for side, sub in [("L", oos_l), ("S", oos_s)]:
        print(f"\n{side} OOS 出場分佈:")
        for reason in ["TP", "MFE-trail", "MaxHold", "MH-ext", "BE", "SafeNet"]:
            r = sub[sub["exit_reason"] == reason]
            if len(r) > 0:
                print(f"  {reason:10s}: {len(r):3d}t  avg ${r['pnl'].mean():+7.1f}  total ${r['pnl'].sum():+8.0f}")

    # Monthly breakdown
    print("\n" + "=" * 80)
    print("月度明細")
    print("=" * 80)
    months_all = sorted(oos["month"].unique())
    print(f"{'Month':>8s}  {'L_t':>4s} {'L_PnL':>8s} {'L_WR':>6s}  {'S_t':>4s} {'S_PnL':>8s} {'S_WR':>6s}  {'Total':>8s}")
    for m in months_all:
        ml = oos_l[oos_l["month"] == m]
        ms = oos_s[oos_s["month"] == m]
        l_pnl = ml["pnl"].sum() if len(ml) > 0 else 0
        s_pnl = ms["pnl"].sum() if len(ms) > 0 else 0
        l_wr = (ml["pnl"] > 0).mean() * 100 if len(ml) > 0 else 0
        s_wr = (ms["pnl"] > 0).mean() * 100 if len(ms) > 0 else 0
        total = l_pnl + s_pnl
        marker = " <<<" if total < 100 else ""
        print(f"{m:>8s}  {len(ml):4d} {l_pnl:+8.0f} {l_wr:5.0f}%  {len(ms):4d} {s_pnl:+8.0f} {s_wr:5.0f}%  {total:+8.0f}{marker}")

    # === WEAK MONTH DEEP DIVE ===
    print("\n" + "=" * 80)
    print("弱月深度分析（L+S < $100 的月份）")
    print("=" * 80)

    for m in months_all:
        ml = oos_l[oos_l["month"] == m]
        ms = oos_s[oos_s["month"] == m]
        total = ml["pnl"].sum() + ms["pnl"].sum()
        if total >= 100:
            continue

        print(f"\n--- {m} (L+S ${total:+.0f}) ---")

        for side, sub in [("L", ml), ("S", ms)]:
            if len(sub) == 0:
                print(f"  {side}: 0 trades")
                continue
            print(f"\n  {side}: {len(sub)}t, PnL ${sub['pnl'].sum():+.0f}")
            for _, t in sub.iterrows():
                gk_str = f"GK={t['gk_at_entry']:.1f}"
                btc_str = f"BTC24h={t['btc_ret_24h']:+.1f}%"
                btc_ema = "BTC>EMA" if t.get("btc_above_ema20") else "BTC<EMA"
                vol_str = f"vol={t['entry_volume']:.0f}"
                mfe_str = f"MFE={t['running_mfe_pct']:.1f}%"
                mh_str = "MH5" if t.get("mh_reduced") else "MH6"
                print(f"    {t['entry_dt'].strftime('%m-%d %H:00')} → "
                      f"{t['exit_dt'].strftime('%m-%d %H:00')} "
                      f"${t['pnl']:+7.1f} {t['exit_reason']:8s} "
                      f"{gk_str} {btc_str} {btc_ema} {mfe_str} hold={t['bars_held']}")

    # === SIGNAL QUALITY ANALYSIS ===
    print("\n" + "=" * 80)
    print("信號品質分析：弱月 vs 強月")
    print("=" * 80)

    weak_months = set()
    strong_months = set()
    for m in months_all:
        ml = oos_l[oos_l["month"] == m]
        ms = oos_s[oos_s["month"] == m]
        total = ml["pnl"].sum() + ms["pnl"].sum()
        if total < 100:
            weak_months.add(m)
        else:
            strong_months.add(m)

    for side, sub in [("L", oos_l), ("S", oos_s)]:
        weak = sub[sub["month"].isin(weak_months)]
        strong = sub[sub["month"].isin(strong_months)]
        print(f"\n{side} 弱月({len(weak_months)}) vs 強月({len(strong_months)}):")
        for label, grp in [("弱月", weak), ("強月", strong)]:
            if len(grp) == 0:
                continue
            print(f"  {label}: {len(grp)}t, avg PnL ${grp['pnl'].mean():+.1f}, WR {(grp['pnl']>0).mean()*100:.0f}%")
            print(f"    avg GK:  {grp['gk_at_entry'].mean():.1f}")
            print(f"    avg ATR: {grp['entry_atr14'].mean():.1f}")
            print(f"    avg MFE: {grp['running_mfe_pct'].mean():.1f}%")
            print(f"    avg BTC24h: {grp['btc_ret_24h'].mean():+.2f}%")
            print(f"    BTC>EMA%: {grp['btc_above_ema20'].mean()*100:.0f}%")
            print(f"    avg ETH6h: {grp['eth_ret_6h'].mean():+.2f}%")

            # Exit distribution
            for reason in ["TP", "MFE-trail", "MaxHold", "MH-ext", "BE", "SafeNet"]:
                r = grp[grp["exit_reason"] == reason]
                if len(r) > 0:
                    print(f"    {reason:10s}: {len(r):3d}t ({len(r)/len(grp)*100:4.0f}%) avg ${r['pnl'].mean():+.1f}")

    # === BTC CONDITION ANALYSIS ===
    print("\n" + "=" * 80)
    print("BTC 狀態對 L 交易的影響")
    print("=" * 80)

    for label, condition in [
        ("BTC>EMA20", oos_l[oos_l["btc_above_ema20"] == True]),
        ("BTC<EMA20", oos_l[oos_l["btc_above_ema20"] == False]),
    ]:
        if len(condition) == 0:
            continue
        print(f"\n  {label}: {len(condition)}t")
        print(f"    PnL ${condition['pnl'].sum():+.0f}, avg ${condition['pnl'].mean():+.1f}, WR {(condition['pnl']>0).mean()*100:.0f}%")
        for reason in ["TP", "MFE-trail", "MaxHold", "MH-ext", "BE", "SafeNet"]:
            r = condition[condition["exit_reason"] == reason]
            if len(r) > 0:
                print(f"    {reason:10s}: {len(r):3d}t avg ${r['pnl'].mean():+.1f}")

    # BTC 24h return buckets
    print("\n  L 按 BTC 24h return 分組:")
    oos_l_btc = oos_l.copy()
    oos_l_btc["btc_bucket"] = pd.cut(oos_l_btc["btc_ret_24h"], bins=[-999, -3, -1, 0, 1, 3, 999],
                                      labels=["<-3%", "-3~-1%", "-1~0%", "0~1%", "1~3%", ">3%"])
    for bucket in ["<-3%", "-3~-1%", "-1~0%", "0~1%", "1~3%", ">3%"]:
        grp = oos_l_btc[oos_l_btc["btc_bucket"] == bucket]
        if len(grp) > 0:
            print(f"    {bucket:>8s}: {len(grp):3d}t, avg ${grp['pnl'].mean():+7.1f}, WR {(grp['pnl']>0).mean()*100:3.0f}%, total ${grp['pnl'].sum():+.0f}")

    # === BTC CONDITION for S ===
    print("\n" + "=" * 80)
    print("BTC 狀態對 S 交易的影響")
    print("=" * 80)

    for label, condition in [
        ("BTC>EMA20", oos_s[oos_s["btc_above_ema20"] == True]),
        ("BTC<EMA20", oos_s[oos_s["btc_above_ema20"] == False]),
    ]:
        if len(condition) == 0:
            continue
        print(f"\n  {label}: {len(condition)}t")
        print(f"    PnL ${condition['pnl'].sum():+.0f}, avg ${condition['pnl'].mean():+.1f}, WR {(condition['pnl']>0).mean()*100:.0f}%")

    # === VOLUME ANALYSIS ===
    print("\n" + "=" * 80)
    print("Volume 分析")
    print("=" * 80)

    vol_median = df["volume"].median()
    for side, sub in [("L", oos_l), ("S", oos_s)]:
        high_vol = sub[sub["entry_volume"] >= vol_median]
        low_vol = sub[sub["entry_volume"] < vol_median]
        print(f"\n  {side} 高量(>median): {len(high_vol)}t, avg ${high_vol['pnl'].mean():+.1f}, WR {(high_vol['pnl']>0).mean()*100:.0f}%")
        print(f"  {side} 低量(<median): {len(low_vol)}t, avg ${low_vol['pnl'].mean():+.1f}, WR {(low_vol['pnl']>0).mean()*100:.0f}%")

    # === GK PERCENTILE BINS ===
    print("\n" + "=" * 80)
    print("GK Percentile 分組績效")
    print("=" * 80)

    for side, sub, thresh in [("L", oos_l, L_GK_THRESH), ("S", oos_s, S_GK_THRESH)]:
        sub_c = sub.copy()
        sub_c["gk_bin"] = pd.cut(sub_c["gk_at_entry"], bins=[0, 5, 10, 15, 20, thresh],
                                  labels=[f"0-5", "5-10", "10-15", "15-20", f"20-{thresh}"])
        print(f"\n  {side} GK bins:")
        for b in sub_c["gk_bin"].cat.categories:
            grp = sub_c[sub_c["gk_bin"] == b]
            if len(grp) > 0:
                print(f"    {b:>6s}: {len(grp):3d}t, avg ${grp['pnl'].mean():+7.1f}, WR {(grp['pnl']>0).mean()*100:3.0f}%, total ${grp['pnl'].sum():+.0f}")

    # === ENTRY HOUR ANALYSIS ===
    print("\n" + "=" * 80)
    print("進場時間分析")
    print("=" * 80)

    for side, sub in [("L", oos_l), ("S", oos_s)]:
        print(f"\n  {side} 進場時間分佈:")
        hour_grp = sub.groupby("entry_hour").agg(
            count=("pnl", "size"),
            avg_pnl=("pnl", "mean"),
            total_pnl=("pnl", "sum"),
            wr=("pnl", lambda x: (x > 0).mean() * 100)
        ).sort_index()
        for h, r in hour_grp.iterrows():
            print(f"    {h:2d}:00  {r['count']:3.0f}t  avg ${r['avg_pnl']:+7.1f}  total ${r['total_pnl']:+7.0f}  WR {r['wr']:3.0f}%")

    # === MISSED OPPORTUNITIES ===
    print("\n" + "=" * 80)
    print("錯過的信號分析（滿足 GK+BRK 但被 session/CD/cap 擋掉）")
    print("=" * 80)

    blocked_l = 0
    blocked_l_would_win = 0
    for i in range(split_bar, len(df)):
        row = df.iloc[i]
        gk_l = row.get("gk_pctile", np.nan)
        if pd.isna(gk_l):
            continue
        if gk_l < L_GK_THRESH and row["brk_long"]:
            if not row["session_l"]:
                # Check what would have happened
                if i + L_MH < len(df):
                    future_close = df.iloc[i + L_MH]["close"]
                    future_pnl = (future_close - row["close"]) / row["close"] * 100
                    blocked_l += 1
                    if future_pnl > 0.5:  # would have been profitable after fee
                        blocked_l_would_win += 1

    print(f"  L: {blocked_l} signals blocked by session filter")
    if blocked_l > 0:
        print(f"      of which {blocked_l_would_win} ({blocked_l_would_win/blocked_l*100:.0f}%) would have been profitable")

    # === CONSECUTIVE TRADE ANALYSIS ===
    print("\n" + "=" * 80)
    print("連續交易分析")
    print("=" * 80)

    for side, sub in [("L", oos_l), ("S", oos_s)]:
        sub_sorted = sub.sort_values("entry_bar")
        streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        for _, t in sub_sorted.iterrows():
            if t["pnl"] > 0:
                if streak > 0:
                    streak += 1
                else:
                    streak = 1
                max_win_streak = max(max_win_streak, streak)
            else:
                if streak < 0:
                    streak -= 1
                else:
                    streak = -1
                max_loss_streak = max(max_loss_streak, abs(streak))
        print(f"  {side}: max win streak={max_win_streak}, max loss streak={max_loss_streak}")


def main():
    print("Loading data...")
    eth, btc = load_data()
    print(f"ETH: {len(eth)} bars, {eth['datetime'].iloc[0]} to {eth['datetime'].iloc[-1]}")
    print(f"BTC: {len(btc)} bars")

    # Compute indicators
    eth = compute_indicators(eth)

    # BTC EMA20
    btc["ema20"] = btc["close"].ewm(span=20).mean()

    # Split
    split_bar = len(eth) // 2
    print(f"Split at bar {split_bar} ({eth.iloc[split_bar]['datetime']})")

    # Run backtest
    print("\nRunning V14 backtest...")
    trades = run_v14_backtest(eth, btc, start_bar=WARMUP, end_bar=len(eth))
    print(f"Total trades: {len(trades)}")

    # Analyze
    analyze(trades, eth, btc, split_bar)


if __name__ == "__main__":
    main()
