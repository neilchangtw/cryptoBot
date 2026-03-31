"""
v5 策略大規模研究 — 28 指標策略全面對比

5 大類 × 28 種策略，統一回測框架（實戰角度）：
  進場: 信號 bar 決策，下根 bar open + 0.01% 滑價
  止損: 2.0 × ATR（方向驗證）
  TP1:  1.5 × ATR → 平 20% + 保本
  移動止損: 自適應 ATR + RSI Trail
  同方向限制: 最多 3 單

分類:
  T## : 趨勢跟蹤 (Trend Following) — 9 種
  M## : 動量 (Momentum)             — 5 種
  B## : 突破 (Breakout)             — 3 種
  R## : 均值回歸改良 (Mean Reversion) — 3 種
  X## : 多時間框架 (Multi-Timeframe)  — 5 種
  C## : 多指標組合 (Combo)           — 3 種

執行: python backtest/research_v5.py
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ══════════════════════════════════════════════════════════════
#  常數
# ══════════════════════════════════════════════════════════════
MARGIN = 100
LEVERAGE = 20
FEE = 0.0004        # 0.04%
SLIP_PCT = 0.01     # 0.01% slippage
MAX_SAME = 3
SL_ATR = 2.0        # 統一 SL 倍數
TP1_ATR = 1.5       # 統一 TP1 倍數
TP1_PCT = 0.20      # TP1 平 20%
SWING_W = 5


# ══════════════════════════════════════════════════════════════
#  Supertrend 計算
# ══════════════════════════════════════════════════════════════

def _supertrend(high, low, close, atr, mult):
    """回傳 Supertrend 方向: 1=多, -1=空"""
    hl2 = ((high + low) / 2).values
    up = (hl2 + mult * atr.values).copy()
    dn = (hl2 - mult * atr.values).copy()
    c = close.values
    n = len(c)
    d = np.ones(n)

    for i in range(1, n):
        if not np.isnan(up[i - 1]):
            if up[i] > up[i - 1] and c[i - 1] <= up[i - 1]:
                up[i] = up[i - 1]
        if not np.isnan(dn[i - 1]):
            if dn[i] < dn[i - 1] and c[i - 1] >= dn[i - 1]:
                dn[i] = dn[i - 1]
        if d[i - 1] == 1:
            d[i] = -1 if c[i] < dn[i] else 1
        else:
            d[i] = 1 if c[i] > up[i] else -1

    return pd.Series(d, index=close.index)


# ══════════════════════════════════════════════════════════════
#  指標計算（全部一次算好）
# ══════════════════════════════════════════════════════════════

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # ── ATR(14) ──
    hl = h - l
    hc = (h - c.shift(1)).abs()
    lc = (l - c.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50, raw=False)

    # ── RSI(14) Wilder ──
    d = c.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    ls = (-d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + g / ls.replace(0, 1e-10))

    # ── EMA ──
    for s in [9, 12, 20, 21, 26, 50]:
        df[f"ema{s}"] = c.ewm(span=s, adjust=False).mean()

    # ── BB(20,2) ──
    df["bb_mid"] = c.rolling(20).mean()
    bstd = c.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bstd
    df["bb_lower"] = df["bb_mid"] - 2 * bstd
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, 1)

    # ── MACD(12,26,9) ──
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = e12 - e26
    df["macd_sig"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_sig"]

    # ── Stochastic(14,3,3) ──
    ll14 = l.rolling(14).min()
    hh14 = h.rolling(14).max()
    denom = (hh14 - ll14).replace(0, 1)
    df["stoch_k"] = 100 * (c - ll14) / denom
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # ── CCI(20) ──
    tp = (h + l + c) / 3
    tp_sma = tp.rolling(20).mean()
    tp_mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["cci"] = (tp - tp_sma) / (0.015 * tp_mad.replace(0, 1))

    # ── Williams %R(14) ──
    df["willr"] = -100 * (hh14 - c) / denom

    # ── ADX + DI(14) ──
    up_m = h - h.shift(1)
    dn_m = l.shift(1) - l
    pdm = pd.Series(np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0), index=df.index)
    s_atr = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    df["plus_di"] = 100 * pdm.ewm(alpha=1 / 14, min_periods=14).mean() / s_atr.replace(0, 1)
    df["minus_di"] = 100 * mdm.ewm(alpha=1 / 14, min_periods=14).mean() / s_atr.replace(0, 1)
    di_sum = (df["plus_di"] + df["minus_di"]).replace(0, 1)
    dx = 100 * (df["plus_di"] - df["minus_di"]).abs() / di_sum
    df["adx"] = dx.ewm(alpha=1 / 14, min_periods=14).mean()

    # ── Donchian(20) ──
    df["don_hi"] = h.rolling(20).max()
    df["don_lo"] = l.rolling(20).min()

    # ── Keltner(20, 1.5) ──
    df["kelt_mid"] = c.ewm(span=20, adjust=False).mean()
    df["kelt_upper"] = df["kelt_mid"] + 1.5 * df["atr"]
    df["kelt_lower"] = df["kelt_mid"] - 1.5 * df["atr"]

    # ── Ichimoku(9,26,52) ──
    df["tenkan"] = (h.rolling(9).max() + l.rolling(9).min()) / 2
    df["kijun"] = (h.rolling(26).max() + l.rolling(26).min()) / 2
    df["span_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    df["span_b"] = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)

    # ── Supertrend ──
    df["st2_dir"] = _supertrend(h, l, c, df["atr"], 2)
    df["st3_dir"] = _supertrend(h, l, c, df["atr"], 3)

    # ── Volume ──
    df["vol_sma"] = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_sma"].replace(0, 1)

    # ── OBV ──
    df["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    df["obv_ema"] = df["obv"].ewm(span=20, adjust=False).mean()

    return df


def add_1h_trend(df_main, df_5m):
    """在 df_main 加入 1h EMA(50) 趨勢方向（用已完成的 1h bar）"""
    df_1h = df_5m.resample("1h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()
    df_1h["trend_up"] = (df_1h["close"] > df_1h["ema50"]).shift(1)

    out = df_main.copy()
    out["trend_up"] = df_1h["trend_up"].reindex(out.index, method="ffill")
    out["trend_up"] = out["trend_up"].fillna(False)
    return out


# ══════════════════════════════════════════════════════════════
#  信號產生器 — 28 種
# ══════════════════════════════════════════════════════════════

def _xup(s1, s2):
    return (s1 > s2) & (s1.shift(1) <= s2.shift(1))

def _xdn(s1, s2):
    return (s1 < s2) & (s1.shift(1) >= s2.shift(1))

def _first(cond):
    """只在條件首次成立時觸發"""
    return cond & ~cond.shift(1).fillna(False)

# ── 趨勢跟蹤 (T01-T09) ──

def sig_T01(df):
    """EMA Cross 9/21"""
    return _xup(df["ema9"], df["ema21"]), _xdn(df["ema9"], df["ema21"])

def sig_T02(df):
    """EMA Cross 20/50"""
    return _xup(df["ema20"], df["ema50"]), _xdn(df["ema20"], df["ema50"])

def sig_T03(df):
    """MACD Signal Cross"""
    return _xup(df["macd"], df["macd_sig"]), _xdn(df["macd"], df["macd_sig"])

def sig_T04(df):
    """MACD Zero Cross"""
    z = pd.Series(0, index=df.index, dtype=float)
    return _xup(df["macd"], z), _xdn(df["macd"], z)

def sig_T05(df):
    """Supertrend(2) Flip"""
    p = df["st2_dir"].shift(1)
    return (df["st2_dir"] == 1) & (p == -1), (df["st2_dir"] == -1) & (p == 1)

def sig_T06(df):
    """Supertrend(3) Flip"""
    p = df["st3_dir"].shift(1)
    return (df["st3_dir"] == 1) & (p == -1), (df["st3_dir"] == -1) & (p == 1)

def sig_T07(df):
    """Donchian 20 Breakout"""
    above = df["close"] > df["don_hi"].shift(1)
    below = df["close"] < df["don_lo"].shift(1)
    return _first(above), _first(below)

def sig_T08(df):
    """ADX+DI Cross (ADX>25)"""
    ok = df["adx"] > 25
    return ok & _xup(df["plus_di"], df["minus_di"]), ok & _xdn(df["plus_di"], df["minus_di"])

def sig_T09(df):
    """Ichimoku Cloud Cross"""
    ct = df[["span_a", "span_b"]].max(axis=1)
    cb = df[["span_a", "span_b"]].min(axis=1)
    return _first(df["close"] > ct), _first(df["close"] < cb)

# ── 動量 (M01-M05) ──

def sig_M01(df):
    """RSI Cross 50"""
    lv = pd.Series(50.0, index=df.index)
    return _xup(df["rsi"], lv), _xdn(df["rsi"], lv)

def sig_M02(df):
    """RSI Cross 40/60"""
    return _xup(df["rsi"], pd.Series(40.0, index=df.index)), \
           _xdn(df["rsi"], pd.Series(60.0, index=df.index))

def sig_M03(df):
    """Stochastic %K/%D Cross (zones)"""
    lo = _xup(df["stoch_k"], df["stoch_d"]) & (df["stoch_k"].shift(1) < 20)
    sh = _xdn(df["stoch_k"], df["stoch_d"]) & (df["stoch_k"].shift(1) > 80)
    return lo, sh

def sig_M04(df):
    """CCI Cross ±100"""
    return _xup(df["cci"], pd.Series(-100.0, index=df.index)), \
           _xdn(df["cci"], pd.Series(100.0, index=df.index))

def sig_M05(df):
    """Williams %R Cross Zones"""
    return _xup(df["willr"], pd.Series(-80.0, index=df.index)), \
           _xdn(df["willr"], pd.Series(-20.0, index=df.index))

# ── 突破 (B01-B03) ──

def sig_B01(df):
    """BB Squeeze Breakout"""
    squeeze = df["bb_width"] < df["bb_width"].rolling(100).quantile(0.20)
    was = squeeze.shift(1).fillna(False)
    return was & (df["close"] > df["bb_upper"]), was & (df["close"] < df["bb_lower"])

def sig_B02(df):
    """Keltner Squeeze Breakout"""
    inside = (df["bb_upper"] < df["kelt_upper"]) & (df["bb_lower"] > df["kelt_lower"])
    was = inside.shift(1).fillna(False)
    return was & (df["close"] > df["bb_upper"]), was & (df["close"] < df["bb_lower"])

def sig_B03(df):
    """Big Candle (>1.5×ATR body)"""
    body = (df["close"] - df["open"]).abs()
    big = body > 1.5 * df["atr"]
    return big & (df["close"] > df["open"]), big & (df["close"] < df["open"])

# ── 均值回歸改良 (R01-R03) ──

def sig_R01(df):
    """RSI+BB (v4 baseline on 15m)"""
    return (df["rsi"] < 30) & (df["close"] < df["bb_lower"]), \
           (df["rsi"] > 70) & (df["close"] > df["bb_upper"])

def sig_R02(df):
    """RSI+BB + 1h Trend Filter"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    lo = (df["rsi"] < 30) & (df["close"] < df["bb_lower"]) & tu
    sh = (df["rsi"] > 70) & (df["close"] > df["bb_upper"]) & ~tu
    return lo, sh

