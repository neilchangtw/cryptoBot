"""
交易日誌 — 記錄完整交易生命週期，用於與回測數據對比分析

CSV 格式：
  進場時寫入一行（出場欄位留空）
  TP1/出場時更新該行

欄位與回測輸出對齊：
  entry_time, exit_time, side, entry_price, exit_price,
  qty, rsi, atr, structural_sl, tp1_target, exit_reason, pnl, ...
"""
import os
import csv
from datetime import datetime

JOURNAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.csv")

COLUMNS = [
    "trade_id",
    "entry_time", "exit_time",
    "side",
    "entry_price", "exit_price",
    "qty", "margin",
    # 進場指標
    "rsi_entry", "atr_entry", "atr_pctile_entry",
    "bb_lower", "bb_upper",
    "structural_sl", "tp1_target", "swing_level",
    # TP1
    "tp1_hit", "tp1_time", "tp1_price",
    # 出場
    "exit_reason",  # sl_hit / adaptive_trail / manual
    "realized_pnl", "pnl_pct",
    "duration_min",
    # 出場時指標
    "rsi_exit", "atr_pctile_exit",
]


def _ensure_header():
    """確保 CSV 存在且有 header"""
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()


def log_entry(trade_id, side, entry_price, qty, margin,
              rsi, atr, atr_pctile, bb_lower, bb_upper,
              structural_sl, tp1_target, swing_level):
    """開倉時記錄進場資訊"""
    _ensure_header()
    row = {
        "trade_id": trade_id,
        "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "side": side,
        "entry_price": f"{entry_price:.2f}",
        "qty": qty,
        "margin": f"{margin:.2f}",
        "rsi_entry": f"{rsi:.1f}",
        "atr_entry": f"{atr:.2f}",
        "atr_pctile_entry": f"{atr_pctile:.1f}",
        "bb_lower": f"{bb_lower:.2f}",
        "bb_upper": f"{bb_upper:.2f}",
        "structural_sl": f"{structural_sl:.2f}",
        "tp1_target": f"{tp1_target:.2f}",
        "swing_level": f"{swing_level:.2f}",
    }
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writerow(row)
    print(f"  [Journal] Entry logged: {trade_id}")


def log_tp1(trade_id, tp1_price, rsi=None):
    """TP1 觸發時更新記錄"""
    updates = {
        "tp1_hit": "Y",
        "tp1_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tp1_price": f"{tp1_price:.2f}",
    }
    _update_row(trade_id, updates)
    print(f"  [Journal] TP1 logged: {trade_id} @ {tp1_price:.2f}")


def log_exit(trade_id, exit_price, exit_reason, realized_pnl, pnl_pct,
             rsi_exit=None, atr_pctile_exit=None):
    """出場時更新記錄（SL / Trail / Manual）"""
    now = datetime.now()

    updates = {
        "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "exit_price": f"{exit_price:.2f}",
        "exit_reason": exit_reason,
        "realized_pnl": f"{realized_pnl:.4f}",
        "pnl_pct": f"{pnl_pct:.2f}",
    }

    if rsi_exit is not None:
        updates["rsi_exit"] = f"{rsi_exit:.1f}"
    if atr_pctile_exit is not None:
        updates["atr_pctile_exit"] = f"{atr_pctile_exit:.1f}"

    # 計算持倉時間
    trade = get_trade(trade_id)
    if trade and trade.get("entry_time"):
        try:
            entry_dt = datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S")
            updates["duration_min"] = f"{(now - entry_dt).total_seconds() / 60:.1f}"
        except Exception:
            pass

    _update_row(trade_id, updates)
    print(f"  [Journal] Exit logged: {trade_id} reason={exit_reason} "
          f"pnl={realized_pnl:.4f} ({pnl_pct:+.2f}%)")


def _update_row(trade_id, updates):
    """更新指定 trade_id 的行"""
    if not os.path.exists(JOURNAL_FILE):
        return

    rows = []
    found = False
    with open(JOURNAL_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("trade_id") == trade_id:
                row.update(updates)
                found = True
            rows.append(row)

    if not found:
        return

    with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def get_trade(trade_id):
    """取得指定交易記錄"""
    if not os.path.exists(JOURNAL_FILE):
        return None
    with open(JOURNAL_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("trade_id") == trade_id:
                return row
    return None


def get_open_trades(side=None):
    """取得所有尚未出場的交易"""
    if not os.path.exists(JOURNAL_FILE):
        return []
    open_trades = []
    with open(JOURNAL_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("exit_time"):
                if side is None or row.get("side") == side:
                    open_trades.append(row)
    return open_trades


def find_trade_id_by_position(side, entry_price=None):
    """用 side 找到匹配的 open trade_id（最新的優先）"""
    open_trades = get_open_trades(side=side)
    if not open_trades:
        return None
    # 只有一筆：直接回傳
    if len(open_trades) == 1:
        return open_trades[0]["trade_id"]
    # 多筆：用 entry_price 匹配最接近的
    if entry_price is not None:
        best = None
        best_diff = float("inf")
        for trade in open_trades:
            try:
                t_entry = float(trade.get("entry_price", 0))
                diff = abs(t_entry - entry_price)
                if diff < best_diff:
                    best_diff = diff
                    best = trade
            except (ValueError, TypeError):
                continue
        if best:
            return best["trade_id"]
    # fallback：回傳最新的
    return open_trades[-1]["trade_id"]
