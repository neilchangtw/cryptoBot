"""實戰交易明細重播與風控狀態稽核。

這不是用 K 棒重新猜成交價的純回測，而是把已發生的實戰進出場與
實際淨損益依時間重播，重建 executor 的日/月損益、月進場數與連虧冷卻。
用途是驗證歷史實戰明細，以及解釋標準回測多出的交易；不把實戰結果
當成未來策略績效。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta


BASE_L_MONTHLY_LOSS_CAP = -75.0
BASE_S_MONTHLY_LOSS_CAP = -150.0
BASE_DAILY_LOSS_LIMIT = -200.0
MONTHLY_ENTRY_CAP = 20
CONSEC_LOSS_PAUSE = 4
CONSEC_LOSS_COOLDOWN_HOURS = 24


def parse_replay_time(value) -> datetime:
    """解析 trade_viewer 統一後的 UTC+8 實際成交時間。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"無法解析實戰重播時間：{value!r}")


def margin_for_time(dt: datetime, margin_schedule) -> float:
    """依進場/檢查時間取得保證金；schedule 須按日期遞增。"""
    effective = float(margin_schedule[0][1]) if margin_schedule else 200.0
    for start_date, margin in margin_schedule:
        if dt.strftime("%Y-%m-%d") >= str(start_date):
            effective = float(margin)
        else:
            break
    return effective


def _remaining_bars(until: datetime | None, now: datetime) -> int:
    if until is None or now >= until:
        return 0
    return max(1, int(math.ceil((until - now).total_seconds() / 3600.0)))


@dataclass
class ReplayState:
    """executor 風控狀態的無副作用重播版本。"""

    monthly_pnl: dict[str, float] = field(default_factory=lambda: {"L": 0.0, "S": 0.0})
    monthly_entries: dict[str, int] = field(default_factory=lambda: {"L": 0, "S": 0})
    monthly_key: str | None = None
    daily_pnl: float = 0.0
    daily_key: str | None = None
    consec_losses: int = 0
    cooldown_until: datetime | None = None
    open_positions: set[str] = field(default_factory=set)

    def rollover(self, dt: datetime) -> None:
        month_key = dt.strftime("%Y-%m")
        day_key = dt.strftime("%Y-%m-%d")
        if self.monthly_key != month_key:
            self.monthly_key = month_key
            self.monthly_pnl = {"L": 0.0, "S": 0.0}
            self.monthly_entries = {"L": 0, "S": 0}
        if self.daily_key != day_key:
            self.daily_key = day_key
            self.daily_pnl = 0.0

    def _caps(self, dt: datetime) -> tuple[float, float, float]:
        scale = margin_for_time(dt, self.margin_schedule)
        return (
            BASE_DAILY_LOSS_LIMIT * scale / 200.0,
            BASE_L_MONTHLY_LOSS_CAP * scale / 200.0,
            BASE_S_MONTHLY_LOSS_CAP * scale / 200.0,
        )

    # Assigned by replay_trades; kept on state so state_at() uses the same schedule.
    margin_schedule: list[tuple[str, float]] = field(default_factory=list, repr=False)

    def block_reasons(self, side: str, dt: datetime) -> list[str]:
        """回傳與 executor 相同優先順序的風控阻擋原因。"""
        self.rollover(dt)
        daily_cap, l_cap, s_cap = self._caps(dt)
        reasons = []

        if self.consec_losses >= CONSEC_LOSS_PAUSE and self.cooldown_until and dt < self.cooldown_until:
            remaining = _remaining_bars(self.cooldown_until, dt)
            reasons.append(f"連虧{self.consec_losses}筆冷卻中（剩{remaining}bar）")

        if self.daily_pnl <= daily_cap:
            reasons.append(f"日虧${self.daily_pnl:.2f}已達上限${daily_cap:.2f}")

        cap = l_cap if side == "L" else s_cap
        pnl = self.monthly_pnl.get(side, 0.0)
        if pnl <= cap:
            reasons.append(f"{side}月虧${pnl:.2f}已達上限${cap:.2f}")

        entries = self.monthly_entries.get(side, 0)
        if entries >= MONTHLY_ENTRY_CAP:
            reasons.append(f"{side}月進場{entries}筆已達上限{MONTHLY_ENTRY_CAP}")

        return reasons

    def apply_entry(self, row: dict, dt: datetime) -> None:
        self.rollover(dt)
        side = str(row["side"]).upper()
        self.monthly_entries[side] = self.monthly_entries.get(side, 0) + 1
        self.open_positions.add(str(row["id"]))

    def apply_exit(self, row: dict, dt: datetime) -> None:
        self.rollover(dt)
        side = str(row["side"]).upper()
        pnl = float(row["pnl"])
        self.open_positions.discard(str(row["id"]))
        self.daily_pnl = round(self.daily_pnl + pnl, 4)
        self.monthly_pnl[side] = round(self.monthly_pnl.get(side, 0.0) + pnl, 4)
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= CONSEC_LOSS_PAUSE:
                self.cooldown_until = dt + timedelta(hours=CONSEC_LOSS_COOLDOWN_HOURS)
        else:
            self.consec_losses = 0