def sig_R03(df):
    """Stochastic + BB"""
    return (df["stoch_k"] < 20) & (df["close"] < df["bb_lower"]), \
           (df["stoch_k"] > 80) & (df["close"] > df["bb_upper"])

# ── 多時間框架 (X01-X05) ──

def sig_X01(df):
    """1h Trend + RSI Cross 40/60"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    lo = tu & _xup(df["rsi"], pd.Series(40.0, index=df.index))
    sh = ~tu & _xdn(df["rsi"], pd.Series(60.0, index=df.index))
    return lo, sh

def sig_X02(df):
    """1h Trend + EMA 9/21 Cross"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    return tu & _xup(df["ema9"], df["ema21"]), ~tu & _xdn(df["ema9"], df["ema21"])

def sig_X03(df):
    """1h Trend + MACD Signal Cross"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    return tu & _xup(df["macd"], df["macd_sig"]), ~tu & _xdn(df["macd"], df["macd_sig"])

def sig_X04(df):
    """1h Trend + Donchian Breakout"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    above = df["close"] > df["don_hi"].shift(1)
    below = df["close"] < df["don_lo"].shift(1)
    return tu & _first(above), ~tu & _first(below)

def sig_X05(df):
    """1h Trend + RSI Pullback"""
    tu = df.get("trend_up", pd.Series(True, index=df.index))
    # RSI 近 3 bar 曾低於 35 後回升
    dipped = (df["rsi"].shift(1) < 35) | (df["rsi"].shift(2) < 35) | (df["rsi"].shift(3) < 35)
    spiked = (df["rsi"].shift(1) > 65) | (df["rsi"].shift(2) > 65) | (df["rsi"].shift(3) > 65)
    lo = tu & dipped & (df["rsi"] > 35)
    sh = ~tu & spiked & (df["rsi"] < 65)
    return _first(lo), _first(sh)

