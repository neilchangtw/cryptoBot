"""
下載 ETHUSDT 15m 歷史 K 線資料（730 天）
Binance Futures 公開 API，每次最多 1500 根，需分批抓取
"""
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

def fetch_klines_batch(symbol, interval, start_ms, end_ms, limit=1500):
    """Fetch one batch of klines."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    for attempt in range(3):
        try:
            resp = requests.get(FUTURES_KLINES_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise e


def download_full_history(symbol, interval, days=730):
    """Download full history in batches."""
    # End time: now
    end_dt = datetime.utcnow()
    # Start time: days ago
    start_dt = end_dt - timedelta(days=days)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    # Interval in ms
    interval_map = {"15m": 15*60*1000, "5m": 5*60*1000, "1h": 3600*1000}
    interval_ms = interval_map[interval]

    all_data = []
    current_start = start_ms
    batch_count = 0

    while current_start < end_ms:
        data = fetch_klines_batch(symbol, interval, current_start, end_ms, limit=1500)
        if not data:
            break

        all_data.extend(data)
        batch_count += 1

        # Next batch starts after last candle
        last_open_time = data[-1][0]
        current_start = last_open_time + interval_ms

        if batch_count % 10 == 0:
            print(f"  {symbol} {interval}: {batch_count} batches, {len(all_data)} bars so far...")

        # Rate limit: max 1200 requests/min, be conservative
        time.sleep(0.2)

    print(f"  {symbol} {interval}: DONE — {batch_count} batches, {len(all_data)} bars total")

    # Convert to DataFrame
    cols = ["ot", "open", "high", "low", "close", "volume",
            "ct", "qv", "trades", "tbv", "tbqv", "ig"]
    df = pd.DataFrame(all_data, columns=cols)

    # Convert types
    for col in ["open", "high", "low", "close", "volume", "qv", "tbv", "tbqv"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype(int)
    df["ot"] = pd.to_numeric(df["ot"])
    df["ct"] = pd.to_numeric(df["ct"])
    df["ig"] = 0

    # Add datetime (UTC+8)
    df["datetime"] = pd.to_datetime(df["ot"], unit="ms") + timedelta(hours=8)
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # Remove duplicates
    df = df.drop_duplicates(subset=["ot"]).sort_values("ot").reset_index(drop=True)

    return df


if __name__ == "__main__":
    print("=== Downloading ETHUSDT 15m (730 days) ===")
    eth_15m = download_full_history("ETHUSDT", "15m", days=730)
    out_path = "data/ETHUSDT_15m_latest730d.csv"
    eth_15m.to_csv(out_path, index=False)
    print(f"Saved: {out_path} ({len(eth_15m)} bars)")
    print(f"Date range: {eth_15m['datetime'].iloc[0]} ~ {eth_15m['datetime'].iloc[-1]}")
