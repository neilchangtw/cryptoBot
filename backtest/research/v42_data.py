"""V42 資料：data.binance.vision 的 UM 永續 1h K 線與 fundingRate 月檔（給 VPS 用）。

預登記：doc/v42_new_strategy_screen_plan.md。
只在 VPS 執行（不得在公司網路／設備下載行情）。可續傳：已完成的月份記在
data/v42/_raw/ 下的完成標記，重跑只補缺的月份。

範圍 2020-11～2026-10（2020-11～12 只作指標暖機與 B 的回看期）。
最後一個月（月檔尚未發布）K 線改用每日檔；funding 沒有每日檔，改用 Binance 公開
/fapi/v1/fundingRate（不需 API key）補當月。

輸出：
    data/v42/{SYMBOL}_1h.csv       open,high,low,close,volume,taker_buy_volume,datetime（UTC+8，同 fetch_backtest_data）
    data/v42/{SYMBOL}_funding.csv  funding_time_ms,funding_time_utc,rate（同 data/ETHUSDT_funding.csv）

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v42_data.py fetch
    .venv/bin/python backtest/research/v42_data.py fetch --symbols BTCUSDT SOLUSDT
    .venv/bin/python backtest/research/v42_data.py status
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "v42"
RAW_DIR = DATA_DIR / "_raw"

A_SYMBOLS = ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
B_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT",
             "DOGEUSDT", "SOLUSDT", "DOTUSDT", "LTCUSDT", "LINKUSDT"]
SYMBOLS = list(dict.fromkeys(["ETHUSDT"] + A_SYMBOLS + B_SYMBOLS))   # ETH = 現行策略基準
FIRST_MONTH = "2020-11"
LAST_DAY = "2026-10-28"         # 保留期最後一天（含）

BASE = "https://data.binance.vision/data/futures/um"
KLINE_MONTHLY = BASE + "/monthly/klines/{s}/1h/{s}-1h-{m}.zip"
KLINE_DAILY = BASE + "/daily/klines/{s}/1h/{s}-1h-{d}.zip"
FUND_MONTHLY = BASE + "/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip"
FAPI_FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def months(first: str = FIRST_MONTH, last_day: str = LAST_DAY) -> list[str]:
    return [p.strftime("%Y-%m") for p in pd.period_range(first, last_day[:7], freq="M")]


def fetch_bytes(url: str) -> bytes | None:
    """404 → None（檔案未發布）；其他錯誤重試 6 次後拋出。"""
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            print(f"  HTTP {err.code}，重試 {attempt + 1}：{url}", flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            print(f"  {err}，重試 {attempt + 1}：{url}", flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"下載連續失敗：{url}")


def read_zip_csv(blob: bytes, names: list[str] | None = None) -> pd.DataFrame:
    """data.binance.vision 舊檔無表頭、新檔有表頭，兩者都吃。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf, zf.open(zf.namelist()[0]) as fh:
        text = fh.read().decode("utf-8")
    first = text.split("\n", 1)[0].split(",")[0].strip()
    has_header = not first.lstrip("-").isdigit()
    if names is None:
        return pd.read_csv(io.StringIO(text))
    df = pd.read_csv(io.StringIO(text), header=0 if has_header else None)
    df = df.iloc[:, :len(names)]
    df.columns = names
    return df