# ── 多指標組合 (C01-C03) ──

def sig_C01(df):
    """RSI Cross 50 + MACD > 0"""
    lo = _xup(df["rsi"], pd.Series(50.0, index=df.index)) & (df["macd"] > 0)
    sh = _xdn(df["rsi"], pd.Series(50.0, index=df.index)) & (df["macd"] < 0)
    return lo, sh

def sig_C02(df):
    """Stochastic Zones + ADX > 20"""
    ok = df["adx"] > 20
    lo = ok & _xup(df["stoch_k"], df["stoch_d"]) & (df["stoch_k"].shift(1) < 20)
    sh = ok & _xdn(df["stoch_k"], df["stoch_d"]) & (df["stoch_k"].shift(1) > 80)
    return lo, sh

def sig_C03(df):
    """EMA 9/21 Cross + Volume > 1.5×avg"""
    vol_ok = df["vol_ratio"] > 1.5
    return vol_ok & _xup(df["ema9"], df["ema21"]), vol_ok & _xdn(df["ema9"], df["ema21"])


# ══════════════════════════════════════════════════════════════
#  策略登記表
# ══════════════════════════════════════════════════════════════

ALL_STRATEGIES = {
    # 趨勢
    "T01_EMA9_21":        ("EMA Cross 9/21",                sig_T01),
    "T02_EMA20_50":       ("EMA Cross 20/50",               sig_T02),
    "T03_MACD_SIG":       ("MACD Signal Cross",             sig_T03),
    "T04_MACD_ZERO":      ("MACD Zero Cross",               sig_T04),
    "T05_SUPERTREND2":    ("Supertrend(2)",                  sig_T05),
    "T06_SUPERTREND3":    ("Supertrend(3)",                  sig_T06),
    "T07_DONCHIAN":       ("Donchian 20 Break",             sig_T07),
    "T08_ADX_DI":         ("ADX+DI Cross",                  sig_T08),
    "T09_ICHIMOKU":       ("Ichimoku Cloud",                sig_T09),
    # 動量
    "M01_RSI50":          ("RSI Cross 50",                  sig_M01),
    "M02_RSI40_60":       ("RSI Cross 40/60",               sig_M02),
    "M03_STOCH":          ("Stochastic K/D",                sig_M03),
    "M04_CCI":            ("CCI ±100",                      sig_M04),
    "M05_WILLR":          ("Williams %R",                   sig_M05),
    # 突破
    "B01_BB_SQUEEZE":     ("BB Squeeze Break",              sig_B01),
    "B02_KELT_SQUEEZE":   ("Keltner Squeeze",               sig_B02),
    "B03_BIG_CANDLE":     ("Big Candle >1.5ATR",            sig_B03),
    # 均值回歸
    "R01_RSI_BB":         ("RSI+BB (v4 base)",              sig_R01),
    "R02_RSI_BB_TREND":   ("RSI+BB+Trend",                  sig_R02),
    "R03_STOCH_BB":       ("Stoch+BB",                      sig_R03),
    # 多時間框架
    "X01_1H_RSI":         ("1h Trend+RSI 40/60",            sig_X01),
    "X02_1H_EMA":         ("1h Trend+EMA Cross",            sig_X02),
    "X03_1H_MACD":        ("1h Trend+MACD",                 sig_X03),
    "X04_1H_DONCHIAN":    ("1h Trend+Donchian",             sig_X04),
    "X05_1H_PULLBACK":    ("1h Trend+Pullback",             sig_X05),
    # 組合
    "C01_RSI_MACD":       ("RSI50+MACD>0",                  sig_C01),
    "C02_STOCH_ADX":      ("Stoch+ADX>20",                  sig_C02),
    "C03_EMA_VOL":        ("EMA Cross+Volume",              sig_C03),
}


