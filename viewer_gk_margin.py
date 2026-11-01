"""GK 分組配保證金的儀表板計算（只讀，不影響實盤）。

實盤邏輯在 strategy.gk_margin_mult；這裡的切點與預設倍數沿用 viewer_stack_shadow.N1_TABLE，
tests/test_gk_margin.py 檢查兩邊一致。viewer 不載入 .env，只從檔案取下面幾個非機密鍵。
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from viewer_lowvol_shadow import UTC8
from viewer_stack_shadow import N1_TABLE

LIVE_FROM = datetime(2026, 11, 1, 9, 1, tzinfo=UTC8)      # VPS 開啟時間；之前的單不算分組績效
KEYS = ("GK_MARGIN_ENABLED", "GK_MARGIN_MULT_L", "GK_MARGIN_MULT_S", "MARGIN_PER_TRADE")
GROUP_NAMES = ("低", "中", "高")


def read_settings(path: Path) -> dict:
    """只解析 KEYS；讀不到檔案時回傳空 dict（畫面顯示未知）。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() in KEYS:
            out[key.strip()] = value.split("#")[0].strip().strip("'\"")
    return out


def _mults(raw, default):
    try:
        values = tuple(float(x) for x in str(raw).split(","))
    except ValueError:
        return default
    return values if len(values) == 3 else default


def config(settings: dict) -> dict:
    year = max(N1_TABLE)
    enabled = None
    if settings:
        enabled = settings.get("GK_MARGIN_ENABLED", "N").upper() in ("Y", "YES", "TRUE", "1", "ON")
    try:
        base = float(settings.get("MARGIN_PER_TRADE", 200))
    except ValueError:
        base = 200.0
    tiers = {}
    for side in ("L", "S"):
        cuts, default = N1_TABLE[year][side]
        tiers[side] = dict(cuts=list(cuts), mults=list(_mults(settings.get(f"GK_MARGIN_MULT_{side}", ""), default)))
    return dict(enabled=enabled, base=base, tiers=tiers)


def group_of(side: str, gk, tiers: dict):
    return None if gk is None else bisect_right(tiers[side]["cuts"], gk)


def next_margin(side: str, gk, cfg: dict):
    """下一筆若開倉的 (保證金, 倍數)；未開或 GK 缺值時 1 倍。"""
    group = group_of(side, gk, cfg["tiers"])
    mult = cfg["tiers"][side]["mults"][group] if cfg["enabled"] and group is not None else 1.0
    return round(cfg["base"] * mult, 2), mult


def ledger(rows: list[dict], cfg: dict) -> dict:
    """rows：實盤已平倉交易（實際金額）。LIVE_FROM 起依進場 GK 分組。"""
    out = {}
    for side in ("L", "S"):
        groups = [dict(name=GROUP_NAMES[k], trades=0, wins=0, pnl=0.0) for k in range(3)]
        for row in rows:
            if row.get("side") != side or row.get("pnl") is None:
                continue
            entered = datetime.fromisoformat(str(row.get("entry_time_utc")).replace("Z", "+00:00"))
            gk = row.get("gk_pctile") if side == "L" else row.get("gk_pctile_s")
            group = group_of(side, gk, cfg["tiers"])
            if entered < LIVE_FROM or group is None:
                continue
            item = groups[group]
            item["trades"] += 1
            item["wins"] += row["pnl"] > 0
            item["pnl"] = round(item["pnl"] + row["pnl"], 2)
        for item in groups:
            item["win_rate"] = item["wins"] / item["trades"] * 100 if item["trades"] else None
        out[side] = groups
    return out
