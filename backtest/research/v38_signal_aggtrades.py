"""V38 訊號小時逐筆成交（aggTrades）特徵：只抓回測進場那一小時，原始資料不落地。

預登記：doc/v38_signal_aggtrades_plan.md（看資料前已鎖定特徵與判定門檻）。
只在 VPS（或個人環境）執行；不得在公司網路／公司設備下載行情。不 import 進實盤程式。

用法（VPS，cryptobot 使用者，~/cryptoBot）：
    .venv/bin/python run_backtest.py --refresh            # 更新 1h K 線
    .venv/bin/python backtest/research/v38_signal_aggtrades.py fetch      # 每日檔下載→取訊號小時→刪檔；可中斷續傳
    .venv/bin/python backtest/research/v38_signal_aggtrades.py status
分析（任何環境，只需 features.csv）：
    python backtest/research/v38_signal_aggtrades.py analyze
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "doc" / "research_results" / "v38_signal_aggtrades"
FEATURES = OUT_DIR / "features.csv"
TRADES = OUT_DIR / "trades.csv"

HISTORY_START = "2024-11-01"
SPLIT = "2026-01-01"           # 開發期 < SPLIT ≤ 保留期
EXTRA_COST = 5.0
LARGE_NOTIONAL = 100_000.0     # 單筆 aggTrade 名目 ≥ $100k 視為大單
FEATURE_COLS = [
    "taker_last15", "delta_last15", "intensity_last15", "large_share", "large_imbalance",
    "accept_share", "cross_minute", "post_cross_taker", "vwap_gap_pct", "post_cross_adverse_pct",
]


# ---------------------------------------------------------------- 交易清單
def baseline_trades() -> pd.DataFrame:
    candles = pd.read_csv(base.DATA_PATH)
    dts = pd.to_datetime(candles["datetime"]).to_numpy()
    engine = base.load_engine()
    ind = engine.compute_indicators(candles)
    start_bar = int(np.searchsorted(dts, np.datetime64(pd.Timestamp(HISTORY_START))))
    frame = pd.DataFrame(engine.simulate_v14_detailed(
        ind, dts, start_bar=start_bar, realistic=True, extra_cost=EXTRA_COST))
    close = candles["close"].astype(float)
    frame["breakout_level"] = np.where(
        frame["side"] == "L",
        close.shift(1).rolling(15).max().to_numpy()[frame["entry_bar"]],
        close.shift(1).rolling(15).min().to_numpy()[frame["entry_bar"]],
    )
    frame["signal_close"] = close.to_numpy()[frame["entry_bar"]]
    # datetime = K 棒開盤時間（UTC+8）；訊號棒 = [open, open+1h)，收盤即進場
    frame["open_utc_ms"] = ((pd.to_datetime(frame["entry_dt"]) - pd.Timedelta(hours=8)
                             - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)).astype("int64")
    frame["key"] = frame["side"] + "_" + pd.to_datetime(frame["entry_dt"]).dt.strftime("%Y%m%d%H")
    return frame


# ---------------------------------------------------------------- 下載（每日檔，用完即刪）
# 幣安 REST aggTrades 只能查最近 2 天（code -4166），改用 data.binance.vision 每日檔。
DAILY_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades/ETHUSDT/ETHUSDT-aggTrades-{day}.zip"
AGG_COLS = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id",
            "transact_time", "is_buyer_maker"]


def download(url: str, dest: Path) -> bool:
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, dest.open("wb") as fh:
                while chunk := resp.read(1 << 20):
                    fh.write(chunk)
            return True
        except urllib.error.HTTPError as err:
            if err.code == 404:                   # 當天檔案尚未發布
                return False
            print(f"  下載失敗 HTTP {err.code}，重試 {attempt + 1}", flush=True)
            time.sleep(10 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            print(f"  下載失敗 {err}，重試 {attempt + 1}", flush=True)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"下載連續失敗：{url}")


def day_hours(zip_path: Path, hour_starts: list[int]) -> dict[int, pd.DataFrame]:
    """讀一天的 aggTrades，只留指定小時；回傳 {小時起點 ms: DataFrame[p,q,T,m]}。"""
    with zipfile.ZipFile(zip_path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            has_header = not fh.readline().decode().strip()[:1].isdigit()
        keep = {h: [] for h in hour_starts}
        with zf.open(name) as fh:
            reader = pd.read_csv(fh, header=0 if has_header else None, names=AGG_COLS,
                                 usecols=["price", "quantity", "transact_time", "is_buyer_maker"],
                                 chunksize=1_000_000)
            for chunk in reader:
                t = chunk["transact_time"].to_numpy()
                for h in hour_starts:
                    mask = (t >= h) & (t < h + 3_600_000)
                    if mask.any():
                        keep[h].append(chunk[mask])
    out = {}
    for h, parts in keep.items():
        if not parts:
            out[h] = pd.DataFrame(columns=["p", "q", "T", "m"])
            continue
        df = pd.concat(parts)
        m = df["is_buyer_maker"]
        if m.dtype == object:
            m = m.astype(str).str.lower().eq("true")
        out[h] = pd.DataFrame({"p": df["price"].astype(float), "q": df["quantity"].astype(float),
                               "T": df["transact_time"].astype("int64"), "m": m.astype(bool)})
    return out


# ---------------------------------------------------------------- 特徵（皆以交易方向對齊）
def features(trades: pd.DataFrame, row: pd.Series) -> dict:
    sgn = 1 if row["side"] == "L" else -1
    t0 = int(row["open_utc_ms"])
    p, q = trades["p"].to_numpy(), trades["q"].to_numpy()
    minute = (trades["T"].to_numpy() - t0) / 60_000.0
    buyer_taker = ~trades["m"].to_numpy().astype(bool)          # m=True：買方是 maker → 賣方主動
    aligned = buyer_taker if sgn > 0 else ~buyer_taker
    total = q.sum()
    last = minute >= 45
    first = minute < 45
    notional = p * q
    large = notional >= LARGE_NOTIONAL
    level = float(row["breakout_level"])
    beyond = (p > level) if sgn > 0 else (p < level)
    cross_idx = np.flatnonzero(beyond)
    out = {"key": row["key"], "n_agg": int(len(trades)), "volume": float(total)}
    if total <= 0:
        return out
    out["taker_last15"] = float(q[last & aligned].sum() / q[last].sum()) if q[last].sum() > 0 else math.nan
    out["delta_last15"] = float((q[last & aligned].sum() - q[last & ~aligned].sum()) / total)
    out["intensity_last15"] = float(last.sum() / (first.sum() / 3)) if first.sum() > 0 else math.nan
    out["large_share"] = float(q[large].sum() / total)
    lt = q[large].sum()
    out["large_imbalance"] = float((q[large & aligned].sum() - q[large & ~aligned].sum()) / lt) if lt > 0 else 0.0
    out["accept_share"] = float(q[beyond].sum() / total)
    vwap = float(notional.sum() / total)
    out["vwap_gap_pct"] = float(sgn * (row["signal_close"] / vwap - 1) * 100)
    if len(cross_idx):
        k = cross_idx[0]
        out["cross_minute"] = float(minute[k])
        post_q = q[k:]
        out["post_cross_taker"] = float(post_q[aligned[k:]].sum() / post_q.sum())
        worst = p[k:].min() if sgn > 0 else p[k:].max()
        out["post_cross_adverse_pct"] = float(sgn * (p[k] - worst) / p[k] * 100)
    else:
        out["cross_minute"] = math.nan
        out["post_cross_taker"] = math.nan
        out["post_cross_adverse_pct"] = math.nan
    return out


def cmd_fetch(args) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trades = baseline_trades()
    trades.to_csv(TRADES, index=False)
    done = set(pd.read_csv(FEATURES)["key"]) if FEATURES.exists() else set()
    todo = trades[~trades["key"].isin(done)].copy()
    todo["utc_day"] = pd.to_datetime(todo["open_utc_ms"], unit="ms").dt.strftime("%Y-%m-%d")
    days = sorted(todo["utc_day"].unique())
    print(f"交易 {len(trades)} 筆，已完成 {len(done)}，待抓 {len(todo)} 筆／{len(days)} 天", flush=True)
    header = ["key", "n_agg", "volume"] + FEATURE_COLS
    new_file = not FEATURES.exists()
    tmp = Path(args.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    with FEATURES.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        if new_file:
            writer.writeheader()
        for n, day in enumerate(days, 1):
            started = time.time()
            rows = todo[todo["utc_day"] == day]
            zip_path = tmp / f"ETHUSDT-aggTrades-{day}.zip"
            if not download(DAILY_URL.format(day=day), zip_path):
                print(f"[{n}/{len(days)}] {day} 尚無每日檔，略過（下次重跑再補）", flush=True)
                continue
            size_mb = zip_path.stat().st_size / 1e6
            try:
                hours = day_hours(zip_path, [int(x) for x in rows["open_utc_ms"]])
            finally:
                zip_path.unlink(missing_ok=True)       # 原始逐筆不保留
            for _, row in rows.iterrows():
                writer.writerow({k: features(hours[int(row["open_utc_ms"])], row).get(k, "")
                                 for k in header})
            fh.flush()
            print(f"[{n}/{len(days)}] {day} {size_mb:.0f}MB，{len(rows)} 筆訊號，"
                  f"{time.time() - started:.0f}s", flush=True)
            time.sleep(args.pause)


def cmd_status(_args) -> None:
    n_trades = len(pd.read_csv(TRADES)) if TRADES.exists() else 0
    n_done = len(pd.read_csv(FEATURES)) if FEATURES.exists() else 0
    print(f"已完成 {n_done}/{n_trades}")


# ---------------------------------------------------------------- 分析（依預登記門檻）
def rank_auc(x: np.ndarray, y: np.ndarray) -> float:
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    pos, neg = y.sum(), (~y).sum()
    if pos == 0 or neg == 0:
        return math.nan
    ranks = pd.Series(x).rank().to_numpy()
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg))


def cmd_analyze(args) -> None:
    trades = pd.read_csv(TRADES)
    feats = pd.read_csv(FEATURES)
    df = trades.merge(feats, on="key", how="inner")
    df["win"] = df["pnl_usd"] > 0
    df["entry_dt"] = pd.to_datetime(df["entry_dt"])
    dev, hold = df[df.entry_dt < SPLIT], df[df.entry_dt >= SPLIT]
    print(f"有特徵交易 {len(df)}/{len(trades)}：開發 {len(dev)}、保留 {len(hold)}")
    rng = np.random.default_rng(38)
    alpha = 0.05 / (len(FEATURE_COLS) * 2)
    rows = []
    for side in ("L", "S"):
        d, h = dev[dev.side == side], hold[hold.side == side]
        for col in FEATURE_COLS:
            xd, yd = d[col].to_numpy(float), d["win"].to_numpy(bool)
            auc_d = rank_auc(xd, yd)
            null = np.array([abs(rank_auc(xd, rng.permutation(yd)) - 0.5) for _ in range(args.perm)])
            p = float((1 + (null >= abs(auc_d - 0.5)).sum()) / (1 + args.perm))
            auc_h = rank_auc(h[col].to_numpy(float), h["win"].to_numpy(bool))
            same = (auc_d - 0.5) * (auc_h - 0.5) > 0 and abs(auc_h - 0.5) >= 0.05
            rows.append({"side": side, "feature": col, "dev_auc": round(auc_d, 3), "dev_p": round(p, 4),
                         "hold_auc": round(auc_h, 3), "pass": bool(p < alpha and same)})
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    print(f"\n第一階段門檻：開發期 permutation p < {alpha:.4f}（Bonferroni）且保留期 AUC 同方向、|AUC-0.5| ≥ 0.05")
    passed = table[table["pass"]]
    print("通過：" + (", ".join(passed.side + ":" + passed.feature) if len(passed) else "無 → REJECTED"))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--tmp-dir", default="/tmp/v38_aggtrades", help="單日壓縮檔暫存處，讀完即刪")
    f.add_argument("--pause", type=float, default=2.0, help="每天之間暫停秒數")
    sub.add_parser("status")
    a = sub.add_parser("analyze")
    a.add_argument("--perm", type=int, default=2000)
    args = ap.parse_args()
    {"fetch": cmd_fetch, "status": cmd_status, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
