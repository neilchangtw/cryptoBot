"""
ETH 1h V14+R 雙策略 — 純指標計算 + 信號判斷（無副作用）

策略 L（做多）：GK(5/20)<25 壓縮突破 + TP 3.5% + MFE trail + MaxHold 6(cond5) + ext2 BE
  + R gate：SMA200 100-bar 斜率 > +4.5% 時 block L（強多頭 regime 失效）
  V14+R: OOS +$121, V23 12/13 gates PASS

策略 S（做空）：GK(10/30)<35 壓縮突破 + TP 2.0% + MaxHold 10 + ext2 BE
  + R gate：SMA200 100-bar 斜率 |slope| < 1.0% 時 block S（橫盤 regime 失效）
  V14+R: OOS +$258, V23 12/13 gates PASS

L+S 合計 V14+R: PnL +6%, MDD -11%, Sharpe +18%, Worst30d -35% vs V14

V14→V14+R 變更（新增 regime gate，出場邏輯不動）：
  新增 R gate：SMA200 100-bar 相對斜率（shift(1) 防前瞻）
    - L 被阻擋：slope > +4.5%
    - S 被阻擋：|slope| < 1.0%
"""
import numpy as np
import pandas as pd

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 共用常數
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
L_GK_SHORT = 5             # L GK 短期均值窗口
L_GK_LONG = 20             # L GK 長期均值窗口
S_GK_SHORT = 10            # S GK 短期均值窗口
S_GK_LONG = 30             # S GK 長期均值窗口
GK_WIN = 100               # GK percentile 滾動窗口
BRK_LOOK = 15              # L/S 共用 breakout lookback（15 bar）
BLOCK_H = {0, 1, 2, 12}    # UTC+8 封鎖時段（L/S 共用）
L_BLOCK_D = {5, 6}         # L 封鎖星期（Sat=5, Sun=6）
S_BLOCK_D = {0, 5, 6}      # S 封鎖星期（Mon=0, Sat=5, Sun=6）
FEE = 4.0                  # 每筆交易成本（含滑價）$4
MARGIN = 200               # 每筆保證金 $200
LEVERAGE = 20              # 槓桿倍數
NOTIONAL = MARGIN * LEVERAGE  # $4,000 名目金額

# ── V14+R Regime Gate ──
R_SMA_WIN = 200            # SMA 窗口
R_SLOPE_WIN = 100          # 斜率回看窗口（100-bar 前值）
R_TH_UP = 0.045            # L 被 block：slope > +4.5%（強多頭）
R_TH_SIDE = 0.010          # S 被 block：|slope| < 1.0%（橫盤）

WARMUP_BARS = max(GK_WIN + S_GK_LONG + 20, R_SMA_WIN + R_SLOPE_WIN + 10)  # 310 bar（R gate 需更長暖機）

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# L 策略常數（做多）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
L_GK_THRESH = 25           # L 的 GK 壓縮閾值
L_SAFENET_PCT = 0.035      # L SafeNet -3.5%
L_TP_PCT = 0.035           # L 固定止盈 +3.5%（V11-E: 2.0%→3.5%）
L_MAX_HOLD = 6             # L 最大持倉 6 bar（V11-E: 5→6）
L_EXT_BARS = 2             # L MaxHold 延長 2 bar（V13 新增）
L_EXIT_CD = 6              # L 出場後冷卻 6 bar
L_MAX_TOTAL = 1            # L 最多同時 1 筆
L_MONTHLY_ENTRY_CAP = 20   # L 每月最多 20 筆進場
L_MONTHLY_LOSS_CAP = -75   # L 月虧 -$75 停

# V14 MFE Trailing（L only）
L_MFE_ACT = 0.010          # MFE 啟動門檻：浮盈曾達 1.0%
L_MFE_TRAIL_DD = 0.008     # MFE 回吐門檻：從高點回落 0.8%
L_MFE_MIN_BAR = 1          # MFE 最早觸發 bar（持倉 >= 1 bar）

