"""
V27 Engine — meta-labeling 研究用引擎載入器
=============================================
不複製引擎（V24/V25 的複製法會漂移），改為讀取線上引擎 v14_export_trades.py 原始碼、
文字 patch 注入 `veto(i, side)` hook 後 exec：
  - veto=None → 與線上引擎完全一致（有 parity assert 保證）
  - veto 回傳 True → 該 bar 該方向不進場（其餘邏輯完全不動，cap/cooldown 不消耗）

另提供 build_features()：每根 bar 的「進場時已知」特徵（全部只用 <= i 的資料，無前瞻）。
"""
import os
import sys
import importlib.util

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
DATA_DIR = os.path.join(ROOT, 'data')
ENGINE_PATH = os.path.join(SCRIPT_DIR, 'v14_export_trades.py')

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # 引擎內 `from strategy import ...`（V25-D 單一來源）


def load_engine(with_veto=True):
    """載入線上引擎；with_veto=True 時注入 entry veto hook。"""
    with open(ENGINE_PATH, encoding='utf-8') as f:
        src = f.read()

    if with_veto:
        pairs = [
            ("def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
             "                          realistic=False, slip_bps=0.0, margin_schedule=None):",
             "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
             "                          realistic=False, slip_bps=0.0, margin_schedule=None,\n"
             "                          veto=None):"),
            ("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):",
             "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and "
             "(veto is None or not veto(i, 'L'))):"),
            ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):",
             "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and "
             "(veto is None or not veto(i, 'S'))):"),
        ]
        for old, new in pairs:
            assert src.count(old) == 1, f"engine patch anchor not found/unique: {old[:60]}"
            src = src.replace(old, new)

    # 只取定義區（去掉 __main__ 匯出 Excel 區塊）
    src = src.split("if __name__ == '__main__':")[0]

    mod_name = 'v27_engine_patched' if with_veto else 'v27_engine_orig'
    spec = importlib.util.spec_from_loader(mod_name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__['__file__'] = ENGINE_PATH  # SCRIPT_DIR 相對路徑正確
    exec(compile(src, ENGINE_PATH, 'exec'), mod.__dict__)
    return mod


def load_data(symbol='ETHUSDT'):
    df = pd.read_csv(os.path.join(DATA_DIR, f'{symbol}_1h_latest730d.csv'))
    df['datetime'] = df['datetime'].astype(str)
    return df


def build_features(df, ind):
    """每根 bar 的進場時特徵（皆為 close[i] 決策當下已知，無前瞻）。

    回傳 dict[str, np.ndarray]，長度 = len(df)。
    """
    o = df['open'].to_numpy(float)
    h = df['high'].to_numpy(float)
    l = df['low'].to_numpy(float)
    c = df['close'].to_numpy(float)
    v = df['volume'].to_numpy(float)
    tb = df['taker_buy_volume'].to_numpy(float)
    n = len(c)

    cs = pd.Series(c)
    logret = np.log(cs / cs.shift(1))

    # True Range / ATR14（含 bar i）
    prev_c = np.roll(c, 1); prev_c[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
    atr14_pct = atr14 / c * 100

    rv24 = (logret.rolling(24).std() * 100).to_numpy()

    def pct_ret(k):
        return ((cs / cs.shift(k) - 1) * 100).to_numpy()

    sma50 = cs.rolling(50).mean()
    sma200 = cs.rolling(200).mean()
    dist_sma50 = ((cs / sma50 - 1) * 100).to_numpy()
    dist_sma200 = ((cs / sma200 - 1) * 100).to_numpy()

    hh48 = pd.Series(h).rolling(48).max()
    ll48 = pd.Series(l).rolling(48).min()
    rng48 = (hh48 - ll48).replace(0, np.nan)
    rpos48 = ((cs - ll48) / rng48).to_numpy()

    vol_ma20 = pd.Series(v).rolling(20).mean().replace(0, np.nan)
    vol_ratio20 = (pd.Series(v) / vol_ma20).to_numpy()

    vs = pd.Series(v).replace(0, np.nan)
    taker_imb = (pd.Series(tb) / vs - 0.5).to_numpy()
    taker_imb6 = (pd.Series(tb).rolling(6).sum()
                  / pd.Series(v).rolling(6).sum().replace(0, np.nan) - 0.5).to_numpy()

    rng = np.maximum(h - l, 1e-12)
    body_ratio = np.abs(c - o) / rng
    upper_wick = (h - np.maximum(c, o)) / rng
    lower_wick = (np.minimum(c, o) - l) / rng

    # 連續同向收盤 streak（進場 bar 含自身，向前數）
    sign = np.sign(np.diff(c, prepend=c[0]))
    streak = np.zeros(n)
    for i in range(1, n):
        streak[i] = streak[i - 1] + sign[i] if sign[i] == sign[i - 1] and sign[i] != 0 else sign[i]

    # breakout margin（與引擎 high15/low15 同定義：shift(1) 後 rolling 15）
    shifted_close = np.roll(c, 1); shifted_close[0] = np.nan
    high_15 = pd.Series(shifted_close).rolling(15).max().to_numpy()
    low_15 = pd.Series(shifted_close).rolling(15).min().to_numpy()
    brk_margin_L = (c - high_15) / high_15 * 100
    brk_margin_S = (low_15 - c) / low_15 * 100

    # 壓縮持續長度：pctile 連續 < TH 的 bar 數（含 i）
    def compress_len(pct, th):
        out = np.zeros(n)
        run = 0
        for i in range(n):
            run = run + 1 if (not np.isnan(pct[i]) and pct[i] < th) else 0
            out[i] = run
        return out

    dt = pd.to_datetime(df['datetime'])
    hours = dt.dt.hour.to_numpy(float)
    dows = dt.dt.dayofweek.to_numpy(float)

    # BTC 對齊特徵（同窗口快取，依 datetime 對齊）
    btc = load_data('BTCUSDT')
    bmap = pd.Series(btc['close'].to_numpy(float), index=btc['datetime'])
    bc = bmap.reindex(df['datetime']).to_numpy()
    bcs = pd.Series(bc).ffill()
    btc_ret24 = ((bcs / bcs.shift(24) - 1) * 100).to_numpy()

    feats = {
        'gk_pctile_L': ind['pctile_L'],
        'gk_pctile_S': ind['pctile_S'],
        'slope': ind['slope'] * 100,           # SMA200 相對斜率 %
        'atr14_pct': atr14_pct,
        'rv24': rv24,
        'ret4': pct_ret(4),
        'ret24': pct_ret(24),
        'ret72': pct_ret(72),
        'dist_sma50': dist_sma50,
        'dist_sma200': dist_sma200,
        'rpos48': rpos48,
        'vol_ratio20': vol_ratio20,
        'taker_imb': taker_imb,
        'taker_imb6': taker_imb6,
        'body_ratio': body_ratio,
        'upper_wick': upper_wick,
        'lower_wick': lower_wick,
        'streak': streak,
        'brk_margin_L': brk_margin_L,
        'brk_margin_S': brk_margin_S,
        'compress_len_L': compress_len(ind['pctile_L'], 25),
        'compress_len_S': compress_len(ind['pctile_S'], 35),
        'hour': hours,
        'dow': dows,
        'btc_ret24': btc_ret24,
        'ethbtc_spread24': pct_ret(24) - btc_ret24,
    }
    return feats


# 每側分類器實際使用的特徵欄（side 專屬欄位取自己那側）
FEATURE_COLS_COMMON = [
    'slope', 'atr14_pct', 'rv24', 'ret4', 'ret24', 'ret72',
    'dist_sma50', 'dist_sma200', 'rpos48', 'vol_ratio20',
    'taker_imb', 'taker_imb6', 'body_ratio', 'upper_wick', 'lower_wick',
    'streak', 'hour', 'dow', 'btc_ret24', 'ethbtc_spread24',
]


def feature_cols(side):
    if side == 'L':
        return ['gk_pctile_L', 'brk_margin_L', 'compress_len_L'] + FEATURE_COLS_COMMON
    return ['gk_pctile_S', 'brk_margin_S', 'compress_len_S'] + FEATURE_COLS_COMMON


def trades_with_features(trades, feats):
    """把每筆交易配上進場 bar 的特徵，回傳 DataFrame。"""
    rows = []
    for t in trades:
        b = t['entry_bar']
        row = dict(t)
        for k, arr in feats.items():
            row[k] = float(arr[b]) if not np.isnan(arr[b]) else np.nan
        rows.append(row)
    tdf = pd.DataFrame(rows)
    tdf['y_mh'] = (tdf['exit_reason'] == 'MH').astype(int)
    tdf['y_loss'] = (tdf['pnl_usd'] < 0).astype(int)
    return tdf


if __name__ == '__main__':
    # Parity check：veto=None 必須與原引擎逐筆一致
    df = load_data()
    orig = load_engine(with_veto=False)
    pat = load_engine(with_veto=True)

    ind = orig.compute_indicators(df)
    dts = df['datetime'].values
    for realistic in (True, False):
        t0 = orig.simulate_v14_detailed(ind, dts, realistic=realistic)
        t1 = pat.simulate_v14_detailed(ind, dts, realistic=realistic, veto=None)
        assert t0 == t1, f"parity FAIL (realistic={realistic})"
        pnl = sum(t['pnl_usd'] for t in t0)
        mh = [t for t in t0 if t['exit_reason'] == 'MH']
        print(f"parity OK realistic={realistic}: {len(t0)} trades, "
              f"PnL ${pnl:+.0f}, MH {len(mh)} 筆 ${sum(t['pnl_usd'] for t in mh):+.0f}")

    # veto 全擋 sanity：交易數必為 0
    t2 = pat.simulate_v14_detailed(ind, dts, realistic=True, veto=lambda i, s: True)
    assert len(t2) == 0
    print("veto-all OK: 0 trades")
