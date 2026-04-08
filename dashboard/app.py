"""
CryptoBot Dashboard — FastAPI 後端 + PyWebView 桌面視窗

純看盤模式：只讀取 CSV / eth_state.json，不控制機器人。
支援 Paper / Live 帳戶切換（?mode=paper|live）。
"""
import sys
import os
import json
import math
import time
import threading
from pathlib import Path

# 加入專案根目錄，讓 import data_feed / strategy / check_health 可用
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import numpy as np
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI(title="CryptoBot Dashboard")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 路徑切換 (Paper / Live)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_paths(mode: str = "paper"):
    """回傳該模式的 state 檔 + 資料目錄"""
    if mode == "live":
        return {
            "state": ROOT_DIR / "eth_state_live.json",
            "data_dir": ROOT_DIR / "data_live",
        }
    return {
        "state": ROOT_DIR / "eth_state.json",
        "data_dir": ROOT_DIR / "data",
    }


def read_csv_safe(filepath, **kwargs):
    """安全讀取 CSV，檔案不存在或出錯時回傳空 DataFrame"""
    try:
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return pd.read_csv(filepath, **kwargs)
    except Exception:
        pass
    return pd.DataFrame()


def clean_value(v):
    """把 NaN / Inf 轉成 None（JSON 不接受）"""
    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def df_to_records(df):
    """DataFrame → list of dict，清理 NaN"""
    records = df.to_dict("records")
    return [{k: clean_value(v) for k, v in row.items()} for row in records]


def utc8_to_ts(dt_str):
    """UTC+8 時間字串 → epoch seconds（不轉 UTC，讓圖表直接顯示 UTC+8 時間）"""
    try:
        import calendar
        dt = pd.Timestamp(dt_str)
        return int(calendar.timegm(dt.timetuple()))
    except Exception:
        return 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API 端點
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status")
async def api_status(mode: str = Query("paper")):
    """即時狀態：餘額、持倉、今日 PnL、GK、健康度"""
    paths = get_paths(mode)
    result = {
        "mode": mode,
        "account_balance": 0,
        "bar_counter": 0,
        "last_bar_time": None,
        "positions": {"total": 0, "long_count": 0, "short_count": 0, "details": []},
        "today_pnl": 0,
        "today_trades": 0,
        "today_wins": 0,
        "today_losses": 0,
        "gk_pctile": None,
        "last_close": None,
        "health": None,
    }

    # 讀 state
    state_path = paths["state"]
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            result["account_balance"] = state.get("account_balance", 0)
            result["bar_counter"] = state.get("bar_counter", 0)
            result["last_bar_time"] = state.get("last_bar_time")

            # 持倉
            positions = state.get("positions", {})
            details = []
            for tid, pos in positions.items():
                details.append({
                    "trade_id": tid,
                    "side": pos.get("side"),
                    "sub_strategy": pos.get("sub_strategy"),
                    "entry_price": pos.get("entry_price"),
                    "entry_time_utc8": pos.get("entry_time_utc8"),
                    "bars_held": pos.get("bars_held", 0),
                })
            l_count = sum(1 for d in details if d["sub_strategy"] == "L")
            s_count = sum(1 for d in details if (d.get("sub_strategy") or "").startswith("S"))
            result["positions"] = {
                "total": len(details),
                "long_count": l_count,
                "short_count": s_count,
                "details": details,
            }

            # 今日統計
            daily = state.get("daily_stats", {})
            today_key = max(daily.keys()) if daily else None
            if today_key and today_key in daily:
                d = daily[today_key]
                result["today_pnl"] = d.get("pnl", 0)
                result["today_trades"] = d.get("trades_opened", 0)
                result["today_wins"] = d.get("wins", 0)
                result["today_losses"] = d.get("losses", 0)
        except Exception:
            pass

    # 最新 GK（從 bar_snapshots，每小時更新）
    snap_csv = paths["data_dir"] / "bar_snapshots.csv"
    snap_df = read_csv_safe(snap_csv)
    if len(snap_df) > 0:
        last = snap_df.iloc[-1]
        result["gk_pctile"] = clean_value(last.get("gk_pctile"))

    # 最新價格（Binance ticker API，即時更新）
    try:
        import requests
        resp = requests.get("https://fapi.binance.com/fapi/v2/ticker/price",
                            params={"symbol": "ETHUSDT"}, timeout=5)
        if resp.ok:
            result["last_close"] = round(float(resp.json().get("price", 0)), 2)
    except Exception:
        if len(snap_df) > 0:
            result["last_close"] = clean_value(snap_df.iloc[-1].get("close"))

    # 健康度
    try:
        import check_health
        health = check_health.check_health(days=30)
        result["health"] = health
    except Exception:
        result["health"] = {"overall": "UNKNOWN", "checks": []}

    return result


@app.get("/api/klines")
async def api_klines(limit: int = Query(1500, ge=50, le=1500)):
    """K 線 + EMA20 + GK pctile（不分 mode，同一個市場）"""
    import data_feed
    import strategy

    eth_df = data_feed.fetch_klines("ETHUSDT", "1h", limit)
    df = strategy.compute_indicators(eth_df)

    candles = []
    ema20 = []
    gk_pctile = []

    for i in range(len(df)):
        row = df.iloc[i]
        ts = utc8_to_ts(row["datetime"])
        if ts <= 0:
            continue
        candles.append({
            "time": ts,
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        })
        e = clean_value(row.get("ema20"))
        if e is not None:
            ema20.append({"time": ts, "value": round(e, 2)})
        g = clean_value(row.get("gk_pctile"))
        if g is not None:
            gk_pctile.append({"time": ts, "value": round(g, 2)})

    return {"candles": candles, "ema20": ema20, "gk_pctile": gk_pctile}