# V14 Conditional MH（L only）
L_COND_CHECK_BAR = 2       # 在 bar 2 檢查
L_COND_EXIT_THRESH = -0.01 # 虧損 >= 1.0% 觸發
L_COND_REDUCED_MH = 5      # 縮短後的 MH（原 6 → 5）

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# S 策略常數（做空）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
S_GK_THRESH = 35           # S 的 GK 壓縮閾值（V13: 30→35）
S_SAFENET_PCT = 0.04       # S SafeNet +4.0%
S_TP_PCT = 0.02            # S 固定止盈 -2.0%（V11-E: 1.5%→2.0%）
S_MAX_HOLD = 10            # S 最大持倉 10 bar（V13: 7→10）
S_EXT_BARS = 2             # S MaxHold 延長 2 bar（V13 新增）
S_EXIT_CD = 8              # S 出場後冷卻 8 bar
S_MAX_TOTAL = 1            # S 最多同時 1 筆
S_MONTHLY_ENTRY_CAP = 20   # S 每月最多 20 筆進場
S_MONTHLY_LOSS_CAP = -150  # S 月虧 -$150 停

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 風控熔斷
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DAILY_LOSS_LIMIT = -200    # 日虧 $200 停（L+S 合計）
CONSEC_LOSS_PAUSE = 4      # 連虧 4 筆冷卻
CONSEC_LOSS_COOLDOWN = 24  # 連虧冷卻 24 bar


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    計算所有策略指標（V13: L/S 各自 GK 窗口 + 獨立 session filter）。

    Input:
        df: DataFrame with columns [open, high, low, close, volume, datetime]
            datetime 必須是 UTC+8
    Returns:
        df copy with added indicator columns
    """
    d = df.copy()

    # EMA20（lifecycle 記錄用）
    d["ema20"] = d["close"].ewm(span=20).mean()

    # ── GK Volatility ──
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl ** 2 - (2 * np.log(2) - 1) * ln_co ** 2

    # L: mean(5)/mean(20)
    gk_short_l = gk.rolling(L_GK_SHORT).mean()
    gk_long_l = gk.rolling(L_GK_LONG).mean()
    d["gk_ratio"] = gk_short_l / gk_long_l

    # S: mean(10)/mean(30)
    gk_short_s = gk.rolling(S_GK_SHORT).mean()
    gk_long_s = gk.rolling(S_GK_LONG).mean()
    d["gk_ratio_s"] = gk_short_s / gk_long_s

    # GK Percentile: shift(1) BEFORE rolling — 防前瞻
    # ★ rank percentile（與研究腳本一致）
    _rank_pctile = lambda s: (
        ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100
        if len(s) > 1 else 50
    )
    d["gk_pctile"] = d["gk_ratio"].shift(1).rolling(GK_WIN).apply(
        _rank_pctile, raw=False
    )
    d["gk_pctile_s"] = d["gk_ratio_s"].shift(1).rolling(GK_WIN).apply(
        _rank_pctile, raw=False
    )

    # ── Breakout: L/S 共用 BL15 ──
    # ★ current close 方法（與研究腳本一致）
    d["breakout_15bar_max"] = d["close"].shift(1).rolling(BRK_LOOK).max()
    d["breakout_15bar_min"] = d["close"].shift(1).rolling(BRK_LOOK).min()
    d["breakout_long"] = d["close"] > d["breakout_15bar_max"]
    d["breakout_short"] = d["close"] < d["breakout_15bar_min"]

    # ── Session Filter（V13: L/S 獨立） ──
    d["hour_utc8"] = d["datetime"].dt.hour
    d["weekday_utc8"] = d["datetime"].dt.weekday
    d["session_ok_l"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["weekday_utc8"].isin(L_BLOCK_D))
    d["session_ok_s"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["weekday_utc8"].isin(S_BLOCK_D))

    # ── V14+R Regime Gate: SMA200 100-bar 相對斜率（shift(1) 防前瞻）──
    sma = d["close"].rolling(R_SMA_WIN).mean()
    slope = (sma - sma.shift(R_SLOPE_WIN)) / sma.shift(R_SLOPE_WIN)
    d["sma200"] = sma
    d["sma_slope"] = slope.shift(1)  # 只用昨日斜率，防前瞻
    d["regime_block_l"] = d["sma_slope"] > R_TH_UP
    d["regime_block_s"] = d["sma_slope"].abs() < R_TH_SIDE

    return d


def evaluate_long_signal(df: pd.DataFrame, idx: int,
                         open_positions: dict,
                         last_exits: dict,
                         bar_counter: int,
                         monthly_pnl_l: float = 0.0,
                         monthly_entries_l: int = 0) -> dict:
    """
    評估 L（做多）進場信號。

    GK<25 AND breakout_long AND session AND cooldown AND maxTotal=1
    AND monthly entry cap AND monthly loss cap

    Returns:
        {action, sub_strategy, reason, indicators} 或 None
    """
    row = df.iloc[idx]
    indicators = _collect_indicators(row)

    gp = row["gk_pctile"]
    if pd.isna(gp):
        return None

    # GK 壓縮
    if gp >= L_GK_THRESH:
        return None

    # Breakout long
    if not _safe_bool(row.get("breakout_long")):
        return None

    # Session（V13: L 獨立 session filter）
    if not _safe_bool(row.get("session_ok_l")):
        return None

    # V14+R Regime Gate: block L in strong uptrend
    if _safe_bool(row.get("regime_block_l")):
        return None

    # Cooldown
    last_l = last_exits.get("L", -9999)
    if (bar_counter - last_l) < L_EXIT_CD:
        return None

    # maxTotal=1
    l_count = sum(1 for p in open_positions.values() if p.get("sub_strategy") == "L")
    if l_count >= L_MAX_TOTAL:
        return None

    # Monthly entry cap
    if monthly_entries_l >= L_MONTHLY_ENTRY_CAP:
        return None

    # Monthly loss cap
    if monthly_pnl_l <= L_MONTHLY_LOSS_CAP:
        return None

    return {
        "action": "BUY",
        "sub_strategy": "L",
        "reason": f"GK={gp:.1f}<{L_GK_THRESH}+BRK{BRK_LOOK}",
        "indicators": indicators,
    }


def evaluate_short_signal(df: pd.DataFrame, idx: int,
                          open_positions: dict,
                          last_exits: dict,
                          bar_counter: int,
                          monthly_pnl_s: float = 0.0,
                          monthly_entries_s: int = 0) -> dict:
    """
    評估 S（做空）進場信號。

    GK_S<35 AND breakout_short AND session_s AND cooldown AND maxTotal=1
    AND monthly entry cap AND monthly loss cap

    Returns:
        {action, sub_strategy, reason, indicators} 或 None
    """
    row = df.iloc[idx]
    indicators = _collect_indicators(row)

    gp = row["gk_pctile_s"]
    if pd.isna(gp):
        return None

    # GK 壓縮（V13: S 用自己的 GK pctile, 閾值 35）
    if gp >= S_GK_THRESH:
        return None

    # Breakout short
    if not _safe_bool(row.get("breakout_short")):
        return None

    # Session（V13: S 獨立 session filter）
    if not _safe_bool(row.get("session_ok_s")):
        return None

    # V14+R Regime Gate: block S in sideways regime
    if _safe_bool(row.get("regime_block_s")):
        return None

    # Cooldown
    last_s = last_exits.get("S", -9999)
    if (bar_counter - last_s) < S_EXIT_CD:
        return None

    # maxTotal=1
    s_count = sum(1 for p in open_positions.values() if p.get("sub_strategy") == "S")
    if s_count >= S_MAX_TOTAL:
        return None

    # Monthly entry cap
    if monthly_entries_s >= S_MONTHLY_ENTRY_CAP:
        return None

    # Monthly loss cap
    if monthly_pnl_s <= S_MONTHLY_LOSS_CAP:
        return None

    return {
        "action": "SELL",
        "sub_strategy": "S",
        "reason": f"GK={gp:.1f}<{S_GK_THRESH}+BRK{BRK_LOOK}",
        "indicators": indicators,
    }


def check_exit_long(entry_price: float,
                    entry_bar_counter: int, current_bar_counter: int,
                    bar_high: float, bar_low: float, bar_close: float,
                    extension_active: bool = False,
                    extension_start_bar: int = 0,
                    running_mfe: float = 0.0,
                    mh_reduced: bool = False) -> dict:
    """
    L 策略出場條件（V14: MFE Trailing + Conditional MH + 條件式延長 + BE trail）。

    優先順序：SafeNet 3.5% → TP 3.5% → MFE-trail → ext(BE/MH-ext) → MH(5or6)

    Returns:
        {"exit": bool, "reason": str, "exit_price": float,
         "start_extension": bool, "running_mfe": float, "mh_reduced": bool}
    """
    bars_held = current_bar_counter - entry_bar_counter
    effective_mh = L_COND_REDUCED_MH if mh_reduced else L_MAX_HOLD  # 5 or 6

    # 0. 更新 running MFE（用 bar_high）
    bar_mfe = (bar_high - entry_price) / entry_price
    new_mfe = max(running_mfe, bar_mfe)

    base = {"running_mfe": new_mfe, "mh_reduced": mh_reduced}

    # 1. SafeNet: low <= entry*(1-3.5%)（始終有效，含 extension 期間）
    safenet_level = entry_price * (1 - L_SAFENET_PCT)
    if bar_low <= safenet_level:
        ep = safenet_level - (safenet_level - bar_low) * 0.25
        return {"exit": True, "reason": "SafeNet", "exit_price": ep,
                "start_extension": False, **base}

    # 2. TP: high >= entry*(1+3.5%)（始終有效，含 extension 期間）
    tp_level = entry_price * (1 + L_TP_PCT)
    if bar_high >= tp_level:
        return {"exit": True, "reason": "TP", "exit_price": tp_level,
                "start_extension": False, **base}

    # 3. MFE Trailing（V14 新增）
    if bars_held >= L_MFE_MIN_BAR and new_mfe >= L_MFE_ACT:
        current_pnl_pct = (bar_close - entry_price) / entry_price
        drawdown_from_mfe = new_mfe - current_pnl_pct
        if drawdown_from_mfe >= L_MFE_TRAIL_DD:
            return {"exit": True, "reason": "MFE-trail", "exit_price": bar_close,
                    "start_extension": False, **base}

    # 4. Extension 階段：已進入延長期
    if extension_active:
        ext_bars = current_bar_counter - extension_start_bar
        # BE trail: 價格回到進場價以下 → 平保出場
        if bar_low <= entry_price:
            return {"exit": True, "reason": "BE", "exit_price": entry_price,
                    "start_extension": False, **base}
        # Extension 超時
        if ext_bars >= L_EXT_BARS:
            return {"exit": True, "reason": "MH-ext", "exit_price": bar_close,
                    "start_extension": False, **base}
        return {"exit": False, "reason": "", "exit_price": 0.0,
                "start_extension": False, **base}

    # 5. Conditional MH 判定（V14 新增：bar 2 時執行一次）
    new_mh_reduced = mh_reduced
    if not mh_reduced and bars_held == L_COND_CHECK_BAR:
        pnl_pct = (bar_close - entry_price) / entry_price
        if pnl_pct <= L_COND_EXIT_THRESH:
            new_mh_reduced = True
            effective_mh = L_COND_REDUCED_MH
    base["mh_reduced"] = new_mh_reduced

    # 6. MaxHold: bars >= effective_mh(5or6) → 判斷是否進入延長期
    if bars_held >= effective_mh:
        pnl_pct = (bar_close - entry_price) / entry_price
        if pnl_pct > 0:
            # 正收益 → 啟動延長期
            return {"exit": False, "reason": "", "exit_price": 0.0,
                    "start_extension": True, **base}
        else:
            return {"exit": True, "reason": "MaxHold", "exit_price": bar_close,
                    "start_extension": False, **base}

    return {"exit": False, "reason": "", "exit_price": 0.0,
            "start_extension": False, **base}


def check_exit_short(entry_price: float,
                     entry_bar_counter: int, current_bar_counter: int,
                     bar_high: float, bar_low: float, bar_close: float,
                     extension_active: bool = False,
                     extension_start_bar: int = 0) -> dict:
    """
    S 策略出場條件（V13: 條件式 MaxHold 延長 + BE trail）。

    優先順序：SafeNet 4.0% → TP 2.0% → MaxHold 10（+ext2 BE）

    Returns:
        {"exit": bool, "reason": str, "exit_price": float,
         "start_extension": bool}
    """
    bars_held = current_bar_counter - entry_bar_counter

    # 1. SafeNet: high >= entry*(1+4.0%)
    safenet_level = entry_price * (1 + S_SAFENET_PCT)
    if bar_high >= safenet_level:
        ep = safenet_level + (bar_high - safenet_level) * 0.25
        return {"exit": True, "reason": "SafeNet", "exit_price": ep,
                "start_extension": False}

    # 2. TP: low <= entry*(1-2.0%)
    tp_level = entry_price * (1 - S_TP_PCT)
    if bar_low <= tp_level:
        return {"exit": True, "reason": "TP", "exit_price": tp_level,
                "start_extension": False}

    # 3. Extension 階段：已進入延長期
    if extension_active:
        ext_bars = current_bar_counter - extension_start_bar
        # BE trail: 價格回到進場價以上 → 平保出場
        if bar_high >= entry_price:
            return {"exit": True, "reason": "BE", "exit_price": entry_price,
                    "start_extension": False}
        # Extension 超時
        if ext_bars >= S_EXT_BARS:
            return {"exit": True, "reason": "MH-ext", "exit_price": bar_close,
                    "start_extension": False}
        return {"exit": False, "reason": "", "exit_price": 0.0,
                "start_extension": False}

    # 4. MaxHold: bars >= 10 → 判斷是否進入延長期
    if bars_held >= S_MAX_HOLD:
        pnl_pct = (entry_price - bar_close) / entry_price
        if pnl_pct > 0:
            # 正收益 → 啟動延長期
            return {"exit": False, "reason": "", "exit_price": 0.0,
                    "start_extension": True}
        else:
            return {"exit": True, "reason": "MaxHold", "exit_price": bar_close,
                    "start_extension": False}

    return {"exit": False, "reason": "", "exit_price": 0.0,
            "start_extension": False}


def compute_pnl(entry_price: float, exit_price: float, side: str) -> tuple:
    """
    計算損益。

    Returns:
        (pnl_usd, pnl_pct)
        pnl_usd 已扣除 FEE ($4)
    """
    if side == "long":
        gross = (exit_price - entry_price) * NOTIONAL / entry_price
    else:
        gross = (entry_price - exit_price) * NOTIONAL / entry_price
    net = gross - FEE
    pct = net / MARGIN * 100
    return round(net, 4), round(pct, 4)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 內部工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _safe_float(val):
    """安全轉換為 float，NaN 回傳 None"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_bool(val):
    """安全轉 bool"""
    if val is None:
        return False
    try:
        if pd.isna(val):
            return False
    except (ValueError, TypeError):
        pass
    return bool(val)


