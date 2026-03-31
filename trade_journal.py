"""
交易日誌 — SQLite 版

記錄完整交易生命週期，用於與回測數據對比分析。
  進場時 INSERT 一行（出場欄位留 NULL）
  TP1/出場時 UPDATE 該行

SQLite 優勢（vs CSV）：
  - UPDATE 單行不需讀寫整個檔案
  - 內建 transaction lock，多 thread 安全
  - SQL 查詢比 pandas filter 快
"""
import os
import sqlite3
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_journal.db")


def _get_conn():
    """取得 SQLite 連線（WAL mode + busy timeout）"""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row  # 讓查詢結果可用 dict-like 存取
    conn.execute("PRAGMA journal_mode=WAL")  # 讀寫並發安全
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_table():
    """確保資料表存在"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id       TEXT PRIMARY KEY,
            entry_time     TEXT,
            exit_time      TEXT,
            side           TEXT,
            entry_price    REAL,
            exit_price     REAL,
            qty            REAL,
            margin         REAL,
            rsi_entry      REAL,
            atr_entry      REAL,
            atr_pctile_entry REAL,
            bb_lower       REAL,
            bb_upper       REAL,
            structural_sl  REAL,
            tp1_target     REAL,
            swing_level    REAL,
            tp1_hit        TEXT,
            tp1_time       TEXT,
            tp1_price      REAL,
            exit_reason    TEXT,
            realized_pnl   REAL,
            pnl_pct        REAL,
            duration_min   REAL,
            rsi_exit       REAL,
            atr_pctile_exit REAL
        )
    """)
    conn.commit()
    conn.close()


# 啟動時確保表存在
_ensure_table()


def log_entry(trade_id, side, entry_price, qty, margin,
              rsi, atr, atr_pctile, bb_lower, bb_upper,
              structural_sl, tp1_target, swing_level):
    """開倉時記錄進場資訊"""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO trades (
            trade_id, entry_time, side, entry_price, qty, margin,
            rsi_entry, atr_entry, atr_pctile_entry,
            bb_lower, bb_upper, structural_sl, tp1_target, swing_level
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        side, entry_price, qty, margin,
        rsi, atr, atr_pctile,
        bb_lower, bb_upper, structural_sl, tp1_target, swing_level,
    ))
    conn.commit()
    conn.close()
    print(f"  [Journal] Entry logged: {trade_id}")


def log_tp1(trade_id, tp1_price, rsi=None):
    """TP1 觸發時更新記錄"""
    conn = _get_conn()
    conn.execute("""
        UPDATE trades SET tp1_hit = 'Y', tp1_time = ?, tp1_price = ?
        WHERE trade_id = ?
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tp1_price, trade_id))
    conn.commit()
    conn.close()
    print(f"  [Journal] TP1 logged: {trade_id} @ {tp1_price:.2f}")


def log_exit(trade_id, exit_price, exit_reason, realized_pnl, pnl_pct,
             rsi_exit=None, atr_pctile_exit=None):
    """出場時更新記錄（SL / Trail / Manual）"""
    now = datetime.now()

    # 計算持倉時間
    duration_min = None
    trade = get_trade(trade_id)
    if trade and trade["entry_time"]:
        try:
            entry_dt = datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S")
            duration_min = (now - entry_dt).total_seconds() / 60
        except Exception:
            pass

    conn = _get_conn()
    conn.execute("""
        UPDATE trades SET
            exit_time = ?, exit_price = ?, exit_reason = ?,
            realized_pnl = ?, pnl_pct = ?, duration_min = ?,
            rsi_exit = ?, atr_pctile_exit = ?
        WHERE trade_id = ?
    """, (
        now.strftime("%Y-%m-%d %H:%M:%S"), exit_price, exit_reason,
        realized_pnl, pnl_pct, duration_min,
        rsi_exit, atr_pctile_exit,
        trade_id,
    ))
    conn.commit()
    conn.close()
    print(f"  [Journal] Exit logged: {trade_id} reason={exit_reason} "
          f"pnl={realized_pnl:.4f} ({pnl_pct:+.2f}%)")


def get_trade(trade_id):
    """取得指定交易記錄"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_open_trades(side=None):
    """取得所有尚未出場的交易"""
    conn = _get_conn()
    if side:
        rows = conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NULL AND side = ?", (side,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades WHERE exit_time IS NULL"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def find_trade_id_by_position(side, entry_price=None):
    """用 side 找到匹配的 open trade_id（最新的優先）"""
    open_trades = get_open_trades(side=side)
    if not open_trades:
        return None
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
    return open_trades[-1]["trade_id"]


def get_all_trades():
    """取得所有交易記錄"""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY entry_time").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    """取得交易統計摘要"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN exit_time IS NOT NULL THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl_pct <= 0 AND exit_time IS NOT NULL THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN exit_time IS NOT NULL THEN pnl_pct END) as avg_pnl_pct,
            AVG(CASE WHEN exit_time IS NOT NULL THEN duration_min END) as avg_duration
        FROM trades
    """).fetchone()
    conn.close()
    return dict(row)
