"""
V14 執行引擎 — 持倉管理 + 狀態持久化 + 風控熔斷

支持雙策略：
  L（做多）：sub_strategy="L", maxTotal=1
  S（做空）：sub_strategy="S", maxTotal=1

Paper mode: 用計算的價格模擬成交
Live mode:  呼叫 binance_trade.py 下單

風控熔斷：
  日虧 -$200 停 / L 月虧 -$75 停 / S 月虧 -$150 停
  連虧 4 筆 → 24 bar 冷卻

V14 新增：L 持倉新增 running_mfe / mh_reduced 欄位（MFE Trailing + Conditional MH）。
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


class Executor:
    def __init__(self, state_path: str = None):
        if state_path is None:
            # 依 PAPER_TRADING 切 eth_state.json / eth_state_live.json
            fname = "eth_state.json" if PAPER_TRADING else "eth_state_live.json"
            state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
        self.state_path = state_path

        # 核心狀態
        self.positions = {}          # {trade_id: position_dict}，所有未平倉持倉
        self.last_exits = {          # 各策略最後出場的 bar_counter（cooldown 用）
            "L": -9999, "S": -9999
        }
        self.account_balance = None  # USDT 帳戶餘額
        self.bar_counter = 0         # 已處理的 bar 數
        self.last_bar_time = None    # 最後處理的 bar 時間（防重複）
        self.trade_number = 0        # 累計交易編號
        self.daily_stats = {}        # 每日統計 {date_str: {...}}

        # 風控熔斷狀態
        self.monthly_pnl = {"L": 0.0, "S": 0.0}     # 當月各策略已實現 PnL
        self.monthly_entries = {"L": 0, "S": 0}       # 當月各策略進場筆數
        self.monthly_key = None                        # 當前月份 key "YYYY-MM"
        self.daily_pnl = 0.0                           # 當日 PnL
        self.daily_key = None                          # 當日 key "YYYY-MM-DD"
        self.consec_losses = 0                         # 連續虧損筆數
        self.consec_loss_cooldown_until = 0            # 連虧冷卻結束的 bar_counter
        self.paused = False                            # Telegram /pause 暫停開倉

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
            self.account_balance = state.get("account_balance", None)
            self.bar_counter = state.get("bar_counter", 0)
            self.last_bar_time = state.get("last_bar_time", None)
            self.trade_number = state.get("trade_number", 0)
            self.daily_stats = state.get("daily_stats", {})

            # 載入 last_exits，支持 v6 → V10 遷移
            raw_exits = state.get("last_exits", {})
            if "S1" in raw_exits or "S2" in raw_exits:
                # v6 格式：S1-S4 → 合併為 S（取最新的）
                logger.info("Migrating last_exits from v6 format (S1-S4 → S)")
                s_vals = [raw_exits.get(k, -9999) for k in ["S1", "S2", "S3", "S4"]]
                self.last_exits = {
                    "L": raw_exits.get("L", -9999),
                    "S": max(s_vals),
                }
            elif "long" in raw_exits or "short" in raw_exits:
                # 更舊的格式
                logger.info("Migrating last_exits from legacy format")
                self.last_exits = {
                    "L": raw_exits.get("long", -9999),
                    "S": raw_exits.get("short", -9999),
                }
            else:
                self.last_exits = {
                    "L": raw_exits.get("L", -9999),
                    "S": raw_exits.get("S", -9999),
                }

            # 遷移 positions: S1-S4 → S; V25-D: 補 entry_regime
            for tid, pos in list(self.positions.items()):
                sub = pos.get("sub_strategy", "")
                if sub in ("S1", "S2", "S3", "S4"):
                    pos["sub_strategy"] = "S"
                    logger.info(f"Migrated position {tid}: {sub} → S")
                elif sub == "" and pos.get("side") == "long":
                    pos["sub_strategy"] = "L"
                elif sub == "" and pos.get("side") == "short":
                    pos["sub_strategy"] = "S"
                # V25-D: 舊持倉缺 entry_regime → "NA"（落到 V14 default 出場）
                if "entry_regime" not in pos:
                    pos["entry_regime"] = "NA"

            # 載入風控熔斷狀態
            cb = state.get("circuit_breaker", {})
            self.monthly_pnl = cb.get("monthly_pnl", {"L": 0.0, "S": 0.0})
            self.monthly_entries = cb.get("monthly_entries", {"L": 0, "S": 0})
            self.monthly_key = cb.get("monthly_key", None)
            self.daily_pnl = cb.get("daily_pnl", 0.0)
            self.daily_key = cb.get("daily_key", None)
            self.consec_losses = cb.get("consec_losses", 0)
            self.consec_loss_cooldown_until = cb.get("consec_loss_cooldown_until", 0)

            # 載入暫停狀態
            self.paused = state.get("paused", False)

            logger.info(f"State loaded: {len(self.positions)} positions, "
                        f"bar_counter={self.bar_counter}"
                        + (" PAUSED" if self.paused else ""))
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _init_balance(self):
        """從幣安帳戶取得實際 USDT 錢包餘額（每次啟動都同步）"""
        try:
            import binance_trade
            balance = binance_trade.get_wallet_balance()
            if balance > 0:
                self.account_balance = balance
                logger.info(f"Balance from Binance: ${balance:.2f}")
            else:
                if self.account_balance is None:
                    self.account_balance = 0.0
                logger.warning(f"Binance returned 0 wallet balance, keeping ${self.account_balance:.2f}")
        except Exception as e:
            logger.error(f"Failed to fetch balance from Binance: {e}")
            if self.account_balance is None:
                self.account_balance = 0.0

    def _sync_balance(self):
        """同步幣安實際錢包餘額"""
        try:
            import binance_trade
            balance = binance_trade.get_wallet_balance()
            if balance > 0:
                old = self.account_balance
                self.account_balance = balance
                logger.info(f"Balance synced: ${old:.2f} → ${balance:.2f}")
        except Exception as e:
            logger.error(f"Balance sync failed: {e}")

    def save_state(self):
        """儲存狀態到 eth_state.json"""
        state = {
            "positions": self.positions,
            "last_exits": self.last_exits,
            "account_balance": round(self.account_balance, 4),
            "bar_counter": self.bar_counter,
            "last_bar_time": self.last_bar_time,
            "trade_number": self.trade_number,
            "daily_stats": self.daily_stats,
            "circuit_breaker": {
                "monthly_pnl": self.monthly_pnl,
                "monthly_entries": self.monthly_entries,
                "monthly_key": self.monthly_key,
                "daily_pnl": self.daily_pnl,
                "daily_key": self.daily_key,
                "consec_losses": self.consec_losses,
                "consec_loss_cooldown_until": self.consec_loss_cooldown_until,
            },
            "paused": self.paused,
        }
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp_path, self.state_path)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 風控熔斷
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def update_period_keys(self, dt_utc8: datetime):
        """更新月/日 key，跨月/日時重置計數器"""
        month_key = dt_utc8.strftime("%Y-%m")
        day_key = dt_utc8.strftime("%Y-%m-%d")

        if self.monthly_key != month_key:
            if self.monthly_key is not None:
                logger.info(f"Month rollover: {self.monthly_key} → {month_key} | "
                            f"L: ${self.monthly_pnl['L']:.2f} ({self.monthly_entries['L']}t), "
                            f"S: ${self.monthly_pnl['S']:.2f} ({self.monthly_entries['S']}t)")
            self.monthly_key = month_key
            self.monthly_pnl = {"L": 0.0, "S": 0.0}
            self.monthly_entries = {"L": 0, "S": 0}

        if self.daily_key != day_key:
            if self.daily_key is not None:
                logger.info(f"Day rollover: {self.daily_key} → {day_key} | "
                            f"Daily PnL: ${self.daily_pnl:.2f}")
            self.daily_key = day_key
            self.daily_pnl = 0.0

    def check_circuit_breaker(self, side: str) -> tuple:
        """
        檢查風控熔斷是否允許進場。

        Args:
            side: "L" or "S"

        Returns:
            (allowed: bool, reason: str)
        """
        # 1. 連虧冷卻
        if self.consec_losses >= strategy.CONSEC_LOSS_PAUSE:
            if self.bar_counter < self.consec_loss_cooldown_until:
                remaining = self.consec_loss_cooldown_until - self.bar_counter
                return False, f"連虧{self.consec_losses}筆冷卻中（剩{remaining}bar）"

        # 2. 日虧上限
        if self.daily_pnl <= strategy.DAILY_LOSS_LIMIT:
            return False, f"日虧${self.daily_pnl:.0f}已達上限${strategy.DAILY_LOSS_LIMIT}"

        # 3. 月虧上限（per-strategy）
        if side == "L":
            cap = strategy.L_MONTHLY_LOSS_CAP
            pnl = self.monthly_pnl.get("L", 0.0)
        else:
            cap = strategy.S_MONTHLY_LOSS_CAP
            pnl = self.monthly_pnl.get("S", 0.0)
        if pnl <= cap:
            return False, f"{side}月虧${pnl:.0f}已達上限${cap}"

        # 4. 月度進場上限
        if side == "L":
            entry_cap = strategy.L_MONTHLY_ENTRY_CAP
            entries = self.monthly_entries.get("L", 0)
        else:
            entry_cap = strategy.S_MONTHLY_ENTRY_CAP
            entries = self.monthly_entries.get("S", 0)
        if entries >= entry_cap:
            return False, f"{side}月進場{entries}筆已達上限{entry_cap}"

        return True, ""

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 持倉操作
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def open_position(self, side: str, sub_strategy: str, entry_price: float,
                      bar_counter: int, signal_indicators: dict,
                      bar_data: dict, btc_context: dict) -> str:
        """
        開倉。

        Args:
            side: "long" or "short"
            sub_strategy: "L" or "S"
            entry_price: 進場價
            bar_counter: 當前 bar 計數器
            signal_indicators: 信號觸發時的指標快照
            bar_data: 信號 bar 的數據
            btc_context: {"btc_close": float}

        Returns:
            trade_id or None
        """
        self.trade_number += 1
        dt_utc8 = bar_data["datetime"]
        dt_utc = dt_utc8 - timedelta(hours=8) if isinstance(dt_utc8, datetime) else dt_utc8
        trade_id = f"{dt_utc8.strftime('%Y%m%d_%H%M%S')}_{sub_strategy}"

        qty = strategy.NOTIONAL / entry_price

        # 決定 SafeNet 價格
        if side == "long":
            safenet_pct = strategy.L_SAFENET_PCT
            safenet_price = entry_price * (1 - safenet_pct)
        else:
            safenet_pct = strategy.S_SAFENET_PCT
            safenet_price = entry_price * (1 + safenet_pct)

        # 實際下單
        try:
            import binance_trade
            order_side = "BUY" if side == "long" else "SELL"
            position_side = "LONG" if side == "long" else "SHORT"

            # 防疊倉：檢查 Binance 實際倉位
            bn_positions = binance_trade.get_positions(SYMBOL)
            bn_same_side = [p for p in bn_positions
                           if p.get("position_side") == position_side and p["size"] > 0]
            if bn_same_side:
                bn_size = bn_same_side[0]["size"]
                internal_same = sum(1 for p in self.positions.values()
                                   if ("LONG" if p["side"] == "long" else "SHORT") == position_side)
                if internal_same == 0:
                    # Binance 有倉位但內部沒有 → 孤兒倉位，不可開新倉
                    logger.error(
                        f"BLOCKED: Binance has orphaned {position_side} position "
                        f"(size={bn_size}) not in internal state. "
                        f"Cannot open new {sub_strategy}. Manual cleanup required."
                    )
                    send_telegram_message(
                        f"<b>🚨 開倉被擋！</b>\n"
                        f"⚠️ Binance 有孤兒 {position_side} 倉位（{bn_size}）\n"
                        f"❌ 內部狀態無此倉位\n"
                        f"🔧 需手動清理後才能開新倉"
                    )
                    self.trade_number -= 1
                    return None

            # 只有該方向第一筆持倉時才掛 SL（closePosition=true 覆蓋整個方向）
            existing_same_side = sum(1 for p in self.positions.values()
                                    if ("LONG" if p["side"] == "long" else "SHORT") == position_side)
            sl_price = safenet_price if existing_same_side == 0 else None
            result = binance_trade.place_order(
                SYMBOL, order_side, qty=qty, stop_loss=sl_price,
                strategy_id=f"eth_v10_{sub_strategy}",
                position_side=position_side,
            )
            if result is None:
                logger.error(f"Order failed for {trade_id}")
                self.trade_number -= 1
                return None
            avg_price = float(result.get("avgPrice", 0))
            if avg_price > 0:
                entry_price = avg_price
            qty = float(result.get("executedQty", qty))
            # 取得實際手續費
            entry_order_id = result.get("orderId")
            entry_commission = 0.0
            if entry_order_id:
                comm = binance_trade.get_order_commission(SYMBOL, entry_order_id)
                if comm is not None:
                    entry_commission = comm
                    logger.info(f"Entry commission: ${comm:.4f}")
        except Exception as e:
            logger.error(f"Order exception: {e}")
            self.trade_number -= 1
            return None

        # 計算進場指標（V13: S 用自己的 GK）
        if sub_strategy == "S":
            gk_pctile = signal_indicators.get("gk_pctile_s")
            gk_ratio = signal_indicators.get("gk_ratio_s")
        else:
            gk_pctile = signal_indicators.get("gk_pctile")
            gk_ratio = signal_indicators.get("gk_ratio")
        ema20 = signal_indicators.get("ema20")
        signal_close = signal_indicators.get("close")
        brk_max = signal_indicators.get("breakout_15bar_max")
        brk_min = signal_indicators.get("breakout_15bar_min")

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

        # 更新月度進場計數
        self.monthly_entries[sub_strategy] = self.monthly_entries.get(sub_strategy, 0) + 1

        # V25-D: 判定 entry regime（出場參數查表用）
        entry_regime = strategy.classify_regime(signal_indicators.get("sma_slope"))

        # 建立持倉記錄（V14: 含 extension + MFE trail + conditional MH 欄位；V25-D: +entry_regime）
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
            "extension_active": False,
            "extension_start_bar": 0,
            "running_mfe": 0.0,
            "mh_reduced": False,
            "entry_regime": entry_regime,
            "entry_order_id": entry_order_id,
            "entry_commission": entry_commission,
        }
        self.positions[trade_id] = position

        # 記錄到 trades.csv
        exit_cd = strategy.L_EXIT_CD if sub_strategy == "L" else strategy.S_EXIT_CD
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
            "was_cooldown_trade": bars_since == exit_cd,
            "bars_since_last_exit": bars_since,
            "btc_close_at_entry": round(btc_close, 2) if btc_close else "",
            "eth_btc_ratio_at_entry": round(eth_btc, 6) if eth_btc is not None else "",
            "eth_24h_change_pct": round(eth_24h, 2) if eth_24h is not None else "",
            "entry_regime": entry_regime,
        }
        recorder.record_trade_open(trade_record)

        # 同步幣安實際餘額
        self._sync_balance()

        # Telegram 通知
        env = "模擬" if PAPER_TRADING else "實戰"
        if sub_strategy == "L":
            direction = "做多 📈"
            sub_label = "L 多單"
            action = "🐂 衝啊！壓縮突破做多"
        else:
            direction = "做空 📉"
            sub_label = "S 空單"
            action = "🐻 空它！壓縮突破做空"
        sn_price = safenet_price
        if sub_strategy == "L":
            tp_pct = strategy.get_l_tp(entry_regime)
            max_hold = strategy.get_l_mh(entry_regime)
        else:
            tp_pct = strategy.S_TP_PCT
            max_hold = strategy.get_s_mh(entry_regime)
        msg = (
            f"<b>🎰 下注！（{env}）</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{action}\n"
            f"🏷 策略：{sub_label}\n"
            f"💲 進場價：${entry_price:.2f}\n"
            f"🎯 止盈：{tp_pct*100:.1f}% ｜ ⏰ 最長 {max_hold}h\n"
            f"🛡 安全網：${sn_price:.2f}（{safenet_pct*100:.1f}%）\n"
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
        """平倉。平倉失敗時保留持倉狀態，下一 bar 重試。"""
        pos = self.positions.get(trade_id)
        if pos is None:
            logger.warning(f"close_position: {trade_id} not found")
            return None

        side = pos["side"]
        sub_strategy = pos.get("sub_strategy", "L")
        entry_price = pos["entry_price"]

        # 實際平倉（含重試）
        actual_exit_price = exit_price  # fallback: 策略計算價
        exit_commission = 0.0
        close_confirmed = False
        try:
            import binance_trade
            import time as _time
            close_side = "SELL" if side == "long" else "BUY"
            position_side = "LONG" if side == "long" else "SHORT"

            # 嘗試平倉（最多 2 次）
            close_result = None
            for attempt in range(2):
                close_result = binance_trade.place_order(
                    SYMBOL, close_side, qty=pos["qty"], reduce_only=True,
                    strategy_id=f"eth_v10_{sub_strategy}",
                    position_side=position_side,
                )
                if close_result is not None:
                    break
                logger.warning(f"Close order attempt {attempt+1} failed for {trade_id}, retrying...")
                _time.sleep(1)

            if close_result is None:
                # 確認 Binance 上倉位是否仍在（可能超時但實際已成交）
                bn_positions = binance_trade.get_positions(SYMBOL)
                still_open = any(
                    p.get("position_side") == position_side and p["size"] > 0
                    for p in bn_positions
                )
                if still_open:
                    logger.error(
                        f"CRITICAL: Close order FAILED for {trade_id} after 2 attempts. "
                        f"Binance {position_side} position still open. "
                        f"Keeping position in state, will retry next bar."
                    )
                    send_telegram_message(
                        f"<b>🚨 平倉失敗！</b>\n"
                        f"🏷 {trade_id}\n"
                        f"⚠️ Binance 仍有 {position_side} 持倉\n"
                        f"🔄 下一根 bar 將自動重試"
                    )
                    # 標記本倉位為 pending_exit，下一 bar main loop 會直接以 MARKET
                    # 強制平倉、不再檢查策略條件（避免 TP 區間已偏離導致漏接出場）
                    pos["pending_exit"] = exit_reason
                    self.save_state()
                    return None  # 不刪除持倉，不取消 SL，下一 bar 重試
                else:
                    logger.warning(
                        f"Close order returned None but Binance {position_side} "
                        f"appears closed. Proceeding with strategy exit price."
                    )
                    close_confirmed = True
            else:
                close_confirmed = True

            # 使用實際成交價
            if close_result:
                avg = float(close_result.get("avgPrice", 0))
                if avg > 0:
                    actual_exit_price = avg
                    logger.info(f"Actual exit price: ${avg:.4f} (strategy: ${exit_price:.4f})")
                # 查詢實際手續費
                close_order_id = close_result.get("orderId")
                if close_order_id:
                    comm = binance_trade.get_order_commission(SYMBOL, close_order_id)
                    if comm is not None:
                        exit_commission = comm
                        logger.info(f"Exit commission: ${comm:.4f}")

            # 只有平倉確認後才取消 SL
            if close_confirmed:
                remaining = sum(1 for p in self.positions.values()
                               if p.get("trade_id") != trade_id
                               and (("LONG" if p["side"] == "long" else "SHORT") == position_side))
                if remaining == 0:
                    binance_trade.cancel_all_orders(SYMBOL, position_side=position_side)
        except Exception as e:
            logger.error(f"Close order exception: {e}")
            send_telegram_message(f"<b>🚨 平倉異常！</b>\n{trade_id}\n{str(e)[:200]}")
            return None  # 異常時也不刪除持倉

        # 使用實際成交價和手續費計算 PnL
        exit_price = actual_exit_price
        entry_commission = pos.get("entry_commission", 0.0)
        total_commission = entry_commission + exit_commission
        actual_qty = pos.get("qty", strategy.NOTIONAL / entry_price)
        if side == "long":
            gross_pnl = (exit_price - entry_price) * actual_qty
        else:
            gross_pnl = (entry_price - exit_price) * actual_qty
        pnl_usd = round(gross_pnl - total_commission, 4)
        pnl_pct = round(pnl_usd / strategy.MARGIN * 100, 4)
        bars_held = bar_counter - pos["entry_bar_counter"]

        # 同步幣安實際餘額
        self._sync_balance()
        self.last_exits[sub_strategy] = bar_counter

        # 更新風控熔斷狀態
        self.daily_pnl += pnl_usd
        self.monthly_pnl[sub_strategy] = self.monthly_pnl.get(sub_strategy, 0.0) + pnl_usd

        if pnl_usd < 0:
            self.consec_losses += 1
            if self.consec_losses >= strategy.CONSEC_LOSS_PAUSE:
                self.consec_loss_cooldown_until = bar_counter + strategy.CONSEC_LOSS_COOLDOWN
                logger.warning(f"連虧 {self.consec_losses} 筆！冷卻至 bar {self.consec_loss_cooldown_until}")
        else:
            self.consec_losses = 0

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
            "pnl_at_bar7": "",
            "pnl_at_bar12": "",
            "gross_pnl_usd": round(gross_pnl, 4),
            "commission_usd": round(total_commission, 4),
            "net_pnl_usd": round(pnl_usd, 4),
            "net_pnl_pct": round(pnl_pct, 2),
            "win_loss": "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "BREAKEVEN"),
        }
        recorder.record_trade_close(trade_id, exit_data)

        # Telegram
        env = "模擬" if PAPER_TRADING else "實戰"
        sub_label = "L 多單" if sub_strategy == "L" else "S 空單"
        exit_map = {
            "SafeNet": "🆘 安全網接住了",
            "TP": "🎯 精準止盈，完美收割",
            "MFE-trail": "📐 浮盈回吐，鎖利出場",
            "MaxHold": "⏰ 時間到，強制下課",
            "MH-ext": "⏰ 延長賽結束，收工",
            "BE": "🔄 平保出場，保本離場",
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

        cb_info = ""
        # 進場冷卻（連虧 24h 優先；否則顯示同側 L=6h / S=8h）
        if self.consec_losses >= strategy.CONSEC_LOSS_PAUSE:
            cd_bars = strategy.CONSEC_LOSS_COOLDOWN
            cb_info = f"\n⚠️ 連虧{self.consec_losses}筆，L+S 冷卻 {cd_bars}h"
        else:
            exit_cd = strategy.L_EXIT_CD if sub_strategy == "L" else strategy.S_EXIT_CD
            cb_info = f"\n⏱ {sub_strategy} 進場冷卻 {exit_cd}h（避免反覆進出）"
        if self.daily_pnl <= strategy.DAILY_LOSS_LIMIT:
            cb_info += f"\n🚫 日虧${self.daily_pnl:.0f}，今日停工"

        msg = (
            f"<b>{result_header}（{env}）</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🏷 {sub_label}：${pos['entry_price']:.2f} → ${exit_price:.2f}\n"
            f"📋 {exit_text}\n"
            f"💰 {result_text}（{pnl_pct:+.1f}%）\n"
            f"⏱ 抱了 {bars_held}h ｜ 最慘 -{abs(pos.get('mae_pct', 0)):.1f}%\n"
            f"🏦 金庫：${self.account_balance:.2f}{cb_info}"
        )
        send_telegram_message(msg)

        del self.positions[trade_id]
        logger.info(f"Closed {trade_id} | {exit_reason} | PnL ${pnl_usd:.2f} | "
                    f"consec_losses={self.consec_losses} daily=${self.daily_pnl:.2f} "
                    f"monthly_L=${self.monthly_pnl['L']:.2f} monthly_S=${self.monthly_pnl['S']:.2f}")
        self.save_state()

        return {"pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                "bars_held": bars_held, "trade_id": trade_id,
                "commission": total_commission}

    def update_tracking(self, trade_id: str, bar_data: dict, bar_counter: int):
        """每 bar 更新持倉追蹤：MAE/MFE。"""
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

        if side == "long":
            adverse = (bar_low - entry_price) / entry_price * 100
            favorable = (bar_high - entry_price) / entry_price * 100
        else:
            adverse = (entry_price - bar_high) / entry_price * 100
            favorable = (entry_price - bar_low) / entry_price * 100

        if adverse < pos.get("mae_pct", 0):
            pos["mae_pct"] = adverse
            pos["mae_time_bar"] = bars_held

        if favorable > pos.get("mfe_pct", 0):
            pos["mfe_pct"] = favorable
            pos["mfe_time_bar"] = bars_held

    def get_open_positions(self) -> list:
        """回傳所有持倉的 list"""
        return list(self.positions.values())

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 每日統計
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _today_key(self) -> str:
        # 用 UTC+8 確保與 main_eth.py 的日結 rollover (flush_daily_summary) 對齊；
        # 舊版用 datetime.utcnow() 會讓 00:00-08:00 UTC+8 的交易被歸到前一 UTC+8 日，
        # daily_summary.csv 日期攤銷會錯 8 小時。
        return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")

    def _ensure_daily(self):
        key = self._today_key()
        if key not in self.daily_stats:
            self.daily_stats[key] = {
                "trades_opened": 0, "trades_closed": 0,
                "wins": 0, "losses": 0,
                "pnl": 0.0, "signals_fired": 0, "signals_blocked": 0,
                "safenet_count": 0,
                "tp_count": 0, "mfe_trail_count": 0, "maxhold_count": 0,
                "mh_ext_count": 0, "be_count": 0,
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

    def record_close(self, pnl_usd: float, exit_reason: str, hold_bars: int, commission: float = 0.0):
        self._ensure_daily()
        d = self.daily_stats[self._today_key()]
        d["total_commission"] = d.get("total_commission", 0) + commission
        d["trades_closed"] += 1
        d["pnl"] += pnl_usd
        if pnl_usd > 0:
            d["wins"] += 1
        elif pnl_usd < 0:
            d["losses"] += 1
        reason_map = {
            "SafeNet": "safenet_count",
            "TP": "tp_count",
            "MFE-trail": "mfe_trail_count",
            "MaxHold": "maxhold_count",
            "MH-ext": "mh_ext_count",
            "BE": "be_count",
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
        s_count = sum(1 for p in self.positions.values() if p.get("sub_strategy") == "S")
        open_pos = f"L:{l_count} S:{s_count}" if (l_count + s_count) > 0 else ""

        stats = {
            "date": date_str,
            "total_trades": d["trades_opened"],
            "long_trades": "",
            "short_trades": "",
            "wins": d["wins"],
            "losses": d["losses"],
            "gross_pnl": round(d["pnl"] + d.get("total_commission", 0), 2),
            "net_pnl": round(d["pnl"], 2),
            "safenet_count": d["safenet_count"],
            "earlyStop_count": 0,
            "trail_count": 0,
            "tp_count": d.get("tp_count", 0),
            # V14 新增出場類型：MFE-trail（浮盈回吐鎖利），過去漏填此欄永遠為空
            "mfe_trail_count": d.get("mfe_trail_count", 0),
            "maxhold_count": d.get("maxhold_count", 0),
            "mh_ext_count": d.get("mh_ext_count", 0),
            "be_count": d.get("be_count", 0),
            "avg_hold_hours": round(avg_hold, 1),
            "longest_hold_hours": d["max_hold_hours"],
            "account_balance": round(self.account_balance, 2),
            "cumulative_pnl": round(self.account_balance - 1000.0, 2),
            "open_position": open_pos,
            "system_alerts": 0,
        }
        recorder.record_daily_summary(stats)