# ══════════════════════════════════════════════════════════════
#  自適應 Trail（同 monitor / v4 research）
# ══════════════════════════════════════════════════════════════

def _trail_mult(atr_pctile, rsi, side):
    base = 1.0 + (atr_pctile / 100) * 1.5
    if side == "long" and rsi > 65:
        return base * 0.6
    if side == "short" and rsi < 35:
        return base * 0.6
    return base


# ══════════════════════════════════════════════════════════════
#  持倉 + 回測引擎
# ══════════════════════════════════════════════════════════════

@dataclass
class Pos:
    side: str
    entry_bar: int
    entry_price: float
    notional: float
    sl: float
    tp1: float
    atr_entry: float
    phase: int = 1
    tp1_done: bool = False
    tp1_pnl: float = 0.0
    trail_hi: float = 0.0
    trail_lo: float = 0.0

    def __post_init__(self):
        self.trail_hi = self.entry_price
        self.trail_lo = self.entry_price


def run_backtest(df, long_sig, short_sig):
    """統一回測引擎，回傳 list of trade dicts"""
    notional = MARGIN * LEVERAGE  # 2000
    positions: List[Pos] = []
    trades = []
    cum_pnl = 0.0
    equity = []

    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    atr_a = df["atr"].values
    rsi_a = df["rsi"].values
    atrp_a = df["atr_pctile"].values
    lo_a = long_sig.values.astype(bool)
    sh_a = short_sig.values.astype(bool)

    start = 120  # 確保指標暖機

    for i in range(start, len(df) - 1):
        nxt_o = o[i + 1]
        nxt_h = h[i + 1]
        nxt_l = l[i + 1]
        nxt_atr = atr_a[i + 1] if not np.isnan(atr_a[i + 1]) else atr_a[i]
        nxt_rsi = rsi_a[i + 1] if not np.isnan(rsi_a[i + 1]) else 50
        nxt_atrp = atrp_a[i + 1] if not np.isnan(atrp_a[i + 1]) else 50

        # ── 更新持倉（用 i+1 的 bar）──
        closed = []
        for pos in positions:
            res = _check(pos, nxt_h, nxt_l, nxt_o, nxt_atr, nxt_rsi, nxt_atrp, i + 1)
            if res:
                cum_pnl += res["pnl"]
                trades.append({
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "exit_price": res["ep"],
                    "exit_reason": res["reason"],
                    "pnl": res["pnl"],
                    "entry_bar": pos.entry_bar,
                    "exit_bar": i + 1,
                    "tp1_done": pos.tp1_done,
                })
                closed.append(pos)
        for p in closed:
            positions.remove(p)

        # ── 檢查進場 ──
        if pd.isna(atr_a[i]) or atr_a[i] <= 0:
            equity.append(cum_pnl)
            continue

        atr_now = atr_a[i]
        long_n = sum(1 for p in positions if p.side == "long")
        short_n = sum(1 for p in positions if p.side == "short")

        # 做多
        if lo_a[i] and long_n < MAX_SAME:
            entry = nxt_o * (1 + SLIP_PCT / 100)
            sl = entry - SL_ATR * atr_now
            tp1 = entry + TP1_ATR * atr_now
            positions.append(Pos(
                side="long", entry_bar=i + 1, entry_price=entry,
                notional=notional, sl=sl, tp1=tp1, atr_entry=atr_now))

        # 做空
        if sh_a[i] and short_n < MAX_SAME:
            entry = nxt_o * (1 - SLIP_PCT / 100)
            sl = entry + SL_ATR * atr_now
            tp1 = entry - TP1_ATR * atr_now
            positions.append(Pos(
                side="short", entry_bar=i + 1, entry_price=entry,
                notional=notional, sl=sl, tp1=tp1, atr_entry=atr_now))

        equity.append(cum_pnl)

    # 強制平倉
    last_c = c[-1]
    for pos in positions:
        pnl = _pnl_full(pos, last_c)
        trades.append({
            "side": pos.side, "entry_price": pos.entry_price,
            "exit_price": last_c, "exit_reason": "end_of_data",
            "pnl": pnl, "entry_bar": pos.entry_bar,
            "exit_bar": len(df) - 1, "tp1_done": pos.tp1_done})

    return trades, equity


