"""
Binance K線資料擷取模組

從 Binance Futures 公開 API 抓取 ETHUSDT / BTCUSDT 1h K線。
- 使用正式端點（非 testnet，testnet K 線資料不完整）
- 3 次重試 + 指數退避
- 55 秒記憶體快取（避免同 cycle 重複呼叫）
- datetime 轉 UTC+8（匹配回測 CSV 格式）
"""
import os
import time
import logging
import requests
import pandas as pd
from datetime import timedelta

import paths  # 多實例：共用 K 線快取放程式目錄（所有實例共用）

logger = logging.getLogger("data_feed")

FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

_cache = {}
_CACHE_TTL = 55  # 進程內快取：略短於 60s，確保每小時 cycle 拿到新資料

# ── 跨實例共用 K 線快取 ──
# 多實例（有設 INSTANCE_DIR）時，同一份 ETH/BTC 1h K 線對所有人相同 → 只讓一個實例去抓、
# 其他實例讀共用檔，避免 N 個實例每小時各打一次 Binance。用 flock 去重；任何問題一律
# 退回「各自直接抓」（fail-open，絕不擋交易）。單人（未設 INSTANCE_DIR）維持原本行為。
_SHARED = paths.INSTANCE_DIR != paths.CODE_DIR      # 是否多實例
_SHARED_DIR = os.path.join(paths.CODE_DIR, "cache")  # 程式目錄下，所有實例共用
_SHARED_TTL = 55                                     # 秒；同一整點窗口內共用，下個整點自然過期


def _shared_files(symbol, interval):
    base = os.path.join(_SHARED_DIR, f"{symbol}_{interval}")
    return base + ".csv", base + ".lock"


def _read_shared(csv_path, min_rows):
    """共用檔存在且夠新（且列數足夠）才回傳 DataFrame，否則 None。"""
    try:
        if not os.path.exists(csv_path):
            return None
        if time.time() - os.path.getmtime(csv_path) >= _SHARED_TTL:
            return None
        df = pd.read_csv(csv_path, parse_dates=["datetime"])
        if len(df) < min_rows:
            return None
        return df
    except Exception:
        return None


def _write_shared(df, csv_path):
    """原子寫入共用檔（先寫 tmp 再 rename，避免其他實例讀到半個檔）。"""
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        tmp = f"{csv_path}.tmp.{os.getpid()}"
        df.to_csv(tmp, index=False)
        os.replace(tmp, csv_path)
    except Exception as e:
        logger.debug(f"write shared cache failed: {e}")


def _fetch_from_binance(symbol, interval, limit, max_retries):
    """實際打 Binance 公開端點抓 K 線並整理成標準 DataFrame。"""
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
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # open_time (ms) → UTC datetime → +8h → 匹配回測 CSV 格式
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    df = df[["open", "high", "low", "close", "volume", "taker_buy_base", "datetime"]].copy()
    df.rename(columns={"taker_buy_base": "taker_buy_volume"}, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _shared_fetch(symbol, interval, limit, max_retries):
    """多實例共用抓取：先讀共用檔；沒有就搶 flock 去抓，其他實例讀檔。
    任何共用快取異常都退回直接抓（fail-open）。"""
    csv_path, lock_path = _shared_files(symbol, interval)

    df = _read_shared(csv_path, limit)
    if df is not None:
        return df

    try:
        import fcntl  # Linux（VPS）；本機 Windows 無 → 退回直接抓
        os.makedirs(_SHARED_DIR, exist_ok=True)
        with open(lock_path, "w") as lf:
            got = False
            deadline = time.time() + 8  # 最多等 8s，避免持鎖者卡住拖垮全部
            while time.time() < deadline:
                try:
                    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    got = True
                    break
                except OSError:
                    # 別人正在抓 → 先看看檔好了沒
                    df = _read_shared(csv_path, limit)
                    if df is not None:
                        return df
                    time.sleep(0.3)
            if got:
                try:
                    df = _read_shared(csv_path, limit)  # 拿到鎖再確認一次
                    if df is None:
                        df = _fetch_from_binance(symbol, interval, limit, max_retries)
                        _write_shared(df, csv_path)
                    return df
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"shared fetch fallback: {e}")

    # 拿不到鎖且檔還沒好，或非 Linux → 自己抓（保底）
    df = _fetch_from_binance(symbol, interval, limit, max_retries)
    if _SHARED:
        _write_shared(df, csv_path)
    return df


def fetch_klines(symbol: str = "ETHUSDT", interval: str = "1h",
                 limit: int = 150, max_retries: int = 3) -> pd.DataFrame:
    """
    從 Binance Futures 抓最新 K 線（公開端點，不需 API key）。

    多實例（INSTANCE_DIR 有設）時走跨實例共用檔快取，每小時只有一個實例真的打 Binance；
    單人維持原本每實例各自抓。

    Returns:
        DataFrame with columns: [open, high, low, close, volume, datetime, taker_buy_volume]
        datetime 為 UTC+8（匹配回測 CSV），index 為 0-based integer
    """
    cache_key = (symbol, interval)
    now = time.time()
    if cache_key in _cache and now - _cache[cache_key]["time"] < _CACHE_TTL:
        return _cache[cache_key]["df"].copy()

    if _SHARED:
        df = _shared_fetch(symbol, interval, limit, max_retries)
    else:
        df = _fetch_from_binance(symbol, interval, limit, max_retries)

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
