"""
V19 R0: Download External Data for Non-OHLCV Alpha Research
============================================================
Part A: Binance Futures — OI, L/S Ratio, Taker Volume (1h)
Part B: Yahoo Finance  — SPX, DXY, VIX, US10Y (daily)
Part C: Alternative.me — Crypto Fear & Greed Index (daily)

Usage: python backtest/research/v19_r0_download_data.py
"""

import requests
import pandas as pd
import time
import os
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', '..', 'data')
BINANCE_BASE = "https://fapi.binance.com"
SYMBOL = "ETHUSDT"
DAYS_BACK = 730


# =========================================================================
# Part A: Binance Futures Market Data
# =========================================================================

def download_binance_series(endpoint, symbol, period, label, days_back=DAYS_BACK):
    """Generic paginated downloader for Binance /futures/data/* endpoints."""
    print(f"\n--- {label} ---")
    url = f"{BINANCE_BASE}{endpoint}"

    all_data = []
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
    cursor = start_ms
    retries = 0

    while cursor < end_ms:
        params = {
            'symbol': symbol, 'period': period,
            'limit': 500, 'startTime': cursor, 'endTime': end_ms,
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
        except Exception as e:
            print(f"  Request error: {e}")
            retries += 1
            if retries > 3:
                break
            time.sleep(2)
            continue

        if resp.status_code == 429:
            print("  Rate limited, waiting 60s...")
            time.sleep(60)
            continue
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:200]}")
            break

        data = resp.json()
        if not data:
            break

        all_data.extend(data)
        cursor = data[-1].get('timestamp', 0) + 1
        if len(data) < 500:
            break
        time.sleep(0.3)

    if not all_data:
        print(f"  NO DATA returned")
        return None

    df = pd.DataFrame(all_data)
    if 'timestamp' in df.columns:
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df['datetime'] = df['datetime'].dt.tz_convert('Asia/Taipei').dt.strftime('%Y-%m-%d %H:%M:%S')
        df = df.sort_values('timestamp').drop_duplicates(subset=['timestamp'])

    print(f"  {len(df)} rows: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df


def download_binance_data():
    print("=" * 60)
    print("Part A: Binance Futures Market Data")
    print("=" * 60)
    results = {}

    configs = [
        ('/futures/data/openInterestHist', 'Open Interest (1h)',
         f'{SYMBOL}_OI_1h.csv',
         ['sumOpenInterest', 'sumOpenInterestValue']),
        ('/futures/data/globalLongShortAccountRatio', 'Global L/S Ratio (1h)',
         f'{SYMBOL}_LSR_Global_1h.csv',
         ['longShortRatio', 'longAccount', 'shortAccount']),
        ('/futures/data/topLongShortPositionRatio', 'Top Trader L/S Position (1h)',
         f'{SYMBOL}_LSR_TopPosition_1h.csv',
         ['longShortRatio', 'longAccount', 'shortAccount']),
        ('/futures/data/topLongShortAccountRatio', 'Top Trader L/S Account (1h)',
         f'{SYMBOL}_LSR_TopAccount_1h.csv',
         ['longShortRatio', 'longAccount', 'shortAccount']),
        ('/futures/data/takerlongshortRatio', 'Taker Buy/Sell Volume (1h)',
         f'{SYMBOL}_TakerVol_1h.csv',
         ['buySellRatio', 'buyVol', 'sellVol']),
    ]

    for endpoint, label, filename, float_cols in configs:
        df = download_binance_series(endpoint, SYMBOL, '1h', label)
        if df is not None:
            for col in float_cols:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            path = os.path.join(DATA_DIR, filename)
            df.to_csv(path, index=False)
            print(f"  Saved: {filename}")
            results[label.split('(')[0].strip()] = len(df)
    return results


# =========================================================================
# Part B: Macro Data (Yahoo Finance)
# =========================================================================

def download_macro_data():
    print("\n" + "=" * 60)
    print("Part B: Macro Data (Yahoo Finance)")
    print("=" * 60)
    results = {}

    try:
        import yfinance as yf
    except ImportError:
        print("  yfinance not installed. Skipping.")
        return results

    tickers = {
        'SPX': '^GSPC', 'DXY': 'DX-Y.NYB', 'VIX': '^VIX', 'US10Y': '^TNX',
    }
    end_date = datetime.now()
    start_date = end_date - timedelta(days=DAYS_BACK)

    for name, ticker in tickers.items():
        print(f"\n--- {name} ({ticker}) ---")
        try:
            df = yf.download(ticker, start=start_date.strftime('%Y-%m-%d'),
                             end=end_date.strftime('%Y-%m-%d'),
                             interval='1d', progress=False)
            if df.empty:
                print(f"  No data")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            if 'Date' in df.columns:
                df = df.rename(columns={'Date': 'date'})
            path = os.path.join(DATA_DIR, f'{name}_daily.csv')
            df.to_csv(path, index=False)
            print(f"  {len(df)} rows: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
            print(f"  Saved: {name}_daily.csv")
            results[name] = len(df)
        except Exception as e:
            print(f"  Error: {e}")
    return results


# =========================================================================
# Part C: Fear & Greed Index
# =========================================================================

def download_fgi():
    print("\n" + "=" * 60)
    print("Part C: Crypto Fear & Greed Index")
    print("=" * 60)
    results = {}

    print("\n--- Fear & Greed Index ---")
    try:
        resp = requests.get("https://api.alternative.me/fng/",
                            params={'limit': 0, 'format': 'json'}, timeout=30)
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}")
            return results

        data = resp.json()
        if 'data' not in data:
            print(f"  Unexpected format: {list(data.keys())}")
            return results

        df = pd.DataFrame(data['data'])
        df['timestamp'] = df['timestamp'].astype(int)
        df['date'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d')
        df['value'] = df['value'].astype(int)
        df = df.sort_values('date').reset_index(drop=True)

        cutoff = (datetime.now() - timedelta(days=DAYS_BACK)).strftime('%Y-%m-%d')
        df = df[df['date'] >= cutoff].copy()

        path = os.path.join(DATA_DIR, 'FearGreed_daily.csv')
        df.to_csv(path, index=False)
        print(f"  {len(df)} rows: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
        print(f"  Saved: FearGreed_daily.csv")
        results['FGI'] = len(df)
    except Exception as e:
        print(f"  Error: {e}")
    return results


# =========================================================================
# Main
# =========================================================================

if __name__ == '__main__':
    print(f"V19 R0: Download External Data ({DAYS_BACK} days)")
    print(f"Output: {os.path.abspath(DATA_DIR)}\n")

    r_a = download_binance_data()
    r_b = download_macro_data()
    r_c = download_fgi()

    # --- Summary ---
    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)

    eth_path = os.path.join(DATA_DIR, 'ETHUSDT_1h_latest730d.csv')
    if os.path.exists(eth_path):
        eth = pd.read_csv(eth_path)
        print(f"\nETH 1h baseline: {len(eth)} bars")
        print(f"  {eth['datetime'].iloc[0]} ~ {eth['datetime'].iloc[-1]}")

    all_r = {**r_a, **r_b, **r_c}
    print(f"\n{'Source':<30} {'Rows':>8}  {'Days':>6}")
    print("-" * 50)
    for k, v in all_r.items():
        days = v / 24 if v > 1000 else v
        print(f"{k:<30} {v:>8}  {days:>6.0f}")

    # Sufficiency
    print(f"\n--- Sufficiency (need 365+ days for IS/OOS) ---")
    for k, v in all_r.items():
        days = v / 24 if v > 1000 else v
        ok = "OK" if days >= 365 else "SHORT"
        print(f"  {k}: {days:.0f} days -- {ok}")

    print("\nDone.")
