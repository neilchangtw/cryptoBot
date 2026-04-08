"""
執行引擎 — 持倉管理 + 狀態持久化

支持雙策略：
  L（做多）：sub_strategy="L", maxSame=9
  S（做空）：sub_strategy="S1"~"S4", maxSame=5/子策略

Paper mode: 用計算的價格模擬成交
Live mode:  呼叫 binance_trade.py 下單

狀態持久化到 eth_state.json，重啟後可恢復。
"""
import os
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

import strategy
import recorder
from telegram_notify import send_telegram_message

load_dotenv()

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
SYMBOL = os.getenv("SYMBOL", "ETHUSDT")
logger = logging.getLogger("executor")

# sub_strategy → max_same 的映射
_SUB_MAX = {"L": strategy.L_MAX_SAME}
for s in strategy.S_SUBS:
    _SUB_MAX[s["id"]] = s["max_same"]


class Executor:
    def __init__(self, state_path: str = None):
        if state_path is None:
            state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eth_state.json")
        self.state_path = state_path

        # 核心狀態
        self.positions = {}          # {trade_id: position_dict}，所有未平倉持倉
        self.pending_entry = None    # 保留相容性（目前未使用）
        self.last_exits = {          # 各子策略最後出場的 bar_counter（cooldown 用）
            "L": -9999, "S1": -9999, "S2": -9999, "S3": -9999, "S4": -9999
        }
        self.account_balance = None  # USDT 帳戶餘額
        self.bar_counter = 0         # 已處理的 bar 數
        self.last_bar_time = None    # 最後處理的 bar 時間（防重複）
        self.trade_number = 0        # 累計交易編號
        self.daily_stats = {}        # 每日統計 {date_str: {...}}

        self._load_state()
        self._init_balance()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 狀態持久化
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_state(self):
        """從 eth_state.json 載入狀態"""
        if not os.path.exists(self.state_path):
            logger.info("No state file found, starting fresh")
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self.positions = state.get("positions", {})
            self.pending_entry = state.get("pending_entry", None)
            self.account_balance = state.get("account_balance", None)
            self.bar_counter = state.get("bar_counter", 0)
            self.last_bar_time = state.get("last_bar_time", None)
            self.trade_number = state.get("trade_number", 0)
            self.daily_stats = state.get("daily_stats", {})

            # 遷移 last_exits: 舊單策略格式 {"long":x,"short":y} → 新雙策略格式
            # 舊 long → L, 舊 short → S1~S4（共用同一個值）
            raw_exits = state.get("last_exits", {})
            if "long" in raw_exits or "short" in raw_exits:
                logger.info("Migrating last_exits from old format")
                old_long = raw_exits.get("long", -9999)
                old_short = raw_exits.get("short", -9999)
                self.last_exits = {
                    "L": old_long,
                    "S1": old_short, "S2": old_short,
                    "S3": old_short, "S4": old_short,
                }
            else:
                self.last_exits = raw_exits
                # 確保所有 key 都存在
                for key in ["L", "S1", "S2", "S3", "S4"]:
                    if key not in self.last_exits:
                        self.last_exits[key] = -9999

            # 遷移 positions: 若無 sub_strategy 欄位則補上
            for tid, pos in self.positions.items():
                if "sub_strategy" not in pos:
                    if pos.get("side") == "long":
                        pos["sub_strategy"] = "L"
                    else:
                        pos["sub_strategy"] = "S1"
                    logger.info(f"Migrated position {tid} → sub_strategy={pos['sub_strategy']}")

            logger.info(f"State loaded: {len(self.positions)} positions, "
                        f"bar_counter={self.bar_counter}")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _init_balance(self):
        """從幣安帳戶取得實際 USDT 餘額"""
        if self.account_balance is not None:
            logger.info(f"Balance from state: ${self.account_balance:.2f}")
            return
        try:
            import binance_trade
            balance = binance_trade.get_available_balance()
            if balance > 0:
                self.account_balance = balance
                logger.info(f"Balance from Binance: ${balance:.2f}")
            else:
                self.account_balance = 0.0
                logger.warning("Binance returned 0 balance, using $0")
        except Exception as e:
            logger.error(f"Failed to fetch balance from Binance: {e}, defaulting to $0")
            self.account_balance = 0.0

    def save_state(self):
        """儲存狀態到 eth_state.json"""
        state = {
            "positions": self.positions,
            "pending_entry": self.pending_entry,
            "last_exits": self.last_exits,
            "account_balance": round(self.account_balance, 4),
            "bar_counter": self.bar_counter,
            "last_bar_time": self.last_bar_time,
            "trade_number": self.trade_number,
            "daily_stats": self.daily_stats,
        }
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp_path, self.state_path)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 持倉操作
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def can_open_sub(self, sub_strategy: str) -> bool:
        """是否可以開倉（按 sub_strategy 計數）"""
        count = sum(1 for p in self.positions.values()
                    if p.get("sub_strategy") == sub_strategy)
        max_same = _SUB_MAX.get(sub_strategy, 5)
        return count < max_same

    def open_position(self, side: str, sub_strategy: str, entry_price: float,
                      bar_counter: int, signal_indicators: dict,
                      bar_data: dict, btc_context: dict) -> str:
        """
        開倉。

        Args:
            side: "long" or "short"
            sub_strategy: "L" / "S1" / "S2" / "S3" / "S4"
            entry_price: 進場價
            bar_counter: 當前 bar 計數器
            signal_indicators: 信號觸發時的指標快照
            bar_data: 信號 bar 的數據
            btc_context: {"btc_close": float}

        Returns:
            trade_id
        """
        self.trade_number += 1
        dt_utc8 = bar_data["datetime"]
        dt_utc = dt_utc8 - timedelta(hours=8) if isinstance(dt_utc8, datetime) else dt_utc8
        trade_id = f"{dt_utc8.strftime('%Y%m%d_%H%M%S')}_{sub_strategy}"

        qty = strategy.NOTIONAL / entry_price

        # 實際下單
        try:
            import binance_trade
            order_side = "BUY" if side == "long" else "SELL"
            position_side = "LONG" if side == "long" else "SHORT"
            safenet_price = entry_price * (1 - strategy.SAFENET_PCT) if side == "long" \
                else entry_price * (1 + strategy.SAFENET_PCT)
            # 只有該方向第一筆持倉時才掛 SL（closePosition=true 會覆蓋整個方向）
            existing_same_side = sum(1 for p in self.positions.values()
                                    if ("LONG" if p["side"] == "long" else "SHORT") == position_side)
            sl_price = safenet_price if existing_same_side == 0 else None
            result = binance_trade.place_order(
                SYMBOL, order_side, stop_loss=sl_price,
                strategy_id=f"eth_dual_{sub_strategy}",
                position_side=position_side,
            )
            if result is None:
                logger.error(f"Order failed for {trade_id}")
                return None
            avg_price = float(result.get("avgPrice", 0))
            if avg_price > 0:
                entry_price = avg_price
            qty = float(result.get("executedQty", qty))
        except Exception as e:
            logger.error(f"Order exception: {e}")
            return None

        # 計算進場指標
        gk_pctile = signal_indicators.get("gk_pctile")
        gk_ratio = signal_indicators.get("gk_ratio")
        ema20 = signal_indicators.get("ema20")
        signal_close = signal_indicators.get("close")
        brk_max = signal_indicators.get("breakout_10bar_max")
        brk_min = signal_indicators.get("breakout_10bar_min")

        if side == "long" and brk_max and brk_max > 0:
            brk_strength = (entry_price - brk_max) / brk_max * 100
        elif side == "short" and brk_min and brk_min > 0:
            brk_strength = (brk_min - entry_price) / brk_min * 100
        else:
            brk_strength = None

        ema20_dist = (entry_price - ema20) / entry_price * 100 if ema20 and entry_price > 0 else None

        btc_close = btc_context.get("btc_close")
        eth_btc = entry_price / btc_close if btc_close and btc_close > 0 else None
        eth_24h = bar_data.get("eth_24h_change_pct")
        bars_since = bar_counter - self.last_exits.get(sub_strategy, -9999)

        # 建立持倉記錄
        position = {
            "trade_id": trade_id,
            "side": side,
            "sub_strategy": sub_strategy,
            "entry_price": entry_price,
            "entry_time_utc": str(dt_utc),
            "entry_time_utc8": str(dt_utc8),
            "entry_bar_counter": bar_counter,
            "qty": qty,
            "bars_held": 0,
            "mae_pct": 0.0,
            "mfe_pct": 0.0,
            "mae_time_bar": 0,
            "mfe_time_bar": 0,
            "pnl_at_bar7": None,
            "pnl_at_bar12": None,
        }
        self.positions[trade_id] = position

        # 記錄到 trades.csv
        trade_record = {
            "trade_id": trade_id,
            "trade_number": self.trade_number,
            "entry_time_utc": str(dt_utc),
            "entry_time_utc8": str(dt_utc8),
            "entry_weekday": dt_utc8.weekday() if isinstance(dt_utc8, datetime) else "",
            "entry_hour_utc8": dt_utc8.hour if isinstance(dt_utc8, datetime) else "",
            "direction": side.upper(),
            "sub_strategy": sub_strategy,
            "entry_price": round(entry_price, 4),
            "entry_signal_bar_close": round(signal_close, 4) if signal_close else "",
            "gk_pctile_at_entry": round(gk_pctile, 2) if gk_pctile is not None else "",
            "gk_ratio_at_entry": round(gk_ratio, 6) if gk_ratio is not None else "",
            "breakout_bar_close": round(signal_close, 4) if signal_close else "",
            "breakout_10bar_max": round(brk_max, 4) if brk_max else "",
            "breakout_10bar_min": round(brk_min, 4) if brk_min else "",
            "breakout_strength_pct": round(brk_strength, 4) if brk_strength is not None else "",
            "ema20_at_entry": round(ema20, 4) if ema20 else "",
            "ema20_distance_pct": round(ema20_dist, 2) if ema20_dist is not None else "",
            # 判斷是否剛過 cooldown 就進場
            "was_cooldown_trade": bars_since == (
                strategy.L_EXIT_CD if sub_strategy == "L"
                else next((s["exit_cd"] for s in strategy.S_SUBS if s["id"] == sub_strategy), 6)
            ),
            "bars_since_last_exit": bars_since,
            "btc_close_at_entry": round(btc_close, 2) if btc_close else "",
            "eth_btc_ratio_at_entry": round(eth_btc, 6) if eth_btc is not None else "",
            "eth_24h_change_pct": round(eth_24h, 2) if eth_24h is not None else "",
        }
        recorder.record_trade_open(trade_record)

        # Telegram 通知
        env = "模擬" if PAPER_TRADING else "實戰"
        if sub_strategy == "L":
            direction = "做多 📈"
            sub_label = "L 多單"
            action = "🐂 衝啊！抄底進場"
        else:
            direction = "做空 📉"
            sub_label = f"{sub_strategy} 空單"
            action = "🐻 空它！高空進場"
        sn_price = entry_price * (1 - strategy.SAFENET_PCT) if side == "long" \
            else entry_price * (1 + strategy.SAFENET_PCT)
        msg = (
            f"<b>🎰 下注！（{env}）</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{action}\n"
            f"🏷 策略：{sub_label}\n"
            f"💲 進場價：${entry_price:.2f}\n"
            f"🛡 安全網：${sn_price:.2f}（±{strategy.SAFENET_PCT*100:.1f}%）\n"
            f"🔋 壓縮能量：{gk_pctile:.1f}\n"
            f"📝 第 {self.trade_number} 筆 ｜ 💰 金庫 ${self.account_balance:.2f}"
        )
        send_telegram_message(msg)

        logger.info(f"Opened {sub_strategy} {side} @ ${entry_price:.2f} | {trade_id}")
        self.save_state()
        return trade_id

    def close_position(self, trade_id: str, exit_price: float, exit_reason: str,
                       bar_counter: int, bar_data: dict,
                       btc_context: dict) -> dict:
        """平倉。"""
        pos = self.positions.get(trade_id)
        if pos is None:
            logger.warning(f"close_position: {trade_id} not found")
            return None

        side = pos["side"]
        sub_strategy = pos.get("sub_strategy", "L")
        entry_price = pos["entry_price"]

        # 實際平倉
        try:
            import binance_trade
            close_side = "SELL" if side == "long" else "BUY"
            position_side = "LONG" if side == "long" else "SHORT"
            binance_trade.place_order(
                SYMBOL, close_side, qty=pos["qty"], reduce_only=True,
                strategy_id=f"eth_dual_{sub_strategy}",
                position_side=position_side,
            )
            # 只有該方向最後一筆平倉時才取消該方向的 SL
            remaining = sum(1 for p in self.positions.values()
                           if p.get("trade_id") != trade_id
                           and (("LONG" if p["side"] == "long" else "SHORT") == position_side))
            if remaining == 0:
                binance_trade.cancel_all_orders(SYMBOL, position_side=position_side)
        except Exception as e:
            logger.error(f"Close order exception: {e}")

        # 計算 PnL
        pnl_usd, pnl_pct = strategy.compute_pnl(entry_price, exit_price, side)
        gross_pnl = pnl_usd + strategy.FEE
        bars_held = bar_counter - pos["entry_bar_counter"]

        # 更新帳戶
        self.account_balance += pnl_usd
        self.last_exits[sub_strategy] = bar_counter

        dt_utc8 = bar_data["datetime"]
        dt_utc = dt_utc8 - timedelta(hours=8) if isinstance(dt_utc8, datetime) else dt_utc8

        # 更新 trades.csv
        exit_data = {
            "exit_time_utc": str(dt_utc),
            "exit_time_utc8": str(dt_utc8),
            "exit_type": exit_reason,
            "exit_price": round(exit_price, 4),
            "exit_trigger_bar": bars_held,
            "hold_bars": bars_held,
            "hold_hours": bars_held,
            "max_adverse_excursion_pct": round(pos.get("mae_pct", 0), 2),
            "max_adverse_excursion_usd": round(pos.get("mae_pct", 0) / 100 * strategy.NOTIONAL, 2),
            "max_favorable_excursion_pct": round(pos.get("mfe_pct", 0), 2),
            "max_favorable_excursion_usd": round(pos.get("mfe_pct", 0) / 100 * strategy.NOTIONAL, 2),
            "mae_time_bar": pos.get("mae_time_bar", ""),
            "mfe_time_bar": pos.get("mfe_time_bar", ""),
            "pnl_at_bar7": round(pos["pnl_at_bar7"], 2) if pos.get("pnl_at_bar7") is not None else "",
            "pnl_at_bar12": round(pos["pnl_at_bar12"], 2) if pos.get("pnl_at_bar12") is not None else "",
            "gross_pnl_usd": round(gross_pnl, 4),
            "commission_usd": strategy.FEE,
            "net_pnl_usd": round(pnl_usd, 4),
            "net_pnl_pct": round(pnl_pct, 2),
            "win_loss": "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BREAKEVEN"),
        }
        recorder.record_trade_close(trade_id, exit_data)

        # Telegram
        env = "模擬" if PAPER_TRADING else "實戰"
        if sub_strategy == "L":
            sub_label = "L 多單"
        else:
            sub_label = f"{sub_strategy} 空單"
        exit_map = {
            "SafeNet": "🆘 安全網接住了",
            "Trail": "🏃 追蹤停利，落袋為安",
            "EarlyStop": "✂️ 苗頭不對，提前跑路",
            "TP": "🎯 精準止盈，完美收割",
            "MaxHold": "⏰ 時間到，強制下課",
        }
        exit_text = exit_map.get(exit_reason, exit_reason)
        if pnl_usd > 0:
            result_header = "💵 印到鈔票了！"
            result_text = f"賺 ${abs(pnl_usd):.2f}"
        elif pnl_usd < 0:
            result_header = "🔥 紙燒掉了…"
            result_text = f"虧 ${abs(pnl_usd):.2f}"
        else:
            result_header = "😐 白忙一場"
            result_text = "打平"
        msg = (
            f"<b>{result_header}（{env}）</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🏷 {sub_label}：${pos['entry_price']:.2f} → ${exit_price:.2f}\n"
            f"📋 {exit_text}\n"
            f"💰 {result_text}（{pnl_pct:+.1f}%）\n"
            f"⏱ 抱了 {bars_held}h ｜ 最慘 -{abs(pos.get('mae_pct', 0)):.1f}%\n"
            f"🏦 金庫：${self.account_balance:.2f}"
        )
        send_telegram_message(msg)

        del self.positions[trade_id]
        logger.info(f"Closed {trade_id} | {exit_reason} | PnL ${pnl_usd:.2f}")
        self.save_state()

        return {"pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                "bars_held": bars_held, "trade_id": trade_id}

    def update_tracking(self, trade_id: str, bar_data: dict, bar_counter: int):
        """每 bar 更新持倉追蹤：MAE/MFE, pnl_at_bar7/bar12。"""
        pos = self.positions.get(trade_id)
        if pos is None:
            return

        entry_price = pos["entry_price"]
        if not entry_price or entry_price <= 0:
            logger.error(f"update_tracking: {trade_id} has invalid entry_price={entry_price}, skipping")
            return

        side = pos["side"]
        bars_held = bar_counter - pos["entry_bar_counter"]
        pos["bars_held"] = bars_held

        bar_high = bar_data["high"]
        bar_low = bar_data["low"]
        bar_close = bar_data["close"]

        if side == "long":
            adverse = (bar_low - entry_price) / entry_price * 100
            favorable = (bar_high - entry_price) / entry_price * 100
            current_pnl_pct = (bar_close - entry_price) / entry_price * 100
        else:
            adverse = (entry_price - bar_high) / entry_price * 100
            favorable = (entry_price - bar_low) / entry_price * 100
            current_pnl_pct = (entry_price - bar_close) / entry_price * 100

        if adverse < pos.get("mae_pct", 0):
            pos["mae_pct"] = adverse
            pos["mae_time_bar"] = bars_held

        if favorable > pos.get("mfe_pct", 0):
            pos["mfe_pct"] = favorable
            pos["mfe_time_bar"] = bars_held

        if bars_held == 7 and pos.get("pnl_at_bar7") is None:
            pos["pnl_at_bar7"] = current_pnl_pct
        if bars_held == 12 and pos.get("pnl_at_bar12") is None:
            pos["pnl_at_bar12"] = current_pnl_pct

    def get_open_positions(self) -> list:
        """回傳所有持倉的 list"""
        return list(self.positions.values())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 每日統計
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _today_key(self) -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _ensure_daily(self):
        key = self._today_key()
        if key not in self.daily_stats:
            self.daily_stats[key] = {
                "trades_opened": 0, "trades_closed": 0,
                "wins": 0, "losses": 0,
                "pnl": 0.0, "signals_fired": 0, "signals_blocked": 0,
                "safenet_count": 0, "earlyStop_count": 0, "trail_count": 0,
                "tp_count": 0, "maxhold_count": 0,
                "hold_hours_sum": 0, "max_hold_hours": 0,
            }

    def record_signal(self, fired: bool):
        self._ensure_daily()
        key = self._today_key()
        if fired:
            self.daily_stats[key]["signals_fired"] += 1
        else:
            self.daily_stats[key]["signals_blocked"] += 1

    def record_open(self):
        self._ensure_daily()
        self.daily_stats[self._today_key()]["trades_opened"] += 1

    def record_close(self, pnl_usd: float, exit_reason: str, hold_bars: int):
        self._ensure_daily()
        d = self.daily_stats[self._today_key()]
        d["trades_closed"] += 1
        d["pnl"] += pnl_usd
        if pnl_usd > 0:
            d["wins"] += 1
        elif pnl_usd < 0:
            d["losses"] += 1
        # 出場原因計數（注意 key 大小寫需與 daily_stats 一致）
        reason_map = {
            "SafeNet": "safenet_count",
            "EarlyStop": "earlyStop_count",
            "Trail": "trail_count",
            "TP": "tp_count",
            "MaxHold": "maxhold_count",
        }
        reason_key = reason_map.get(exit_reason)
        if reason_key and reason_key in d:
            d[reason_key] += 1
        d["hold_hours_sum"] += hold_bars
        d["max_hold_hours"] = max(d["max_hold_hours"], hold_bars)

    def flush_daily_summary(self, date_str: str):
        """把某天的統計寫入 daily_summary.csv"""
        if date_str not in self.daily_stats:
            return
        d = self.daily_stats[date_str]
        n_closed = d["trades_closed"]
        avg_hold = d["hold_hours_sum"] / n_closed if n_closed > 0 else 0

        # 計算持倉狀態
        l_count = sum(1 for p in self.positions.values() if p.get("sub_strategy") == "L")
        s_count = sum(1 for p in self.positions.values() if p.get("sub_strategy", "").startswith("S"))
        open_pos = f"L:{l_count} S:{s_count}" if (l_count + s_count) > 0 else ""

        stats = {
            "date": date_str,
            "total_trades": d["trades_opened"],
            "long_trades": "",
            "short_trades": "",
            "wins": d["wins"],
            "losses": d["losses"],
            "gross_pnl": round(d["pnl"] + strategy.FEE * n_closed, 2),
            "net_pnl": round(d["pnl"], 2),
            "safenet_count": d["safenet_count"],
            "earlyStop_count": d["earlyStop_count"],
            "trail_count": d.get("trail_count", 0),
            "avg_hold_hours": round(avg_hold, 1),
            "longest_hold_hours": d["max_hold_hours"],
            "account_balance": round(self.account_balance, 2),
            "cumulative_pnl": round(self.account_balance - 10000.0, 2),
            "open_position": open_pos,
            "system_alerts": 0,
        }
        recorder.record_daily_summary(stats)
