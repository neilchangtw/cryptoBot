"""
4 層 CSV 記錄系統

Layer 1: bar_snapshots.csv     — 每小時一行，所有指標 + 信號評估
Layer 2: position_lifecycle.csv — 持倉期間每 bar 一行，MAE/MFE 追蹤
Layer 3: trades.csv            — 每筆交易一行，進場到出場完整記錄
Layer 4: daily_summary.csv     — 每日一行，當日彙總

原則：有用才記，每個欄位必須能回答一個具體複盤問題。
"""
import os
import csv
from datetime import datetime, timedelta

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 路徑
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

BAR_SNAPSHOTS_CSV = os.path.join(DATA_DIR, "bar_snapshots.csv")
POSITION_LIFECYCLE_CSV = os.path.join(DATA_DIR, "position_lifecycle.csv")
TRADES_CSV = os.path.join(DATA_DIR, "trades.csv")
DAILY_SUMMARY_CSV = os.path.join(DATA_DIR, "daily_summary.csv")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 欄位定義
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BAR_SNAPSHOT_FIELDS = [
    # 時間
    "bar_time_utc", "bar_time_utc8", "bar_weekday",
    # K 棒原始數據
    "open", "high", "low", "close", "volume", "taker_buy_volume",
    # GK 指標
    "gk_ratio", "gk_pctile",
    # Breakout 指標
    "breakout_long", "breakout_short",
    # L 專用指標
    "skew_20", "ret_sign_15",
    # EMA
    "ema20",
    # Session 狀態
    "session_allowed",
    # 信號評估
    "long_signal", "short_signals", "signal_detail",
    # 當前持倉狀態
    "long_positions", "short_positions", "total_unrealized_pnl",
]

POSITION_LIFECYCLE_FIELDS = [
    "trade_id", "lifecycle_bar", "bar_time_utc", "bar_time_utc8",
    "open", "high", "low", "close",
    # 損益追蹤
    "entry_price", "current_price", "unrealized_pnl_usd", "unrealized_pnl_pct",
    "max_adverse_so_far", "max_favorable_so_far",
    # 出場機制狀態
    "ema20", "distance_to_ema20_pct", "safenet_distance_pct",
    "earlyStop_eligible",
    # 本根是否出場
    "exit_triggered", "exit_type", "exit_price", "exit_pnl_usd",
]

TRADES_FIELDS = [
    # 交易識別
    "trade_id", "trade_number",
    # 進場資訊
    "entry_time_utc", "entry_time_utc8", "entry_weekday", "entry_hour_utc8",
    "direction", "sub_strategy", "entry_price", "entry_signal_bar_close",
    # 進場指標快照
    "gk_pctile_at_entry", "gk_ratio_at_entry",
    "breakout_bar_close", "breakout_10bar_max", "breakout_10bar_min",
    "breakout_strength_pct",
    "ema20_at_entry", "ema20_distance_pct",
    "was_cooldown_trade", "bars_since_last_exit",
    # 持倉過程統計
    "hold_bars", "hold_hours",
    "max_adverse_excursion_pct", "max_adverse_excursion_usd",
    "max_favorable_excursion_pct", "max_favorable_excursion_usd",
    "mae_time_bar", "mfe_time_bar",
    "pnl_at_bar7", "pnl_at_bar12",
    # 出場資訊
    "exit_time_utc", "exit_time_utc8",
    "exit_type", "exit_price", "exit_trigger_bar",
    # 損益計算
    "gross_pnl_usd", "commission_usd", "net_pnl_usd", "net_pnl_pct", "win_loss",
    # 市場背景
    "btc_close_at_entry", "eth_btc_ratio_at_entry", "eth_24h_change_pct",
    # 事後驗證欄位（複盤填寫）
    "backtest_had_same_trade", "backtest_entry_price",
    "backtest_exit_type", "backtest_pnl_usd", "discrepancy_note",
    # 主觀複盤（手動填寫）
    "review_note", "pattern_tag", "lesson",
]

