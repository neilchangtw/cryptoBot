"""V44 三合一 SHADOW 計算（只供 vps_viewer 顯示，不影響實盤）。

每筆倍數 m = N1 × CT5 × Q，規則鎖定於 doc/v44_maxhold_reframe_plan.md 第 26 節：
  Q：第 15 輪 Q20W30 暫停 → 0
  CT5：R = close[i-1] / close[i-1-720] − 1；逆勢且 |R| ≤ 15% → 0.5
  N1：依進場 GK 分位查 N1_TABLE（每年初依 backtest/research/v44_stack_locked.py 重排一次）
評估點：滿 30 筆或 2027-04-30，先到者。評估前不改參數。
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

from viewer_lowvol_shadow import LOCK_TIME, UTC8

R_BARS = 720
CT_LIMIT = 0.15
CT_MULT = 0.5
TARGET_TRADES = 30
DEADLINE = "2027-04-30"
DOC_PATH = "doc/research_results/v44/v44_stack_locked_results.md"
N1_TABLE = {  # 年: {方向: (切點, 三組倍數)}
    2026: {"L": ((7.766667, 17.2), (0.6, 0.9, 1.5)), "S": ((11.1, 24.2), (1.5, 0.9, 0.6))},
}


def past_returns(bars: list[dict]) -> dict:
    """time_ms → R（第 i 根用 close[i-1] / close[i-1-720] − 1）。"""
    closes = [bar["close"] for bar in bars]
    out = {}
    for i in range(R_BARS + 1, len(bars) + 1):
        ms = bars[i]["time_ms"] if i < len(bars) else None
        value = closes[i - 1] / closes[i - 1 - R_BARS] - 1
        out[ms] = value                                       # None = 下一根（還沒收盤）
    return out


def ct_mult(side: str, r: float) -> float:
    against = (side == "L" and r < 0) or (side == "S" and r > 0)
    return CT_MULT if against and abs(r) <= CT_LIMIT else 1.0


def n1_mult(side: str, gk, year: int) -> float:
    table = N1_TABLE.get(year) or N1_TABLE[max(N1_TABLE)]
    cuts, mults = table[side]
    return mults[bisect_right(cuts, 50.0 if gk is None else gk)]


def multiplier(row: dict, paused_by_time: dict, r_by_time: dict):
    """回傳 (m, 說明)；對不到 K 棒時 m 是 None。"""
    opened = datetime.fromisoformat(str(row.get("entry_signal_time_utc")).replace("Z", "+00:00"))
    ms = int(opened.timestamp() * 1000)
    paused, r = paused_by_time.get(ms), r_by_time.get(ms)
    if paused is None or r is None:
        return None
    side = row.get("side")
    gk = row.get("gk_pctile") if side == "L" else row.get("gk_pctile_s")
    n1 = n1_mult(side, gk, opened.astimezone(UTC8).year)
    return 0.0 if paused else n1 * ct_mult(side, r)


def _curve(values):
    total = peak = mdd = 0.0
    for value in values:
        total += value
        peak = max(peak, total)
        mdd = max(mdd, peak - total)
    return round(total, 2), round(mdd, 2)


def shadow_ledger(rows: list[dict], paused_by_time: dict, r_by_time: dict, limit=8) -> dict:
    """rows：已平倉交易，依出場排序，用 pnl_200u。"""
    picked = []
    unknown = 0
    for row in rows:
        opened = datetime.fromisoformat(str(row.get("entry_signal_time_utc")).replace("Z", "+00:00"))
        if opened < LOCK_TIME or row.get("pnl_200u") is None:
            continue
        m = multiplier(row, paused_by_time, r_by_time)
        if m is None:
            unknown += 1
            continue
        picked.append((row, m))
    active = [m for _, m in picked if m > 0]
    mean_m = sum(active) / len(active) if active else None
    actual = [row["pnl_200u"] for row, _ in picked]
    shadow = [row["pnl_200u"] * m / mean_m if mean_m else 0.0 for row, m in picked]
    actual_pnl, actual_mdd = _curve(actual)
    shadow_pnl, shadow_mdd = _curve(shadow)
    recent = [dict(number=row.get("number"), side=row.get("side"), entry=row.get("entry_time_display"), m=round(m, 2),
                   pnl=round(row["pnl_200u"], 2)) for row, m in picked[-limit:][::-1]]
    return dict(trades=len(picked), unknown=unknown, mean_m=round(mean_m, 3) if mean_m else None,
                actual_pnl=actual_pnl, actual_mdd=actual_mdd, shadow_pnl=shadow_pnl, shadow_mdd=shadow_mdd,
                target=TARGET_TRADES, deadline=DEADLINE, recent=recent)


def current_state(paused: bool, r_next: float, year: int) -> dict:
    out = dict(paused=paused, r_pct=round(r_next * 100, 2), trend="上漲" if r_next > 0 else "下跌", sides={})
    table = N1_TABLE.get(year) or N1_TABLE[max(N1_TABLE)]
    for side in ("L", "S"):
        ct = ct_mult(side, r_next)
        against = (side == "L" and r_next < 0) or (side == "S" and r_next > 0)
        cuts, mults = table[side]
        out["sides"][side] = dict(against=against, ct=ct, q=0.0 if paused else 1.0, cuts=list(cuts),
                                  n1=list(mults), final=[0.0 if paused else round(x * ct, 3) for x in mults])
    return out
