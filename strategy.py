"""
ETH 1h 雙策略 — 純指標計算 + 信號判斷（無副作用）

策略 L（做多）：GK+Skew+RetSign OR-entry, maxSame=9, EMA20 trail 出場
  OOS: $13,776, PF 2.90, WR 45%, MDD 12.3%

策略 S（做空）：CMP-Portfolio 4 子策略並行
  Sub1: GK40+BL8, Sub2: GK40+BL15, Sub3: GK30+BL10, Sub4: GK40+BL12
  TP 2% + MaxHold 12 bar, EXIT_CD=6, maxSame=5/子策略
  OOS: $10,049, PF 1.71, WR 65%, MDD 17.6%

合計 OOS: $23,825, 8/8 Gate PASS
"""
import numpy as np
import pandas as pd

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 共用常數
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GK_SHORT = 5               # GK 短期均值窗口
GK_LONG = 20               # GK 長期均值窗口
GK_WIN = 100               # GK percentile 滾動窗口
BRK_LOOK = 10              # L 策略 breakout lookback（10 bar）
SAFENET_PCT = 0.055         # ±5.5% 安全網（L/S 共用）
BLOCK_H = {0, 1, 2, 12}    # UTC+8 封鎖時段
BLOCK_D = {0, 5, 6}        # 封鎖星期（Mon=0, Sat=5, Sun=6）
FEE = 2.0                  # 每筆交易成本（含滑價）
MARGIN = 100               # 每筆保證金
LEVERAGE = 20              # 槓桿倍數
NOTIONAL = MARGIN * LEVERAGE  # $2,000 名目金額
WARMUP_BARS = GK_WIN + GK_LONG + 20  # 140 bar 暖機期

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# L 策略常數
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
L_GK_THRESH = 30           # L 的 GK 壓縮閾值
L_MAX_SAME = 9             # L 最大同時持倉數
L_EXIT_CD = 12             # L 出場後冷卻 12 bar
MIN_TRAIL = 7              # EMA20 Trail 最小持倉 bar 數
EARLY_STOP_PCT = 0.010     # EarlyStop 虧損閾值 1%
EARLY_STOP_END = 12        # EarlyStop 適用範圍上限 bar

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# S 策略常數（CMP-Portfolio）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CMP_TP_PCT = 0.02           # 固定止盈 2%
CMP_MAX_HOLD = 12           # 最大持倉 12 bar

S_SUBS = [
    {"id": "S1", "gk_thresh": 40, "brk_look": 8,  "max_same": 5, "exit_cd": 6},
    {"id": "S2", "gk_thresh": 40, "brk_look": 15, "max_same": 5, "exit_cd": 6},
    {"id": "S3", "gk_thresh": 30, "brk_look": 10, "max_same": 5, "exit_cd": 6},
    {"id": "S4", "gk_thresh": 40, "brk_look": 12, "max_same": 5, "exit_cd": 6},
]

