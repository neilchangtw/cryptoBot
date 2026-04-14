"""
ETH 1h V13 雙策略 — 純指標計算 + 信號判斷（無副作用）

策略 L（做多）：GK(5/20)<25 壓縮突破 + TP 3.5% + MaxHold 6 + ext2 BE
  OOS: $1,596, WR 43%, MDD $231, WF 6/6+7/8

策略 S（做空）：GK(10/30)<35 壓縮突破 + TP 2.0% + MaxHold 10 + ext2 BE
  OOS: $2,408, WR 62%, MDD $313, WF 5/6+7/8

L+S 合計：$4,004, 11/13 正月, worst -$32, WF L 6/6+7/8, S 5/6+7/8

V11-E→V13 變更：
  S GK 窗口: mean(5)/mean(20) → mean(10)/mean(30)
  S GK 閾值: <30 → <35
  S MaxHold: 7 → 10
  L/S MaxHold: 固定出場 → 條件式延長 + BE trail
  L block_days: {Mon,Sat,Sun} → {Sat,Sun}
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
WARMUP_BARS = GK_WIN + S_GK_LONG + 20  # 150 bar 暖機期（取較大窗口）

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
                    extension_start_bar: int = 0) -> dict:
    """
    L 策略出場條件（V13: 條件式 MaxHold 延長 + BE trail）。

    優先順序：SafeNet 3.5% → TP 3.5% → MaxHold 6（+ext2 BE）

    Returns:
        {"exit": bool, "reason": str, "exit_price": float,
         "start_extension": bool}
    """
    bars_held = current_bar_counter - entry_bar_counter

    # 1. SafeNet: low <= entry*(1-3.5%)
    safenet_level = entry_price * (1 - L_SAFENET_PCT)
    if bar_low <= safenet_level:
        ep = safenet_level - (safenet_level - bar_low) * 0.25
        return {"exit": True, "reason": "SafeNet", "exit_price": ep,
                "start_extension": False}

    # 2. TP: high >= entry*(1+3.5%)
    tp_level = entry_price * (1 + L_TP_PCT)
    if bar_high >= tp_level:
        return {"exit": True, "reason": "TP", "exit_price": tp_level,
                "start_extension": False}

    # 3. Extension 階段：已進入延長期
    if extension_active:
        ext_bars = current_bar_counter - extension_start_bar
        # BE trail: 價格回到進場價以下 → 平保出場
        if bar_low <= entry_price:
            return {"exit": True, "reason": "BE", "exit_price": entry_price,
                    "start_extension": False}
        # Extension 超時
        if ext_bars >= L_EXT_BARS:
            return {"exit": True, "reason": "MH-ext", "exit_price": bar_close,
                    "start_extension": False}
        return {"exit": False, "reason": "", "exit_price": 0.0,
                "start_extension": False}

    # 4. MaxHold: bars >= 6 → 判斷是否進入延長期
    if bars_held >= L_MAX_HOLD:
        pnl_pct = (bar_close - entry_price) / entry_price
        if pnl_pct > 0:
            # 正收益 → 啟動延長期
            return {"exit": False, "reason": "", "exit_price": 0.0,
                    "start_extension": True}
        else:
            return {"exit": True, "reason": "MaxHold", "exit_price": bar_close,
                    "start_extension": False}

    return {"exit": False, "reason": "", "exit_price": 0.0,
            "start_extension": False}


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
        "hour_utc8": int(row.get("hour_utc8", -1)),
        "weekday_utc8": int(row.get("weekday_utc8", -1)),
    }
