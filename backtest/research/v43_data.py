"""V43 資料：data.binance.vision 的現貨 1h K 線月檔（BTCUSDT、ETHUSDT；給 VPS 用）。

預登記：doc/v43_funding_carry_plan.md。
只在 VPS 執行（不得在公司網路／設備下載行情）。永續 1h K 線與 funding 沿用 V42 的
data/v42/{SYMBOL}_1h.csv、data/v42/{SYMBOL}_funding.csv（先跑過 v42_data.py fetch）。
可續傳：已完成的月份存在 data/v43/_raw/，重跑只補缺的月份；最後一個月用每日檔。
2025-01 起現貨檔時間戳為微秒，parse_klines 已自動換成毫秒。

輸出：
    data/v43/{SYMBOL}_spot_1h.csv  open,high,low,close,volume,taker_buy_volume,datetime（UTC+8 開盤，同 V42）

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v43_data.py fetch
    .venv/bin/python backtest/research/v43_data.py status
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v42_data import (DATA_DIR as PERP_DIR, KLINE_COLS, LAST_DAY, fetch_bytes, months,  # noqa: E402
                      parse_klines, read_zip_csv, save_raw)

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "v43"
RAW_DIR = DATA_DIR / "_raw"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

BASE = "https://data.binance.vision/data/spot"
SPOT_MONTHLY = BASE + "/monthly/klines/{s}/1h/{s}-1h-{m}.zip"
SPOT_DAILY = BASE + "/daily/klines/{s}/1h/{s}-1h-{d}.zip"


def raw_path(symbol: str, kind: str, tag: str) -> Path:
    return RAW_DIR / symbol / f"{kind}_{tag}.csv"


def fetch_spot_month(symbol: str, month: str) -> str:
    out = raw_path(symbol, "spot", month)
    if out.exists():
        return "cached"
    blob = fetch_bytes(SPOT_MONTHLY.format(s=symbol, m=month))
    if blob is not None:
        save_raw(out, parse_klines(read_zip_csv(blob, KLINE_COLS)))
        return "monthly"
    days = pd.date_range(f"{month}-01", min(pd.Timestamp(f"{month}-01") + pd.offsets.MonthEnd(0),
                                            pd.Timestamp(LAST_DAY)), freq="D")
    parts, missing = [], []
    for d in days:
        tag = d.strftime("%Y-%m-%d")
        day_out = raw_path(symbol, "spot_day", tag)
        if not day_out.exists():
            b = fetch_bytes(SPOT_DAILY.format(s=symbol, d=tag))
            if b is None:
                missing.append(tag)
                continue
            save_raw(day_out, parse_klines(read_zip_csv(b, KLINE_COLS)))
            time.sleep(0.2)
        parts.append(pd.read_csv(day_out))
    if missing:
        return f"daily（缺 {len(missing)} 天：{missing[0]}～{missing[-1]}，稍後重跑）"
    save_raw(out, pd.concat(parts, ignore_index=True))
    return f"daily×{len(days)}"


def merge_symbol(symbol: str) -> int:
    parts = [pd.read_csv(p) for m in months() if (p := raw_path(symbol, "spot", m)).exists()]
    if not parts:
        return 0
    k = pd.concat(parts, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    last_close_ms = int((pd.Timestamp(LAST_DAY) + pd.Timedelta(days=1)).value // 1_000_000) - 1
    k = k[k["close_time"] <= last_close_ms]
    out = k[["open", "high", "low", "close", "volume", "taker_buy_volume"]].copy()
    out["datetime"] = pd.to_datetime(k["open_time"], unit="ms") + pd.Timedelta(hours=8)
    out.to_csv(DATA_DIR / f"{symbol}_spot_1h.csv", index=False)
    return len(out)


def cmd_fetch(args) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for s in args.symbols:
        print(f"== {s}", flush=True)
        for m in months():
            r = fetch_spot_month(s, m)
            if r != "cached":
                print(f"  {m}  現貨K線 {r}", flush=True)
                time.sleep(args.pause)
        print(f"  → {s}_spot_1h.csv {merge_symbol(s)} 根", flush=True)
    print("完成；用 status 檢查缺口")


def bars_info(path: Path) -> tuple[int, str, str, int]:
    if not path.exists():
        return 0, "", "", 0
    dt = pd.to_datetime(pd.read_csv(path, usecols=["datetime"])["datetime"])
    return (len(dt), f"{dt.min():%Y-%m-%d %H}", f"{dt.max():%Y-%m-%d %H}",
            int((dt.diff().dropna() != pd.Timedelta(hours=1)).sum()))


def cmd_status(_args) -> None:
    n = len(months())
    ok = True
    print(f"{'檔案':28s} {'月':>7s} {'bars':>7s} {'first(UTC+8)':>14s} {'last(UTC+8)':>14s} {'跳格':>4s}")
    for s in SYMBOLS:
        got = sum(raw_path(s, "spot", m).exists() for m in months())
        b, f, la, g = bars_info(DATA_DIR / f"{s}_spot_1h.csv")
        print(f"{s + '_spot_1h.csv':28s} {f'{got}/{n}':>7s} {b:7d} {f:>14s} {la:>14s} {g:4d}")
        ok &= got == n and b > 0
        b, f, la, g = bars_info(PERP_DIR / f"{s}_1h.csv")
        fp = PERP_DIR / f"{s}_funding.csv"
        nf = len(pd.read_csv(fp)) if fp.exists() else 0
        print(f"{'v42/' + s + '_1h.csv（永續）':28s} {'':>7s} {b:7d} {f:>14s} {la:>14s} {g:4d}  funding {nf} 筆")
        ok &= b > 0 and nf > 0
    if ok:
        print("狀態：完整（跳格>0 代表交易所停機缺 K，屬正常）")
    else:
        print(f"狀態：有缺——現貨請重跑 fetch；永續／funding 缺請先跑 v42_data.py fetch --symbols {' '.join(SYMBOLS)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--symbols", nargs="+", default=SYMBOLS)
    f.add_argument("--pause", type=float, default=0.3)
    sub.add_parser("status")
    args = ap.parse_args()
    {"fetch": cmd_fetch, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