# S 子策略需要的 breakout lookback 集合
_S_BRK_LOOKS = sorted(set(s["brk_look"] for s in S_SUBS))


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    計算所有策略指標。

    Input:
        df: DataFrame with columns [open, high, low, close, volume, datetime]
            datetime 必須是 UTC+8
    Returns:
        df copy with added indicator columns
    """
    d = df.copy()

    # EMA20（L 出場用）
    d["ema20"] = d["close"].ewm(span=20).mean()

    # Returns
    d["ret"] = d["close"].pct_change()

    # ── GK Volatility ──
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl ** 2 - (2 * np.log(2) - 1) * ln_co ** 2
    gk_short = gk.rolling(GK_SHORT).mean()
    gk_long = gk.rolling(GK_LONG).mean()
    d["gk_ratio"] = gk_short / gk_long

    # GK Percentile: shift(1) BEFORE rolling — 防前瞻
    d["gk_pctile"] = d["gk_ratio"].shift(1).rolling(GK_WIN).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50,
        raw=False
    )

    # ── L 專用指標：Skew + RetSign（shift(1) 防前瞻）──
    d["skew_20"] = d["ret"].rolling(20).skew().shift(1)
    d["ret_sign_15"] = (d["ret"] > 0).astype(float).rolling(15).mean().shift(1)

    # ── Breakout: L 用 BL10 ──
    d["close_shift1"] = d["close"].shift(1)
    d["breakout_10bar_max"] = d["close"].shift(2).rolling(BRK_LOOK - 1).max()
    d["breakout_10bar_min"] = d["close"].shift(2).rolling(BRK_LOOK - 1).min()
    d["breakout_long"] = d["close_shift1"] > d["breakout_10bar_max"]
    d["breakout_short"] = d["close_shift1"] < d["breakout_10bar_min"]

    # ── S 子策略用多 lookback breakout ──
    for bl in _S_BRK_LOOKS:
        if bl == BRK_LOOK:
            # BL10 已經計算了
            d[f"brk_short_{bl}"] = d["breakout_short"]
        else:
            d[f"brk_short_{bl}"] = d["close_shift1"] < d["close"].shift(2).rolling(bl - 1).min()

    # ── Session Filter ──
    d["hour_utc8"] = d["datetime"].dt.hour
    d["weekday_utc8"] = d["datetime"].dt.weekday
    d["session_ok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["weekday_utc8"].isin(BLOCK_D))

    return d


def evaluate_long_signal(df: pd.DataFrame, idx: int,
                         open_positions: dict,
                         last_exits: dict,
                         bar_counter: int) -> dict:
    """
    評估 L（做多）進場信號。

    OR-entry: gk<30 OR skew(20)>1.0 OR ret_sign(15)>0.60
    AND: breakout_long + session + cooldown + count < 9

    Returns:
        {action, sub_strategy, reason, indicators} 或 None
    """
    row = df.iloc[idx]

    indicators = _collect_indicators(row)

    gp = row["gk_pctile"]
    if pd.isna(gp):
        return None

    # OR-entry 條件
    cond_gk = gp < L_GK_THRESH
    skew = row.get("skew_20")
    cond_skew = (not pd.isna(skew)) and skew > 1.0
    ret_sign = row.get("ret_sign_15")
    cond_ret = (not pd.isna(ret_sign)) and ret_sign > 0.60

    or_entry = cond_gk or cond_skew or cond_ret
    if not or_entry:
        return None

    # Breakout long
    bl = _safe_bool(row.get("breakout_long"))
    if not bl:
        return None

    # Session
    if not _safe_bool(row.get("session_ok")):
        return None

    # 研究回測無 freshness — 不加此條件

    # Cooldown
    last_l = last_exits.get("L", -9999)
    if (bar_counter - last_l) < L_EXIT_CD:
        return None

    # Position count
    l_count = sum(1 for p in open_positions.values() if p.get("sub_strategy") == "L")
    if l_count >= L_MAX_SAME:
        return None

    # 觸發原因
    reasons = []
    if cond_gk:
        reasons.append(f"gk={gp:.1f}")
    if cond_skew:
        reasons.append(f"skew={skew:.2f}")
    if cond_ret:
        reasons.append(f"ret_sign={ret_sign:.2f}")

    return {
        "action": "BUY",
        "sub_strategy": "L",
        "reason": "OR:" + "+".join(reasons),
        "indicators": indicators,
    }


def evaluate_short_signals(df: pd.DataFrame, idx: int,
                           open_positions: dict,
                           last_exits: dict,
                           bar_counter: int) -> list:
    """
    評估 S（做空）CMP-Portfolio 進場信號。

    遍歷 4 個子策略，各自獨立檢查。
    不套用 freshness（回測無此條件）。

    Returns:
        [{action, sub_strategy, reason, indicators}, ...] (0-4 個)
    """
    row = df.iloc[idx]

    gp = row["gk_pctile"]
    if pd.isna(gp):
        return []

    sok = _safe_bool(row.get("session_ok"))
    if not sok:
        return []

    indicators = _collect_indicators(row)
    signals = []

    for sub in S_SUBS:
        sub_id = sub["id"]

        # GK threshold
        if gp >= sub["gk_thresh"]:
            continue

        # Breakout short (sub-specific lookback)
        brk_col = f"brk_short_{sub['brk_look']}"
        if not _safe_bool(row.get(brk_col)):
            continue

        # Cooldown (per sub-strategy)
        last_exit = last_exits.get(sub_id, -9999)
        if (bar_counter - last_exit) < sub["exit_cd"]:
            continue

        # Position count (per sub-strategy)
        sub_count = sum(1 for p in open_positions.values()
                        if p.get("sub_strategy") == sub_id)
        if sub_count >= sub["max_same"]:
            continue

        signals.append({
            "action": "SELL",
            "sub_strategy": sub_id,
            "reason": f"{sub_id}:gk{sub['gk_thresh']}_BL{sub['brk_look']}",
            "indicators": indicators,
        })

    return signals


def check_exit(side: str, entry_price: float,
               entry_bar_counter: int, current_bar_counter: int,
               bar_high: float, bar_low: float, bar_close: float,
               ema20: float) -> dict:
    """
    L 策略出場條件（趨勢跟隨：SafeNet + EarlyStop + EMA20 Trail）。
    只用於 sub_strategy == "L" 的持倉。

    Returns:
        {"exit": bool, "reason": str, "exit_price": float}
    """
    bars_held = current_bar_counter - entry_bar_counter
    sn = SAFENET_PCT
    mt = MIN_TRAIL
    es = EARLY_STOP_PCT
    ese = EARLY_STOP_END

    # 注意：L 策略只做多，side 永遠是 "long"
    if side == "long":
        # 1. SafeNet: 價格跌破 entry*(1-5.5%) → 強制出場
        safenet_level = entry_price * (1 - sn)
        if bar_low <= safenet_level:
            ep = safenet_level - (safenet_level - bar_low) * 0.25
            return {"exit": True, "reason": "SafeNet", "exit_price": ep}

        # 2. bars 7-12: Trail OR EarlyStop
        if mt <= bars_held < ese:
            trail_hit = bar_close <= ema20
            early_hit = bar_close <= entry_price * (1 - es)
            if trail_hit or early_hit:
                reason = "EarlyStop" if (early_hit and not trail_hit) else "Trail"
                return {"exit": True, "reason": reason, "exit_price": bar_close}

        # 3. bars >= 12: Trail only
        if bars_held >= ese and bar_close <= ema20:
            return {"exit": True, "reason": "Trail", "exit_price": bar_close}

    return {"exit": False, "reason": "", "exit_price": 0.0}


def check_exit_cmp(entry_price: float,
                   entry_bar_counter: int, current_bar_counter: int,
                   bar_high: float, bar_low: float, bar_close: float) -> dict:
    """
    S CMP 策略出場條件（快速止盈：SafeNet + TP 2% + MaxHold 12）。
    只用於 sub_strategy == "S1"~"S4" 的持倉。

    優先順序：SafeNet > TP > MaxHold

    Returns:
        {"exit": bool, "reason": str, "exit_price": float}
    """
    bars_held = current_bar_counter - entry_bar_counter

    # 1. SafeNet: high >= entry*(1+5.5%)
    safenet_level = entry_price * (1 + SAFENET_PCT)
    if bar_high >= safenet_level:
        ep = safenet_level + (bar_high - safenet_level) * 0.25
        return {"exit": True, "reason": "SafeNet", "exit_price": ep}

    # 2. Take Profit: low <= entry*(1-2%)
    tp_level = entry_price * (1 - CMP_TP_PCT)
    if bar_low <= tp_level:
        return {"exit": True, "reason": "TP", "exit_price": tp_level}

    # 3. MaxHold: bars >= 12
    if bars_held >= CMP_MAX_HOLD:
        return {"exit": True, "reason": "MaxHold", "exit_price": bar_close}

    return {"exit": False, "reason": "", "exit_price": 0.0}


def compute_pnl(entry_price: float, exit_price: float, side: str) -> tuple:
    """
    計算損益。

    Returns:
        (pnl_usd, pnl_pct)
        pnl_usd 已扣除 FEE ($2)
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
    """收集指標快照"""
    return {
        "gk_pctile": _safe_float(row.get("gk_pctile")),
        "gk_ratio": _safe_float(row.get("gk_ratio")),
        "ema20": _safe_float(row.get("ema20")),
        "close": _safe_float(row.get("close")),
        "close_shift1": _safe_float(row.get("close_shift1")),
        "breakout_10bar_max": _safe_float(row.get("breakout_10bar_max")),
        "breakout_10bar_min": _safe_float(row.get("breakout_10bar_min")),
        "breakout_long": _safe_bool(row.get("breakout_long")),
        "breakout_short": _safe_bool(row.get("breakout_short")),
        "session_ok": _safe_bool(row.get("session_ok")),
        "hour_utc8": int(row.get("hour_utc8", -1)),
        "weekday_utc8": int(row.get("weekday_utc8", -1)),
        "skew_20": _safe_float(row.get("skew_20")),
        "ret_sign_15": _safe_float(row.get("ret_sign_15")),
    }
