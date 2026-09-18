"""VPS 唯讀交易視覺化服務。

此服務與交易機器人完全分離，只讀取：
  - INSTANCE_DIR/data_live/trades.csv
  - data/backtest_trades.txt（由 run_backtest.py -t 輸出的回測快照）
  - INSTANCE_DIR/eth_state_live.json
  - INSTANCE_DIR/logs/
  - Binance Futures 公開 ETHUSDT 1h K 線（不需要 API key）

不載入 .env、不匯入 strategy/executor/binance_trade、不提供任何寫入或下單 API。

資料模式：實戰使用即時公開 K 線與 data_live/trades.csv；回測使用本機 730 天 K 線快取
與 data/backtest_trades.txt。回測沒有即時持倉狀態。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
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
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
KLINE_TTL_SECONDS = 55
MAX_KLINES = 1000
UTC8 = timezone(timedelta(hours=8))


def _number(value, default=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _int(value, default=None):
    number = _number(value, None)
    return int(number) if number is not None else default


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
        "pnl_pct": _number(raw.get("net_pnl_pct"), None),
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
            "pnl_pct": None,
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


class DataStore:
    def __init__(self, allow_network=True):
        self.allow_network = allow_network
        self._lock = threading.RLock()
        self._trades_cache = {}
        self._klines_cache = {}
        self._last_kline_error = None

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
        rows.sort(key=lambda row: row["entry_time_utc"] or "")
        with self._lock:
            self._trades_cache[source] = (mtime, rows)
        return rows

    def _fetch_public_klines(self):
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

    def klines(self, source="live"):
        now = time.time()
        with self._lock:
            cached = self._klines_cache.get(source)
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
                    rows = _parse_local_klines(path)[-MAX_KLINES:]
                    if rows:
                        break
                except OSError as exc:
                    errors.append(str(exc))

        if not rows and self.allow_network:
            try:
                rows = self._fetch_public_klines()
            except (OSError, URLError, TimeoutError, ValueError) as exc:
                errors.append(f"公開 K 線：{exc}")

        if not rows and source == "live":
            for path in local_paths:
                if not path.is_file():
                    continue
                try:
                    rows = _parse_local_klines(path)[-MAX_KLINES:]
                    if rows:
                        break
                except OSError as exc:
                    errors.append(str(exc))

        if not rows:
            raise OSError("；".join(errors) or "找不到 K 線資料")

        with self._lock:
            self._klines_cache[source] = (time.time(), rows)
            self._last_kline_error = errors[-1] if errors else None
        return rows

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
            positions.append({
                "id": str(trade_id),
                "side": side,
                "direction": "Long" if side == "L" else "Short" if side == "S" else side,
                "entry_time_utc": _iso(_parse_datetime(position.get("entry_time_utc8") or position.get("entry_time_utc"))),
                "entry_time_display": _display_time(_parse_datetime(position.get("entry_time_utc8") or position.get("entry_time_utc"))),
                "entry_price": _number(position.get("entry_price")),
                "entry_regime": str(position.get("entry_regime") or "NA"),
                "hold_bars": _int(position.get("bars_held"), None),
                "mfe_pct": _number(position.get("running_mfe_pct"), None),
            })

        updated = _mtime(STATE_PATH)
        return {
            "available": True,
            "error": None,
            "updated_at": _iso(datetime.fromtimestamp(updated, timezone.utc)) if updated else None,
            "last_bar_time": payload.get("last_bar_time"),
            "positions": positions,
        }

    def meta(self):
        sources = []
        for source in ("live", "backtest"):
            path = self.source_path(source)
            available = path.is_file()
            rows = []
            error = None
            if available:
                try:
                    rows = self.trades(source)
                except OSError as exc:
                    error = str(exc)
            sources.append({
                "id": source,
                "label": self.source_label(source),
                "available": available and error is None and bool(rows),
                "path": str(path),
                "count": len(rows),
                "first": rows[0]["entry_time_display"] if rows else None,
                "last": rows[-1]["exit_time_display"] if rows else None,
                "error": error or (None if available else "尚未找到資料檔"),
            })
        return {"sources": sources, "timezone": "Asia/Taipei"}

    def data(self, source="live", days=30, side="ALL"):
        candles = self.klines(source)
        if not candles:
            raise OSError("沒有 K 線資料")
        days = max(1, min(int(days), 730))
        latest = datetime.fromtimestamp(candles[-1]["time_ms"] / 1000, timezone.utc)
        start = latest - timedelta(days=days)
        selected_candles = [
            row for row in candles
            if datetime.fromtimestamp(row["time_ms"] / 1000, timezone.utc) >= start
        ]

        trades = self.trades(source)
        selected_trades = []
        for row in trades:
            if side != "ALL" and row["side"] != side:
                continue
            entry_ms = _parse_datetime(row["entry_time_utc"])
            exit_ms = _parse_datetime(row["exit_time_utc"])
            if (entry_ms and entry_ms >= start) or (exit_ms and exit_ms >= start):
                selected_trades.append(row)

        closed = [row for row in selected_trades if row["closed"]]
        pnl_values = [row["pnl"] for row in closed if row["pnl"] is not None]
        wins = sum(value > 0 for value in pnl_values)
        state = self.state() if source == "live" else {
            "available": False,
            "error": "回測資料沒有即時持倉狀態",
            "updated_at": None,
            "positions": [],
        }
        return {
            "server_time_utc": _iso(datetime.now(timezone.utc)),
            "server_time_display": _display_time(datetime.now(timezone.utc)),
            "timezone": "Asia/Taipei",
            "source": source,
            "source_label": self.source_label(source),
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

    def health(self, source="live"):
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
            "trades_exists": self.source_path(source).is_file(),
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
                if parsed.path == "/api/data":
                    query = parse_qs(parsed.query)
                    source = query.get("source", ["live"])[0].lower()
                    if source not in {"live", "backtest"}:
                        raise ValueError("資料來源只能是 live 或 backtest")
                    days = query.get("days", ["30"])[0]
                    side = query.get("side", ["ALL"])[0].upper()
                    if side not in {"ALL", "L", "S"}:
                        raise ValueError("方向只能是 ALL、L 或 S")
                    self._json(store.data(source=source, days=days, side=side))
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