def _check(pos, bar_h, bar_l, bar_o, atr, rsi, atrp, bar_idx):
    """檢查持倉：SL / TP1 / Trail"""
    if pos.side == "long":
        # SL
        if bar_l <= pos.sl:
            pnl = _pnl_full(pos, pos.sl)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "ep": pos.sl, "reason": reason}
        # TP1
        if not pos.tp1_done and bar_h >= pos.tp1:
            g = (pos.tp1 - pos.entry_price) / pos.entry_price * (pos.notional * TP1_PCT)
            pos.tp1_pnl = g - pos.notional * TP1_PCT * FEE * 2
            pos.tp1_done = True
            pos.phase = 2
            pos.sl = pos.entry_price
            pos.trail_hi = max(pos.trail_hi, bar_h)
        # Trail
        if pos.phase == 2:
            pos.trail_hi = max(pos.trail_hi, bar_h)
            mult = _trail_mult(atrp, rsi, "long")
            new_sl = max(pos.trail_hi - atr * mult, pos.entry_price)
            if new_sl > pos.sl:
                pos.sl = new_sl
            if bar_l <= pos.sl:
                ep = max(pos.sl, bar_o)
                pnl = pos.tp1_pnl + _partial_pnl(pos, ep, 1 - TP1_PCT)
                return {"pnl": pnl, "ep": ep, "reason": "adaptive_trail"}

    else:  # short
        if bar_h >= pos.sl:
            pnl = _pnl_full(pos, pos.sl)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "ep": pos.sl, "reason": reason}
        if not pos.tp1_done and bar_l <= pos.tp1:
            g = (pos.entry_price - pos.tp1) / pos.entry_price * (pos.notional * TP1_PCT)
            pos.tp1_pnl = g - pos.notional * TP1_PCT * FEE * 2
            pos.tp1_done = True
            pos.phase = 2
            pos.sl = pos.entry_price
            pos.trail_lo = min(pos.trail_lo, bar_l)
        if pos.phase == 2:
            pos.trail_lo = min(pos.trail_lo, bar_l)
            mult = _trail_mult(atrp, rsi, "short")
            new_sl = min(pos.trail_lo + atr * mult, pos.entry_price)
            if new_sl < pos.sl:
                pos.sl = new_sl
            if bar_h >= pos.sl:
                ep = min(pos.sl, bar_o)
                pnl = pos.tp1_pnl + _partial_pnl(pos, ep, 1 - TP1_PCT)
                return {"pnl": pnl, "ep": ep, "reason": "adaptive_trail"}
    return None


