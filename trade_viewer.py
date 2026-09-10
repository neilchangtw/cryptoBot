"""本機唯讀交易檢視器：ETH 1h 收盤價折線與進出場對照表。

只讀 K 線與交易紀錄，不載入 .env、不連 Binance、不啟動交易程序。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "trade_viewer.html"
KLINE_PATH = ROOT / "data" / "ETHUSDT_1h_latest730d.csv"
DOWNLOADS = Path.home() / "Downloads"

TEXT_TRADE_RE = re.compile(
    r"^\s*(?P<number>\d+)\s+"
    r"(?P<side>[LS])\s+"
    r"(?P<entry_time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"(?P<entry_price>\d+(?:\.\d+)?)\s+"
    r"(?P<exit_time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"
    r"(?P<exit_price>\d+(?:\.\d+)?)\s+"
    r"(?P<exit_reason>.+?)\s+"
    r"(?P<hold>\d+)\s+"
    r"(?P<tail>.+?)\s*$"
)
LIVE_TAIL_RE = re.compile(
    r"^(?P<pnl>[+-]?\d+(?:\.\d+)?)\s+(?P<regime>.+?)$"
)
BACKTEST_TAIL_RE = re.compile(
    r"^(?P<margin>\d+(?:\.\d+)?)\s+"
    r"(?P<pnl>[+-]?\d+(?:\.\d+)?)\s+"
    r"(?P<move>[+-]?\d+(?:\.\d+)?)\s+"
    r"(?P<pnl_pct>[+-]?\d+(?:\.\d+)?)\s+"
    r"(?P<regime>.+?)$"
)


def _parse_time(value: str) -> datetime:
    text = str(value or "").strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"無法解析時間：{value!r}")


def _number(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return float(default)


def _side(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in {"L", "LONG"}:
        return "L"
    if text in {"S", "SHORT"}:
        return "S"
    return text[:1] or "?"


def _clean_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def load_text_trades(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = TEXT_TRADE_RE.match(line)
        if not match:
            continue
        item = match.groupdict()
        tail = BACKTEST_TAIL_RE.match(item["tail"]) or LIVE_TAIL_RE.match(item["tail"])
        if not tail:
            continue
        tail_item = tail.groupdict()
        rows.append({
            "id": f"{path.stem}-{item['number']}",
            "number": int(item["number"]),
            "side": item["side"],
            "entry_time": item["entry_time"],
            "entry_price": _number(item["entry_price"]),
            "exit_time": item["exit_time"],
            "exit_price": _number(item["exit_price"]),
            "exit_reason": _clean_label(item["exit_reason"]),
            "hold": int(item["hold"]),
            "pnl": _number(tail_item["pnl"]),
            "regime": _clean_label(tail_item["regime"]),
        })
    if not rows:
        raise ValueError(f"找不到交易明細列：{path}")
    return rows


def load_csv_trades(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for index, raw in enumerate(csv.DictReader(handle), start=1):
            pnl_raw = raw.get("net_pnl_usd", raw.get("pnl_usd", ""))
            if str(pnl_raw or "").strip() == "":
                continue
            entry_raw = raw.get("entry_time_utc8", raw.get("entry_dt", ""))
            exit_raw = raw.get("exit_time_utc8", raw.get("exit_dt", ""))
            if not entry_raw or not exit_raw:
                continue
            entry_time = _parse_time(entry_raw)
            exit_time = _parse_time(exit_raw)
            # recorder 的 *_utc8 是訊號 K 棒開盤；實際成交在收盤後一小時。
            if "entry_time_utc8" in raw:
                entry_time += timedelta(hours=1)
                exit_time += timedelta(hours=1)
            number = raw.get("trade_number") or raw.get("trade_id") or index
            rows.append({
                "id": str(raw.get("trade_id") or f"csv-{number}"),
                "number": int(number) if str(number).isdigit() else str(number),
                "side": _side(raw.get("sub_strategy") or raw.get("direction")),
                "entry_time": entry_time.strftime("%Y-%m-%d %H:%M"),
                "entry_price": _number(raw.get("entry_price")),
                "exit_time": exit_time.strftime("%Y-%m-%d %H:%M"),
                "exit_price": _number(raw.get("exit_price")),
                "exit_reason": _clean_label(raw.get("exit_type") or raw.get("exit_reason")),
                "hold": int(_number(raw.get("hold_bars", raw.get("hold_hours", 0)))),
                "pnl": _number(pnl_raw),
                "regime": _clean_label(raw.get("entry_regime")),
            })
    if not rows:
        raise ValueError(f"CSV 沒有已平倉交易：{path}")
    return rows


def load_trades(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        return load_csv_trades(path)
    return load_text_trades(path)


def discover_sources() -> dict[str, Path]:
    candidates = {
        "live": [ROOT / "data_live" / "trades.csv", DOWNLOADS / "實戰ALL.txt"],
        "backtest": [DOWNLOADS / "回測ALL.txt"],
        "paper": [ROOT / "data" / "trades.csv"],
    }
    found = {}
    for name, paths in candidates.items():
        found_path = next((path for path in paths if path.is_file()), None)
        if found_path:
            found[name] = found_path
    return found


class DataStore:
    def __init__(self, sources: dict[str, Path]):
        self.sources = sources
        self._trade_cache = {}
        self._kline_cache = None
        self._kline_mtime = None

    def trades(self, source: str) -> list[dict]:
        path = self.sources[source]
        mtime = path.stat().st_mtime_ns
        cached = self._trade_cache.get(source)
        if cached and cached[0] == mtime:
            return cached[1]
        rows = load_trades(path)
        rows.sort(key=lambda row: row["entry_time"])
        self._trade_cache[source] = (mtime, rows)
        return rows

    def klines(self) -> list[dict]:
        mtime = KLINE_PATH.stat().st_mtime_ns
        if self._kline_cache is not None and self._kline_mtime == mtime:
            return self._kline_cache
        rows = []
        with KLINE_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                rows.append({"time": raw["datetime"][:16], "close": _number(raw["close"])})
        self._kline_cache = rows
        self._kline_mtime = mtime
        return rows

    def metadata(self) -> dict:
        source_rows = []
        for name, path in self.sources.items():
            trades = self.trades(name)
            first = _parse_time(trades[0]["entry_time"])
            last = _parse_time(trades[-1]["exit_time"])
            source_rows.append({
                "id": name,
                "label": {"live": "正式盤", "backtest": "回測", "paper": "模擬盤"}.get(name, name),
                "path": str(path),
                "count": len(trades),
                "first": first.strftime("%Y-%m-%d"),
                "last": last.strftime("%Y-%m-%d"),
                "default_from": max(first - timedelta(days=1), last - timedelta(days=60)).strftime("%Y-%m-%d"),
                "default_to": (last + timedelta(days=1)).strftime("%Y-%m-%d"),
            })
        klines = self.klines()
        return {
            "sources": source_rows,
            "kline": {
                "path": str(KLINE_PATH),
                "count": len(klines),
                "first": klines[0]["time"],
                "last": klines[-1]["time"],
            },
        }

    def data(self, source: str, date_from: str, date_to: str, side: str) -> dict:
        start = _parse_time(f"{date_from} 00:00")
        end = _parse_time(f"{date_to} 00:00") + timedelta(days=1)
        trades = [
            row for row in self.trades(source)
            if _parse_time(row["entry_time"]) < end
            and _parse_time(row["exit_time"]) >= start
            and (side == "ALL" or row["side"] == side)
        ]
        klines = [
            row for row in self.klines()
            if start <= _parse_time(row["time"]) < end
        ]
        original_points = len(klines)
        if len(klines) > 3000:
            step = max(1, len(klines) // 3000)
            klines = klines[::step]
            if klines[-1]["time"] != self.klines()[-1]["time"]:
                last_in_range = next((row for row in reversed(self.klines()) if _parse_time(row["time"]) < end), None)
                if last_in_range and (not klines or klines[-1]["time"] != last_in_range["time"]):
                    klines.append(last_in_range)
        pnl = sum(row["pnl"] for row in trades)
        wins = sum(row["pnl"] > 0 for row in trades)
        return {
            "candles": klines,
            "trades": trades,
            "stats": {
                "trades": len(trades),
                "wins": wins,
                "losses": len(trades) - wins,
                "win_rate": wins / len(trades) * 100 if trades else 0,
                "pnl": pnl,
                "points": original_points,
                "shown_points": len(klines),
            },
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

        def do_GET(self):
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    body = HTML_PATH.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parsed.path == "/api/meta":
                    self._json(store.metadata())
                    return
                if parsed.path == "/api/data":
                    query = parse_qs(parsed.query)
                    source = query.get("source", [""])[0]
                    side = query.get("side", ["ALL"])[0].upper()
                    if source not in store.sources:
                        raise ValueError("找不到指定資料來源")
                    if side not in {"ALL", "L", "S"}:
                        raise ValueError("方向只能是 ALL、L 或 S")
                    self._json(store.data(
                        source,
                        query.get("from", [""])[0],
                        query.get("to", [""])[0],
                        side,
                    ))
                    return
                self.send_error(404)
            except (OSError, ValueError, KeyError) as exc:
                self._json({"error": str(exc)}, status=400)

        def log_message(self, fmt, *args):
            if args and str(args[1]) != "200":
                super().log_message(fmt, *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="ETH K 線與交易對照表（唯讀）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not HTML_PATH.is_file():
        raise SystemExit(f"缺少頁面：{HTML_PATH}")
    if not KLINE_PATH.is_file():
        raise SystemExit(f"缺少 K 線：{KLINE_PATH}")
    sources = discover_sources()
    if not sources:
        raise SystemExit("找不到交易資料：請放入 data_live/trades.csv、data/trades.csv 或 Downloads/*ALL.txt")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(DataStore(sources)))
    url = f"http://{args.host}:{args.port}"
    print(f"交易檢視器：{url}")
    print("按 Ctrl+C 關閉；本工具只讀資料，不會連線或下單。")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