def parse_klines(df: pd.DataFrame) -> pd.DataFrame:
    for c in ("open_time", "close_time"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
        big = df[c] > 10**14            # 部分新檔為微秒
        df.loc[big, c] = df.loc[big, c] // 1000
    for c in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open_time", "open", "high", "low", "close"])


# ---------------------------------------------------------------- 原始片段
def raw_path(symbol: str, kind: str, tag: str) -> Path:
    return RAW_DIR / symbol / f"{kind}_{tag}.csv"


def save_raw(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def fetch_kline_month(symbol: str, month: str) -> str:
    out = raw_path(symbol, "kline", month)
    if out.exists():
        return "cached"
    blob = fetch_bytes(KLINE_MONTHLY.format(s=symbol, m=month))
    if blob is not None:
        save_raw(out, parse_klines(read_zip_csv(blob, KLINE_COLS)))
        return "monthly"
    # 月檔未發布（通常是最後一個月）→ 每日檔；未完成的月份不寫完成標記，下次重跑再補
    days = pd.date_range(f"{month}-01", min(pd.Timestamp(f"{month}-01") + pd.offsets.MonthEnd(0),
                                            pd.Timestamp(LAST_DAY)), freq="D")
    parts, missing = [], []
    for d in days:
        tag = d.strftime("%Y-%m-%d")
        day_out = raw_path(symbol, "kline_day", tag)
        if not day_out.exists():
            b = fetch_bytes(KLINE_DAILY.format(s=symbol, d=tag))
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


def fetch_funding_month(symbol: str, month: str) -> str:
    out = raw_path(symbol, "funding", month)
    if out.exists():
        return "cached"
    blob = fetch_bytes(FUND_MONTHLY.format(s=symbol, m=month))
    if blob is not None:
        df = read_zip_csv(blob)
        cols = {c.lower(): c for c in df.columns}
        t = pd.to_numeric(df[cols.get("calc_time", df.columns[0])])
        rate = pd.to_numeric(df[cols.get("last_funding_rate", df.columns[-1])])
        save_raw(out, pd.DataFrame({"funding_time_ms": t.astype("int64"), "rate": rate}))
        return "monthly"
    start = int(pd.Timestamp(f"{month}-01", tz="UTC").value // 1_000_000)
    end_ts = min(pd.Timestamp(f"{month}-01", tz="UTC") + pd.offsets.MonthBegin(1),
                 pd.Timestamp(LAST_DAY, tz="UTC") + pd.Timedelta(days=1))
    end = int(end_ts.value // 1_000_000) - 1
    rows, cur = [], start
    while cur <= end:
        q = urllib.parse.urlencode({"symbol": symbol, "startTime": cur, "endTime": end, "limit": 1000})
        data = json.loads(fetch_bytes(f"{FAPI_FUNDING}?{q}") or b"[]")
        if not data:
            break
        rows += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in data]
        cur = rows[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.3)
    if not rows:
        return "無資料（稍後重跑）"
    complete = end_ts <= pd.Timestamp.now(tz="UTC")
    df = pd.DataFrame(rows, columns=["funding_time_ms", "rate"])
    if complete:
        save_raw(out, df)
        return f"fapi×{len(df)}"
    save_raw(raw_path(symbol, "funding_partial", month), df)   # 未結束月份：只存暫存，不當完成
    return f"fapi×{len(df)}（月份未結束，暫存）"


# ---------------------------------------------------------------- 合併輸出
def merge_symbol(symbol: str) -> tuple[int, int]:
    ms = months()
    kparts = [pd.read_csv(p) for m in ms if (p := raw_path(symbol, "kline", m)).exists()]
    n_k = n_f = 0
    if kparts:
        k = pd.concat(kparts, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
        last_close_ms = int((pd.Timestamp(LAST_DAY) + pd.Timedelta(days=1)).value // 1_000_000) - 1
        k = k[k["close_time"] <= last_close_ms]               # 只留已收盤、保留期之內的 K 線
        out = k[["open", "high", "low", "close", "volume", "taker_buy_volume"]].copy()
        out["datetime"] = pd.to_datetime(k["open_time"], unit="ms") + pd.Timedelta(hours=8)
        out.to_csv(DATA_DIR / f"{symbol}_1h.csv", index=False)
        n_k = len(out)
    fparts = []
    for m in ms:
        p = raw_path(symbol, "funding", m)
        if not p.exists():
            p = raw_path(symbol, "funding_partial", m)
        if p.exists():
            fparts.append(pd.read_csv(p))
    if fparts:
        f = pd.concat(fparts, ignore_index=True).sort_values("funding_time_ms")
        f["funding_time_ms"] = (f["funding_time_ms"] + 1_800_000) // 3_600_000 * 3_600_000   # calc_time 常帶毫秒／秒偏移，對齊整點
        f = f.drop_duplicates("funding_time_ms", keep="last")
        f = f[f["funding_time_ms"] < int((pd.Timestamp(LAST_DAY) + pd.Timedelta(days=1)).value // 1_000_000)]
        f.insert(1, "funding_time_utc", pd.to_datetime(f["funding_time_ms"], unit="ms").dt.strftime("%Y-%m-%d %H:%M:%S"))
        f.to_csv(DATA_DIR / f"{symbol}_funding.csv", index=False)
        n_f = len(f)
    return n_k, n_f


def cmd_fetch(args) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for s in args.symbols:
        print(f"== {s}", flush=True)
        for m in months():
            k = fetch_kline_month(s, m)
            f = fetch_funding_month(s, m)
            if k != "cached" or f != "cached":
                print(f"  {m}  K線 {k}  funding {f}", flush=True)
                time.sleep(args.pause)
        n_k, n_f = merge_symbol(s)
        print(f"  → {s}_1h.csv {n_k} 根、{s}_funding.csv {n_f} 筆", flush=True)
    print("完成；用 status 檢查缺口")


def symbol_status(symbol: str) -> dict:
    ms = months()
    got_k = [m for m in ms if raw_path(symbol, "kline", m).exists()]
    got_f = [m for m in ms if raw_path(symbol, "funding", m).exists()
             or raw_path(symbol, "funding_partial", m).exists()]
    info = {"symbol": symbol, "kline_months": f"{len(got_k)}/{len(ms)}",
            "funding_months": f"{len(got_f)}/{len(ms)}", "bars": 0, "first": "", "last": "",
            "gaps": 0, "funding": 0}
    kp = DATA_DIR / f"{symbol}_1h.csv"
    if kp.exists():
        dt = pd.to_datetime(pd.read_csv(kp, usecols=["datetime"])["datetime"])
        info.update(bars=len(dt), first=f"{dt.min():%Y-%m-%d %H}", last=f"{dt.max():%Y-%m-%d %H}",
                    gaps=int((dt.diff().dropna() != pd.Timedelta(hours=1)).sum()))
    fp = DATA_DIR / f"{symbol}_funding.csv"
    if fp.exists():
        info["funding"] = len(pd.read_csv(fp))
    return info


def cmd_status(_args) -> None:
    print(f"{'symbol':10s} {'K線月':>7s} {'fund月':>7s} {'bars':>7s} {'first(UTC+8)':>14s} {'last(UTC+8)':>14s} {'跳格':>4s} {'funding':>7s}")
    ok = True
    for s in SYMBOLS:
        i = symbol_status(s)
        print(f"{s:10s} {i['kline_months']:>7s} {i['funding_months']:>7s} {i['bars']:7d} {i['first']:>14s} "
              f"{i['last']:>14s} {i['gaps']:4d} {i['funding']:7d}")
        ok &= i["bars"] > 0 and i["funding"] > 0
    print("狀態：" + ("全部有檔（跳格>0 代表交易所停機缺 K，屬正常；月份不齊請重跑 fetch）" if ok else "有缺，請重跑 fetch"))


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