DAILY_SUMMARY_FIELDS = [
    "date",
    "total_trades", "long_trades", "short_trades",
    "wins", "losses",
    "gross_pnl", "net_pnl",
    "safenet_count", "earlyStop_count", "trail_count", "tp_count", "maxhold_count",
    "avg_hold_hours", "longest_hold_hours",
    "account_balance", "cumulative_pnl",
    "open_position", "system_alerts",
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 內部工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ensure_dirs():
    """確保 data/ 和 logs/ 目錄存在"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


def _ensure_csv(filepath: str, headers: list):
    """如果 CSV 不存在，建立空檔案並寫入 header"""
    _ensure_dirs()
    if not os.path.exists(filepath):
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()


def _append_row(filepath: str, headers: list, row: dict):
    """追加一行到 CSV"""
    _ensure_csv(filepath, headers)
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writerow(row)


def _fmt(val, decimals=4):
    """格式化數值：None→空字串，float→指定小數位"""
    if val is None:
        return ""
    if isinstance(val, float):
        return round(val, decimals)
    return val


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: bar_snapshots.csv
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def record_bar_snapshot(bar_data: dict, indicators: dict,
                        signal_result: dict, position_state: dict):
    """
    記錄每小時 K 棒快照。無論有沒有交易都記錄。

    Args:
        bar_data: {datetime, open, high, low, close, volume, taker_buy_volume}
                  datetime 是 UTC+8
        indicators: compute_indicators() 在此 bar 的值
        signal_result: {long_signal, short_signals, signal_detail}
        position_state: {long_positions, short_positions, total_unrealized_pnl}
    """
    dt_utc8 = bar_data["datetime"]
    dt_utc = dt_utc8 - timedelta(hours=8) if isinstance(dt_utc8, datetime) else dt_utc8

    row = {
        "bar_time_utc": str(dt_utc),
        "bar_time_utc8": str(dt_utc8),
        "bar_weekday": dt_utc8.weekday() if isinstance(dt_utc8, datetime) else "",
        "open": _fmt(bar_data.get("open")),
        "high": _fmt(bar_data.get("high")),
        "low": _fmt(bar_data.get("low")),
        "close": _fmt(bar_data.get("close")),
        "volume": _fmt(bar_data.get("volume"), 2),
        "taker_buy_volume": _fmt(bar_data.get("taker_buy_volume"), 2),
        "gk_ratio": _fmt(indicators.get("gk_ratio"), 6),
        "gk_pctile": _fmt(indicators.get("gk_pctile"), 2),
        "breakout_long": indicators.get("breakout_long", False),
        "breakout_short": indicators.get("breakout_short", False),
        "skew_20": _fmt(indicators.get("skew_20"), 3),
        "ret_sign_15": _fmt(indicators.get("ret_sign_15"), 3),
        "ema20": _fmt(indicators.get("ema20")),
        "session_allowed": indicators.get("session_ok", False),
        "long_signal": signal_result.get("long_signal", "HOLD"),
        "short_signals": signal_result.get("short_signals", "HOLD"),
        "signal_detail": signal_result.get("signal_detail", ""),
        "long_positions": position_state.get("long_positions", 0),
        "short_positions": position_state.get("short_positions", 0),
        "total_unrealized_pnl": _fmt(position_state.get("total_unrealized_pnl"), 2),
    }
    _append_row(BAR_SNAPSHOTS_CSV, BAR_SNAPSHOT_FIELDS, row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: position_lifecycle.csv
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def record_position_bar(trade_id: str, position: dict, bar_data: dict,
                        ema20: float, exit_result: dict = None):
    """
    記錄持倉期間每 bar 的生命週期。

    Args:
        trade_id: 交易 ID
        position: {side, entry_price, entry_bar_counter, bars_held, mae_pct, mfe_pct}
        bar_data: {datetime, open, high, low, close}
        ema20: 當前 EMA20
        exit_result: check_exit() 的結果（如果本 bar 出場）
    """
    dt_utc8 = bar_data["datetime"]
    dt_utc = dt_utc8 - timedelta(hours=8) if isinstance(dt_utc8, datetime) else dt_utc8
    entry_price = position["entry_price"]
    side = position["side"]
    close = bar_data["close"]
    bars_held = position["bars_held"]

    # 防護：entry_price 為 0 時跳過（testnet 偶爾回傳 avgPrice=0）
    if not entry_price or entry_price <= 0:
        return

    # 未實現損益
    if side == "long":
        unr_pct = (close - entry_price) / entry_price * 100
    else:
        unr_pct = (entry_price - close) / entry_price * 100
    from strategy import NOTIONAL
    unr_usd = unr_pct / 100 * NOTIONAL

    # EMA20 距離
    ema_dist = (close - ema20) / close * 100 if close > 0 else 0

    # SafeNet 距離（從 strategy 讀取常數，避免硬編碼）
    from strategy import SAFENET_PCT
    if side == "long":
        sn_level = entry_price * (1 - SAFENET_PCT)
        sn_dist = (close - sn_level) / close * 100
    else:
        sn_level = entry_price * (1 + SAFENET_PCT)
        sn_dist = (sn_level - close) / close * 100

    exited = exit_result is not None and exit_result.get("exit", False)

    row = {
        "trade_id": trade_id,
        "lifecycle_bar": bars_held,
        "bar_time_utc": str(dt_utc),
        "bar_time_utc8": str(dt_utc8),
        "open": _fmt(bar_data.get("open")),
        "high": _fmt(bar_data.get("high")),
        "low": _fmt(bar_data.get("low")),
        "close": _fmt(close),
        "entry_price": _fmt(entry_price),
        "current_price": _fmt(close),
        "unrealized_pnl_usd": _fmt(unr_usd, 2),
        "unrealized_pnl_pct": _fmt(unr_pct, 2),
        "max_adverse_so_far": _fmt(position.get("mae_pct"), 2),
        "max_favorable_so_far": _fmt(position.get("mfe_pct"), 2),
        "ema20": _fmt(ema20),
        "distance_to_ema20_pct": _fmt(ema_dist, 2),
        "safenet_distance_pct": _fmt(sn_dist, 2),
        "earlyStop_eligible": 7 <= bars_held < 12 if position.get("sub_strategy", "L") == "L" else False,
        "exit_triggered": exited,
        "exit_type": exit_result.get("reason", "") if exited else "",
        "exit_price": _fmt(exit_result.get("exit_price")) if exited else "",
        "exit_pnl_usd": "",
    }

    if exited:
        from strategy import compute_pnl
        pnl_usd, _ = compute_pnl(entry_price, exit_result["exit_price"], side)
        row["exit_pnl_usd"] = _fmt(pnl_usd, 2)

    _append_row(POSITION_LIFECYCLE_CSV, POSITION_LIFECYCLE_FIELDS, row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: trades.csv
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def record_trade_open(trade: dict):
    """
    進場時寫入一行（出場欄位空白）。

    trade dict 需包含:
        trade_id, trade_number, entry_time_utc, entry_time_utc8,
        entry_weekday, entry_hour_utc8, direction, entry_price,
        entry_signal_bar_close, gk_pctile_at_entry, gk_ratio_at_entry,
        breakout_bar_close, breakout_10bar_max, breakout_10bar_min,
        breakout_strength_pct, ema20_at_entry, ema20_distance_pct,
        was_cooldown_trade, bars_since_last_exit,
        btc_close_at_entry, eth_btc_ratio_at_entry, eth_24h_change_pct
    """
    _ensure_csv(TRADES_CSV, TRADES_FIELDS)
    row = {field: trade.get(field, "") for field in TRADES_FIELDS}
    _append_row(TRADES_CSV, TRADES_FIELDS, row)


def record_trade_close(trade_id: str, exit_data: dict):
    """
    出場時更新 trades.csv 中對應 trade_id 的行。
    使用 read-modify-write（檔案最多幾百行，效能無問題）。

    exit_data 需包含:
        exit_time_utc, exit_time_utc8, exit_type, exit_price, exit_trigger_bar,
        hold_bars, hold_hours,
        max_adverse_excursion_pct, max_adverse_excursion_usd,
        max_favorable_excursion_pct, max_favorable_excursion_usd,
        mae_time_bar, mfe_time_bar, pnl_at_bar7, pnl_at_bar12,
        gross_pnl_usd, commission_usd, net_pnl_usd, net_pnl_pct, win_loss
    """
    _ensure_csv(TRADES_CSV, TRADES_FIELDS)

    # 讀取所有行
    rows = []
    with open(TRADES_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["trade_id"] == trade_id:
                row.update({k: v for k, v in exit_data.items() if k in TRADES_FIELDS})
            rows.append(row)

    # 重寫
    with open(TRADES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 4: daily_summary.csv
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def record_daily_summary(stats: dict):
    """
    每日結束時寫入一行彙總。

    stats dict 需包含 DAILY_SUMMARY_FIELDS 中的欄位。
    """
    row = {field: stats.get(field, "") for field in DAILY_SUMMARY_FIELDS}
    _append_row(DAILY_SUMMARY_CSV, DAILY_SUMMARY_FIELDS, row)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 讀取工具（給 check_health / compare_backtest 用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def read_trades() -> list:
    """讀取 trades.csv，回傳 list of dict"""
    if not os.path.exists(TRADES_CSV):
        return []
    import pandas as pd
    df = pd.read_csv(TRADES_CSV)
    return df.to_dict("records")


def read_bar_snapshots() -> list:
    """讀取 bar_snapshots.csv"""
    if not os.path.exists(BAR_SNAPSHOTS_CSV):
        return []
    import pandas as pd
    df = pd.read_csv(BAR_SNAPSHOTS_CSV)
    return df.to_dict("records")


def read_daily_summaries() -> list:
    """讀取 daily_summary.csv"""
    if not os.path.exists(DAILY_SUMMARY_CSV):
        return []
    import pandas as pd
    df = pd.read_csv(DAILY_SUMMARY_CSV)
    return df.to_dict("records")
