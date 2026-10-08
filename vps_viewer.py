"""VPS 唯讀交易視覺化服務。

此服務與交易機器人完全分離，只讀取：
  - INSTANCE_DIR/data_live/trades.csv
  - data/backtest_trades.txt（由 run_backtest.py -t 輸出的回測快照）
  - data/backtest_trade_metrics.csv（回測逐筆 MAE／MFE／進場 GK，由 refresh_viewer_backtest.py 產生）
  - run_backtest.py 的 MARGIN_SCHEDULE、strategy.py 的 V29 門檻常數（以 ast 讀取字面值，不執行、不匯入）
  - INSTANCE_DIR/eth_state_live.json
  - INSTANCE_DIR/logs/
  - Binance Futures 公開 ETHUSDT 1h K 線（不需要 API key）

不載入 .env、不匯入 strategy/executor/binance_trade、不提供任何寫入或下單 API。

資料模式：實戰使用即時公開 K 線與 data_live/trades.csv；回測使用本機 730 天 K 線快取
與 data/backtest_trades.txt。回測沒有即時持倉狀態。
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = Path(
    os.environ.get("VIEWER_INSTANCE_DIR")
    or os.environ.get("INSTANCE_DIR")
    or str(ROOT)
).expanduser().resolve()
LIVE_DIR = INSTANCE_DIR / "data_live"
STATE_PATH = INSTANCE_DIR / "eth_state_live.json"
LOG_DIR = INSTANCE_DIR / "logs"
HTML_PATH = ROOT / "vps_viewer.html"
LOCAL_KLINE_PATH = ROOT / "data" / "ETHUSDT_1h_latest730d.csv"
SHARED_KLINE_PATH = ROOT / "cache" / "ETHUSDT_1h.csv"
DEFAULT_BACKTEST_PATH = ROOT / "data" / "backtest_trades.txt"
DEFAULT_BACKTEST_SNAPSHOT_PATH = ROOT / "data" / "backtest_bar_snapshots.csv"
DEFAULT_BACKTEST_LIFECYCLE_PATH = ROOT / "data" / "backtest_position_lifecycle.csv"
DEFAULT_BACKTEST_METRICS_PATH = ROOT / "data" / "backtest_trade_metrics.csv"
RUN_BACKTEST_PATH = ROOT / "run_backtest.py"
STRATEGY_PATH = ROOT / "strategy.py"
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"
KLINE_TTL_SECONDS = 55
PRICE_TTL_SECONDS = 5
PRICE_FAILURE_TTL_SECONDS = 3
SIGNAL_TTL_SECONDS = 20
SIGNAL_TIMEOUT_SECONDS = 20
MAX_KLINES = 1000
MAX_RANGE_KLINES = 30000
UTC8 = timezone(timedelta(hours=8))
# 200U × 20x 研究基準名目；「200U 基準」PnL = 實際 PnL × 4000 ÷ 當筆名目（與 edge_falsify 相同換算）
BASELINE_MARGIN = 200.0
LEVERAGE = 20.0
BASELINE_NOTIONAL = BASELINE_MARGIN * LEVERAGE
ROLLING_WINDOW = 20
EDGE_RECENT_TRADES = 30
EDGE_MONTHS = 6
BASES = {"actual", "200u"}


def _number(value, default=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _first_number(row: dict, *names):
    for name in names:
        value = _number(row.get(name), None)
        if value is not None:
            return value
    return None


def _int(value, default=None):
    number = _number(value, None)
    return int(number) if number is not None else default


def _truthy(value):
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "ok"}


def _side(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in {"L", "LONG"}:
        return "L"
    if text in {"S", "SHORT"}:
        return "S"
    return text[:1] or "?"


def _parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text[:19], fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC8)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


def _display_time(value: datetime | None) -> str | None:
    return value.astimezone(UTC8).strftime("%Y-%m-%d %H:%M") if value else None


def _date_window(start_date: str, end_date: str):
    """Convert inclusive Asia/Taipei calendar dates to a UTC half-open range."""
    try:
        start_day = date.fromisoformat(str(start_date))
        end_day = date.fromisoformat(str(end_date))
    except (TypeError, ValueError) as exc:
        raise ValueError("請選擇有效的開始與結束日期") from exc
    if end_day < start_day:
        raise ValueError("結束日期不可早於開始日期")
    start = datetime.combine(start_day, datetime_time.min, UTC8).astimezone(timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), datetime_time.min, UTC8).astimezone(timezone.utc)
    return start, end


def _date_label(value: datetime | None):
    return value.astimezone(UTC8).date().isoformat() if value else None


def _mtime(path: Path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _read_json(path: Path):
    """Read an atomically replaced state file with a short retry."""
    last_error = None
    for _ in range(3):
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle), None
        except (OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(0.05)
    return None, last_error or "無法讀取 JSON"


def _read_csv(path: Path, retries=3):
    """Read a CSV while recorder may be appending or atomically rewriting it."""
    last_error = None
    for _ in range(retries):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle)), None
        except (OSError, UnicodeError, csv.Error) as exc:
            last_error = str(exc)
            time.sleep(0.05)
    return [], last_error or "無法讀取 CSV"


def _execution_time(raw_value):
    """Recorder stores the signal bar open in *_utc8; execution is bar close +1h."""
    parsed = _parse_datetime(raw_value)
    return parsed + timedelta(hours=1) if parsed else None


def _trade_row(raw: dict, index: int) -> dict | None:
    entry_signal = _parse_datetime(raw.get("entry_time_utc8") or raw.get("entry_dt"))
    exit_signal = _parse_datetime(raw.get("exit_time_utc8") or raw.get("exit_dt"))
    if entry_signal is None:
        return None

    entry_time = _execution_time(raw.get("entry_time_utc8") or raw.get("entry_dt"))
    exit_time = _execution_time(raw.get("exit_time_utc8") or raw.get("exit_dt"))
    pnl = _number(raw.get("net_pnl_usd"), None)
    side = _side(raw.get("sub_strategy") or raw.get("direction"))
    trade_id = str(raw.get("trade_id") or f"csv-{index}")
    # trades.csv 的 gk_pctile_at_entry 是「該筆實際方向」的 GK；
    # 舊資料沒有獨立的 gk_pctile_s_at_entry 欄位，S 需回退讀取同一欄位。
    entry_gk = _first_number(
        raw,
        "gk_pctile_s_at_entry" if side == "S" else "gk_pctile_at_entry",
        "gk_pctile_at_entry",
        "gk_pctile_s" if side == "S" else "gk_pctile",
    )
    return {
        "id": trade_id,
        "number": raw.get("trade_number") or trade_id,
        "side": side,
        "direction": "Long" if side == "L" else "Short" if side == "S" else side,
        "entry_signal_time_utc": _iso(entry_signal),
        "entry_signal_time_display": _display_time(entry_signal),
        "exit_signal_time_utc": _iso(exit_signal),
        "exit_signal_time_display": _display_time(exit_signal),
        "entry_time_utc": _iso(entry_time),
        "entry_time_display": _display_time(entry_time),
        "exit_time_utc": _iso(exit_time),
        "exit_time_display": _display_time(exit_time),
        "entry_price": _number(raw.get("entry_price")),
        "exit_price": _number(raw.get("exit_price")),
        "exit_reason": str(raw.get("exit_type") or raw.get("exit_reason") or "進行中").strip(),
        "hold_bars": _int(raw.get("hold_bars"), None),
        "hold_hours": _number(raw.get("hold_hours"), None),
        "pnl": pnl,
        "gross_pnl": _number(raw.get("gross_pnl_usd"), None),
        "pnl_pct": _number(raw.get("net_pnl_pct"), None),
        "mae_pct": _first_number(raw, "max_adverse_excursion_pct", "mae_pct"),
        "mfe_pct": _first_number(raw, "max_favorable_excursion_pct", "mfe_pct"),
        "gk_pctile": entry_gk if side == "L" else None,
        "gk_pctile_s": entry_gk if side == "S" else None,
        "gk_ratio": _first_number(raw, "gk_ratio_at_entry", "gk_ratio"),
        "breakout_strength_pct": _first_number(raw, "breakout_strength_pct"),
        "regime": str(raw.get("entry_regime") or "NA").strip(),
        "closed": exit_time is not None and pnl is not None,
    }


def _backtest_text_rows(path: Path) -> list[dict]:
    """讀取 run_backtest.py -t 的文字明細，不載入策略或交易模組。"""
    try:
        from trade_viewer import load_trades
    except ImportError as exc:
        raise OSError(f"回測明細解析器不存在：{exc}") from exc

    try:
        parsed_rows = load_trades(path)
    except (OSError, ValueError) as exc:
        raise OSError(f"回測明細讀取失敗：{exc}") from exc

    rows = []
    for index, raw in enumerate(parsed_rows, start=1):
        entry_time = _parse_datetime(raw.get("entry_time"))
        exit_time = _parse_datetime(raw.get("exit_time"))
        if entry_time is None:
            continue
        # 回測 -t 已輸出實際成交時間；同時還原訊號 K 棒開盤時間供對照。
        entry_signal = entry_time - timedelta(hours=1)
        exit_signal = exit_time - timedelta(hours=1) if exit_time else None
        side = _side(raw.get("side"))
        trade_id = str(raw.get("id") or f"backtest-{index}")
        rows.append({
            "id": trade_id,
            "number": raw.get("number") or index,
            "side": side,
            "direction": "Long" if side == "L" else "Short" if side == "S" else side,
            "entry_signal_time_utc": _iso(entry_signal),
            "entry_signal_time_display": _display_time(entry_signal),
            "exit_signal_time_utc": _iso(exit_signal),
            "exit_signal_time_display": _display_time(exit_signal),
            "entry_time_utc": _iso(entry_time),
            "entry_time_display": _display_time(entry_time),
            "exit_time_utc": _iso(exit_time),
            "exit_time_display": _display_time(exit_time),
            "entry_price": _number(raw.get("entry_price")),
            "exit_price": _number(raw.get("exit_price")),
            "exit_reason": str(raw.get("exit_reason") or "進行中").strip(),
            "hold_bars": _int(raw.get("hold"), None),
            "hold_hours": _number(raw.get("hold"), None),
            "pnl": _number(raw.get("pnl"), None),
            "backtest_margin": raw.get("margin"),
            "pnl_pct": None,
            "mae_pct": None,
            "mfe_pct": None,
            "gk_pctile": None,
            "gk_pctile_s": None,
            "gk_ratio": None,
            "breakout_strength_pct": None,
            "regime": str(raw.get("regime") or "NA").strip(),
            "closed": exit_time is not None and raw.get("pnl") is not None,
        })
    return rows


def _parse_binance_klines(data):
    rows = []
    now = datetime.now(timezone.utc)
    for item in data:
        if len(item) < 7:
            continue
        open_time = datetime.fromtimestamp(float(item[0]) / 1000, timezone.utc)
        close_time = datetime.fromtimestamp(float(item[6]) / 1000, timezone.utc)
        rows.append({
            "time_utc": _iso(open_time),
            "time_display": _display_time(open_time),
            "time_ms": int(float(item[0])),
            "open": _number(item[1]),
            "high": _number(item[2]),
            "low": _number(item[3]),
            "close": _number(item[4]),
            "volume": _number(item[5]),
            "closed": now >= close_time + timedelta(milliseconds=1),
            "source": "binance_public",
        })
    return rows


def _parse_local_klines(path: Path):
    raw_rows, error = _read_csv(path)
    if error:
        raise OSError(f"K 線快取讀取失敗：{error}")
    rows = []
    now = datetime.now(timezone.utc)
    for raw in raw_rows:
        opened = _parse_datetime(raw.get("datetime"))
        if opened is None:
            continue
        rows.append({
            "time_utc": _iso(opened),
            "time_display": _display_time(opened),
            "time_ms": int(opened.timestamp() * 1000),
            "open": _number(raw.get("open")),
            "high": _number(raw.get("high")),
            "low": _number(raw.get("low")),
            "close": _number(raw.get("close")),
            "volume": _number(raw.get("volume")),
            "closed": now >= opened + timedelta(hours=1),
            "source": "local_cache",
        })
    return rows


def _literal_constants(path: Path, names: set[str]) -> dict:
    """只用 ast 讀模組頂層的字面值常數；不執行、不匯入該模組。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in names:
            try:
                found[target.id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return found


def _margin_schedule() -> list[tuple[str, float]]:
    """run_backtest.MARGIN_SCHEDULE（單一來源）；讀不到時視為全程 200U。"""
    value = _literal_constants(RUN_BACKTEST_PATH, {"MARGIN_SCHEDULE"}).get("MARGIN_SCHEDULE")
    schedule = []
    for item in value or []:
        try:
            schedule.append((str(item[0])[:10], float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(schedule) or [("2000-01-01", BASELINE_MARGIN)]


def _scheduled_margin(entry_display: str | None, schedule) -> float:
    margin = schedule[0][1]
    for start, value in schedule:
        if str(entry_display or "")[:10] >= start:
            margin = value
    return margin


def _edge_thresholds() -> dict:
    values = _literal_constants(STRATEGY_PATH, {"EDGE_CUSUM_YELLOW", "EDGE_CUSUM_RED"})
    return {
        "yellow": float(values.get("EDGE_CUSUM_YELLOW", 600.0)),
        "red": float(values.get("EDGE_CUSUM_RED", 800.0)),
    }


def _exit_code(reason) -> str:
    """出場原因（實戰全名或回測「中文 (英文)」）→ edge_falsify 短碼。"""
    text = str(reason or "").strip()
    if "(" in text and text.endswith(")"):
        text = text[text.rfind("(") + 1:-1].strip()
    return {"SafeNet": "SN", "MFE-trail": "MFE", "MaxHold": "MH", "MH-ext": "MHx"}.get(text, text)


def _attach_size(row: dict, schedule, margin=None, gross_pnl=None) -> dict:
    """補上保證金、名目與 200U 基準 PnL。

    回測明細直接有 Mgn(U)；實戰 trades.csv 沒有保證金欄，先用毛損益 ÷ 價格變動推回名目，
    無法推算（例如進出場同價）時才退回 MARGIN_SCHEDULE。
    """
    notional = None
    size_source = None
    if margin:
        notional = float(margin) * LEVERAGE
        size_source = "回測明細"
    entry, exit_price = row.get("entry_price"), row.get("exit_price")
    if notional is None and gross_pnl is not None and entry and exit_price:
        direction = -1.0 if row.get("side") == "S" else 1.0
        move = (float(exit_price) - float(entry)) / float(entry) * direction
        if abs(move) >= 1e-4:
            estimated = float(gross_pnl) / move
            if estimated > 0:
                notional = estimated
                size_source = "成交推算"
    if notional is None:
        notional = _scheduled_margin(row.get("entry_time_display"), schedule) * LEVERAGE
        size_source = "保證金排程"
    row["notional"] = round(notional, 2)
    row["margin"] = round(notional / LEVERAGE / 10) * 10 if size_source == "成交推算" else round(notional / LEVERAGE, 2)
    row["size_source"] = size_source
    row["pnl_actual"] = row.get("pnl")
    row["pnl_200u"] = (
        round(float(row["pnl"]) * BASELINE_NOTIONAL / notional, 6)
        if row.get("pnl") is not None and notional else None
    )
    return row


def _with_basis(rows: list[dict], basis: str) -> list[dict]:
    if basis != "200u":
        return rows
    return [{**row, "pnl": row.get("pnl_200u")} if row.get("pnl") is not None else row for row in rows]


def _basis(value) -> str:
    basis = str(value or "actual").lower()
    if basis not in BASES:
        raise ValueError("金額基準只能是 actual 或 200u")
    return basis


class DataStore:
    def __init__(self, allow_network=True):
        self.allow_network = allow_network
        self._lock = threading.RLock()
        self._trades_cache = {}
        self._klines_cache = {}
        self._price_cache = None
        self._price_retry_after = 0.0
        self._price_last_error = None
        self._signal_cache = None
        self._signal_refresh_lock = threading.Lock()
        self._price_refresh_lock = threading.Lock()
        self._last_kline_error = None
        self._backtest_metrics_status = {"available": False, "matched": 0, "error": None}

    @property
    def backtest_path(self):
        configured = os.environ.get("VIEWER_BACKTEST_PATH")
        if configured:
            return Path(configured).expanduser().resolve()
        for candidate in (
            DEFAULT_BACKTEST_PATH,
            ROOT / "data" / "backtest_trades.csv",
            ROOT / "backtest_trades.txt",
            ROOT / "backtest_trades.csv",
        ):
            if candidate.is_file():
                return candidate
        return DEFAULT_BACKTEST_PATH

    def source_path(self, source):
        if source == "live":
            return LIVE_DIR / "trades.csv"
        if source == "backtest":
            return self.backtest_path
        raise ValueError("資料來源只能是 live 或 backtest")

    @staticmethod
    def source_label(source):
        return {"live": "實戰", "backtest": "回測"}.get(source, source)

    def available_date_bounds(self, source, trades=None):
        timestamps = []
        for row in trades if trades is not None else self.trades(source):
            for key in ("entry_time_utc", "exit_time_utc"):
                stamp = _parse_datetime(row.get(key))
                if stamp:
                    timestamps.append(stamp)

        if source == "backtest":
            for path in (LOCAL_KLINE_PATH, SHARED_KLINE_PATH):
                if not path.is_file():
                    continue
                try:
                    candles = _parse_local_klines(path)
                except OSError:
                    continue
                if candles:
                    timestamps.extend((
                        datetime.fromtimestamp(candles[0]["time_ms"] / 1000, timezone.utc),
                        datetime.fromtimestamp(candles[-1]["time_ms"] / 1000, timezone.utc),
                    ))
                    break
        elif source == "live":
            timestamps.append(datetime.now(timezone.utc))

        return {
            "min_date": _date_label(min(timestamps)) if timestamps else None,
            "max_date": _date_label(max(timestamps)) if timestamps else None,
        }

    def artifact_path(self, source, artifact):
        if source == "live":
            return LIVE_DIR / artifact
        if source != "backtest":
            raise ValueError("資料來源只能是 live 或 backtest")
        configured = os.environ.get({
            "bar_snapshots.csv": "VIEWER_BACKTEST_SNAPSHOTS_PATH",
            "position_lifecycle.csv": "VIEWER_BACKTEST_LIFECYCLE_PATH",
        }.get(artifact, ""))
        if configured:
            return Path(configured).expanduser().resolve()
        return {
            "bar_snapshots.csv": DEFAULT_BACKTEST_SNAPSHOT_PATH,
            "position_lifecycle.csv": DEFAULT_BACKTEST_LIFECYCLE_PATH,
        }.get(artifact, ROOT / "data" / artifact)

    def read_artifact(self, source, artifact):
        path = self.artifact_path(source, artifact)
        if not path.is_file():
            return [], None
        rows, error = _read_csv(path)
        if error:
            return [], str(error)
        return rows, None

    def trades(self, source="live"):
        path = self.source_path(source)
        mtime = _mtime(path)
        with self._lock:
            cached = self._trades_cache.get(source)
            if cached and mtime is not None and mtime == cached[0]:
                return cached[1]
        if mtime is None:
            return []
        if source == "live" or path.suffix.lower() == ".csv":
            raw_rows, error = _read_csv(path)
            if error:
                raise OSError(f"交易紀錄讀取失敗：{error}")
            rows = []
            for index, raw in enumerate(raw_rows, start=1):
                parsed = _trade_row(raw, index)
                if parsed:
                    rows.append(parsed)
        else:
            rows = _backtest_text_rows(path)
        schedule = _margin_schedule()
        for row in rows:
            _attach_size(
                row, schedule,
                margin=row.pop("backtest_margin", None),
                gross_pnl=row.pop("gross_pnl", None),
            )
        if source == "backtest":
            self._merge_backtest_metrics(rows)
        rows.sort(key=lambda row: row["entry_time_utc"] or "")
        with self._lock:
            self._trades_cache[source] = (mtime, rows)
        return rows

    @property
    def backtest_metrics_path(self):
        configured = os.environ.get("VIEWER_BACKTEST_METRICS_PATH")
        if configured:
            return Path(configured).expanduser().resolve()
        return DEFAULT_BACKTEST_METRICS_PATH

    def _merge_backtest_metrics(self, rows):
        """以（方向, 進場成交時刻）對齊回測逐筆 MAE／MFE／進場 GK；缺檔時維持缺值。"""
        path = self.backtest_metrics_path
        self._backtest_metrics_status = {"available": False, "path": str(path), "matched": 0, "error": None}
        if not path.is_file():
            self._backtest_metrics_status["error"] = "尚未產生回測逐筆指標檔（執行 refresh_viewer_backtest.py）"
            return
        raw_rows, error = _read_csv(path)
        if error:
            self._backtest_metrics_status["error"] = str(error)
            return
        metrics = {}
        for raw in raw_rows:
            key = (_side(raw.get("side")), str(raw.get("entry_exec_utc8") or "")[:16])
            metrics[key] = raw
        matched = 0
        for row in rows:
            raw = metrics.get((row.get("side"), str(row.get("entry_time_display") or "")[:16]))
            if not raw:
                continue
            matched += 1
            row["mae_pct"] = _number(raw.get("mae_pct"), None)
            row["mfe_pct"] = _number(raw.get("mfe_pct"), None)
            gk = _number(raw.get("gk_pctile"), None)
            if row.get("side") == "S":
                row["gk_pctile_s"] = gk
            else:
                row["gk_pctile"] = gk
        self._backtest_metrics_status.update({"available": matched > 0, "matched": matched, "total": len(rows)})

    def _fetch_public_klines(self, start_ms=None, end_ms=None):
        if start_ms is None or end_ms is None:
            query = urlencode({"symbol": "ETHUSDT", "interval": "1h", "limit": MAX_KLINES})
            request = Request(
                f"{BINANCE_KLINES_URL}?{query}",
                headers={"User-Agent": "cryptobot-readonly-viewer/1.0"},
            )
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = _parse_binance_klines(payload)
            if len(rows) < 2:
                raise OSError("Binance 公開 K 線資料不足")
            return rows

        if end_ms < start_ms:
            raise ValueError("結束日期不可早於開始日期")
        estimated_bars = (end_ms - start_ms) // 3_600_000 + 1
        if estimated_bars > MAX_RANGE_KLINES:
            raise ValueError(f"單次圖表最多載入 {MAX_RANGE_KLINES:,} 根 1h K 線，請縮短日期範圍")

        raw_rows = []
        cursor = start_ms
        while cursor <= end_ms:
            query = urlencode({
                "symbol": "ETHUSDT",
                "interval": "1h",
                "startTime": cursor,
                "endTime": end_ms,
                "limit": MAX_KLINES,
            })
            request = Request(
                f"{BINANCE_KLINES_URL}?{query}",
                headers={"User-Agent": "cryptobot-readonly-viewer/1.0"},
            )
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload:
                break
            raw_rows.extend(payload)
            last_open_ms = int(payload[-1][0])
            next_cursor = last_open_ms + 3_600_000
            if next_cursor <= cursor:
                raise OSError("Binance K 線分頁時間未前進，已停止載入")
            cursor = next_cursor
            if len(payload) < MAX_KLINES:
                break

        rows = _parse_binance_klines(raw_rows)
        if not rows:
            raise OSError("所選日期沒有可用的 Binance 公開 K 線")
        return rows

    @staticmethod
    def _price_result(cache, stale=False, error=None):
        fetched_at, payload = cache
        return {
            **payload,
            "stale": stale,
            "age_seconds": max(0, int(time.monotonic() - fetched_at)),
            "error": error,
        }

    def price(self):
        if not self.allow_network:
            raise OSError("此 Viewer 已停用即時行情連線")

        now = time.monotonic()
        with self._lock:
            cache = self._price_cache
            if cache and now - cache[0] < PRICE_TTL_SECONDS:
                return self._price_result(cache)
            if now < self._price_retry_after:
                if cache:
                    return self._price_result(cache, stale=True, error=self._price_last_error)
                raise OSError(self._price_last_error or "即時行情暫時無法取得")

        with self._price_refresh_lock:
            now = time.monotonic()
            with self._lock:
                cache = self._price_cache
                if cache and now - cache[0] < PRICE_TTL_SECONDS:
                    return self._price_result(cache)
                if now < self._price_retry_after:
                    if cache:
                        return self._price_result(cache, stale=True, error=self._price_last_error)
                    raise OSError(self._price_last_error or "即時行情暫時無法取得")

            try:
                query = urlencode({"symbol": "ETHUSDT"})
                request = Request(
                    f"{BINANCE_PRICE_URL}?{query}",
                    headers={"User-Agent": "cryptobot-readonly-viewer/1.0"},
                )
                with urlopen(request, timeout=4) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("symbol") != "ETHUSDT":
                    raise ValueError("Binance 即時行情回應格式錯誤")
                price = _number(payload.get("price"))
                exchange_time_ms = _int(payload.get("time"))
                if price is None or price <= 0:
                    raise ValueError("Binance 即時行情價格無效")
                updated_at_ms = int(time.time() * 1000)
                exchange_time = datetime.fromtimestamp(exchange_time_ms / 1000, timezone.utc) if exchange_time_ms else None
                updated_at = datetime.fromtimestamp(updated_at_ms / 1000, timezone.utc)
                result = {
                    "symbol": "ETHUSDT",
                    "price": price,
                    "exchange_time_ms": exchange_time_ms,
                    "exchange_time_display": exchange_time.astimezone(UTC8).strftime("%Y-%m-%d %H:%M:%S") if exchange_time else None,
                    "updated_at_ms": updated_at_ms,
                    "updated_at_display": updated_at.astimezone(UTC8).strftime("%Y-%m-%d %H:%M:%S"),
                    "source": "Binance USDⓈ-M Futures 最新成交價",
                }
            except (OSError, URLError, TimeoutError, ValueError) as exc:
                error = f"即時行情讀取失敗：{exc}"
                with self._lock:
                    self._price_last_error = error
                    self._price_retry_after = time.monotonic() + PRICE_FAILURE_TTL_SECONDS
                    cache = self._price_cache
                if cache:
                    return self._price_result(cache, stale=True, error=error)
                raise OSError(error) from exc

            fetched_at = time.monotonic()
            with self._lock:
                self._price_cache = (fetched_at, result)
                self._price_retry_after = 0.0
                self._price_last_error = None
                return self._price_result(self._price_cache)

    def klines(self, source="live", start=None, end=None):
        start_ms = int(start.timestamp() * 1000) if start else None
        end_ms = int(end.timestamp() * 1000) - 1 if end else None
        cache_key = (source, start_ms, end_ms)
        now = time.time()
        with self._lock:
            cached = self._klines_cache.get(cache_key)
            if cached and now - cached[0] < KLINE_TTL_SECONDS:
                return cached[1]

        rows = None
        errors = []
        local_paths = (
            (LOCAL_KLINE_PATH, SHARED_KLINE_PATH)
            if source == "backtest"
            else (SHARED_KLINE_PATH, LOCAL_KLINE_PATH)
        )
        if source == "backtest":
            for path in local_paths:
                if not path.is_file():
                    continue
                try:
                    rows = _parse_local_klines(path)
                    if rows:
                        break
                except OSError as exc:
                    errors.append(str(exc))

        # Historical live dates are fetched from Binance in bounded pages; current-only
        # requests keep the existing 1,000-bar behavior.
        if (source == "live" or not rows) and self.allow_network:
            try:
                rows = self._fetch_public_klines(start_ms, end_ms)
            except (OSError, URLError, TimeoutError, ValueError) as exc:
                errors.append(f"公開 K 線：{exc}")

        if not rows and source == "live":
            for path in local_paths:
                if not path.is_file():
                    continue
                try:
                    rows = _parse_local_klines(path)
                    if rows:
                        break
                except OSError as exc:
                    errors.append(str(exc))

        if not rows:
            raise OSError("；".join(errors) or "找不到 K 線資料")

        if start_ms is not None and end_ms is not None:
            rows = [row for row in rows if start_ms <= row["time_ms"] <= end_ms]
        elif source == "backtest":
            rows = rows[-MAX_KLINES:]

        with self._lock:
            self._klines_cache[cache_key] = (time.time(), rows)
            self._last_kline_error = errors[-1] if errors else None
        return rows

    def selection_window(self, source="live", start_date=None, end_date=None, days=30):
        if start_date is not None or end_date is not None:
            if not start_date or not end_date:
                raise ValueError("開始與結束日期都必須選擇")
            start, end = _date_window(start_date, end_date)
        else:
            recent = self.klines(source)
            if not recent:
                raise OSError("沒有 K 線資料")
            days = max(1, min(int(days), 730))
            latest = datetime.fromtimestamp(recent[-1]["time_ms"] / 1000, timezone.utc)
            latest_day = latest.astimezone(UTC8).date()
            end = datetime.combine(latest_day + timedelta(days=1), datetime_time.min, UTC8).astimezone(timezone.utc)
            start = end - timedelta(days=days)
        bounds = self.available_date_bounds(source)
        start_day, end_day = start.astimezone(UTC8).date(), (end - timedelta(microseconds=1)).astimezone(UTC8).date()
        if bounds["min_date"] and start_day < date.fromisoformat(bounds["min_date"]):
            raise ValueError(f"開始日期不能早於最早可選日 {bounds['min_date']}")
        if bounds["max_date"] and end_day > date.fromisoformat(bounds["max_date"]):
            raise ValueError(f"結束日期不能晚於最新可選日 {bounds['max_date']}")
        return start, end

    def select_trades(self, source, start, end, side="ALL", basis="actual"):
        trades = _with_basis(self.trades(source), basis)
        selected_trades = []
        for row in trades:
            if side != "ALL" and row["side"] != side:
                continue
            entry_ms = _parse_datetime(row["entry_time_utc"])
            exit_ms = _parse_datetime(row["exit_time_utc"])
            # Realized performance is grouped by fill/exit time. An open trade
            # remains visible in the interval containing its entry time.
            event_time = exit_ms if exit_ms is not None else entry_ms
            if event_time is not None and start <= event_time < end:
                selected_trades.append(row)
        return selected_trades

    def selection(self, source="live", days=30, side="ALL", start_date=None, end_date=None, basis="actual"):
        start, end = self.selection_window(source, start_date, end_date, days)
        selected_candles = self.klines(source, start, end)
        selected_trades = self.select_trades(source, start, end, side, basis)
        return selected_candles, selected_trades, start, end

    def state(self):
        payload, error = _read_json(STATE_PATH)
        if error:
            return {
                "available": False,
                "error": error if STATE_PATH.exists() else "尚未找到 eth_state_live.json",
                "updated_at": _iso(datetime.fromtimestamp(_mtime(STATE_PATH), timezone.utc))
                if _mtime(STATE_PATH) else None,
                "positions": [],
            }

        positions = []
        for trade_id, position in (payload.get("positions") or {}).items():
            side = _side(position.get("sub_strategy") or position.get("side"))
            entry_signal_time = _parse_datetime(
                position.get("entry_time_utc8") or position.get("entry_time_utc")
            )
            entry_time = _execution_time(
                position.get("entry_time_utc8") or position.get("entry_time_utc")
            )
            positions.append({
                "id": str(trade_id),
                "side": side,
                "direction": "Long" if side == "L" else "Short" if side == "S" else side,
                "entry_signal_time_utc": _iso(entry_signal_time),
                "entry_signal_time_display": _display_time(entry_signal_time),
                "entry_time_utc": _iso(entry_time),
                "entry_time_display": _display_time(entry_time),
                "entry_price": _number(position.get("entry_price")),
                "qty": _number(position.get("qty")),
                "entry_regime": str(position.get("entry_regime") or "NA"),
                "hold_bars": _int(position.get("bars_held"), None),
                "mfe_pct": _number(position.get("running_mfe_pct"), None),
                "mae_pct": _number(position.get("mae_pct"), None),
            })

        updated = _mtime(STATE_PATH)
        return {
            "available": True,
            "error": None,
            "updated_at": _iso(datetime.fromtimestamp(updated, timezone.utc)) if updated else None,
            "last_bar_time": payload.get("last_bar_time"),
            "positions": positions,
        }

    @staticmethod
    def _mark_positions(state, candles):
        """用公開 K 線標記持倉現價與估算浮動損益，完全唯讀。"""
        latest = candles[-1] if candles else None
        if not latest or _number(latest.get("close"), None) is None:
            return state
        current_price = float(latest["close"])
        for position in state.get("positions", []):
            entry = _number(position.get("entry_price"), None)
            if entry is None or entry <= 0:
                continue
            side = position.get("side")
            move_pct = ((current_price - entry) / entry * 100
                        if side == "L" else (entry - current_price) / entry * 100)
            qty = _number(position.get("qty"), None)
            position["current_price"] = current_price
            position["price_time_display"] = latest.get("time_display")
            position["price_is_closed"] = bool(latest.get("closed"))
            position["unrealized_pnl_pct"] = move_pct
            position["unrealized_pnl_usd"] = move_pct / 100 * entry * qty if qty else None
        return state

    def signal(self):
        """呼叫既有唯讀診斷，避免 viewer 複製策略判斷。"""
        now = time.time()
        with self._lock:
            cached = self._signal_cache
            if cached and now - cached[0] < SIGNAL_TTL_SECONDS:
                return cached[1]

        with self._signal_refresh_lock:
            now = time.time()
            with self._lock:
                cached = self._signal_cache
                if cached and now - cached[0] < SIGNAL_TTL_SECONDS:
                    return cached[1]

            env = os.environ.copy()
            env["INSTANCE_DIR"] = str(INSTANCE_DIR)
            command = [sys.executable, str(ROOT / "check_signal.py"), "--live", "--json"]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(ROOT),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=SIGNAL_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise OSError(f"即時開單條件檢查失敗：{exc}") from exc

            output = completed.stdout.strip()
            if completed.returncode != 0:
                detail = completed.stderr.strip() or output or f"exit code {completed.returncode}"
                raise OSError(f"即時開單條件檢查失敗：{detail[-500:]}")
            try:
                payload = json.loads(output)
            except json.JSONDecodeError as exc:
                detail = completed.stderr.strip() or output
                raise OSError(f"即時開單條件格式錯誤：{detail[-500:]}") from exc

            payload.update({
                "server_time_utc": _iso(datetime.now(timezone.utc)),
                "server_time_display": _display_time(datetime.now(timezone.utc)),
                "source_label": "實戰",
                "cache_ttl_seconds": SIGNAL_TTL_SECONDS,
            })
            with self._lock:
                self._signal_cache = (time.time(), payload)
            return payload

    def meta(self):
        sources = []
        for source in ("live", "backtest"):
            path = self.source_path(source)
            available = path.is_file()
            modified = _mtime(path)
            rows = []
            error = None
            if available:
                try:
                    rows = self.trades(source)
                except OSError as exc:
                    error = str(exc)
            bounds = self.available_date_bounds(source, rows) if error is None else {"min_date": None, "max_date": None}
            sources.append({
                "id": source,
                "label": self.source_label(source),
                "available": available and error is None and bool(rows),
                "path": str(path),
                "count": len(rows),
                "first": rows[0]["entry_time_display"] if rows else None,
                "last": rows[-1]["exit_time_display"] if rows else None,
                "min_date": bounds["min_date"],
                "max_date": bounds["max_date"],
                "updated_at": _iso(datetime.fromtimestamp(modified, timezone.utc)) if modified else None,
                "updated_at_display": _display_time(datetime.fromtimestamp(modified, timezone.utc)) if modified else None,
                "error": error or (None if available else "尚未找到資料檔"),
            })
        return {"sources": sources, "timezone": "Asia/Taipei"}

    def data(self, source="live", days=30, side="ALL", start_date=None, end_date=None, basis="actual"):
        selected_candles, selected_trades, start, end = self.selection(
            source, days, side, start_date, end_date, basis
        )
        trades_mtime = _mtime(self.source_path(source))

        closed = [row for row in selected_trades if row["closed"]]
        pnl_values = [row["pnl"] for row in closed if row["pnl"] is not None]
        wins = sum(value > 0 for value in pnl_values)
        state = self.state() if source == "live" else {
            "available": False,
            "error": "回測資料沒有即時持倉狀態",
            "updated_at": None,
            "positions": [],
        }
        if source == "live":
            current_candles = selected_candles
            if not current_candles or current_candles[-1]["time_ms"] < (time.time() - 3 * 3600) * 1000:
                current_candles = self.klines("live")
            self._mark_positions(state, current_candles)
        return {
            "server_time_utc": _iso(datetime.now(timezone.utc)),
            "server_time_display": _display_time(datetime.now(timezone.utc)),
            "timezone": "Asia/Taipei",
            "source": source,
            "source_label": self.source_label(source),
            "basis": basis,
            "date_range": {
                "start": _date_label(start),
                "end": _date_label(end - timedelta(microseconds=1)),
            },
            "source_updated_at": _iso(datetime.fromtimestamp(trades_mtime, timezone.utc)) if trades_mtime else None,
            "source_updated_at_display": (
                _display_time(datetime.fromtimestamp(trades_mtime, timezone.utc)) if trades_mtime else None
            ),
            "candles": selected_candles,
            "trades": selected_trades,
            "positions": state["positions"],
            "state": state,
            "stats": {
                "trades": len(closed),
                "wins": wins,
                "losses": len(pnl_values) - wins,
                "pnl": sum(pnl_values),
                "last_closed_candle": next(
                    (row for row in reversed(selected_candles) if row["closed"]), None
                ),
                "forming_candle": next(
                    (row for row in reversed(selected_candles) if not row["closed"]), None
                ),
            },
            "sources": {
                "trades": str(self.source_path(source)),
                "state": str(STATE_PATH),
                "kline": "本機 730 天快取（回測）或 Binance Futures 公開 API（實戰）",
            },
        }

    def analysis(self, source="live", days=30, side="ALL", start_date=None, end_date=None, basis="actual"):
        start, end = self.selection_window(source, start_date, end_date, days)
        selected_trades = self.select_trades(source, start, end, side, basis)
        rolling = self._rolling_metrics(source, side, basis)
        closed = [row for row in selected_trades if row["closed"] and row["pnl"] is not None]
        ordered = sorted(
            closed,
            key=lambda row: row.get("exit_time_utc") or row.get("entry_time_utc") or "",
        )
        pnls = [float(row["pnl"]) for row in closed]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        equity = []
        for row in ordered:
            cumulative += float(row["pnl"])
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
            equity.append({
                "id": row.get("id"),
                "time_utc": row.get("exit_time_utc") or row.get("entry_time_utc"),
                "time_display": row.get("exit_time_display") or row.get("entry_time_display"),
                "number": row.get("number"),
                "side": row.get("side"),
                "pnl": float(row["pnl"]),
                "cumulative_pnl": round(cumulative, 6),
                "peak_pnl": round(peak, 6),
                "drawdown": round(cumulative - peak, 6),
                "mae_pct": row.get("mae_pct"),
                "mfe_pct": row.get("mfe_pct"),
                "gk_pctile": row.get("gk_pctile"),
                "gk_pctile_s": row.get("gk_pctile_s"),
                "exit_reason": row.get("exit_reason"),
                **rolling.get(str(row.get("id")), {}),
            })

        def summary(rows):
            values = [float(row["pnl"]) for row in rows if row.get("pnl") is not None]
            positive = [value for value in values if value > 0]
            negative = [value for value in values if value < 0]
            return {
                "trades": len(values),
                "wins": len(positive),
                "losses": len(negative),
                "breakeven": len(values) - len(positive) - len(negative),
                "win_rate": len(positive) / len(values) * 100 if values else 0.0,
                "pnl": round(sum(values), 6),
                "avg_pnl": round(sum(values) / len(values), 6) if values else 0.0,
                "avg_win": round(sum(positive) / len(positive), 6) if positive else 0.0,
                "avg_loss": round(sum(negative) / len(negative), 6) if negative else 0.0,
            }

        def grouped(rows, key_fn):
            groups = {}
            for row in rows:
                key = str(key_fn(row) or "NA").strip() or "NA"
                groups.setdefault(key, []).append(row)
            result = []
            for key, group_rows in groups.items():
                item = summary(group_rows)
                item["key"] = key
                item["label"] = key
                result.append(item)
            return sorted(result, key=lambda item: item["pnl"], reverse=True)

        monthly_groups = {}
        for row in ordered:
            stamp = row.get("exit_time_display") or row.get("entry_time_display") or "NA"
            monthly_groups.setdefault(str(stamp)[:7], []).append(row)
        monthly = []
        for period, rows in sorted(monthly_groups.items()):
            item = summary(rows)
            item["period"] = period
            monthly.append(item)

        daily_groups = {}
        for row in ordered:
            stamp = row.get("exit_time_display") or row.get("entry_time_display") or "NA"
            daily_groups.setdefault(str(stamp)[:10], []).append(row)
        daily = []
        for period, rows in sorted(daily_groups.items()):
            item = summary(rows)
            item["period"] = period
            daily.append(item)

        hold_bins = (("0-3h", 0, 3), ("4-7h", 4, 7), ("8-11h", 8, 11), ("12h+", 12, None))
        hold_distribution = []
        for label, lower, upper in hold_bins:
            group_rows = []
            for row in closed:
                hold = row.get("hold_hours")
                if hold is None:
                    hold = row.get("hold_bars")
                if hold is None:
                    continue
                if float(hold) >= lower and (upper is None or float(hold) <= upper):
                    group_rows.append(row)
            item = summary(group_rows)
            item.update({"key": label, "label": label})
            hold_distribution.append(item)

        max_win_streak = max_loss_streak = current_streak = 0
        current_kind = None
        running = 0
        for row in ordered:
            kind = "W" if float(row["pnl"]) > 0 else "L" if float(row["pnl"]) < 0 else "B"
            if kind == current_kind:
                running += 1
            else:
                current_kind, running = kind, 1
            if kind == "W":
                max_win_streak = max(max_win_streak, running)
            if kind == "L":
                max_loss_streak = max(max_loss_streak, running)
        if current_kind == "B":
            current_streak = 0
        else:
            current_streak = running if ordered else 0

        snapshot_rows, snapshot_error = self.read_artifact(source, "bar_snapshots.csv")
        lifecycle_rows, lifecycle_error = self.read_artifact(source, "position_lifecycle.csv")
        snapshot_selected = []
        for raw in snapshot_rows:
            stamp = _parse_datetime(raw.get("bar_time_utc8"))
            if stamp and start <= stamp < end:
                snapshot_selected.append(raw)
        snapshot_selected.sort(key=lambda row: row.get("bar_time_utc8") or "")
        gk_series = []
        breakout_counts = {"Long": 0, "Short": 0}
        for raw in snapshot_selected:
            gk_l = _number(raw.get("gk_pctile"), None)
            gk_s = _number(raw.get("gk_pctile_s"), None)
            if gk_l is not None or gk_s is not None:
                gk_series.append({
                    "time_utc": _iso(_parse_datetime(raw.get("bar_time_utc8"))),
                    "time_display": _display_time(_parse_datetime(raw.get("bar_time_utc8"))),
                    "long": gk_l,
                    "short": gk_s,
                })
            if str(raw.get("breakout_long", "")).lower() in {"true", "1", "yes"}:
                breakout_counts["Long"] += 1
            if str(raw.get("breakout_short", "")).lower() in {"true", "1", "yes"}:
                breakout_counts["Short"] += 1
        gk_stats = {}
        for key, threshold in (("long", 25.0), ("short", 35.0)):
            values = [point[key] for point in gk_series if point[key] is not None]
            gk_stats[f"{key}_rate"] = sum(value < threshold for value in values) / len(values) * 100 if values else None
            gk_stats[f"{key}_latest"] = values[-1] if values else None
        # 長區間不再跳點抽樣（會漏掉短暫壓縮）：每桶保留最小／最大值與低於門檻的根數。
        max_points = 1500
        if len(gk_series) > max_points:
            size = -(-len(gk_series) // max_points)
            buckets = []
            for index in range(0, len(gk_series), size):
                chunk = gk_series[index:index + size]
                item = {
                    "time_utc": chunk[0]["time_utc"],
                    "time_display": chunk[0]["time_display"],
                    "time_end_utc": chunk[-1]["time_utc"],
                    "bars": len(chunk),
                }
                for key, threshold in (("long", 25.0), ("short", 35.0)):
                    values = [point[key] for point in chunk if point[key] is not None]
                    item[key] = round(sum(values) / len(values), 3) if values else None
                    item[f"{key}_min"] = min(values) if values else None
                    item[f"{key}_max"] = max(values) if values else None
                    item[f"{key}_below"] = sum(value < threshold for value in values)
                buckets.append(item)
            gk_series = buckets

        def no_trade_reasons(rows, side):
            """用 bar snapshot 的已保存 gate 統計未開單原因；不推估未保存風控。"""
            signal_key = "long_signal" if side == "L" else "short_signals"
            gk_key = "gk_pctile" if side == "L" else "gk_pctile_s"
            breakout_key = "breakout_long" if side == "L" else "breakout_short"
            session_key = "session_ok_l" if side == "L" else "session_ok_s"
            regime_key = "regime_block_l" if side == "L" else "regime_block_s"
            position_key = "long_positions" if side == "L" else "short_positions"
            threshold = 25.0 if side == "L" else 35.0
            reasons = {}
            signal_bars = 0
            blocked_bars = 0
            for raw in rows:
                signal = str(raw.get(signal_key) or "").strip().upper()
                if signal and signal not in {"HOLD", "NONE", "N/A"}:
                    signal_bars += 1
                    continue
                blocked_bars += 1
                gk = _number(raw.get(gk_key), None)
                if gk is None:
                    label = "GK 暖機／資料不足"
                elif gk >= threshold:
                    label = f"GK 未壓縮（≥{threshold:g}%）"
                elif str(raw.get(breakout_key) or "").strip() == "":
                    label = "突破欄位不足"
                elif not _truthy(raw.get(breakout_key)):
                    label = "未突破 15-bar"
                elif str(raw.get(session_key) or "").strip() == "":
                    label = "時段欄位不足"
                elif not _truthy(raw.get(session_key)):
                    label = "封鎖時段／休市日"
                elif _truthy(raw.get(regime_key)):
                    label = "Regime gate"
                elif _int(raw.get(position_key), 0) >= 1:
                    label = "已有同向持倉"
                else:
                    label = "風控／冷卻／其他（快照未保存）"
                reasons[label] = reasons.get(label, 0) + 1
            reason_rows = [
                {"label": label, "count": count}
                for label, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
            ]
            return {
                "sample_bars": len(rows),
                "blocked_bars": blocked_bars,
                "signal_bars": signal_bars,
                "reasons": reason_rows,
                "note": "統計依已保存的 GK、突破、時段、Regime、同向持倉欄位；月虧上限與出場冷卻未逐根保存，合併於其他。",
            }

        no_trade = {
            "L": no_trade_reasons(snapshot_selected, "L"),
            "S": no_trade_reasons(snapshot_selected, "S"),
        }

        selected_ids = {str(row.get("id")) for row in selected_trades}
        lifecycle_by_bar = {}
        for raw in lifecycle_rows:
            if str(raw.get("trade_id")) not in selected_ids:
                continue
            stamp = _parse_datetime(raw.get("bar_time_utc8"))
            if stamp is None or not start <= stamp < end:
                continue
            bar = _int(raw.get("lifecycle_bar"), None)
            pnl_pct = _number(raw.get("unrealized_pnl_pct"), None)
            if bar is None or pnl_pct is None:
                continue
            lifecycle_by_bar.setdefault(bar, []).append(pnl_pct)
        lifecycle_series = [
            {"bar": bar, "avg_unrealized_pnl_pct": round(sum(values) / len(values), 6), "observations": len(values)}
            for bar, values in sorted(lifecycle_by_bar.items())
        ]

        mae_mfe = [
            {
                "number": row.get("number"),
                "side": row.get("side"),
                "mae_pct": row.get("mae_pct"),
                "mfe_pct": row.get("mfe_pct"),
                "pnl": row.get("pnl"),
            }
            for row in closed
            if row.get("mae_pct") is not None and row.get("mfe_pct") is not None
        ]

        rolling_values = [value["rolling_mfe"] for value in rolling.values() if value.get("rolling_mfe") is not None]
        in_range = [point["rolling_mfe"] for point in equity if point.get("rolling_mfe") is not None]
        rolling_summary = {
            "window": ROLLING_WINDOW,
            "history_median_mfe": round(sorted(rolling_values)[len(rolling_values) // 2], 4) if rolling_values else None,
            "range_first_mfe": in_range[0] if in_range else None,
            "range_last_mfe": in_range[-1] if in_range else None,
            "range_min_mfe": min(in_range) if in_range else None,
            "mfe_available": bool(rolling_values),
        }
        comparison = self._compare_with_backtest(start, end, side, basis, closed) if source == "live" else None

        return {
            "source": source,
            "source_label": self.source_label(source),
            "basis": basis,
            "rolling": rolling_summary,
            "comparison": comparison,
            "backtest_metrics": dict(self._backtest_metrics_status) if source == "backtest" else None,
            "summary": {
                **summary(closed),
                "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else (999.0 if gross_profit else 0.0),
                "max_drawdown": round(max_drawdown, 6),
                "best_trade": max(pnls) if pnls else 0.0,
                "worst_trade": min(pnls) if pnls else 0.0,
                "avg_hold_hours": round(sum(float(row.get("hold_hours") or 0) for row in closed) / len(closed), 6) if closed else 0.0,
            },
            "equity": equity,
            "drawdown": equity,
            "daily": daily,
            "monthly": monthly,
            "by_side": grouped(closed, lambda row: "Long" if row.get("side") == "L" else "Short"),
            "by_exit_reason": grouped(closed, lambda row: row.get("exit_reason")),
            "by_regime": grouped(closed, lambda row: row.get("regime")),
            "hold_distribution": hold_distribution,
            "streaks": {
                "max_win": max_win_streak,
                "max_loss": max_loss_streak,
                "current_kind": current_kind,
                "current_length": current_streak,
            },
            "mae_mfe": mae_mfe,
            "evidence": {
                "bar_snapshots": {
                    "available": bool(snapshot_rows) and snapshot_error is None,
                    "rows": len(snapshot_rows),
                    "error": snapshot_error,
                    "gk_series": gk_series,
                    "gk_stats": gk_stats,
                    "breakout_counts": breakout_counts,
                    "no_trade_reasons": no_trade,
                },
                "position_lifecycle": {
                    "available": bool(lifecycle_rows) and lifecycle_error is None,
                    "rows": len(lifecycle_rows),
                    "error": lifecycle_error,
                    "series": lifecycle_series,
                },
                "tp_safenet_maxhold_lines": {
                    "available": False,
                    "reason": "目前紀錄沒有逐根保存可驗證的 TP／SafeNet／MaxHold 價格線",
                },
            },
        }

    def _closed_sorted(self, source, side="ALL", basis="actual"):
        rows = [
            row for row in _with_basis(self.trades(source), basis)
            if row["closed"] and row.get("pnl") is not None and (side == "ALL" or row["side"] == side)
        ]
        return sorted(rows, key=lambda row: row.get("exit_time_utc") or row.get("entry_time_utc") or "")

    def _rolling_metrics(self, source, side="ALL", basis="actual"):
        """全歷史逐筆滾動平均（近 ROLLING_WINDOW 筆），讓區間開頭也有完整窗口。"""
        result = {}
        window = []
        min_count = 5

        def average(values):
            valid = [float(value) for value in values if value is not None]
            return round(sum(valid) / len(valid), 4) if len(valid) >= min_count else None

        for row in self._closed_sorted(source, side, basis):
            window.append(row)
            window = window[-ROLLING_WINDOW:]
            result[str(row.get("id"))] = {
                "rolling_mfe": average(item.get("mfe_pct") for item in window),
                "rolling_mae": average(item.get("mae_pct") for item in window),
                "rolling_pnl_200u": average(item.get("pnl_200u") for item in window),
                "rolling_count": len(window),
            }
        return result

    def _compare_with_backtest(self, start, end, side, basis, live_closed):
        """同一日期區間的實戰 vs 回測快照：累積曲線與逐筆對齊（方向 + 進場成交時刻）。"""
        try:
            backtest_rows = self.select_trades("backtest", start, end, side, basis)
        except (OSError, ValueError) as exc:
            return {"available": False, "reason": f"回測快照讀取失敗：{exc}"}
        if not self.source_path("backtest").is_file():
            return {"available": False, "reason": "VPS 上尚未產生回測快照"}
        backtest_closed = sorted(
            [row for row in backtest_rows if row["closed"] and row.get("pnl") is not None],
            key=lambda row: row.get("exit_time_utc") or "",
        )
        live_sorted = sorted(live_closed, key=lambda row: row.get("exit_time_utc") or "")

        def curve(rows):
            total = 0.0
            points = []
            for row in rows:
                total += float(row["pnl"])
                points.append({
                    "time_utc": row.get("exit_time_utc"),
                    "time_display": row.get("exit_time_display"),
                    "cumulative_pnl": round(total, 6),
                    "number": row.get("number"),
                    "side": row.get("side"),
                })
            return points

        def key(row):
            return row.get("side"), str(row.get("entry_time_display") or "")[:16]

        backtest_by_key = {key(row): row for row in backtest_closed}
        live_by_key = {key(row): row for row in live_sorted}
        matched = [(row, backtest_by_key[key(row)]) for row in live_sorted if key(row) in backtest_by_key]
        brief = lambda row: {
            "number": row.get("number"),
            "side": row.get("side"),
            "entry_time_display": row.get("entry_time_display"),
            "exit_reason": row.get("exit_reason"),
            "pnl": row.get("pnl"),
        }
        bounds = self.available_date_bounds("backtest")
        coverage_note = None
        if bounds.get("min_date") and _date_label(start) < bounds["min_date"]:
            coverage_note = f"回測快照最早只到 {bounds['min_date']}，更早的實戰交易沒有對照"
        return {
            "available": True,
            "live": {"trades": len(live_sorted), "pnl": round(sum(float(r["pnl"]) for r in live_sorted), 6), "curve": curve(live_sorted)},
            "backtest": {"trades": len(backtest_closed), "pnl": round(sum(float(r["pnl"]) for r in backtest_closed), 6), "curve": curve(backtest_closed)},
            "matched": len(matched),
            "matched_pnl_gap": round(sum(float(live["pnl"]) - float(bt["pnl"]) for live, bt in matched), 6),
            "exit_reason_mismatch": sum(
                1 for live, bt in matched if _exit_code(live.get("exit_reason")) != _exit_code(bt.get("exit_reason"))
            ),
            "live_only": [brief(row) for row in live_sorted if key(row) not in backtest_by_key],
            "backtest_only": [brief(row) for row in backtest_closed if key(row) not in live_by_key],
            "coverage_note": coverage_note,
            "backtest_updated_at": _iso(datetime.fromtimestamp(_mtime(self.source_path("backtest")), timezone.utc))
            if _mtime(self.source_path("backtest")) else None,
        }

    def edge(self, source="live"):
        """策略健康度：V29 CUSUM + 證偽檢查（Edge／突破延續／尾部）+ 月度燈號連續數。

        判讀函式直接沿用 edge_falsify.py（只含純計算，不匯入 strategy）；實盤貼合項需要回測引擎，
        Viewer 不執行，改由「實戰 vs 回測」卡片與 analyze.py 呈現。
        """
        try:
            import edge_falsify
        except ImportError as exc:
            raise OSError(f"edge_falsify.py 無法載入：{exc}") from exc

        rows = self._closed_sorted(source)
        thresholds = _edge_thresholds()
        constants = _literal_constants(STRATEGY_PATH, {"EDGE_CUSUM_K"})
        cusum_k = float(constants.get("EDGE_CUSUM_K", 14.3))

        def light(hp):
            if hp is None:
                return None
            return "green" if hp >= 60 else "yellow" if hp >= 25 else "red"

        def evaluate(subset):
            pnls = [float(row["pnl_200u"]) for row in subset if row.get("pnl_200u") is not None]
            codes = [_exit_code(row.get("exit_reason")) for row in subset]
            if not pnls:
                return None
            checks = [
                ("edge", "Edge 強度", *edge_falsify._check_edge(pnls)),
                ("continuation", "突破延續", *edge_falsify._check_continuation(codes)),
                ("tail", "尾部風險", *edge_falsify._check_tail(pnls, codes)),
            ]
            items = [
                {"key": key, "name": name, "hp": None if hp is None else round(hp, 1), "light": light(hp), "note": note}
                for key, name, hp, note in checks
            ]
            scored = [item["hp"] for item in items if item["hp"] is not None]
            worst = min(scored) if scored else None
            return {
                "trades": len(pnls),
                "avg_pnl_200u": round(sum(pnls) / len(pnls), 2),
                "baseline_avg_pnl_200u": edge_falsify.BASE_AVG_R,
                "items": items,
                "overall_hp": worst,
                "overall_light": light(worst),
            }

        recent = rows[-EDGE_RECENT_TRADES:]
        current = evaluate(recent)

        # 月度燈號：每個月底（本月為截至目前）回看最近 30 筆，與 analyze.py 每月檢查的視角一致。
        def exit_month(row):
            return str(row.get("exit_time_display") or "")[:7]

        if source == "live":
            last_month = datetime.now(UTC8).strftime("%Y-%m")
        else:
            last_month = exit_month(rows[-1]) if rows else datetime.now(UTC8).strftime("%Y-%m")
        months = []
        year, month = int(last_month[:4]), int(last_month[5:7])
        for _ in range(EDGE_MONTHS):
            months.append(f"{year:04d}-{month:02d}")
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        months.reverse()
        monthly = []
        for label in months:
            subset = [row for row in rows if exit_month(row) <= label][-EDGE_RECENT_TRADES:]
            result = evaluate(subset) if len(subset) >= 10 else None
            monthly.append({
                "month": label,
                "in_progress": source == "live" and label == last_month,
                "closed_in_month": sum(1 for row in rows if exit_month(row) == label),
                "overall_hp": result["overall_hp"] if result else None,
                "light": result["overall_light"] if result else None,
                "avg_pnl_200u": result["avg_pnl_200u"] if result else None,
            })
        streak = 0
        for item in reversed(monthly):
            if item["light"] in {"yellow", "red"}:
                streak += 1
            else:
                break

        # V29 健康度：實戰以機器人狀態檔為準；回測（或狀態檔缺值）以同公式回放。
        replay = 0.0
        series = []
        red_episodes = 0
        replay_level = "green"
        level_since = None
        for row in rows:
            if row.get("pnl_200u") is None:
                continue
            replay = max(0.0, replay + (cusum_k - float(row["pnl_200u"])))
            next_level = "red" if replay > thresholds["red"] else "yellow" if replay > thresholds["yellow"] else "green"
            if next_level != replay_level:
                red_episodes += next_level == "red"
                replay_level = next_level
                level_since = row.get("exit_time_display")
            series.append({
                "time_display": row.get("exit_time_display"),
                "pct": round(max(0.0, (thresholds["red"] - replay) / thresholds["red"] * 100), 1),
            })
        cusum = replay
        cusum_source = "交易回放"
        if source == "live":
            payload, error = _read_json(STATE_PATH)
            stored = (payload or {}).get("edge_health") if not error else None
            if isinstance(stored, dict) and _number(stored.get("cusum"), None) is not None:
                cusum = float(stored["cusum"])
                cusum_source = "機器人狀態檔"
        pct = max(0.0, (thresholds["red"] - cusum) / thresholds["red"] * 100)
        level = "red" if cusum > thresholds["red"] else "yellow" if cusum > thresholds["yellow"] else "green"
        yellow_pct = (thresholds["red"] - thresholds["yellow"]) / thresholds["red"] * 100
        recent_series = series[-60:]

        return {
            "source": source,
            "source_label": self.source_label(source),
            "server_time_display": _display_time(datetime.now(timezone.utc)),
            "recent_window": EDGE_RECENT_TRADES,
            "current": current,
            # 回測報告（run_backtest.py）的證偽檢查是看整段交易；健康度卡片看最近 30 筆，兩者並列避免誤讀。
            "full_window": evaluate(rows) if source == "backtest" else None,
            "monthly": monthly,
            "non_green_streak": streak,
            "freeze_rule": "連續兩個月 🟡（或更差）→ 凍結加碼；🔴 → 檢視是否退回 200U／暫停（V29 SOP）",
            "v29": {
                "pct": round(pct, 1),
                "level": level,
                "cusum": round(cusum, 2),
                "cusum_source": cusum_source,
                "replay_pct": round(max(0.0, (thresholds["red"] - replay) / thresholds["red"] * 100), 1),
                "replay_level_since": level_since if replay_level != "green" else None,
                "replay_red_episodes": red_episodes,
                "yellow_pct": round(yellow_pct, 1),
                "min_recent_pct": min((point["pct"] for point in recent_series), default=None),
                "series": recent_series,
            },
            "fidelity_note": "實盤貼合需執行回測引擎，Viewer 不跑；請看「實戰 vs 回測」卡片，或在 VPS 執行 analyze.py。",
            "basis_note": "Edge／尾部金額一律換算為 200U 保證金基準（實際 PnL × $4,000 ÷ 當筆名目）。",
        }

    def trade_detail(self, source="live", trade_id=""):
        """回傳單筆交易與持倉生命週期，僅讀取 CSV。"""
        trade_id = str(trade_id or "")
        if not trade_id or len(trade_id) > 200:
            raise ValueError("交易編號格式不正確")
        trade = next((row for row in self.trades(source) if str(row.get("id")) == trade_id), None)
        if trade is None:
            raise ValueError("找不到指定交易")

        lifecycle_rows, lifecycle_error = self.read_artifact(source, "position_lifecycle.csv")
        lifecycle = []
        for raw in lifecycle_rows:
            if str(raw.get("trade_id") or "") != trade_id:
                continue
            stamp = _parse_datetime(raw.get("bar_time_utc8") or raw.get("bar_time_utc"))
            lifecycle.append({
                "bar": _int(raw.get("lifecycle_bar"), None),
                "time_utc": _iso(stamp),
                "time_display": _display_time(stamp),
                "current_price": _number(raw.get("current_price"), None),
                "unrealized_pnl_usd": _number(raw.get("unrealized_pnl_usd"), None),
                "unrealized_pnl_pct": _number(raw.get("unrealized_pnl_pct"), None),
                "max_adverse_so_far": _number(raw.get("max_adverse_so_far"), None),
                "max_favorable_so_far": _number(raw.get("max_favorable_so_far"), None),
                "safenet_distance_pct": _number(raw.get("safenet_distance_pct"), None),
                "exit_triggered": _truthy(raw.get("exit_triggered")),
                "exit_type": str(raw.get("exit_type") or "").strip(),
            })
        lifecycle.sort(key=lambda row: (row.get("bar") is None, row.get("bar") or 0))
        return {
            "source": source,
            "source_label": self.source_label(source),
            "trade": trade,
            "lifecycle": lifecycle,
            "lifecycle_available": lifecycle_error is None and bool(lifecycle_rows),
            "lifecycle_error": lifecycle_error,
        }

    def health(self, source="live"):
        trade_path = self.source_path(source)
        trades_mtime = _mtime(trade_path)
        try:
            candles = self.klines(source)
            kline_status = "ok"
            latest = candles[-1] if candles else None
        except OSError as exc:
            kline_status = "error"
            latest = None
            self._last_kline_error = str(exc)
        state = self.state() if source == "live" else {
            "available": False,
            "error": "回測資料沒有即時持倉狀態",
            "updated_at": None,
            "positions": [],
        }
        return {
            "status": "ok" if kline_status == "ok" else "degraded",
            "service": "cryptoviewer",
            "readonly": True,
            "source": source,
            "source_label": self.source_label(source),
            "server_time_utc": _iso(datetime.now(timezone.utc)),
            "kline_status": kline_status,
            "kline_error": self._last_kline_error,
            "latest_kline": latest,
            "trades_exists": trade_path.is_file(),
            "trades_updated_at": _iso(datetime.fromtimestamp(trades_mtime, timezone.utc)) if trades_mtime else None,
            "trades_updated_at_display": (
                _display_time(datetime.fromtimestamp(trades_mtime, timezone.utc)) if trades_mtime else None
            ),
            "state": state,
            "instance_dir": str(INSTANCE_DIR),
        }


def make_handler(store: DataStore):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _html(self):
            body = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self._html()
                    return
                if parsed.path == "/api/health":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    self._json(store.health(source))
                    return
                if parsed.path == "/api/meta":
                    self._json(store.meta())
                    return
                if parsed.path == "/api/price":
                    self._json(store.price())
                    return
                if parsed.path == "/api/signal":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source != "live":
                        raise ValueError("即時開單條件只適用於實戰資料")
                    self._json(store.signal())
                    return
                if parsed.path == "/api/data":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    days = query.get("days", ["30"])[0]
                    start_date = query.get("start", [None])[0]
                    end_date = query.get("end", [None])[0]
                    side = query.get("side", ["ALL"])[0].upper()
                    if side not in {"ALL", "L", "S"}:
                        raise ValueError("方向只能是 ALL、L 或 S")
                    self._json(store.data(
                        source=source, days=days, side=side,
                        start_date=start_date, end_date=end_date,
                        basis=_basis(query.get("basis", ["actual"])[0]),
                    ))
                    return
                if parsed.path == "/api/analysis":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    days = query.get("days", ["30"])[0]
                    start_date = query.get("start", [None])[0]
                    end_date = query.get("end", [None])[0]
                    side = query.get("side", ["ALL"])[0].upper()
                    if side not in {"ALL", "L", "S"}:
                        raise ValueError("方向只能是 ALL、L 或 S")
                    self._json(store.analysis(
                        source=source, days=days, side=side,
                        start_date=start_date, end_date=end_date,
                        basis=_basis(query.get("basis", ["actual"])[0]),
                    ))
                    return
                if parsed.path == "/api/edge":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    self._json(store.edge(source))
                    return
                if parsed.path == "/api/trade":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    trade_id = query.get("id", [""])[0]
                    self._json(store.trade_detail(source=source, trade_id=trade_id))
                    return
                self.send_error(404)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._json({"error": str(exc), "readonly": True}, status=503 if isinstance(exc, OSError) else 400)

        def do_POST(self):
            self._json({"error": "唯讀服務不接受 POST", "readonly": True}, status=405)

        def do_PUT(self):
            self._json({"error": "唯讀服務不接受 PUT", "readonly": True}, status=405)

        def do_DELETE(self):
            self._json({"error": "唯讀服務不接受 DELETE", "readonly": True}, status=405)

        def log_message(self, fmt, *args):
            if args and str(args[1]) not in {"200", "304"}:
                super().log_message(fmt, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="CryptoBot VPS 唯讀交易視覺化服務")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--offline", action="store_true", help="不呼叫 Binance 公開 K 線 API")
    args = parser.parse_args()

    if not HTML_PATH.is_file():
        raise SystemExit(f"缺少頁面：{HTML_PATH}")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(DataStore(not args.offline)))
    print(f"CryptoBot 唯讀 Viewer：http://{args.host}:{args.port}", flush=True)
    print(f"Instance data：{INSTANCE_DIR}", flush=True)
    print("只提供 GET；不載入 .env、不下單、不修改交易狀態。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