@app.get("/api/trades")
async def api_trades(mode: str = Query("paper")):
    """全部交易記錄"""
    paths = get_paths(mode)
    csv_path = paths["data_dir"] / "trades.csv"
    df = read_csv_safe(csv_path)

    if len(df) == 0:
        return {"trades": [], "total": 0}

    # 補 sub_strategy 空值
    if "sub_strategy" in df.columns:
        mask = df["sub_strategy"].isna() | (df["sub_strategy"] == "")
        if "direction" in df.columns:
            df.loc[mask & (df["direction"] == "LONG"), "sub_strategy"] = "L"
            df.loc[mask & (df["direction"] == "SHORT"), "sub_strategy"] = "S1"

    # 加 timestamp 欄位給圖表標記用
    trades = []
    for _, row in df.iterrows():
        t = {k: clean_value(v) for k, v in row.items()}
        t["entry_ts"] = utc8_to_ts(row.get("entry_time_utc8", ""))
        t["exit_ts"] = utc8_to_ts(row.get("exit_time_utc8", ""))
        trades.append(t)

    # 按進場時間倒序
    trades.sort(key=lambda x: x.get("entry_ts", 0), reverse=True)
    return {"trades": trades, "total": len(trades)}


@app.get("/api/daily")
async def api_daily(mode: str = Query("paper")):
    """每日彙總"""
    paths = get_paths(mode)
    csv_path = paths["data_dir"] / "daily_summary.csv"
    df = read_csv_safe(csv_path)

    if len(df) == 0:
        return {"daily": []}

    return {"daily": df_to_records(df)}


@app.get("/api/analytics")
async def api_analytics(mode: str = Query("paper")):
    """收益統計"""
    paths = get_paths(mode)
    csv_path = paths["data_dir"] / "trades.csv"
    df = read_csv_safe(csv_path)

    result = {
        "total_pnl": 0,
        "total_trades": 0,
        "win_rate": 0,
        "profit_factor": 0,
        "avg_hold_bars": 0,
        "cumulative_equity": [],
        "exit_distribution": {},
        "strategy_comparison": {},
    }

    if len(df) == 0:
        return result

    # 只算有 exit 的交易
    closed = df[df["net_pnl_usd"].notna() & (df["net_pnl_usd"] != "")].copy()
    if len(closed) == 0:
        result["total_trades"] = len(df)
        return result

    closed["net_pnl_usd"] = pd.to_numeric(closed["net_pnl_usd"], errors="coerce").fillna(0)
    closed["hold_bars"] = pd.to_numeric(closed.get("hold_bars", pd.Series(dtype=float)), errors="coerce").fillna(0)

    total_trades = len(closed)
    wins = closed[closed["net_pnl_usd"] > 0]
    losses = closed[closed["net_pnl_usd"] < 0]
    total_pnl = float(closed["net_pnl_usd"].sum())
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    gross_wins = float(wins["net_pnl_usd"].sum()) if len(wins) > 0 else 0
    gross_losses = abs(float(losses["net_pnl_usd"].sum())) if len(losses) > 0 else 0
    pf = gross_wins / gross_losses if gross_losses > 0 else (999 if gross_wins > 0 else 0)
    avg_hold = float(closed["hold_bars"].mean())

    # 累計收益曲線
    cum_equity = []
    cum = 0
    for _, row in closed.iterrows():
        cum += float(row["net_pnl_usd"])
        ts = utc8_to_ts(row.get("exit_time_utc8", ""))
        if ts > 0:
            cum_equity.append({"time": ts, "value": round(cum, 2)})

    # 出場原因分佈
    exit_dist = {}
    if "exit_type" in closed.columns:
        for et in closed["exit_type"].dropna():
            et = str(et).strip()
            if et:
                exit_dist[et] = exit_dist.get(et, 0) + 1

    # L vs S 策略比較
    strat_comp = {}
    if "sub_strategy" in closed.columns:
        for sub in ["L", "S1", "S2", "S3", "S4"]:
            sub_df = closed[closed["sub_strategy"] == sub]
            if len(sub_df) > 0:
                sub_wins = sub_df[sub_df["net_pnl_usd"] > 0]
                strat_comp[sub] = {
                    "trades": len(sub_df),
                    "pnl": round(float(sub_df["net_pnl_usd"].sum()), 2),
                    "win_rate": round(len(sub_wins) / len(sub_df) * 100, 1),
                    "avg_pnl": round(float(sub_df["net_pnl_usd"].mean()), 2),
                }

    result.update({
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "avg_hold_bars": round(avg_hold, 1),
        "cumulative_equity": cum_equity,
        "exit_distribution": exit_dist,
        "strategy_comparison": strat_comp,
    })
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 啟動
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def start_server(port=8050):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def kill_port(port):
    """啟動前先殺掉佔用同一 port 的舊進程"""
    import subprocess
    try:
        out = subprocess.check_output(
            f'netstat -aon | findstr :{port} | findstr LISTENING',
            shell=True, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            pid = line.strip().split()[-1]
            if pid.isdigit() and int(pid) != os.getpid():
                subprocess.call(f'taskkill /F /PID {pid}', shell=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


if __name__ == "__main__":
    import webview

    port = 8050
    kill_port(port)
    time.sleep(0.5)

    server = threading.Thread(target=start_server, args=(port,), daemon=True)
    server.start()

    # 等 server 真正就緒再開視窗
    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)

    webview.create_window(
        "印鈔機監控台",
        f"http://127.0.0.1:{port}",
        width=1400,
        height=900,
        min_size=(1100, 700),
    )
    webview.start()
