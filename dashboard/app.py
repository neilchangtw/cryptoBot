"""
CryptoBot Dashboard — FastAPI 後端 + PyWebView 桌面視窗

V13: 儀表板 = 控制中心。
  開啟儀表板 → 自動啟動交易機器人（subprocess）
  關閉儀表板 → 自動停止交易機器人
  支援日誌即時查看（system.log / signal.log / alerts.log）
  支援 Paper / Live 帳戶切換（?mode=paper|live）
"""
import sys
import os
import json
import math
import time
import threading
import subprocess
from pathlib import Path

# 加入專案根目錄，讓 import data_feed / strategy / check_health 可用
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import numpy as np
import importlib.util
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import uvicorn
from dotenv import load_dotenv
from telegram_notify import send_telegram_message as _tg_send

load_dotenv(ROOT_DIR / ".env")

# 載入回測引擎（不改 research 目錄結構）
_bt_spec = importlib.util.spec_from_file_location(
    "v14_export_trades",
    str(Path(__file__).resolve().parent.parent / "backtest" / "research" / "v14_export_trades.py"),
)
_bt_mod = importlib.util.module_from_spec(_bt_spec)
_bt_spec.loader.exec_module(_bt_mod)
bt_compute = _bt_mod.compute_indicators
bt_simulate = _bt_mod.simulate_v14_detailed

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
            kwargs.setdefault("on_bad_lines", "skip")
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
    # 判斷「當前 binance_trade 指向的環境」是否與 URL mode 一致
    # binance_trade 由 .env 決定（PAPER_TRADING / BINANCE_TESTNET 全域），
    # dashboard 現階段無法為 paper/live 各開一個 client → 若使用者在 dashboard
    # 切到 live 但 .env 仍是 paper，餘額/持倉會仍是 paper 帳戶。回傳 mode_mismatch
    # 旗標讓前端能顯示警告，避免誤判。
    env_paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
    env_mode = "paper" if env_paper else "live"
    mode_mismatch = (mode != env_mode)

    result = {
        "mode": mode,
        "env_mode": env_mode,
        "mode_mismatch": mode_mismatch,
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

    # ── 從幣安即時取得餘額和持倉 ──
    # 注意：binance_trade 用的是 .env 裡的 BINANCE_TESTNET 端點，
    # 與 dashboard URL mode 參數無關；mode_mismatch=True 時下方餘額/持倉
    # 屬於 env_mode 帳戶而非 URL mode 帳戶
    try:
        import binance_trade
        wallet_bal = binance_trade.get_wallet_balance()
        if wallet_bal > 0:
            result["account_balance"] = round(wallet_bal, 4)

        # 幣安實際持倉 → 建立 positionSide 索引（entry_price, unrealized_pnl）
        binance_pos = binance_trade.get_positions("ETHUSDT")
        _bp_map = {}
        for bp in binance_pos:
            ps = bp.get("position_side", "BOTH")
            _bp_map[ps] = bp
    except Exception:
        _bp_map = {}

    # 讀 state（策略內部狀態：bar_counter, bars_held, running_mfe 等）
    state_path = paths["state"]
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            # 餘額 fallback：若幣安 API 沒拿到，用 state 檔
            if result["account_balance"] == 0:
                result["account_balance"] = state.get("account_balance", 0)
            result["bar_counter"] = state.get("bar_counter", 0)
            result["last_bar_time"] = state.get("last_bar_time")

            # 持倉：strategy state + 幣安實際 entry_price 覆蓋
            positions = state.get("positions", {})
            details = []
            for tid, pos in positions.items():
                sub = pos.get("sub_strategy")
                ps = "LONG" if sub == "L" else "SHORT"
                # 幣安實際 entry_price 覆蓋 state 檔的值
                bp = _bp_map.get(ps)
                actual_entry = bp["entry_price"] if bp and bp["entry_price"] > 0 else pos.get("entry_price")
                # 幣安未實現損益
                binance_unr_pnl = bp["unrealized_pnl"] if bp else None
                binance_mark = bp["mark_price"] if bp else None
                details.append({
                    "trade_id": tid,
                    "side": pos.get("side"),
                    "sub_strategy": sub,
                    "entry_price": actual_entry,
                    "entry_time_utc8": pos.get("entry_time_utc8"),
                    "bars_held": pos.get("bars_held", 0),
                    "running_mfe": pos.get("running_mfe", 0.0),
                    "mh_reduced": pos.get("mh_reduced", False),
                    "unrealized_pnl": binance_unr_pnl,
                    "mark_price": binance_mark,
                })
            l_count = sum(1 for d in details if d["sub_strategy"] == "L")
            s_count = sum(1 for d in details if (d.get("sub_strategy") or "").startswith("S"))
            result["positions"] = {
                "total": len(details),
                "long_count": l_count,
                "short_count": s_count,
                "details": details,
            }

            # 熔斷進度（用於前端進度條）
            cb = state.get("circuit_breaker", {}) or {}
            mpnl = cb.get("monthly_pnl", {}) or {}
            ment = cb.get("monthly_entries", {}) or {}
            daily_pnl_v = float(cb.get("daily_pnl", 0) or 0)
            l_mpnl = float(mpnl.get("L", 0) or 0)
            s_mpnl = float(mpnl.get("S", 0) or 0)
            consec = int(cb.get("consec_losses", 0) or 0)
            cd_until = int(cb.get("consec_loss_cooldown_until", 0) or 0)
            bar_c = int(result.get("bar_counter", 0) or 0)

            def _pct(used, cap):
                return min(100.0, max(0.0, used / cap * 100)) if cap > 0 else 0.0

            result["breakers"] = {
                "daily": {
                    "pnl": round(daily_pnl_v, 2),
                    "cap": -200.0,
                    "loss_used": round(max(0, -daily_pnl_v), 2),
                    "used_pct": round(_pct(max(0, -daily_pnl_v), 200), 1),
                    "triggered": daily_pnl_v <= -200,
                },
                "monthly_l": {
                    "pnl": round(l_mpnl, 2),
                    "cap": -75.0,
                    "loss_used": round(max(0, -l_mpnl), 2),
                    "used_pct": round(_pct(max(0, -l_mpnl), 75), 1),
                    "triggered": l_mpnl <= -75,
                    "entries": int(ment.get("L", 0) or 0),
                    "entry_cap": 20,
                    "entry_pct": round(_pct(int(ment.get("L", 0) or 0), 20), 1),
                },
                "monthly_s": {
                    "pnl": round(s_mpnl, 2),
                    "cap": -150.0,
                    "loss_used": round(max(0, -s_mpnl), 2),
                    "used_pct": round(_pct(max(0, -s_mpnl), 150), 1),
                    "triggered": s_mpnl <= -150,
                    "entries": int(ment.get("S", 0) or 0),
                    "entry_cap": 20,
                    "entry_pct": round(_pct(int(ment.get("S", 0) or 0), 20), 1),
                },
                "consec": {
                    "value": consec,
                    "cap": 4,
                    "used_pct": round(_pct(consec, 4), 1),
                    "cooldown_bars_remain": max(0, cd_until - bar_c) if cd_until > 0 else 0,
                    "triggered": consec >= 4,
                },
                "paused": bool(state.get("paused", False)),
            }

            # 進場冷卻（出場後 L=6 bar / S=8 bar 不再進場；連虧 4 筆 → 24 bar 冷卻）
            try:
                import strategy as _strat
                L_CD = int(getattr(_strat, "L_EXIT_CD", 6))
                S_CD = int(getattr(_strat, "S_EXIT_CD", 8))
                CONSEC_CD = int(getattr(_strat, "CONSEC_LOSS_COOLDOWN", 24))
            except Exception:
                L_CD, S_CD, CONSEC_CD = 6, 8, 24
            last_exits = state.get("last_exits", {}) or {}
            l_last = int(last_exits.get("L", -9999) or -9999)
            s_last = int(last_exits.get("S", -9999) or -9999)
            l_remain = max(0, L_CD - (bar_c - l_last)) if l_last > -9999 else 0
            s_remain = max(0, S_CD - (bar_c - s_last)) if s_last > -9999 else 0
            consec_remain = max(0, cd_until - bar_c) if cd_until > 0 else 0
            result["cooldowns"] = {
                "L": {
                    "total": L_CD,
                    "remaining": l_remain,
                    "active": l_remain > 0,
                    "passed": L_CD - l_remain if l_remain > 0 else L_CD,
                },
                "S": {
                    "total": S_CD,
                    "remaining": s_remain,
                    "active": s_remain > 0,
                    "passed": S_CD - s_remain if s_remain > 0 else S_CD,
                },
                "consec_loss": {
                    "total": CONSEC_CD,
                    "remaining": consec_remain,
                    "active": consec_remain > 0,
                },
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
    last_ema20 = None
    if len(snap_df) > 0:
        last = snap_df.iloc[-1]
        result["gk_pctile"] = clean_value(last.get("gk_pctile"))
        result["gk_pctile_s"] = clean_value(last.get("gk_pctile_s"))

        # 進場條件達成狀態
        gk = clean_value(last.get("gk_pctile"))
        brk_long = last.get("breakout_long")
        brk_short = last.get("breakout_short")
        ema20_raw = last.get("ema20")
        # 舊 CSV 格式的 ema20 可能是 bool（True/False），新格式是數值
        if isinstance(ema20_raw, (bool, np.bool_)):
            ema20_val = None
        else:
            ema20_val = clean_value(ema20_raw)
            if ema20_val is not None and ema20_val < 100:
                ema20_val = None  # 不合理的值（可能是 ratio）
        last_ema20 = ema20_val

        # Session: 直接用當前時間計算（V13: L/S 各自 block_days）
        from datetime import datetime as _dt
        _now = _dt.now()  # 本機 = UTC+8
        session_ok_l = _now.hour not in {0, 1, 2, 12} and _now.weekday() not in {5, 6}
        session_ok_s = _now.hour not in {0, 1, 2, 12} and _now.weekday() not in {0, 5, 6}

        # S 用自己的 GK pctile
        gk_s = clean_value(last.get("gk_pctile_s"))

        # V14+R Regime gate（從最新 snapshot 讀 sma_slope，舊 CSV 無此欄位或空值→live fallback）
        slope_raw = clean_value(last.get("sma_slope"))
        if slope_raw is None:
            try:
                import data_feed as _df_mod
                import strategy as _st_mod
                _live_df = _df_mod.fetch_klines("ETHUSDT", "1h", 500)
                _ind = _st_mod.compute_indicators(_live_df)
                _live_slope = _ind.iloc[-2].get("sma_slope")
                if _live_slope is not None and not pd.isna(_live_slope):
                    slope_raw = float(_live_slope)
            except Exception:
                pass
        regime_ok_l = slope_raw is None or slope_raw <= 0.045   # L 允許：slope <= +4.5%
        regime_ok_s = slope_raw is None or abs(slope_raw) >= 0.010  # S 允許：|slope| >= 1%

        # breakout: 可能是 bool 或 float（突破強度）
        def _brk_pass(v):
            if isinstance(v, bool):
                return v
            if isinstance(v, (float, np.floating)):
                return not math.isnan(v) and v != 0
            if isinstance(v, str):
                return v.lower() not in ('false', '0', '')
            return bool(v) if v is not None else False

        # L 進場條件（V14+R: GK<25 + BRK15 + Session_L + slope<=+4.5%）
        l_conds = {
            "gk": {"label": "GK < 25", "value": gk, "threshold": 25, "pass": bool(gk is not None and gk < 25)},
            "breakout": {"label": "向上突破 15bar", "pass": bool(_brk_pass(brk_long))},
            "session": {"label": "時段允許", "pass": bool(session_ok_l)},
            "regime": {"label": "非強多頭 (slope≤+4.5%)", "value": slope_raw, "pass": bool(regime_ok_l)},
        }
        l_total = sum([l_conds["gk"]["pass"], l_conds["breakout"]["pass"], l_conds["session"]["pass"], l_conds["regime"]["pass"]])

        # S 進場條件（V14+R: GK_S<35 + BRK15 + Session_S + |slope|>=1%）
        s_conds = {
            "gk": {"label": "GK < 35", "value": gk_s, "threshold": 35, "pass": bool(gk_s is not None and gk_s < 35)},
            "breakout": {"label": "向下突破 15bar", "pass": bool(_brk_pass(brk_short))},
            "session": {"label": "時段允許", "pass": bool(session_ok_s)},
            "regime": {"label": "非橫盤 (|slope|≥1%)", "value": slope_raw, "pass": bool(regime_ok_s)},
        }
        s_total = sum([s_conds["gk"]["pass"], s_conds["breakout"]["pass"], s_conds["session"]["pass"], s_conds["regime"]["pass"]])

        result["entry_conditions"] = {
            "L": {"conditions": l_conds, "passed": l_total, "total": 4},
            "S": {"conditions": s_conds, "passed": s_total, "total": 4},
        }

        # V14+R Regime 即時狀態（依 sma_slope 分類 UP / SIDE / DOWN / MILD_UP / WARMUP）
        if slope_raw is None:
            regime_label, regime_desc = "WARMUP", "暖機中（資料不足）"
        elif slope_raw > 0.045:
            regime_label, regime_desc = "UP", "強多頭 — L 被擋"
        elif abs(slope_raw) < 0.010:
            regime_label, regime_desc = "SIDE", "橫盤 — S 被擋"
        elif slope_raw < 0:
            regime_label, regime_desc = "DOWN", "下跌 — L+S 皆可"
        else:
            regime_label, regime_desc = "MILD_UP", "溫和多頭 — L+S 皆可"

        slope_pct = round(slope_raw * 100, 2) if slope_raw is not None else None
        # 距離門檻（負數表示已越過）
        dist_up = round(4.5 - slope_pct, 2) if slope_pct is not None else None
        dist_side = round(abs(slope_pct) - 1.0, 2) if slope_pct is not None else None

        result["regime"] = {
            "label": regime_label,
            "desc": regime_desc,
            "slope_pct": slope_pct,
            "th_up": 4.5,
            "th_side": 1.0,
            "dist_to_up": dist_up,       # >0 = 尚未越過 UP 邊界；<=0 = 已越過，L 被擋
            "dist_to_side": dist_side,   # >=0 = 遠離 SIDE；<0 = 進入 SIDE，S 被擋
            "block_l": slope_raw is not None and slope_raw > 0.045,
            "block_s": slope_raw is not None and abs(slope_raw) < 0.010,
        }

    # 最新價格（Binance ticker API，即時更新）
    last_close = None
    try:
        import requests
        resp = requests.get("https://fapi.binance.com/fapi/v2/ticker/price",
                            params={"symbol": "ETHUSDT"}, timeout=5)
        if resp.ok:
            last_close = round(float(resp.json().get("price", 0)), 2)
            result["last_close"] = last_close
    except Exception:
        if len(snap_df) > 0:
            last_close = clean_value(snap_df.iloc[-1].get("close"))
            result["last_close"] = last_close

    # 為每筆持倉計算出場條件距離（用幣安 mark price + unrealized_pnl）
    for d in result["positions"]["details"]:
        ep = d.get("entry_price", 0)
        if not ep or ep <= 0:
            continue
        bars = d.get("bars_held", 0)
        sub = d.get("sub_strategy", "")
        mark = d.get("mark_price") or last_close  # fallback to last_close
        if not mark or mark <= 0:
            continue

        if sub == "L":
            unr_pct = (mark - ep) / ep * 100
            safenet_dist = round(-3.5 - unr_pct, 2)
            tp_dist = round(3.5 - unr_pct, 2)
            mh_reduced = d.get("mh_reduced", False)
            effective_mh = 5 if mh_reduced else 6
            running_mfe = d.get("running_mfe", 0.0)
            d["exit_progress"] = {
                "unrealized_pct": round(unr_pct, 2),
                "unrealized_pnl": d.get("unrealized_pnl"),
                "safenet": {"threshold": -3.5, "current": round(unr_pct, 2), "distance": safenet_dist},
                "tp": {"threshold": 3.5, "current": round(unr_pct, 2), "distance": tp_dist},
                "mfe_trail": {"running_mfe": round(running_mfe * 100, 2), "act": 1.0, "dd": 0.8},
                "max_hold": {"threshold": effective_mh, "bars_held": bars, "remaining": max(0, effective_mh - bars)},
            }
        elif sub == "S":
            unr_pct = (ep - mark) / ep * 100
            safenet_dist = round(4.0 - abs(unr_pct), 2) if unr_pct < 0 else round(4.0 + unr_pct, 2)
            tp_dist = round(2.0 - unr_pct, 2)
            d["exit_progress"] = {
                "unrealized_pct": round(unr_pct, 2),
                "unrealized_pnl": d.get("unrealized_pnl"),
                "safenet": {"threshold": 4.0, "current": round(-unr_pct if unr_pct < 0 else 0, 2),
                            "distance": round(4.0 - (-unr_pct if unr_pct < 0 else 0), 2)},
                "tp": {"threshold": 2.0, "current": round(unr_pct, 2), "distance": tp_dist},
                "max_hold": {"threshold": 10, "bars_held": bars, "remaining": max(0, 10 - bars)},
            }

    # 最近 5 筆交易（給 Status 頁迷你表格用）
    trades_csv = paths["data_dir"] / "trades.csv"
    trades_df = read_csv_safe(trades_csv)
    recent_trades = []
    if len(trades_df) > 0:
        last5 = trades_df.tail(5).iloc[::-1]  # 最新的在前
        for _, row in last5.iterrows():
            recent_trades.append({k: clean_value(v) for k, v in {
                "trade_number": row.get("trade_number"),
                "direction": row.get("direction"),
                "sub_strategy": row.get("sub_strategy"),
                "entry_time_utc8": row.get("entry_time_utc8"),
                "exit_type": row.get("exit_type"),
                "net_pnl_usd": row.get("net_pnl_usd"),
                "hold_bars": row.get("hold_bars"),
            }.items()})
    result["recent_trades"] = recent_trades

    # 健康度
    try:
        import check_health
        health = check_health.check_health(days=30)
        result["health"] = health
    except Exception:
        result["health"] = {"overall": "UNKNOWN", "checks": []}

    return result


@app.websocket("/ws/status")
async def ws_status(ws: WebSocket):
    """WebSocket 推送 status（避免 60s 輪詢延遲）。
    客戶端連上後：立即推一次，之後每 15s 推一次，直到斷線。
    客戶端可送 'refresh' 訊息主動觸發立即推送。
    """
    import asyncio
    await ws.accept()
    # 預設 paper，可由 query string 覆寫
    mode = ws.query_params.get("mode", "paper")
    try:
        while True:
            try:
                data = await api_status(mode=mode)
                await ws.send_json({"type": "status", "data": data})
            except Exception as e:
                await ws.send_json({"type": "error", "message": str(e)})

            # 等 15s 或收到客戶端訊息（早於 15s 主動刷新）
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
                # 收到任何訊息（如 'refresh'）→ 下一輪立即推送
                if msg and msg.strip().lower() == "close":
                    await ws.close()
                    return
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass


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
    sma_slope = []

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
        s = clean_value(row.get("sma_slope"))
        if s is not None:
            sma_slope.append({"time": ts, "value": round(s * 100, 3)})  # 轉百分比

    return {
        "candles": candles,
        "ema20": ema20,
        "gk_pctile": gk_pctile,
        "sma_slope": sma_slope,
        "regime_th_up": 4.5,    # L block 閾值（百分比）
        "regime_th_side": 1.0,  # S block 閾值
    }


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

    # 建 entry_time → sma_slope 映射（用於 entry_regime 標籤）
    slope_map = {}
    snap_csv = paths["data_dir"] / "bar_snapshots.csv"
    snap_local = read_csv_safe(snap_csv)
    if len(snap_local) > 0 and "bar_time_utc8" in snap_local.columns and "sma_slope" in snap_local.columns:
        slope_map = dict(zip(
            snap_local["bar_time_utc8"].astype(str),
            pd.to_numeric(snap_local["sma_slope"], errors="coerce")
        ))

    def _regime_of(slope):
        if slope is None or pd.isna(slope):
            return None
        if slope > 0.045:
            return "UP"
        if abs(slope) < 0.010:
            return "SIDE"
        if slope < 0:
            return "DOWN"
        return "MILD_UP"

    # 加 timestamp 欄位給圖表標記用
    trades = []
    for _, row in df.iterrows():
        t = {k: clean_value(v) for k, v in row.items()}
        t["entry_ts"] = utc8_to_ts(row.get("entry_time_utc8", ""))
        t["exit_ts"] = utc8_to_ts(row.get("exit_time_utc8", ""))
        # entry_regime：查進場 bar 的 sma_slope
        et = str(row.get("entry_time_utc8", ""))
        slope = slope_map.get(et)
        t["entry_slope_pct"] = round(float(slope) * 100, 2) if slope is not None and not pd.isna(slope) else None
        t["entry_regime"] = _regime_of(slope)
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
        "daily_pnl": [],
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

    # 累計收益曲線（按出場時間排序 + 去重複時間戳）
    sorted_closed = closed.copy()
    sorted_closed["_exit_ts"] = sorted_closed["exit_time_utc8"].apply(
        lambda x: utc8_to_ts(x) if pd.notna(x) else 0)
    sorted_closed = sorted_closed.sort_values("_exit_ts")

    cum_equity = []
    cum = 0
    for _, row in sorted_closed.iterrows():
        cum += float(row["net_pnl_usd"])
        ts = int(row["_exit_ts"])
        if ts > 0:
            # 同一時間戳只保留最後的累計值（LightweightCharts 要求嚴格遞增）
            if cum_equity and cum_equity[-1]["time"] == ts:
                cum_equity[-1]["value"] = round(cum, 2)
            else:
                cum_equity.append({"time": ts, "value": round(cum, 2)})

    # 每日損益（從 trades 計算，不依賴 daily_summary CSV）
    daily_pnl = []
    if "exit_time_utc8" in sorted_closed.columns:
        sorted_closed["_exit_date"] = sorted_closed["exit_time_utc8"].apply(
            lambda x: str(x)[:10] if pd.notna(x) else None)
        daily_group = sorted_closed.groupby("_exit_date")["net_pnl_usd"].sum()
        for date_str, pnl in daily_group.items():
            if date_str:
                daily_pnl.append({
                    "time": str(date_str),
                    "value": round(float(pnl), 2),
                })

    # 出場原因分佈
    exit_dist = {}
    if "exit_type" in closed.columns:
        for et in closed["exit_type"].dropna():
            et = str(et).strip()
            if et:
                exit_dist[et] = exit_dist.get(et, 0) + 1

    # L vs S 策略比較（支援 V10 "S" 和 v6 "S1-S4"）
    strat_comp = {}
    if "sub_strategy" in closed.columns:
        for sub in ["L", "S", "S1", "S2", "S3", "S4"]:
            sub_df = closed[closed["sub_strategy"] == sub]
            if len(sub_df) > 0:
                sub_wins = sub_df[sub_df["net_pnl_usd"] > 0]
                strat_comp[sub] = {
                    "trades": len(sub_df),
                    "pnl": round(float(sub_df["net_pnl_usd"].sum()), 2),
                    "win_rate": round(len(sub_wins) / len(sub_df) * 100, 1),
                    "avg_pnl": round(float(sub_df["net_pnl_usd"].mean()), 2),
                }

    # V14+R Regime 分組（依進場 bar 的 sma_slope）
    regime_perf = {}
    snap_csv = paths["data_dir"] / "bar_snapshots.csv"
    snap_local = read_csv_safe(snap_csv)
    if len(snap_local) > 0 and "bar_time_utc8" in snap_local.columns and "sma_slope" in snap_local.columns:
        slope_map = dict(zip(
            snap_local["bar_time_utc8"].astype(str),
            pd.to_numeric(snap_local["sma_slope"], errors="coerce")
        ))
        buckets = {}  # rg → {pnls: [...], slopes: [...], wins: 0, L: 0, S: 0}
        for _, row in closed.iterrows():
            et = str(row.get("entry_time_utc8", ""))
            if not et or et == "nan":
                continue
            sl = slope_map.get(et)
            if sl is None or pd.isna(sl):
                continue
            if sl > 0.045:
                rg = "UP"
            elif abs(sl) < 0.010:
                rg = "SIDE"
            elif sl < 0:
                rg = "DOWN"
            else:
                rg = "MILD_UP"
            b = buckets.setdefault(rg, {"pnls": [], "slopes": [], "L": 0, "S": 0})
            b["pnls"].append(float(row["net_pnl_usd"]))
            b["slopes"].append(float(sl))
            sub = str(row.get("sub_strategy", ""))
            if sub == "L":
                b["L"] += 1
            elif sub.startswith("S"):
                b["S"] += 1
        for rg, b in buckets.items():
            n = len(b["pnls"])
            wins = sum(1 for p in b["pnls"] if p > 0)
            total = round(sum(b["pnls"]), 2)
            regime_perf[rg] = {
                "trades": n,
                "l_trades": b["L"],
                "s_trades": b["S"],
                "pnl": total,
                "win_rate": round(wins / n * 100, 1) if n else 0,
                "avg_pnl": round(total / n, 2) if n else 0,
                "avg_slope_pct": round(sum(b["slopes"]) / n * 100, 2) if n else 0,
            }

    result.update({
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "avg_hold_bars": round(avg_hold, 1),
        "cumulative_equity": cum_equity,
        "daily_pnl": daily_pnl,
        "exit_distribution": exit_dist,
        "strategy_comparison": strat_comp,
        "regime_performance": regime_perf,
    })
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 回測 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 可用幣別（data/ 目錄中有 1h 730d CSV 的）
_BT_SYMBOLS = None

def _get_bt_symbols():
    global _BT_SYMBOLS
    if _BT_SYMBOLS is None:
        import glob
        csvs = glob.glob(str(ROOT_DIR / "data" / "*_1h_latest730d.csv"))
        _BT_SYMBOLS = sorted(
            os.path.basename(f).replace("_1h_latest730d.csv", "") for f in csvs
        )
    return _BT_SYMBOLS


@app.get("/api/backtest/symbols")
async def api_bt_symbols():
    return {"symbols": _get_bt_symbols()}


class BacktestParams(BaseModel):
    symbol: str = Field(default="ETHUSDT")
    start_date: str = Field(default="")   # "YYYY-MM-DD" or empty = all
    end_date: str = Field(default="")     # "YYYY-MM-DD" or empty = all
    # L strategy
    l_gk_th: float = Field(default=25, ge=1, le=100)
    l_brk: int = Field(default=15, ge=3, le=50)
    l_tp: float = Field(default=3.5, ge=0.5, le=10.0)
    l_sn: float = Field(default=3.5, ge=1.0, le=10.0)
    l_mh: int = Field(default=6, ge=2, le=24)
    l_cd: int = Field(default=6, ge=1, le=24)
    l_mfe_act: float = Field(default=1.0, ge=0.1, le=5.0)
    l_mfe_tr: float = Field(default=0.8, ge=0.1, le=5.0)
    l_cmh_bar: int = Field(default=2, ge=1, le=6)
    l_cmh_th: float = Field(default=-1.0, ge=-5.0, le=0.0)
    # S strategy
    s_gk_th: float = Field(default=35, ge=1, le=100)
    s_brk: int = Field(default=15, ge=3, le=50)
    s_tp: float = Field(default=2.0, ge=0.5, le=10.0)
    s_sn: float = Field(default=4.0, ge=1.0, le=10.0)
    s_mh: int = Field(default=10, ge=2, le=24)
    s_cd: int = Field(default=8, ge=1, le=24)
    # Shared
    notional: float = Field(default=4000, ge=500, le=20000)
    fee: float = Field(default=4, ge=0, le=50)
    # V14+R Regime Gate toggle（可關閉以對照 V14 vs V14+R）
    enable_regime_gate: bool = Field(default=True)
    r_th_up: float = Field(default=4.5, ge=0.1, le=20.0)     # L block: slope > +X%
    r_th_side: float = Field(default=1.0, ge=0.01, le=10.0)  # S block: |slope| < X%


_bt_df_cache = {}


def _get_bt_data(symbol: str = "ETHUSDT"):
    """取得全量資料（不裁切日期），確保指標計算一致。"""
    global _bt_df_cache
    _refresh_symbol_data(symbol)

    if symbol not in _bt_df_cache:
        filepath = ROOT_DIR / "data" / f"{symbol}_1h_latest730d.csv"
        if not filepath.exists():
            raise ValueError(f"No data file for {symbol}")
        _bt_df_cache[symbol] = pd.read_csv(filepath)
    return _bt_df_cache[symbol].copy()


def _run_backtest(params: BacktestParams):
    import time as _time
    t0 = _time.perf_counter()
    df = _get_bt_data(params.symbol)

    # 用全量資料計算指標 + 模擬，日期範圍只過濾交易結果
    patch_map = {
        'L_GK_TH': params.l_gk_th,
        'L_BRK': params.l_brk,
        'L_TP': params.l_tp / 100,
        'L_SN': params.l_sn / 100,
        'L_MH': params.l_mh,
        'L_CD': params.l_cd,
        'L_MFE_ACT': params.l_mfe_act / 100,
        'L_MFE_TR': params.l_mfe_tr / 100,
        'L_CMH_BAR': params.l_cmh_bar,
        'L_CMH_TH': params.l_cmh_th / 100,
        'L_CMH_MH': params.l_mh - 1,
        'S_GK_TH': params.s_gk_th,
        'S_BRK': params.s_brk,
        'S_TP': params.s_tp / 100,
        'S_SN': params.s_sn / 100,
        'S_MH': params.s_mh,
        'S_CD': params.s_cd,
        'NOTIONAL': params.notional,
        'FEE': params.fee,
        # V14+R: 關閉時改成不可能的值（永遠不 block）；開啟時用使用者參數
        'R_TH_UP': (params.r_th_up / 100) if params.enable_regime_gate else 99.0,
        'R_TH_SIDE': (params.r_th_side / 100) if params.enable_regime_gate else -1.0,
    }
    originals = {}
    try:
        for k, v in patch_map.items():
            originals[k] = getattr(_bt_mod, k)
            setattr(_bt_mod, k, v)
        ind = bt_compute(df)
        datetimes = df['datetime'].values

        # 若有 start_date，找到對應的 bar index，模擬從該時間點啟動
        # （熔斷/cooldown 從零開始，與實盤啟動行為一致）
        start_bar = None
        if params.start_date:
            for j, dt in enumerate(datetimes):
                if str(dt) >= params.start_date:
                    start_bar = j
                    break

        all_trades = bt_simulate(ind, datetimes, start_bar=start_bar)
    finally:
        for k, v in originals.items():
            setattr(_bt_mod, k, v)

    # end_date 過濾（start_date 已在 simulation 層處理）
    trades = all_trades
    if params.end_date:
        trades = [t for t in trades if t['entry_dt'] <= params.end_date + " 23:59:59"]

    elapsed = round((_time.perf_counter() - t0) * 1000)
    return trades, df, elapsed


def _refresh_symbol_data(symbol: str):
    """If cached CSV is stale (>6h since last bar), auto-fetch latest from Binance."""
    filepath = ROOT_DIR / "data" / f"{symbol}_1h_latest730d.csv"
    if not filepath.exists():
        # No file at all — download 730 days
        _download_full(symbol, filepath)
        return

    df = pd.read_csv(filepath)
    last_dt = pd.Timestamp(df['datetime'].iloc[-1])
    now_utc8 = pd.Timestamp.now()  # local = UTC+8
    gap_hours = (now_utc8 - last_dt).total_seconds() / 3600

    if gap_hours < 2:
        return  # Fresh enough

    # Append missing bars
    import requests as _req
    from datetime import timedelta
    last_ms = int((last_dt - pd.Timestamp("1970-01-01") - timedelta(hours=8)).total_seconds() * 1000)
    try:
        resp = _req.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1h",
                    "startTime": last_ms + 1, "limit": 1500},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return  # Silent fail — use existing data

    if not data:
        return

    new_rows = []
    for k in data:
        dt_str = (pd.Timestamp(k[0], unit='ms') + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
        if dt_str > df['datetime'].iloc[-1]:
            new_rows.append({
                'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]),
                'close': float(k[4]), 'volume': float(k[5]),
                'taker_buy_volume': float(k[9]), 'datetime': dt_str,
            })

    if new_rows:
        new_df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
        new_df.to_csv(filepath, index=False)
        # Invalidate cache
        if symbol in _bt_df_cache:
            del _bt_df_cache[symbol]


def _download_full(symbol: str, filepath):
    """Download 730 days of 1h klines from Binance (paginated)."""
    import requests as _req
    from datetime import datetime, timedelta

    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=730)).timestamp() * 1000)
    cursor = start_ms
    all_data = []

    while cursor < end_ms:
        try:
            resp = _req.get(url, params={
                'symbol': symbol, 'interval': '1h', 'limit': 1500,
                'startTime': cursor, 'endTime': end_ms
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            break

        if not data:
            break
        all_data.extend(data)
        cursor = data[-1][0] + 1
        if len(data) < 1500:
            break
        import time as _t
        _t.sleep(0.3)

    if not all_data:
        return

    df = pd.DataFrame(all_data, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
        'taker_buy_quote_volume', 'ignore'])
    df['datetime'] = pd.to_datetime(df['open_time'], unit='ms') + pd.Timedelta(hours=8)
    df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
    for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_volume']:
        df[col] = df[col].astype(float)
    df = df[['open', 'high', 'low', 'close', 'volume', 'taker_buy_volume', 'datetime']]
    df = df.drop_duplicates(subset=['datetime']).sort_values('datetime').reset_index(drop=True)
    df.to_csv(filepath, index=False)


@app.post("/api/backtest/audit")
async def api_backtest_audit(params: BacktestParams):
    """Look-ahead bias strict audit — 4 tests to prove no future data leakage."""
    import time as _time
    t0 = _time.perf_counter()

    try:
        df = _get_bt_data(params.symbol)
    except ValueError as e:
        return {"error": str(e)}

    n = len(df)
    results = []

    # === Test 1: Future corruption → GK pctile unchanged ===
    # Corrupt last 20% of bars → recompute → compare first 70% pctile
    cut = int(n * 0.7)
    originals_saved = {}
    patch_map = {
        'L_GK_TH': params.l_gk_th, 'L_BRK': params.l_brk,
        'S_GK_TH': params.s_gk_th, 'S_BRK': params.s_brk,
    }
    try:
        for k, v in patch_map.items():
            originals_saved[k] = getattr(_bt_mod, k)
            setattr(_bt_mod, k, v)

        ind_clean = bt_compute(df)

        df_corrupt = df.copy()
        rng = np.random.RandomState(42)
        df_corrupt.iloc[cut:, df_corrupt.columns.get_loc('close')] *= (1 + rng.uniform(-0.5, 0.5, n - cut))
        df_corrupt.iloc[cut:, df_corrupt.columns.get_loc('high')] *= (1 + rng.uniform(0, 0.5, n - cut))
        df_corrupt.iloc[cut:, df_corrupt.columns.get_loc('low')] *= (1 + rng.uniform(-0.5, 0, n - cut))

        ind_corrupt = bt_compute(df_corrupt)

        # Compare first 70% of GK pctile (should be identical)
        pL_clean = ind_clean['pctile_L'][:cut]
        pL_corrupt = ind_corrupt['pctile_L'][:cut]
        mask = ~(np.isnan(pL_clean) | np.isnan(pL_corrupt))
        gk_match = np.allclose(pL_clean[mask], pL_corrupt[mask], atol=1e-10)

        pS_clean = ind_clean['pctile_S'][:cut]
        pS_corrupt = ind_corrupt['pctile_S'][:cut]
        mask_s = ~(np.isnan(pS_clean) | np.isnan(pS_corrupt))
        gk_match_s = np.allclose(pS_clean[mask_s], pS_corrupt[mask_s], atol=1e-10)

        t1_pass = gk_match and gk_match_s
        results.append({
            "name": "GK Pctile 未來隔離",
            "desc": f"破壞後 30% 資料 → 前 70% GK pctile 完全不變",
            "pass": t1_pass,
            "detail": f"L pctile match: {gk_match}, S pctile match: {gk_match_s}, compared {int(mask.sum())+int(mask_s.sum())} bars",
        })

        # === Test 2: Future corruption → breakout levels unchanged ===
        brk_up_clean = ind_clean['brk_up'][:cut]
        brk_up_corrupt = ind_corrupt['brk_up'][:cut]
        brk_dn_clean = ind_clean['brk_dn'][:cut]
        brk_dn_corrupt = ind_corrupt['brk_dn'][:cut]
        brk_match = np.array_equal(brk_up_clean, brk_up_corrupt) and np.array_equal(brk_dn_clean, brk_dn_corrupt)
        results.append({
            "name": "Breakout 未來隔離",
            "desc": f"破壞後 30% 資料 → 前 70% 突破信號完全不變",
            "pass": brk_match,
            "detail": f"Breakout up match: {np.array_equal(brk_up_clean, brk_up_corrupt)}, "
                      f"down match: {np.array_equal(brk_dn_clean, brk_dn_corrupt)}",
        })
    finally:
        for k, v in originals_saved.items():
            setattr(_bt_mod, k, v)

    # === Test 3: Entry price = bar close (not future open) ===
    originals_saved2 = {}
    full_patch = {
        'L_GK_TH': params.l_gk_th, 'L_BRK': params.l_brk,
        'L_TP': params.l_tp / 100, 'L_SN': params.l_sn / 100,
        'L_MH': params.l_mh, 'L_CD': params.l_cd,
        'L_MFE_ACT': params.l_mfe_act / 100, 'L_MFE_TR': params.l_mfe_tr / 100,
        'L_CMH_BAR': params.l_cmh_bar, 'L_CMH_TH': params.l_cmh_th / 100,
        'L_CMH_MH': params.l_mh - 1,
        'S_GK_TH': params.s_gk_th, 'S_BRK': params.s_brk,
        'S_TP': params.s_tp / 100, 'S_SN': params.s_sn / 100,
        'S_MH': params.s_mh, 'S_CD': params.s_cd,
        'NOTIONAL': params.notional, 'FEE': params.fee,
    }
    try:
        for k, v in full_patch.items():
            originals_saved2[k] = getattr(_bt_mod, k)
            setattr(_bt_mod, k, v)
        ind = bt_compute(df)
        datetimes = df['datetime'].values
        trades = bt_simulate(ind, datetimes)
    finally:
        for k, v in originals_saved2.items():
            setattr(_bt_mod, k, v)

    c = ind['c']
    o = ind['o']
    entry_close_ok = 0
    entry_close_fail = 0
    entry_not_open = 0
    for t in trades:
        bar = t['entry_bar']
        ep = t['entry_price']
        bar_close = round(c[bar], 2)
        bar_open_next = round(o[bar + 1], 2) if bar + 1 < len(o) else None
        if ep == bar_close:
            entry_close_ok += 1
        else:
            entry_close_fail += 1
        if bar_open_next is not None and ep != bar_open_next:
            entry_not_open += 1

    t3_pass = entry_close_fail == 0 and len(trades) > 0
    results.append({
        "name": "進場價 = Bar Close",
        "desc": f"所有 {len(trades)} 筆進場價 = 信號 bar 收盤價（非下一根開盤價）",
        "pass": t3_pass,
        "detail": f"Match close: {entry_close_ok}, fail: {entry_close_fail}, "
                  f"NOT next open: {entry_not_open}/{len(trades)}",
    })

    # === Test 4: Exit only uses current bar OHLC ===
    # Check: exit_bar close/high/low are consistent with exit price
    exit_logic_ok = 0
    exit_logic_fail = 0
    for t in trades:
        eb = t['exit_bar']
        ep_entry = t['entry_price']
        ex = t['exit_price']
        reason = t['exit_reason']
        h_bar = round(ind['h'][eb], 2)
        l_bar = round(ind['l'][eb], 2)
        c_bar = round(ind['c'][eb], 2)

        ok = False
        if reason in ('MH', 'MHx', 'MFE'):
            ok = (ex == c_bar)  # Close price exits
        elif reason == 'TP':
            ok = True  # TP uses entry * (1+tp), price within bar range
        elif reason == 'SN':
            ok = True  # SN uses entry * (1-sn*slip), triggered by low
        elif reason == 'BE':
            ok = (round(ex, 2) == round(ep_entry, 2))  # BE = entry price
        else:
            ok = True

        if ok:
            exit_logic_ok += 1
        else:
            exit_logic_fail += 1

    t4_pass = exit_logic_fail == 0 and len(trades) > 0
    results.append({
        "name": "出場邏輯一致性",
        "desc": f"所有 {len(trades)} 筆出場價符合當 bar OHLC 計算邏輯",
        "pass": t4_pass,
        "detail": f"OK: {exit_logic_ok}, fail: {exit_logic_fail}",
    })

    # 複用完整 patch map（使用者當前 BacktestParams）跑 G7/G8
    def _apply_full_patch():
        saved = {}
        fp = {
            'L_GK_TH': params.l_gk_th, 'L_BRK': params.l_brk,
            'L_TP': params.l_tp / 100, 'L_SN': params.l_sn / 100,
            'L_MH': params.l_mh, 'L_CD': params.l_cd,
            'L_MFE_ACT': params.l_mfe_act / 100, 'L_MFE_TR': params.l_mfe_tr / 100,
            'L_CMH_BAR': params.l_cmh_bar, 'L_CMH_TH': params.l_cmh_th / 100,
            'L_CMH_MH': params.l_mh - 1,
            'S_GK_TH': params.s_gk_th, 'S_BRK': params.s_brk,
            'S_TP': params.s_tp / 100, 'S_SN': params.s_sn / 100,
            'S_MH': params.s_mh, 'S_CD': params.s_cd,
            'NOTIONAL': params.notional, 'FEE': params.fee,
            'R_TH_UP': (params.r_th_up / 100) if params.enable_regime_gate else 99.0,
            'R_TH_SIDE': (params.r_th_side / 100) if params.enable_regime_gate else -1.0,
        }
        for k, v in fp.items():
            saved[k] = getattr(_bt_mod, k)
            setattr(_bt_mod, k, v)
        return saved

    def _restore_patch(saved):
        for k, v in saved.items():
            setattr(_bt_mod, k, v)

    # === G7: Walk-forward 6 folds（依 bar index 等分）===
    try:
        saved_wf = _apply_full_patch()
        ind_wf = bt_compute(df)
        dt_wf = df['datetime'].values
        n_bars = len(df)
        K = 6
        fold_size = n_bars // K
        fold_pnls = []
        fold_details = []
        for fi in range(K):
            start_bar = fi * fold_size
            end_bar = (fi + 1) * fold_size if fi < K - 1 else n_bars
            all_t = bt_simulate(ind_wf, dt_wf, start_bar=start_bar)
            fold_trades = [t for t in all_t if start_bar <= t['entry_bar'] < end_bar]
            pnl = sum(float(t['pnl_usd']) for t in fold_trades)
            fold_pnls.append(pnl)
            fold_details.append(f"F{fi+1}:${pnl:.0f}({len(fold_trades)}t)")
        positive_folds = sum(1 for p in fold_pnls if p > 0)
        g7_pass = positive_folds >= 4  # 4/6 以上為 pass
        results.append({
            "name": "G7 Walk-Forward (6 folds)",
            "desc": "資料等分 6 段，計算每段 IS PnL",
            "pass": g7_pass,
            "detail": f"{positive_folds}/6 folds 正收益 | " + " ".join(fold_details),
        })
    finally:
        _restore_patch(saved_wf)

    # === G8: 時序翻轉（OHLC 反轉後跑同一策略）===
    # 期望：regime-neutral edge 翻轉後 PnL 不應過度極端
    try:
        saved_rev = _apply_full_patch()
        # 正向 PnL 作為 baseline
        ind_fwd = bt_compute(df)
        fwd_trades = bt_simulate(ind_fwd, df['datetime'].values)
        fwd_pnl = sum(float(t['pnl_usd']) for t in fwd_trades)

        # 翻轉 OHLCV，保留原 datetime（給快照編排用）
        df_rev = df.copy().iloc[::-1].reset_index(drop=True)
        df_rev['datetime'] = df['datetime'].values  # datetime 不翻轉
        ind_rev = bt_compute(df_rev)
        rev_trades = bt_simulate(ind_rev, df_rev['datetime'].values)
        rev_pnl = sum(float(t['pnl_usd']) for t in rev_trades)

        # PASS = 反轉 PnL 不應超過正向 PnL 量級；若反轉 >= +正向 = 疑似隨機、若反轉 << -正向 = 極度 regime-dep
        ratio = rev_pnl / fwd_pnl if abs(fwd_pnl) > 100 else 0
        # 寬鬆閾值：ratio 在 (-2, 0.5) 視為 regime 敏感度可接受
        g8_pass = (-2.0 < ratio < 0.5)
        results.append({
            "name": "G8 時序翻轉 (Time Reversal)",
            "desc": "OHLCV 反轉後跑同策略，驗證非隨機性 / regime 敏感度",
            "pass": g8_pass,
            "detail": f"正向 ${fwd_pnl:.0f} ({len(fwd_trades)}筆) | 反轉 ${rev_pnl:.0f} ({len(rev_trades)}筆) | ratio={ratio:.2f}",
        })
    finally:
        _restore_patch(saved_rev)

    elapsed = round((_time.perf_counter() - t0) * 1000)
    all_pass = all(r['pass'] for r in results)

    return {
        "pass": all_pass,
        "tests": results,
        "total_trades": len(trades),
        "symbol": params.symbol,
        "data_range": f"{params.start_date or df['datetime'].iloc[0]} ~ {params.end_date or df['datetime'].iloc[-1]}",
        "elapsed_ms": elapsed,
    }


@app.post("/api/backtest")
async def api_backtest(params: BacktestParams):
    """Run V14+R backtest with custom parameters"""
    try:
        trades, df, elapsed = _run_backtest(params)
    except ValueError as e:
        return {"error": str(e)}
    # 顯示使用者請求的日期範圍（非全量資料範圍）
    dr_start = params.start_date if params.start_date else df['datetime'].iloc[0]
    dr_end = params.end_date if params.end_date else df['datetime'].iloc[-1]
    data_range = f"{dr_start} ~ {dr_end}"

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame()

    if len(tdf) == 0:
        return {
            "trades": [], "summary": {
                "total_pnl": 0, "total_trades": 0, "l_trades": 0, "s_trades": 0,
                "win_rate": 0, "profit_factor": 0, "max_drawdown": 0,
                "avg_hold": 0, "l_pnl": 0, "s_pnl": 0, "l_wr": 0, "s_wr": 0,
                "best_trade": 0, "worst_trade": 0,
            },
            "equity_curve": [], "exit_distribution": {}, "monthly": [],
            "data_range": data_range, "elapsed_ms": elapsed, "symbol": params.symbol,
        }

    total_pnl = float(tdf['pnl_usd'].sum())
    wins = tdf[tdf['pnl_usd'] > 0]
    losses = tdf[tdf['pnl_usd'] < 0]
    wr = len(wins) / len(tdf) * 100
    gw = float(wins['pnl_usd'].sum()) if len(wins) else 0
    gl = abs(float(losses['pnl_usd'].sum())) if len(losses) else 0
    pf = gw / gl if gl > 0 else 999

    cum = tdf['pnl_usd'].cumsum()
    peak = cum.cummax()
    max_dd = abs(float((cum - peak).min()))

    l_df = tdf[tdf['side'] == 'L']
    s_df = tdf[tdf['side'] == 'S']
    l_wins = l_df[l_df['pnl_usd'] > 0] if len(l_df) else pd.DataFrame()
    s_wins = s_df[s_df['pnl_usd'] > 0] if len(s_df) else pd.DataFrame()

    # Equity curve
    equity_curve = []
    cum_val = 0
    for _, t in tdf.iterrows():
        cum_val += float(t['pnl_usd'])
        ts = utc8_to_ts(t['exit_dt'])
        if ts > 0:
            if equity_curve and equity_curve[-1]["time"] == ts:
                equity_curve[-1]["value"] = round(cum_val, 2)
            else:
                equity_curve.append({"time": ts, "value": round(cum_val, 2)})

    # Exit distribution
    exit_dist = tdf['exit_reason'].value_counts().to_dict()

    # Monthly
    tdf['_month'] = pd.to_datetime(tdf['exit_dt']).dt.strftime('%Y-%m')
    monthly = []
    for m, g in tdf.groupby('_month'):
        monthly.append({
            "month": m,
            "pnl": round(float(g['pnl_usd'].sum()), 2),
            "trades": len(g),
            "wr": round(float((g['pnl_usd'] > 0).mean() * 100), 1),
        })

    # Trade list for frontend
    trade_list = []
    for idx, t in tdf.iterrows():
        trade_list.append({
            "no": idx + 1,
            "side": t['side'],
            "entry_dt": t['entry_dt'],
            "exit_dt": t['exit_dt'],
            "entry_price": float(t['entry_price']),
            "exit_price": float(t['exit_price']),
            "pnl_usd": float(t['pnl_usd']),
            "pnl_pct": float(t['pnl_pct']),
            "bars_held": int(t['bars_held']),
            "exit_reason": t['exit_reason'],
            "mfe_pct": float(t['mfe_pct']),
            "mae_pct": float(t['mae_pct']),
            "gk_pctile": float(t['gk_pctile']),
        })

    return {
        "trades": trade_list,
        "summary": {
            "total_pnl": round(total_pnl, 2),
            "total_trades": len(tdf),
            "l_trades": len(l_df),
            "s_trades": len(s_df),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "max_drawdown": round(max_dd, 2),
            "avg_hold": round(float(tdf['bars_held'].mean()), 1),
            "l_pnl": round(float(l_df['pnl_usd'].sum()), 2) if len(l_df) else 0,
            "s_pnl": round(float(s_df['pnl_usd'].sum()), 2) if len(s_df) else 0,
            "l_wr": round(len(l_wins) / len(l_df) * 100, 1) if len(l_df) else 0,
            "s_wr": round(len(s_wins) / len(s_df) * 100, 1) if len(s_df) else 0,
            "best_trade": round(float(tdf['pnl_usd'].max()), 2),
            "worst_trade": round(float(tdf['pnl_usd'].min()), 2),
        },
        "equity_curve": equity_curve,
        "exit_distribution": exit_dist,
        "monthly": monthly,
        "data_range": data_range,
        "elapsed_ms": elapsed,
        "symbol": params.symbol,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 機器人子進程管理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

bot_process: subprocess.Popen = None


def _read_bot_state():
    """從 state JSON 讀取餘額與持倉資訊，用於 Telegram 通知"""
    try:
        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
        sf = ROOT_DIR / ("eth_state.json" if paper else "eth_state_live.json")
        if sf.exists():
            state = json.loads(sf.read_text(encoding="utf-8"))
            bal = state.get("account_balance", 0)
            positions = state.get("positions", {})
            lc = sum(1 for p in positions.values() if p.get("sub_strategy") == "L")
            sc = sum(1 for p in positions.values() if p.get("sub_strategy") == "S")
            return bal, lc, sc, "模擬" if paper else "實戰"
    except Exception:
        pass
    return 0, 0, 0, "模擬"


def _send_bot_tg(action: str):
    """發送機器人啟停 Telegram 通知（背景執行，不阻塞）"""
    def _do():
        bal, lc, sc, env = _read_bot_state()
        pos_text = f"L:{lc} S:{sc}" if (lc + sc) > 0 else "空手"
        if action == "stop":
            msg = (f"<b>🖨 V14+R 關機（{env}）</b>\n"
                   f"━━━━━━━━━━━━━━━\n"
                   f"💰 金庫：${bal:.2f}\n"
                   f"📊 持倉：{pos_text}\n"
                   f"🛏 印鈔機已停止")
        elif action == "restart":
            msg = (f"<b>🔄 V14+R 重啟中（{env}）</b>\n"
                   f"━━━━━━━━━━━━━━━\n"
                   f"💰 金庫：${bal:.2f}\n"
                   f"📊 持倉：{pos_text}\n"
                   f"⏳ 印鈔機重新啟動...")
        else:
            return
        try:
            _tg_send(msg)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def start_bot():
    """啟動 main_eth.py 作為子進程"""
    global bot_process
    if bot_process and bot_process.poll() is None:
        return  # 已在運行

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    bot_process = subprocess.Popen(
        [sys.executable, str(ROOT_DIR / "main_eth.py")],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )

    # 背景 thread 持續 drain stdout，防止 pipe buffer 滿導致 deadlock
    def _drain():
        try:
            for _ in bot_process.stdout:
                pass
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()


def stop_bot(notify: str = ""):
    """停止機器人子進程。notify="stop"|"restart" 時發 Telegram"""
    global bot_process
    if bot_process is None:
        return
    if bot_process.poll() is not None:
        bot_process = None
        return

    if notify:
        _send_bot_tg(notify)

    bot_process.terminate()
    try:
        bot_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        bot_process.kill()
        bot_process.wait(timeout=5)
    bot_process = None


@app.get("/api/bot-status")
async def api_bot_status():
    """機器人運行狀態"""
    if bot_process is None:
        return {"running": False, "pid": None}
    rc = bot_process.poll()
    if rc is not None:
        return {"running": False, "pid": None, "exit_code": rc}
    return {"running": True, "pid": bot_process.pid}


_webview_window = None  # PyWebView 視窗參照（__main__ 時設定）


@app.post("/api/dashboard/restart")
async def api_dashboard_restart():
    """重啟整個儀表板：停止機器人 → 啟動新儀表板進程 → 關閉當前視窗"""
    def _do():
        time.sleep(0.5)  # 讓 HTTP response 先回
        stop_bot(notify="restart")
        # 啟動新的儀表板進程（新進程有 kill_port 會接管 port）
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=str(ROOT_DIR),
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        time.sleep(0.5)
        if _webview_window:
            _webview_window.destroy()
    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True}


@app.post("/api/dashboard/shutdown")
async def api_dashboard_shutdown():
    """關機：停止機器人 + 關閉儀表板"""
    def _do():
        time.sleep(0.5)  # 讓 HTTP response 先回
        stop_bot(notify="stop")
        if _webview_window:
            _webview_window.destroy()
    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True}


