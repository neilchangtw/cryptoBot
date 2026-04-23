"""
Binance K線資料擷取模組

從 Binance Futures 公開 API 抓取 ETHUSDT / BTCUSDT 1h K線。
- 使用正式端點（非 testnet，testnet K 線資料不完整）
- 3 次重試 + 指數退避
- 55 秒記憶體快取（避免同 cycle 重複呼叫）
- datetime 轉 UTC+8（匹配回測 CSV 格式）
"""
import time
import requests
import pandas as pd
from datetime import timedelta

FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

_cache = {}
_CACHE_TTL = 55  # 略短於 60s，確保每小時 cycle 拿到新資料


def fetch_klines(symbol: str = "ETHUSDT", interval: str = "1h",
                 limit: int = 150, max_retries: int = 3) -> pd.DataFrame:
    """
    從 Binance Futures 抓最新 K 線（公開端點，不需 API key）。

    Returns:
        DataFrame with columns: [open, high, low, close, volume, datetime, taker_buy_volume]
        datetime 為 UTC+8（匹配回測 CSV）
        index 為 0-based integer
    """
    cache_key = (symbol, interval)
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["time"] < _CACHE_TTL:
        return _cache[cache_key]["df"].copy()

    params = {"symbol": symbol, "interval": interval, "limit": limit}
    last_err = None

    for attempt in range(max_retries):
        try:
            resp = requests.get(FUTURES_KLINES_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    else:
        raise ConnectionError(f"Failed to fetch {symbol} {interval} after {max_retries} retries: {last_err}")

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(data, columns=cols)

    # 轉數值
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # open_time (ms) → UTC datetime → +8h → 匹配回測 CSV 格式
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)

    # 只保留需要的欄位
    df = df[["open", "high", "low", "close", "volume", "taker_buy_base", "datetime"]].copy()
    df.rename(columns={"taker_buy_base": "taker_buy_volume"}, inplace=True)
    df.reset_index(drop=True, inplace=True)

    _cache[cache_key] = {"time": now, "df": df}
    return df.copy()


def fetch_eth_and_btc(eth_limit: int = 500) -> tuple:
    """
    同時抓 ETHUSDT + BTCUSDT 1h K 線。
    BTC 資料僅用於市場背景記錄（進場時 BTC 價格、ETH/BTC ratio）。

    eth_limit 需 >= strategy.WARMUP_BARS (310) 才能算出 V14+R sma_slope；
    預設 500 留安全 buffer。

    Returns:
        (eth_df, btc_df) — 兩個 DataFrame，格式同 fetch_klines()
    """
    eth_df = fetch_klines("ETHUSDT", "1h", eth_limit)
    btc_df = fetch_klines("BTCUSDT", "1h", 30)  # BTC 只需最近 30 根（用於 context）
    return eth_df, btc_df


def get_btc_context(btc_df: pd.DataFrame) -> dict:
    """
    從 BTC DataFrame 提取市場背景資訊。

    Returns:
        {"btc_close": float, "eth_btc_ratio": None (需要 eth close 才能算)}
    """
    if btc_df is None or len(btc_df) < 2:
        return {"btc_close": None}
    last_closed = btc_df.iloc[-2]  # 最新已收盤 bar
    return {"btc_close": float(last_closed["close"])}
