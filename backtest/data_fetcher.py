"""
Binance 公開 API 抓取 BTC K線，自動快取到 data/
不需要 API Key
"""
import requests
import pandas as pd
import os
import time
from datetime import datetime, timezone

KLINES_URL = "https://api.binance.com/api/v3/klines"
# data/ 放在專案根目錄（backtest/ 的上一層）
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    start_dt: datetime = None,
    end_dt: datetime = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    從 Binance 抓歷史 K線，自動快取成 CSV。
    重複執行時直接讀快取，只有 force_refresh=True 才重新抓。

    Returns:
        DataFrame with columns: open, high, low, close, volume
        index: UTC datetime
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    if start_dt is None:
        start_dt = datetime(2025, 9, 1, tzinfo=timezone.utc)
    if end_dt is None:
        end_dt = datetime.now(timezone.utc)

    cache_file = os.path.join(
        DATA_DIR,
        f"{symbol}_{interval}_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv",
    )

    if os.path.exists(cache_file) and not force_refresh:
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True)
        print(f"[DataFetcher] 從快取載入 {len(df)} 根 K線: {os.path.basename(cache_file)}")
        return df

    print(f"[DataFetcher] 開始抓取 {symbol} {interval} {start_dt.date()} ~ {end_dt.date()} ...")
    all_rows = []
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    while start_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            break

        all_rows.extend(data)
        last_open_time = data[-1][0]
        start_ms = last_open_time + 1

        print(f"  抓到 {len(all_rows)} 根...", end="\r")

        if len(data) < 1000:
            break

        time.sleep(0.1)

    print(f"\n[DataFetcher] 共抓到 {len(all_rows)} 根 K線")

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(all_rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df = df[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated()].sort_index()

    df.to_csv(cache_file)
    print(f"[DataFetcher] 已存到 {cache_file}")
    return df


def fetch_latest_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200) -> pd.DataFrame:
    """
    抓最新 N 根 K線（給即時交易用，不快取）。
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(data, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("open_time", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    df = fetch_klines()
    print(df.tail())
    print(f"時間範圍: {df.index[0]} ~ {df.index[-1]}")
