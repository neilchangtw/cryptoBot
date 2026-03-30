"""
Plotly 圖表模組
- K線圖 + 進出場標記
- PnL 累積曲線
- 多空損益比較
"""
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def candlestick_with_trades(df_ohlcv: pd.DataFrame, trades_df: pd.DataFrame,
                            days: int = 30) -> go.Figure:
    """
    BTC K線圖 + 進出場標記。
    """
    if df_ohlcv.empty:
        return go.Figure()

    # 截取最近 N 天
    if days and days < 999:
        cutoff = df_ohlcv.index[-1] - pd.Timedelta(days=days)
        df = df_ohlcv[df_ohlcv.index >= cutoff].copy()
    else:
        df = df_ohlcv.copy()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03,
                        row_heights=[0.8, 0.2])

    # K線
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        name="BTC/USDT",
    ), row=1, col=1)

    # 成交量
    colors = ["#26a69a" if c >= o else "#ef5350"
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], marker_color=colors,
        opacity=0.5, name="成交量", showlegend=False,
    ), row=2, col=1)

    if not trades_df.empty:
        # 篩選時間範圍內的交易
        t = trades_df.copy()
        if "entry_time" in t.columns:
            if days and days < 999:
                t = t[t["entry_time"] >= cutoff]

        # 多單進場 (綠色三角形)
        longs = t[t["side"] == "long"]
        if not longs.empty:
            fig.add_trace(go.Scatter(
                x=longs["entry_time"], y=longs["entry_price"],
                mode="markers",
                marker=dict(symbol="triangle-up", size=12, color="#00c853",
                            line=dict(width=1, color="white")),
                name="做多進場",
                hovertemplate="做多進場<br>%{x}<br>$%{y:,.2f}<extra></extra>",
            ), row=1, col=1)

        # 空單進場 (紅色三角形)
        shorts = t[t["side"] == "short"]
        if not shorts.empty:
            fig.add_trace(go.Scatter(
                x=shorts["entry_time"], y=shorts["entry_price"],
                mode="markers",
                marker=dict(symbol="triangle-down", size=12, color="#ff1744",
                            line=dict(width=1, color="white")),
                name="做空進場",
                hovertemplate="做空進場<br>%{x}<br>$%{y:,.2f}<extra></extra>",
            ), row=1, col=1)

        # 出場（依盈虧上色）
        if "exit_time" in t.columns and "exit_price" in t.columns:
            win_exits = t[t["pnl"] > 0]
            loss_exits = t[t["pnl"] <= 0]

            if not win_exits.empty:
                fig.add_trace(go.Scatter(
                    x=win_exits["exit_time"], y=win_exits["exit_price"],
                    mode="markers",
                    marker=dict(symbol="x", size=10, color="#00c853",
                                line=dict(width=2, color="#00c853")),
                    name="獲利出場",
                    hovertemplate=(
                        "獲利出場<br>%{x}<br>$%{y:,.2f}"
                        "<br>損益: $%{customdata:.2f}<extra></extra>"
                    ),
                    customdata=win_exits["pnl"],
                ), row=1, col=1)

            if not loss_exits.empty:
                fig.add_trace(go.Scatter(
                    x=loss_exits["exit_time"], y=loss_exits["exit_price"],
                    mode="markers",
                    marker=dict(symbol="x", size=10, color="#ff1744",
                                line=dict(width=2, color="#ff1744")),
                    name="虧損出場",
                    hovertemplate=(
                        "虧損出場<br>%{x}<br>$%{y:,.2f}"
                        "<br>損益: $%{customdata:.2f}<extra></extra>"
                    ),
                    customdata=loss_exits["pnl"],
                ), row=1, col=1)

    fig.update_layout(
        title="BTC/USDT K線圖",
        template="plotly_dark",
        height=600,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=20, t=60, b=20),
    )
    fig.update_yaxes(title_text="價格 (USDT)", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig


def equity_curve(trades_df: pd.DataFrame) -> go.Figure:
    """PnL 累積曲線"""
    if trades_df.empty:
        return go.Figure()

    t = trades_df.sort_values("exit_time").copy()
    t["cum_pnl"] = t["pnl"].cumsum()

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=t["exit_time"], y=t["cum_pnl"],
        mode="lines",
        line=dict(color="#2196f3", width=2),
        fill="tozeroy",
        fillcolor="rgba(33,150,243,0.15)",
        name="累積損益",
        hovertemplate="累積損益: $%{y:,.2f}<br>%{x}<extra></extra>",
    ))

    # 零線
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

    # 標記最大回撤區間
    cum = t["cum_pnl"].values
    peak = pd.Series(cum).cummax().values
    dd = cum - peak
    worst_idx = dd.argmin()
    peak_idx = pd.Series(cum[:worst_idx + 1]).idxmax()

    if dd[worst_idx] < 0:
        fig.add_vrect(
            x0=t["exit_time"].iloc[peak_idx], x1=t["exit_time"].iloc[worst_idx],
            fillcolor="rgba(255,23,68,0.1)", line_width=0,
            annotation_text=f"最大回撤: ${dd[worst_idx]:.2f}",
            annotation_position="top left",
        )

    fig.update_layout(
        title="損益累積曲線",
        template="plotly_dark",
        height=350,
        yaxis_title="累積損益 (USDT)",
        margin=dict(l=60, r=20, t=60, b=20),
    )
    return fig


def pnl_by_side(trades_df: pd.DataFrame) -> go.Figure:
    """多空損益分佈"""
    if trades_df.empty:
        return go.Figure()

    fig = go.Figure()

    for side, color, name in [("long", "#00c853", "做多"),
                               ("short", "#ff1744", "做空")]:
        subset = trades_df[trades_df["side"] == side]
        if not subset.empty:
            fig.add_trace(go.Histogram(
                x=subset["pnl"], nbinsx=50,
                marker_color=color, opacity=0.7,
                name=name,
            ))

    fig.update_layout(
        title="單筆損益分佈",
        template="plotly_dark",
        height=300,
        barmode="overlay",
        xaxis_title="單筆損益 (USDT)",
        yaxis_title="次數",
        margin=dict(l=60, r=20, t=60, b=20),
    )
    return fig


def exit_reason_chart(trades_df: pd.DataFrame) -> go.Figure:
    """出場原因分佈"""
    if trades_df.empty or "exit_reason" not in trades_df.columns:
        return go.Figure()

    # 先翻譯出場原因
    reason_map = {
        "stop_loss": "止損",
        "atr_trail": "ATR移動止損",
        "ema9_trail": "EMA9移動止損",
        "fixed_2atr": "固定止盈(2xATR)",
        "end_of_data": "資料結束",
    }
    df = trades_df.copy()
    df["exit_reason_zh"] = df["exit_reason"].map(lambda x: reason_map.get(x, x))

    stats = df.groupby("exit_reason_zh").agg(
        count=("pnl", "count"),
        total_pnl=("pnl", "sum"),
    ).sort_values("total_pnl", ascending=True)

    colors = ["#00c853" if v > 0 else "#ff1744" for v in stats["total_pnl"]]

    fig = go.Figure(go.Bar(
        x=stats["total_pnl"], y=stats.index,
        orientation="h",
        marker_color=colors,
        text=[f"{c} 筆" for c in stats["count"]],
        textposition="auto",
        hovertemplate="%{y}<br>損益: $%{x:,.2f}<br>%{text}<extra></extra>",
    ))

    fig.update_layout(
        title="出場原因損益",
        template="plotly_dark",
        height=300,
        xaxis_title="總損益 (USDT)",
        margin=dict(l=120, r=20, t=60, b=20),
    )
    return fig
