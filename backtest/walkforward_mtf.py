"""
對最佳 MTF 組合跑 Walk-Forward 驗證
  多單: m5_first_reversal + atr_trail
  空單: m5_next_open + fixed_2atr

執行: python backtest/walkforward_mtf.py
"""
import os, sys
from datetime import datetime, timezone
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
from strategy_engine import compute_indicators, get_signals
from research_mtf import compute_5m_indicators, spread_1h_to_5m, run_combo, RISK

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

BEST_LONG_ENTRY  = "m5_first_reversal"
BEST_LONG_EXIT   = "atr_trail"
BEST_SHORT_ENTRY = "m5_next_open"
BEST_SHORT_EXIT  = "fixed_2atr"

START       = datetime(2025, 9,  1, tzinfo=timezone.utc)
SPLIT       = datetime(2026, 1,  1, tzinfo=timezone.utc)   # 訓練/測試分界
END         = datetime(2026, 3, 28, tzinfo=timezone.utc)


def run_period(df_1h, df_5m, label):
    r = run_combo(df_1h, df_5m,
                  long_entry=BEST_LONG_ENTRY,  long_exit=BEST_LONG_EXIT,
                  short_entry=BEST_SHORT_ENTRY, short_exit=BEST_SHORT_EXIT)
    sep = "-" * 52
    print(f"\n  [{label}]")
    print(f"  {sep}")
    print(f"  總損益:        ${r['total_pnl']:>9.2f}")
    print(f"  多單:          ${r['long_pnl']:>9.2f}  ({r['long_trades']} 筆)")
    print(f"  空單:          ${r['short_pnl']:>9.2f}  ({r['short_trades']} 筆)")
    print(f"  勝率:          {r['win_rate']:>5.1f}%")
    print(f"  Profit Factor: {r['profit_factor']:>9.2f}")
    print(f"  最大回撤:      ${r['max_drawdown']:>9.2f}")
    return r


if __name__ == "__main__":
    # 抓資料（快取命中）
    df_1h_raw = fetch_klines("BTCUSDT", "1h", START, END)
    df_5m_raw = fetch_klines("BTCUSDT", "5m", START, END)

    df_1h = compute_indicators(df_1h_raw)
    df_1h = get_signals(df_1h)
    df_5m = compute_5m_indicators(df_5m_raw)
    df_5m = spread_1h_to_5m(df_1h, df_5m)

    print("=" * 56)
    print("  Walk-Forward 驗證  (MTF 最佳組合)")
    print(f"  多單: {BEST_LONG_ENTRY} + {BEST_LONG_EXIT}")
    print(f"  空單: {BEST_SHORT_ENTRY} + {BEST_SHORT_EXIT}")
    print(f"  訓練期: {START.date()} ~ {SPLIT.date()}")
    print(f"  測試期: {SPLIT.date()} ~ {END.date()}")
    print("=" * 56)

    # 切分
    df_1h_train = df_1h[df_1h.index <  SPLIT]
    df_5m_train = df_5m[df_5m.index <  SPLIT]
    df_1h_test  = df_1h[df_1h.index >= SPLIT]
    df_5m_test  = df_5m[df_5m.index >= SPLIT]

    r_train = run_period(df_1h_train, df_5m_train, "訓練期 In-Sample")
    r_test  = run_period(df_1h_test,  df_5m_test,  "測試期 Out-of-Sample")
    r_full  = run_period(df_1h,       df_5m,        "全期")

    # 比較表
    print(f"\n{'='*56}")
    print(f"  {'':16} {'訓練期':>10} {'測試期':>10} {'全期':>10}")
    print(f"  {'-'*50}")
    for k, label in [("total_pnl","總損益"), ("win_rate","勝率"),
                      ("profit_factor","PF"), ("max_drawdown","最大回撤")]:
        v_tr = r_train[k]; v_te = r_test[k]; v_fu = r_full[k]
        if k in ("total_pnl","max_drawdown"):
            print(f"  {label:<16} ${v_tr:>8.2f}  ${v_te:>8.2f}  ${v_fu:>8.2f}")
        else:
            print(f"  {label:<16}  {v_tr:>8.2f}   {v_te:>8.2f}   {v_fu:>8.2f}")

    # 存結果
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = pd.DataFrame([
        {"period": "train", **r_train},
        {"period": "test",  **r_test},
        {"period": "full",  **r_full},
    ])
    out = os.path.join(RESULTS_DIR, f"{ts}_walkforward_mtf.csv")
    summary.to_csv(out, index=False)
    print(f"\n  結果已存到 backtest/results/{ts}_walkforward_mtf.csv")
