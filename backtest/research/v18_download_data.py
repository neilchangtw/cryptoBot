"""
V18: Download ETH 15m and 30m klines from Binance Futures.
Paginated fetch (1500 bars per request), 730 days back from now.
Saves to data/ directory with same format as existing 1h CSV.
"""
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


def fetch_all_klines(symbol, interval, days=730, limit=1500):
    """Fetch klines by paginating backwards from now."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 24 * 60 * 60 * 1000

    # Interval to milliseconds
    interval_ms = {
        '5m': 5 * 60 * 1000,
        '15m': 15 * 60 * 1000,
        '30m': 30 * 60 * 1000,
        '1h': 60 * 60 * 1000,
    }[interval]

    all_data = []
    cursor = start_ms
    total_expected = days * 24 * 60 * 60 * 1000 // interval_ms
    fetched = 0

    print(f"Fetching {symbol} {interval} from {days} days ago...")
    print(f"Expected ~{total_expected} bars")

    while cursor < now_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "limit": limit,
        }

        for attempt in range(5):
            try:
                resp = requests.get(FUTURES_KLINES_URL, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    print(f"  Retry {attempt+1}...")
                else:
                    raise ConnectionError(f"Failed after 5 retries: {e}")

        if not data:
            break

        all_data.extend(data)
        fetched += len(data)

        # Move cursor past last bar
        last_open_time = data[-1][0]
        cursor = last_open_time + interval_ms

        pct = min(100, fetched / total_expected * 100)
        print(f"  Fetched {fetched} bars ({pct:.0f}%)", end='\r')

        # Rate limit: 2400 req/min = 40/sec, be conservative
        time.sleep(0.15)

    print(f"\n  Total: {len(all_data)} bars")
    return all_data


def to_dataframe(raw_data):
    """Convert raw kline data to DataFrame matching existing CSV format."""
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(raw_data, columns=cols)

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # UTC -> UTC+8
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)

    df = df[["open", "high", "low", "close", "volume", "taker_buy_base", "datetime"]].copy()
    df.rename(columns={"taker_buy_base": "taker_buy_volume"}, inplace=True)

    # Deduplicate by datetime
    df = df.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)

    return df


def main():
    for interval in ["15m", "30m"]:
        raw = fetch_all_klines("ETHUSDT", interval, days=730)
        df = to_dataframe(raw)

        out_path = DATA_DIR / f"ETHUSDT_{interval}_latest730d.csv"
        df.to_csv(out_path, index=False)
        print(f"Saved {out_path}: {len(df)} bars")
        print(f"  Range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
        print()


if __name__ == "__main__":
    main()
