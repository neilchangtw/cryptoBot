"""V44 第 15 輪低波動暫停 Q20W30 的 SHADOW 計算（只供 vps_viewer 顯示，不影響實盤）。

公式與 backtest/research/v44_r15_lowvolpause.py 的 vol_state 相同：
  ATR14% = TR 的 14 根均值 / close × 100
  V = 過去 30 天（720 根，不含當根）ATR14% 的中位數
  R = V 在過去 730 天 V 值中的百分位（至少 180 天；同值取平均名次）
  R < 20 → 暫停
只用標準庫：viewer 服務 MemoryMax=256M、CPUQuota=25%，不載入 pandas。
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from datetime import datetime, timedelta, timezone

PAUSE_Q = 20.0
WINDOW_DAYS = 30
RANK_BARS = 730 * 24
RANK_MIN_BARS = 180 * 24
ATR_BARS = 14
UTC8 = timezone(timedelta(hours=8))
LOCK_TIME = datetime(2026, 11, 1, tzinfo=UTC8)           # 規則鎖定日：之前的交易不計入
# 鎖定日的 R 要完整 730 天視窗（另加 V 的 30 天暖機），所以固定從這天抓，不隨時間往後移
HISTORY_START = datetime(2024, 7, 1, tzinfo=timezone.utc)
DOC_PATH = "doc/research_results/v44/v44_round15_results.md"


def vol_state(highs, lows, closes, window_days=WINDOW_DAYS):
    """回傳 (V, R) 兩個 list，資料不足的位置是 None。"""
    n = len(closes)
    atr = [None] * n
    trs = [None] * n
    for i in range(1, n):
        pc = closes[i - 1]
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
        if i >= ATR_BARS:
            atr[i] = sum(trs[i - ATR_BARS + 1:i + 1]) / ATR_BARS / closes[i] * 100

    bars = window_days * 24
    v = [None] * n
    window = []
    for i in range(1, n):
        add, drop = atr[i - 1], atr[i - 1 - bars] if i - 1 - bars >= 0 else None
        if add is not None:
            insort(window, add)
        if drop is not None:
            window.pop(bisect_left(window, drop))
        if len(window) == bars:
            middle = bars // 2
            v[i] = window[middle] if bars % 2 else (window[middle - 1] + window[middle]) / 2

    rank = [None] * n
    pool = []
    for i in range(n):
        if v[i] is not None:
            insort(pool, v[i])
        if i >= RANK_BARS and v[i - RANK_BARS] is not None:
            pool.pop(bisect_left(pool, v[i - RANK_BARS]))
        if v[i] is not None and len(pool) >= RANK_MIN_BARS:
            left, right = bisect_left(pool, v[i]), bisect_right(pool, v[i])
            rank[i] = (left + 1 + right) / 2 / len(pool) * 100
    return v, rank


def is_paused(rank_value) -> bool:
    return rank_value is not None and rank_value < PAUSE_Q


def compute_state(bars: list[dict]) -> dict | None:
    """bars 需依時間排序且都已收盤；資料不足回傳 None。"""
    if not bars:
        return None
    v, rank = vol_state([b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars])
    if rank[-1] is None:
        return None
    paused = is_paused(rank[-1])
    since = None
    if paused:
        i = len(bars) - 1
        while i > 0 and is_paused(rank[i - 1]):
            i -= 1
        since = bars[i].get("time_display")
    by_time = {b["time_ms"]: is_paused(r) if r is not None else None for b, r in zip(bars, rank)}
    return dict(v=v[-1], rank=rank[-1], paused=paused, since=since, last_bar=bars[-1].get("time_display"),
                by_time=by_time)


def _group(rows: list[dict]) -> dict:
    wins = sum(1 for row in rows if row["pnl"] > 0)
    return dict(trades=len(rows), wins=wins, win_rate=wins / len(rows) * 100 if rows else None,
                pnl=round(sum(row["pnl"] for row in rows), 2))


def shadow_ledger(rows: list[dict], by_time: dict, limit=8) -> dict:
    """rows：已平倉交易（pnl 已依金額基準換算）。依進場訊號棒是否暫停分組。"""
    normal, blocked, unknown, recent = [], [], [], []
    for row in sorted(rows, key=lambda r: r.get("entry_signal_time_utc") or ""):
        opened = datetime.fromisoformat(str(row.get("entry_signal_time_utc")).replace("Z", "+00:00"))
        if opened < LOCK_TIME:
            continue
        state = by_time.get(int(opened.timestamp() * 1000))
        (unknown if state is None else blocked if state else normal).append(row)
        recent.append(dict(number=row.get("number"), side=row.get("side"), entry=row.get("entry_time_display"),
                           exit_reason=row.get("exit_reason"), pnl=row.get("pnl"), blocked=state))
    return dict(normal=_group(normal), blocked=_group(blocked), unknown=len(unknown), recent=recent[-limit:][::-1])
