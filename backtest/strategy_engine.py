"""
BTC 超買超賣 + 結構止損策略（依 btc_strategy_prompt.md 的 14 項回測結論）

做多：RSI(14) < 30  →  止損: Swing Low - 0.3×ATR  →  TP1 10% + ATR Trail
做空：收盤 > BB上軌 →  止損: Swing High + 0.3×ATR →  TP1 10% + EMA9 Trail
同方向最多 3 單
"""
import pandas as pd
import numpy as np


SWING_N = 5       # Swing High/Low 左右各幾根
BB_PERIOD = 20    # 布林通道週期
BB_STD = 2        # 布林通道標準差倍數
RSI_PERIOD = 14
ATR_PERIOD = 14
EMA_PERIOD = 9


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    在 DataFrame 上計算所有技術指標。
    輸入: open, high, low, close, volume
    輸出: 新增 rsi, bb_upper, bb_lower, bb_mid, atr, ema9, swing_high, swing_low
    """
    df = df.copy()
    n = len(df)

    # RSI(14) — 使用 EWM（alpha=1/14）
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # 布林通道 (20, 2)
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std(ddof=1)
    df["bb_upper"] = df["bb_mid"] + BB_STD * bb_std
    df["bb_lower"] = df["bb_mid"] - BB_STD * bb_std

    # ATR(14)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # EMA9
    df["ema9"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    # Swing High/Low（左右各 SWING_N 根，shift SWING_N 避免 look-ahead）
    raw_sh = pd.Series(np.nan, index=df.index)
    raw_sl = pd.Series(np.nan, index=df.index)

    highs = df["high"].values
    lows = df["low"].values

    for i in range(SWING_N, n - SWING_N):
        window_h = highs[i - SWING_N : i + SWING_N + 1]
        if highs[i] == window_h.max():
            raw_sh.iloc[i] = highs[i]
        window_l = lows[i - SWING_N : i + SWING_N + 1]
        if lows[i] == window_l.min():
            raw_sl.iloc[i] = lows[i]

    # shift(SWING_N)：第 i 根 K 線才確認前 SWING_N 根的 Swing 狀態
    df["swing_high"] = raw_sh.shift(SWING_N).ffill()
    df["swing_low"] = raw_sl.shift(SWING_N).ffill()

    return df


def compute_indicators_v2(df: pd.DataFrame) -> pd.DataFrame:
    """
    擴充指標集：在 v1 基礎上新增 EMA21, Stochastic, MACD, Donchian,
    Volume SMA, Supertrend, BB Width。
    """
    df = compute_indicators(df)

    # ── EMA21 ─────────────────────────────────────────────────
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    # ── Stochastic(14, 3) ────────────────────────────────────
    low14 = df["low"].rolling(14).min()
    high14 = df["high"].rolling(14).max()
    df["stoch_k"] = (df["close"] - low14) / (high14 - low14 + 1e-10) * 100
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ── MACD(12, 26, 9) ──────────────────────────────────────
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_line"] = ema12 - ema26
    df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()

    # ── Donchian(20) — shift(1) 避免 look-ahead ─────────────
    df["donchian_high"] = df["high"].rolling(20).max().shift(1)
    df["donchian_low"] = df["low"].rolling(20).min().shift(1)

    # ── Volume SMA(20) ───────────────────────────────────────
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # ── BB Width ─────────────────────────────────────────────
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_mid"] + 1e-10)
    df["bb_width_min20"] = df["bb_width"].rolling(20).min()

    # ── Supertrend(10, 3) ────────────────────────────────────
    atr10_tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr10 = atr10_tr.rolling(10).mean()

    hl2 = (df["high"] + df["low"]) / 2
    upper_band = hl2 + 3 * atr10
    lower_band = hl2 - 3 * atr10

    st_dir = np.zeros(len(df))      # 1=bullish, -1=bearish
    st_val = np.zeros(len(df))
    final_upper = upper_band.values.copy()
    final_lower = lower_band.values.copy()
    closes = df["close"].values

    # 初始化第一根有效 bar
    for j in range(len(df)):
        if not np.isnan(final_upper[j]):
            st_dir[j] = 1 if closes[j] > final_upper[j] else -1
            st_val[j] = final_lower[j] if st_dir[j] == 1 else final_upper[j]
            break

    for i in range(1, len(df)):
        # 帶狀收斂
        if final_upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
            pass  # keep current
        else:
            final_upper[i] = final_upper[i - 1]

        if final_lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
            pass
        else:
            final_lower[i] = final_lower[i - 1]

        # 方向判斷
        if st_dir[i - 1] == 1:
            st_dir[i] = -1 if closes[i] < final_lower[i] else 1
        elif st_dir[i - 1] == -1:
            st_dir[i] = 1 if closes[i] > final_upper[i] else -1
        else:
            st_dir[i] = 1 if closes[i] > final_upper[i] else -1

        st_val[i] = final_lower[i] if st_dir[i] == 1 else final_upper[i]

    df["supertrend"] = st_val
    df["supertrend_dir"] = st_dir

    return df


def get_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    在含有指標的 DataFrame 上，對每根 K 線標記原始信號。
    只標記信號條件，不考慮同方向限制（由回測引擎處理）。

    新增欄位:
        long_signal:  True 表示 RSI < 30
        short_signal: True 表示 close > bb_upper
    """
    df = df.copy()
    df["long_signal"] = df["rsi"] < 30
    df["short_signal"] = df["close"] > df["bb_upper"]
    return df