def _pnl_full(pos, exit_p):
    """完整持倉 PnL（含 TP1 部分）"""
    if pos.tp1_done:
        return pos.tp1_pnl + _partial_pnl(pos, exit_p, 1 - TP1_PCT)
    d = 1 if pos.side == "long" else -1
    return d * (exit_p - pos.entry_price) / pos.entry_price * pos.notional - pos.notional * FEE * 2


def _partial_pnl(pos, exit_p, pct):
    d = 1 if pos.side == "long" else -1
    n = pos.notional * pct
    return d * (exit_p - pos.entry_price) / pos.entry_price * n - n * FEE


# ══════════════════════════════════════════════════════════════
#  統計
# ══════════════════════════════════════════════════════════════

def calc_metrics(trades, equity, total_days):
    if not trades:
        return {"pnl": 0, "n": 0, "daily": 0, "wr": 0, "pf": 0,
                "dd": 0, "avg_bars": 0, "long_pnl": 0, "short_pnl": 0,
                "win_avg": 0, "loss_avg": 0}

    pnls = np.array([t["pnl"] for t in trades])
    sides = [t["side"] for t in trades]
    durs = np.array([t["exit_bar"] - t["entry_bar"] for t in trades])

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gp = wins.sum() if len(wins) else 0
    gl = abs(losses.sum()) if len(losses) else 1e-10

    eq = np.array(equity) if equity else np.array([0])
    cum = np.cumsum(pnls)
    if len(cum):
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak).min()
    else:
        dd = 0

    long_pnl = sum(t["pnl"] for t in trades if t["side"] == "long")
    short_pnl = sum(t["pnl"] for t in trades if t["side"] == "short")

    return {
        "pnl": round(pnls.sum(), 2),
        "n": len(trades),
        "daily": round(len(trades) / max(total_days, 1), 1),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "pf": round(gp / gl, 2),
        "dd": round(dd, 2),
        "avg_bars": round(durs.mean(), 1),
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "win_avg": round(wins.mean(), 2) if len(wins) else 0,
        "loss_avg": round(losses.mean(), 2) if len(losses) else 0,
    }


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 80)
    print("  v5 策略大規模研究 — 28 指標策略全面對比")
    print("  實戰模式: 下根 bar open + 滑價 | SL 2×ATR | TP1 1.5×ATR | Trail adaptive")
    print("=" * 80)

    # ── 1. 載入 5m 資料 ──
    print("\n[1] 載入 5m 資料...")
    df_5m = fetch_klines(
        symbol="BTCUSDT", interval="5m",
        start_dt=datetime(2025, 10, 1, tzinfo=timezone.utc),
        end_dt=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    print(f"    5m: {len(df_5m)} 根 ({df_5m.index[0].date()} ~ {df_5m.index[-1].date()})")

    # ── 2. 重採樣至 15m ──
    print("[2] 重採樣 15m + 計算全部指標...")
    df_15m_raw = df_5m.resample("15min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()

    df_15m = compute_all(df_15m_raw)
    df_15m = add_1h_trend(df_15m, df_5m)

    total_days = (df_15m.index[-1] - df_15m.index[0]).days
    print(f"    15m: {len(df_15m)} 根, 共 {total_days} 天")

    # ── 3. 跑 28 個策略 ──
    print(f"\n[3] 跑 {len(ALL_STRATEGIES)} 個策略...\n")

    results = {}
    for code, (desc, sig_fn) in ALL_STRATEGIES.items():
        long_sig, short_sig = sig_fn(df_15m)
        # 確保 bool 型別
        long_sig = long_sig.fillna(False).astype(bool)
        short_sig = short_sig.fillna(False).astype(bool)

        trades, equity = run_backtest(df_15m, long_sig, short_sig)
        m = calc_metrics(trades, equity, total_days)
        results[code] = {"metrics": m, "desc": desc, "trades": trades}

        tag = "+" if m["pnl"] > 0 else " "
        print(f"  {tag} {code:<18} {desc:<22} | PnL ${m['pnl']:>9.2f} | "
              f"{m['n']:>5} 筆 ({m['daily']:.1f}/d) | WR {m['wr']:>5.1f}% | "
              f"PF {m['pf']:>5.2f} | DD ${m['dd']:>9.2f}")

    # ══════════════════════════════════════════════════════════
    #  結果排行
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  排行榜（依 PnL 排序）")
    print("=" * 80)

    # 分類標籤
    cat_names = {
        "T": "趨勢", "M": "動量", "B": "突破",
        "R": "均值回歸", "X": "多時間框架", "C": "組合"
    }

    sorted_codes = sorted(results.keys(), key=lambda k: results[k]["metrics"]["pnl"], reverse=True)

    header = (f"{'#':>2} {'策略':<18} {'類別':<6} {'PnL':>9} {'筆數':>5} "
              f"{'日均':>4} {'勝率':>6} {'PF':>5} {'回撤':>9} "
              f"{'多PnL':>9} {'空PnL':>9} {'W均':>7} {'L均':>7}")
    print(header)
    print("-" * len(header))

    for rank, code in enumerate(sorted_codes, 1):
        m = results[code]["metrics"]
        cat = cat_names.get(code[0], "?")
        tag = "***" if m["pnl"] > 0 else "   "
        print(f"{rank:>2} {code:<18} {cat:<6} ${m['pnl']:>8.0f} {m['n']:>5} "
              f"{m['daily']:>4.1f} {m['wr']:>5.1f}% {m['pf']:>5.2f} ${m['dd']:>8.0f} "
              f"${m['long_pnl']:>8.0f} ${m['short_pnl']:>8.0f} "
              f"${m['win_avg']:>6.1f} ${m['loss_avg']:>6.1f} {tag}")

    # ══════════════════════════════════════════════════════════
    #  分類摘要
    # ══════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  分類摘要")
    print("=" * 80)

    for prefix, cat_name in cat_names.items():
        codes = [c for c in sorted_codes if c[0] == prefix]
        if not codes:
            continue
        pnls = [results[c]["metrics"]["pnl"] for c in codes]
        wrs = [results[c]["metrics"]["wr"] for c in codes]
        best_code = max(codes, key=lambda c: results[c]["metrics"]["pnl"])
        best_m = results[best_code]["metrics"]
        print(f"\n  {cat_name} ({len(codes)} 種):")
        print(f"    平均 PnL: ${np.mean(pnls):.0f}  |  最佳: {best_code} (${best_m['pnl']:.0f})")
        print(f"    平均勝率: {np.mean(wrs):.1f}%")
        profitable = sum(1 for p in pnls if p > 0)
        print(f"    盈利策略: {profitable}/{len(codes)}")

    # ══════════════════════════════════════════════════════════
    #  Top 3 Walk-Forward 驗證
    # ══════════════════════════════════════════════════════════
    top3 = sorted_codes[:3]

    print(f"\n{'=' * 80}")
    print(f"  Walk-Forward 驗證 (Top 3)")
    print(f"{'=' * 80}")

    split = df_15m.index[0] + pd.DateOffset(months=4)
    df_is = df_15m[df_15m.index < split]
    df_oos = df_15m[df_15m.index >= split]
    is_days = (df_is.index[-1] - df_is.index[0]).days
    oos_days = (df_oos.index[-1] - df_oos.index[0]).days

    for code in top3:
        desc, sig_fn = ALL_STRATEGIES[code]
        print(f"\n  ── {code} ({desc}) ──")
        for label, sub_df, sub_days in [("IS (4m)", df_is, is_days),
                                         ("OOS (2m)", df_oos, oos_days)]:
            if len(sub_df) < 200:
                print(f"    {label}: 資料不足")
                continue
            ls, ss = sig_fn(sub_df)
            ls = ls.fillna(False).astype(bool)
            ss = ss.fillna(False).astype(bool)
            t, eq = run_backtest(sub_df, ls, ss)
            sm = calc_metrics(t, eq, sub_days)
            print(f"    {label:<10} PnL ${sm['pnl']:>8.2f} | {sm['n']:>4} 筆 | "
                  f"WR {sm['wr']:.1f}% | PF {sm['pf']:.2f} | DD ${sm['dd']:.2f}")

    # ══════════════════════════════════════════════════════════
    #  儲存 Top 1 交易明細
    # ══════════════════════════════════════════════════════════
    os.makedirs(RESULTS_DIR, exist_ok=True)
    best_code = sorted_codes[0]
    best_trades = results[best_code]["trades"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = os.path.join(RESULTS_DIR, f"{ts}_v5research_{best_code}_trades.csv")
    pd.DataFrame(best_trades).to_csv(fname, index=False)
    print(f"\n  [已存] {os.path.basename(fname)}")

    # ══════════════════════════════════════════════════════════
    #  結論
    # ══════════════════════════════════════════════════════════
    profitable_count = sum(1 for c in sorted_codes if results[c]["metrics"]["pnl"] > 0)
    print(f"\n{'=' * 80}")
    print(f"  結論: {profitable_count}/{len(ALL_STRATEGIES)} 策略盈利")
    if profitable_count > 0:
        print(f"  盈利策略:")
        for c in sorted_codes:
            if results[c]["metrics"]["pnl"] > 0:
                m = results[c]["metrics"]
                print(f"    {c} — ${m['pnl']:.0f} | WR {m['wr']}% | PF {m['pf']}")
    else:
        print(f"  所有策略在此期間均虧損。建議考慮:")
        print(f"    1. 更長時間框架 (1h, 4h)")
        print(f"    2. 不同交易品種")
        print(f"    3. 完全不同的策略邏輯")
    print(f"{'=' * 80}")