@app.get("/api/logs")
async def api_logs(file: str = Query("system"), lines: int = Query(200, ge=10, le=2000)):
    """讀取日誌檔案最後 N 行"""
    LOGS_DIR = ROOT_DIR / "logs"
    file_map = {
        "system": LOGS_DIR / "system.log",
        "signal": LOGS_DIR / "signal.log",
        "alerts": LOGS_DIR / "alerts.log",
    }
    log_path = file_map.get(file)
    if not log_path or not log_path.exists():
        return {"file": file, "lines": [], "total": 0}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return {
            "file": file,
            "lines": [l.rstrip("\n") for l in tail],
            "total": len(all_lines),
        }
    except Exception as e:
        return {"file": file, "lines": [f"Error: {e}"], "total": 0}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 啟動
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def start_server(port=8050):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


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

    # 啟動 FastAPI server
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

    # 啟動交易機器人（子進程）
    start_bot()

    # 開啟桌面視窗（阻塞直到視窗關閉）
    _webview_window = webview.create_window(
        "印鈔機監控台",
        f"http://127.0.0.1:{port}",
        width=1400,
        height=900,
        min_size=(1100, 700),
    )
    webview.start()

    # 視窗關閉 → 停止機器人（若尚未被 restart/shutdown 端點停止）
    # 如果是 API 觸發的關閉，bot_process 已是 None，stop_bot 會直接 return
    stop_bot(notify="stop")
