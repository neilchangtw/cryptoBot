"""
回測資料下載器 — 在 VPS（或任何乾淨環境）上補齊 backtest/research 需要的 K 線快取。

data/ 整個目錄被 gitignore，所以 fresh clone / VPS 上沒有 ETHUSDT_1h_latest730d.csv。
本腳本用 Binance Futures 公開端點（與 data_feed.py 同源，不需 API key）分頁抓取
730 天 1h K 線，輸出成研究腳本期望的格式：

    欄位：open,high,low,close,volume,taker_buy_volume,datetime（datetime 為 UTC+8）

用法：
    .venv/bin/python fetch_backtest_data.py                 # ETH + BTC，730 天
    .venv/bin/python fetch_backtest_data.py --days 365      # 只抓 365 天
    .venv/bin/python fetch_backtest_data.py --symbols ETHUSDT  # 只抓 ETH
    .venv/bin/python fetch_backtest_data.py --interval 4h   # 改時框（輸出 *_4h_*.csv）
"""
import os
import time
import argparse
from datetime import datetime, timedelta

import requests
import pandas as pd

FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
PAGE_LIMIT = 1500  # Binance Futures 單次上限
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def fetch_history(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """分頁抓取 [now-days, now] 的 K 線，回傳與快取 CSV 同格式的 DataFrame。"""
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

    rows = []
    cursor = start_ms
    page = 0
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cursor, "limit": PAGE_LIMIT}
        for attempt in range(3):
            try:
                resp = requests.get(FUTURES_KLINES_URL, params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        if not data:
            break
        rows.extend(data)
        page += 1
        last_open = data[-1][0]
        # 下一頁從最後一根的下一毫秒開始；若沒前進就停（避免無限迴圈）
        nxt = last_open + 1
        if nxt <= cursor:
            break
        cursor = nxt
        print(f"  {symbol} {interval}: page {page}, {len(rows)} bars, "
              f"至 {datetime.utcfromtimestamp(last_open/1000) + timedelta(hours=8):%Y-%m-%d %H:%M}")
        time.sleep(0.25)  # 輕量限速，避免觸發 Binance rate limit
        if len(data) < PAGE_LIMIT:
            break  # 已抓到最新

    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)

    for c in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    out = df[["open", "high", "low", "close", "volume", "taker_buy_base", "datetime"]].copy()
    out.rename(columns={"taker_buy_base": "taker_buy_volume"}, inplace=True)
    # 丟掉尚未收盤的最後一根（與 data_feed 行為一致由策略決定，回測用收盤資料更安全）
    return out


def main():
    ap = argparse.ArgumentParser(description="下載 backtest 用的 K 線快取")
    ap.add_argument("--symbols", nargs="+", default=["ETHUSDT", "BTCUSDT"])
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--days", type=int, default=730)
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    for sym in args.symbols:
        print(f"下載 {sym} {args.interval}（{args.days} 天）…")
        df = fetch_history(sym, args.interval, args.days)
        # 命名對齊研究腳本：<SYM>_<interval>_latest730d.csv（沿用既有慣例）
        fname = f"{sym}_{args.interval}_latest730d.csv"
        path = os.path.join(DATA_DIR, fname)
        df.to_csv(path, index=False)
        span = f"{df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}" if len(df) else "空"
        print(f"  ✓ 寫入 {path}（{len(df)} 根，{span}）\n")

    print("完成。現在可以執行 backtest/research/ 內的腳本了。")


if __name__ == "__main__":
    main()