def _collect_indicators(row) -> dict:
    """收集指標快照（V13: 含 L/S 獨立 GK 和 session）"""
    return {
        "gk_pctile": _safe_float(row.get("gk_pctile")),
        "gk_ratio": _safe_float(row.get("gk_ratio")),
        "gk_pctile_s": _safe_float(row.get("gk_pctile_s")),
        "gk_ratio_s": _safe_float(row.get("gk_ratio_s")),
        "ema20": _safe_float(row.get("ema20")),
        "close": _safe_float(row.get("close")),
        "breakout_15bar_max": _safe_float(row.get("breakout_15bar_max")),
        "breakout_15bar_min": _safe_float(row.get("breakout_15bar_min")),
        "breakout_long": _safe_bool(row.get("breakout_long")),
        "breakout_short": _safe_bool(row.get("breakout_short")),
        "session_ok_l": _safe_bool(row.get("session_ok_l")),
        "session_ok_s": _safe_bool(row.get("session_ok_s")),
        "sma200": _safe_float(row.get("sma200")),
        "sma_slope": _safe_float(row.get("sma_slope")),
        "regime_block_l": _safe_bool(row.get("regime_block_l")),
        "regime_block_s": _safe_bool(row.get("regime_block_s")),
        "hour_utc8": int(row.get("hour_utc8", -1)),
        "weekday_utc8": int(row.get("weekday_utc8", -1)),
    }