@dataclass
class ReplayResult:
    rows: list[dict]
    entry_audits: list[dict]
    state: ReplayState
    first_entry: datetime
    last_exit: datetime


def _events(rows: list[dict]) -> list[tuple[datetime, int, str, dict]]:
    events = []
    for row in rows:
        entry_dt = parse_replay_time(row["entry_time"])
        exit_dt = parse_replay_time(row["exit_time"])
        if exit_dt < entry_dt:
            raise ValueError(f"交易出場早於進場：{row.get('id', '?')}")
        # 同一時間先處理出場，再處理進場，對齊 main_eth.py 的執行順序。
        events.append((exit_dt, 0, "exit", row))
        events.append((entry_dt, 1, "entry", row))
    return sorted(events, key=lambda item: (item[0], item[1], str(item[3].get("id", ""))))


def replay_trades(rows: list[dict], margin_schedule=None) -> ReplayResult:
    """重播已發生交易，並記錄每筆實戰進場當下的風控狀態。"""
    if not rows:
        raise ValueError("實戰重播沒有交易資料")
    schedule = list(margin_schedule or [("2000-01-01", 200)])
    ordered = sorted(rows, key=lambda row: parse_replay_time(row["entry_time"]))
    state = ReplayState(margin_schedule=schedule)
    audits = []

    for dt, _priority, kind, row in _events(ordered):
        state.rollover(dt)
        if kind == "entry":
            side = str(row["side"]).upper()
            reasons = state.block_reasons(side, dt)
            audits.append({
                "row": row,
                "entry_time": dt,
                "risk_allowed": not reasons,
                "risk_reasons": reasons,
                "monthly_pnl": dict(state.monthly_pnl),
                "monthly_entries": dict(state.monthly_entries),
                "daily_pnl": state.daily_pnl,
                "consec_losses": state.consec_losses,
                "cooldown_until": state.cooldown_until,
            })
            # 實戰明細是已發生的事實；即使資料顯示違反風控，也要繼續重播，
            # 讓報表能指出違反，而不是靜默刪掉該筆交易。
            state.apply_entry(row, dt)
        else:
            state.apply_exit(row, dt)

    return ReplayResult(
        rows=ordered,
        entry_audits=audits,
        state=state,
        first_entry=parse_replay_time(ordered[0]["entry_time"]),
        last_exit=max(parse_replay_time(row["exit_time"]) for row in ordered),
    )


def state_at(rows: list[dict], target: datetime, margin_schedule=None) -> ReplayState:
    """重播至 hypothetical entry 前，供檢查回測多出的交易。"""
    schedule = list(margin_schedule or [("2000-01-01", 200)])
    state = ReplayState(margin_schedule=schedule)
    for dt, priority, kind, row in _events(sorted(rows, key=lambda r: parse_replay_time(r["entry_time"]))):
        # target 當根先處理實戰出場，再評估 hypothetical entry；不處理同時的實戰進場。
        if dt > target or (dt == target and priority > 0):
            break
        state.rollover(dt)
        if kind == "entry":
            state.apply_entry(row, dt)
        else:
            state.apply_exit(row, dt)
    state.rollover(target)
    return state


def compare_trade_entries(live_rows: list[dict], backtest_rows: list[dict]) -> dict:
    """依方向＋實際成交時間比對兩份明細，不比較編號。"""
    def key(row):
        return (str(row["side"]).upper(), parse_replay_time(row["entry_time"]))

    live_map = {key(row): row for row in live_rows}
    back_map = {key(row): row for row in backtest_rows}
    common_keys = sorted(set(live_map) & set(back_map), key=lambda item: item[1])
    return {
        "common": [(live_map[k], back_map[k]) for k in common_keys],
        "live_only": [live_map[k] for k in sorted(set(live_map) - set(back_map), key=lambda item: item[1])],
        "backtest_only": [back_map[k] for k in sorted(set(back_map) - set(live_map), key=lambda item: item[1])],
    }
