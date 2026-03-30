"""
CryptoBot Dashboard

streamlit run dashboard/app.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd

from dashboard.data_loader import load_trades, load_klines, load_open_positions, list_trade_files
from dashboard.metrics import compute_metrics
from dashboard.charts import (
    candlestick_with_trades, equity_curve, pnl_by_side, exit_reason_chart
)

st.set_page_config(
    page_title="CryptoBot 交易儀表板",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 側邊欄 ──────────────────────────────────────────────────
st.sidebar.title("CryptoBot")
st.sidebar.markdown("BTC/USDT 自動交易系統")

# 交易紀錄選擇
trade_files = list_trade_files()
if trade_files:
    selected_file = st.sidebar.selectbox(
        "交易紀錄檔案",
        trade_files,
        index=0,
    )
    csv_path = os.path.join(
        os.path.dirname(__file__), "..", "backtest", "results", selected_file
    )
else:
    selected_file = None
    csv_path = None

# K線天數
days = st.sidebar.slider("K線顯示天數", 7, 210, 30, step=7)

# 自動更新
auto_refresh = st.sidebar.checkbox("自動更新 (30秒)", value=False)

st.sidebar.markdown("---")
st.sidebar.markdown("**交易策略**")
st.sidebar.markdown("- 做多: RSI(14) < 30")
st.sidebar.markdown("- 做空: 收盤 > 布林上軌")
st.sidebar.markdown("- 時間框架: 1h 定方向 + 5m 進場")

# ── 載入資料 ──────────────────────────────────────────────────
trades = load_trades(csv_path)
klines = load_klines("BTCUSDT", "1h")
positions = load_open_positions()

if trades.empty:
    st.warning("找不到交易紀錄，請先執行回測。")
    st.stop()

metrics = compute_metrics(trades)

# ── Row 1: 指標卡 ────────────────────────────────────────────
st.title("BTC/USDT 交易儀表板")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("總損益", f"${metrics['total_pnl']:,.2f}")
col2.metric("勝率", f"{metrics['win_rate']}%")
col3.metric("獲利因子", f"{metrics['profit_factor']}")
col4.metric("最大回撤", f"${metrics['max_drawdown']:,.2f}")
col5.metric("交易筆數", f"{metrics['trades']}")

# ── Row 2: K線圖 ─────────────────────────────────────────────
st.plotly_chart(
    candlestick_with_trades(klines, trades, days=days),
    use_container_width=True,
)

# ── Row 3: PnL 曲線 + 持倉統計 ──────────────────────────────
left, right = st.columns([3, 2])

with left:
    st.plotly_chart(equity_curve(trades), use_container_width=True)

with right:
    st.subheader("多空績效")

    summary_data = {
        "": ["做多", "做空", "合計"],
        "筆數": [metrics["long_trades"], metrics["short_trades"], metrics["trades"]],
        "損益": [
            f"${metrics['long_pnl']:,.2f}",
            f"${metrics['short_pnl']:,.2f}",
            f"${metrics['total_pnl']:,.2f}",
        ],
        "勝率": [
            f"{metrics['long_win_rate']}%",
            f"{metrics['short_win_rate']}%",
            f"{metrics['win_rate']}%",
        ],
    }
    st.dataframe(pd.DataFrame(summary_data).set_index(""), use_container_width=True)

    if positions:
        st.subheader("當前持倉")
        st.dataframe(pd.DataFrame(positions), use_container_width=True)

# ── Row 4: 分析圖表 ──────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.plotly_chart(pnl_by_side(trades), use_container_width=True)
with c2:
    st.plotly_chart(exit_reason_chart(trades), use_container_width=True)

# ── Row 5: 交易紀錄表 ────────────────────────────────────────
st.subheader("交易明細")

# 欄位重新命名為中文
col_rename = {
    "side": "方向",
    "entry_time": "進場時間",
    "entry_price": "進場價格",
    "exit_time": "出場時間",
    "exit_price": "出場價格",
    "exit_reason": "出場原因",
    "pnl": "損益",
    "tp1_done": "已觸發TP1",
    "duration_bars": "持倉K線數",
}

display_cols = [c for c in col_rename.keys() if c in trades.columns]

display_df = trades[display_cols].copy()
if "pnl" in display_df.columns:
    display_df = display_df.sort_values("exit_time", ascending=False)
    display_df["pnl"] = display_df["pnl"].round(2)
if "entry_price" in display_df.columns:
    display_df["entry_price"] = display_df["entry_price"].round(2)
if "exit_price" in display_df.columns:
    display_df["exit_price"] = display_df["exit_price"].round(2)
if "side" in display_df.columns:
    display_df["side"] = display_df["side"].map({"long": "做多", "short": "做空"})
if "exit_reason" in display_df.columns:
    reason_map = {
        "stop_loss": "止損",
        "atr_trail": "ATR移動止損",
        "ema9_trail": "EMA9移動止損",
        "fixed_2atr": "固定止盈(2xATR)",
        "end_of_data": "資料結束",
    }
    display_df["exit_reason"] = display_df["exit_reason"].map(
        lambda x: reason_map.get(x, x)
    )
if "tp1_done" in display_df.columns:
    display_df["tp1_done"] = display_df["tp1_done"].map({True: "是", False: "否"})

display_df.rename(columns=col_rename, inplace=True)

st.dataframe(display_df, use_container_width=True, height=400)

# ── 自動更新 ──────────────────────────────────────────────────
if auto_refresh:
    import time
    time.sleep(30)
    st.experimental_rerun()
