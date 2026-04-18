"""
V20 R0: Download multi-asset 1h klines from Binance Futures
=============================================================
Downloads 730 days of 1h data for 10 candidate symbols.
"""

import requests
import pandas as pd
import time
import os
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')
BASE_URL = "https://fapi.binance.com"
DAYS_BACK = 730

SYMBOLS = [
    'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT',
    'AVAXUSDT', 'LINKUSDT', 'MATICUSDT', 'LTCUSDT', 'BCHUSDT',
]


def download_klines(symbol, days_back=DAYS_BACK):
    """Download 1h klines with pagination (1500 bars/request)."""
    url = f"{BASE_URL}/fapi/v1/klines"
    all_data = []
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
    cursor = start_ms

    while cursor < end_ms:
        params = {'symbol': symbol, 'interval': '1h', 'limit': 1500,
                  'startTime': cursor, 'endTime': end_ms}
        try:
            resp = requests.get(url, params=params, timeout=30)
        except Exception as e:
            print(f"  Request error: {e}")
            break

        if resp.status_code == 429:
            print("  Rate limited, waiting 30s...")
            time.sleep(30)
            continue
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:150]}")
            return None

        data = resp.json()
        if not data:
            break

        all_data.extend(data)
        cursor = data[-1][0] + 1
        if len(data) < 1500:
            break
        time.sleep(0.3)

    if not all_data:
        print(f"  NO DATA for {symbol}")
        return None

    df = pd.DataFrame(all_data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
        'taker_buy_quote_volume', 'ignore'])

    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms') + pd.Timedelta(hours=8)
    df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')

    for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_volume']:
        df[col] = df[col].astype(float)

    df = df[['open', 'high', 'low', 'close', 'volume', 'taker_buy_volume', 'datetime']]
    df = df.drop_duplicates(subset=['datetime']).sort_values('datetime').reset_index(drop=True)
    return df


if __name__ == '__main__':
    print(f"V20 R0: Download Multi-Asset 1h Data ({DAYS_BACK} days)")
    print(f"Output: {os.path.abspath(DATA_DIR)}\n")

    results = {}
    for symbol in SYMBOLS:
        filename = f'{symbol}_1h_latest730d.csv'
        filepath = os.path.join(DATA_DIR, filename)

        if os.path.exists(filepath):
            existing = pd.read_csv(filepath)
            print(f"{symbol}: already exists ({len(existing)} bars)")
            results[symbol] = len(existing)
            continue

        print(f"{symbol}: downloading...", end=' ', flush=True)
        df = download_klines(symbol)
        if df is not None:
            df.to_csv(filepath, index=False)
            days = len(df) / 24
            print(f"{len(df)} bars ({days:.0f} days): {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
            results[symbol] = len(df)
        else:
            # Try alternative symbol name (e.g., MATIC → POL)
            if symbol == 'MATICUSDT':
                print("  Trying POLUSDT instead...")
                df = download_klines('POLUSDT')
                if df is not None:
                    alt_path = os.path.join(DATA_DIR, 'POLUSDT_1h_latest730d.csv')
                    df.to_csv(alt_path, index=False)
                    print(f"  POLUSDT: {len(df)} bars")
                    results['POLUSDT'] = len(df)
            else:
                results[symbol] = 0

    print(f"\n{'Symbol':<12} {'Bars':>8} {'Days':>6}")
    print("-" * 30)
    for sym, bars in results.items():
        print(f"{sym:<12} {bars:>8} {bars/24:>5.0f}")

    # Also check ETH baseline
    eth_path = os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv')
    if os.path.exists(eth_path):
        eth = pd.read_csv(eth_path)
        print(f"\n{'ETHUSDT':<12} {len(eth):>8} {len(eth)/24:>5.0f}  (baseline)")
