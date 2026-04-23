"""
ETH 1h V14 雙策略 L+S 主循環

單執行緒架構：每小時整點 + 10s 喚醒一次
  1. 取 K 線資料 + 計算指標
  2. 更新風控熔斷（日/月 rollover）
  3. 檢查持倉出場（L → check_exit_long, S → check_exit_short）
  4. 評估進場信號（L/S 各自獨立，maxTotal=1）
  5. 記錄 bar_snapshot + position_lifecycle
  6. 日結統計
  7. 狀態持久化
  8. 定時心跳

策略規格 V14，見 strategy.py。
"""
import os
import sys
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

import threading
import strategy
import data_feed
import recorder
from executor import Executor
from telegram_notify import send_telegram_message, get_pending_commands, skip_old_updates

load_dotenv()

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
SYMBOL = os.getenv("SYMBOL_ETH", "ETHUSDT")
HEARTBEAT_INTERVAL = 1  # 每小時發一次心跳

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def setup_logging():
    fmt = "%(asctime)s [%(name)s] %(levelname)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # system.log — 全部
    system_handler = logging.FileHandler(
        os.path.join(LOGS_DIR, "system.log"), encoding="utf-8"
    )
    system_handler.setLevel(logging.DEBUG)
    system_handler.setFormatter(logging.Formatter(fmt, datefmt))

    # signal.log — 只記信號和交易
    signal_handler = logging.FileHandler(
        os.path.join(LOGS_DIR, "signal.log"), encoding="utf-8"
    )
    signal_handler.setLevel(logging.INFO)
    signal_handler.setFormatter(logging.Formatter(fmt, datefmt))
    signal_handler.addFilter(logging.Filter("signal"))

    # alerts.log — WARNING 以上
    alerts_handler = logging.FileHandler(
        os.path.join(LOGS_DIR, "alerts.log"), encoding="utf-8"
    )
    alerts_handler.setLevel(logging.WARNING)
    alerts_handler.setFormatter(logging.Formatter(fmt, datefmt))

    # Console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(system_handler)
    root.addHandler(alerts_handler)
    root.addHandler(console_handler)

    # signal logger 獨立用自己的 handler
    sig_logger = logging.getLogger("signal")
    sig_logger.addHandler(signal_handler)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 時間工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def now_utc8():
    """取得當前 UTC+8 時間"""
    return datetime.now(timezone(timedelta(hours=8)))


def sleep_until_next_hour(offset_seconds=10):
    """
    睡到下一個整點 + offset_seconds。
    例：offset=10 → 睡到 xx:00:10。
    """
    now = datetime.now(timezone.utc)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    target = next_hour + timedelta(seconds=offset_seconds)
    wait = (target - now).total_seconds()
    if wait < 0:
        wait = 0
    if wait > 0:
        logging.getLogger("main").info(f"Sleeping {wait:.0f}s until {target.strftime('%H:%M:%S')} UTC")
        time.sleep(wait)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Bar 資料提取工具
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def bar_to_dict(df_row) -> dict:
    """把 DataFrame row 轉成 dict"""
    return {
        "datetime": df_row["datetime"],
        "open": float(df_row["open"]),
        "high": float(df_row["high"]),
        "low": float(df_row["low"]),
        "close": float(df_row["close"]),
        "volume": float(df_row["volume"]),
        "taker_buy_volume": float(df_row.get("taker_buy_volume", 0)),
    }


def indicators_to_dict(df_row) -> dict:
    """從指標 DataFrame row 提取指標值"""
    import numpy as np

    def safe(val):
        if val is None:
            return None
        if isinstance(val, (float, np.floating)) and np.isnan(val):
            return None
        return float(val)

    return {
        "gk_pctile": safe(df_row.get("gk_pctile")),
        "gk_ratio": safe(df_row.get("gk_ratio")),
        "gk_pctile_s": safe(df_row.get("gk_pctile_s")),
        "gk_ratio_s": safe(df_row.get("gk_ratio_s")),
        "ema20": safe(df_row.get("ema20")),
        "close": safe(df_row.get("close")),
        "close_shift1": safe(df_row.get("close_shift1")),
        "breakout_15bar_max": safe(df_row.get("breakout_15bar_max")),
        "breakout_15bar_min": safe(df_row.get("breakout_15bar_min")),
        "breakout_long": bool(df_row.get("breakout_long", False)),
        "breakout_short": bool(df_row.get("breakout_short", False)),
        "session_ok_l": bool(df_row.get("session_ok_l", False)),
        "session_ok_s": bool(df_row.get("session_ok_s", False)),
        "hour_utc8": int(df_row.get("hour_utc8", -1)),
        "weekday_utc8": int(df_row.get("weekday_utc8", -1)),
        # V14+R Regime Gate
        "sma200": safe(df_row.get("sma200")),
        "sma_slope": safe(df_row.get("sma_slope")),
        "regime_block_l": bool(df_row.get("regime_block_l", False)),
        "regime_block_s": bool(df_row.get("regime_block_s", False)),
    }


def get_position_state(executor) -> dict:
    """構建當前持倉狀態快照（給 bar_snapshot 用）"""
    positions = executor.get_open_positions()
    l_count = sum(1 for p in positions if p.get("sub_strategy") == "L")
    s_count = sum(1 for p in positions if p.get("sub_strategy") == "S")
    return {
        "long_positions": l_count,
        "short_positions": s_count,
    }


def calc_eth_24h_change(df, idx) -> float:
    """計算 ETH 24h 漲跌幅"""
    if idx < 24:
        return None
    close_now = float(df.iloc[idx]["close"])
    close_24h = float(df.iloc[idx - 24]["close"])
    if close_24h == 0:
        return None
    return (close_now - close_24h) / close_24h * 100


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Telegram 指令處理
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _handle_cleanup(executor, cmd_logger):
    """清理孤兒倉位：平掉 Binance 有但內部沒有的倉位。"""
    try:
        import binance_trade
        bn_positions = binance_trade.get_positions(SYMBOL)
        internal_sides = set()
        for p in executor.positions.values():
            internal_sides.add("LONG" if p["side"] == "long" else "SHORT")

        orphans = [bp for bp in bn_positions
                   if bp["size"] > 0 and bp.get("position_side") not in internal_sides]

        if not orphans:
            send_telegram_message("✅ 沒有孤兒倉位，一切正常！")
            return

        results = []
        for bp in orphans:
            ps = bp["position_side"]
            size = bp["size"]
            close_side = "SELL" if ps == "LONG" else "BUY"
            cmd_logger.info(f"Cleaning orphan: {ps} {size} ETH")

            # 先取消該方向所有訂單（包括 SL）
            try:
                binance_trade.cancel_all_orders(SYMBOL, position_side=ps)
            except Exception:
                pass

            # 市價平倉
            result = binance_trade.place_order(
                SYMBOL, close_side, qty=size, reduce_only=True,
                strategy_id="cleanup",
                position_side=ps,
            )
            if result is not None:
                avg = float(result.get("avgPrice", 0))
                results.append(f"✅ {ps} {size} ETH 已平倉 @ ${avg:.2f}")
                cmd_logger.info(f"Orphan closed: {ps} {size} @ ${avg:.2f}")
            else:
                results.append(f"❌ {ps} {size} ETH 平倉失敗")
                cmd_logger.error(f"Orphan close failed: {ps} {size}")

        msg = "<b>🧹 孤兒清理結果</b>\n" + "\n".join(results)
        send_telegram_message(msg)
    except Exception as e:
        cmd_logger.error(f"Cleanup error: {e}")
        send_telegram_message(f"❌ 清理失敗：{str(e)[:200]}")


def _handle_status(executor, cmd_logger):
    """回報內部狀態 vs Binance 實際倉位。"""
    try:
        import binance_trade
        bn_positions = binance_trade.get_positions(SYMBOL)

        lines = ["<b>📊 倉位同步狀態</b>\n"]

        # 內部持倉
        if executor.positions:
            lines.append("<b>內部持倉：</b>")
            for tid, p in executor.positions.items():
                ps = "LONG" if p["side"] == "long" else "SHORT"
                lines.append(f"  {ps} {p.get('qty', 0):.4f} ETH @ ${p['entry_price']:.2f}")
        else:
            lines.append("內部持倉：空")

        # Binance 持倉
        if bn_positions:
            lines.append("\n<b>Binance 持倉：</b>")
            for bp in bn_positions:
                if bp["size"] > 0:
                    lines.append(
                        f"  {bp['position_side']} {bp['size']} ETH "
                        f"@ ${bp['entry_price']:.2f} "
                        f"(PnL ${bp['unrealized_pnl']:.2f})"
                    )
        else:
            lines.append("\nBinance 持倉：空")

        # 比對
        internal_sides = {("LONG" if p["side"] == "long" else "SHORT")
                         for p in executor.positions.values()}
        bn_sides = {bp["position_side"] for bp in bn_positions if bp["size"] > 0}
        orphans = bn_sides - internal_sides
        ghosts = internal_sides - bn_sides
        if orphans:
            lines.append(f"\n⚠️ 孤兒：{', '.join(orphans)}（/cleanup 可清理）")
        if ghosts:
            lines.append(f"\n⚠️ 幽靈：{', '.join(ghosts)}（內部有 Binance 無）")
        if not orphans and not ghosts:
            lines.append("\n✅ 內部與 Binance 同步正常")

        lines.append(f"\n💰 餘額：${executor.account_balance:.2f}")
        send_telegram_message("\n".join(lines))
    except Exception as e:
        cmd_logger.error(f"Status error: {e}")
        send_telegram_message(f"❌ 查詢失敗：{str(e)[:200]}")


def _handle_balance(executor, cmd_logger):
    """回報餘額 + 未實現損益。"""
    try:
        import binance_trade
        bn_positions = binance_trade.get_positions(SYMBOL)
        unrealized = sum(bp.get("unrealized_pnl", 0) for bp in bn_positions if bp["size"] > 0)
        total = executor.account_balance + unrealized

        lines = [
            "<b>💰 帳戶概覽</b>",
            f"🏦 錢包餘額：${executor.account_balance:.2f}",
        ]
        if unrealized != 0:
            emoji = "📈" if unrealized > 0 else "📉"
            lines.append(f"{emoji} 未實現損益：${unrealized:+.2f}")
            lines.append(f"💎 淨值：${total:.2f}")

        # 保證金使用
        active = sum(1 for _ in executor.positions)
        margin_used = active * strategy.MARGIN
        lines.append(f"🔒 保證金佔用：${margin_used:.0f} / ${strategy.MARGIN * 2:.0f}")

        send_telegram_message("\n".join(lines))
    except Exception as e:
        cmd_logger.error(f"Balance error: {e}")
        send_telegram_message(f"❌ 查詢失敗：{str(e)[:200]}")


def _handle_pnl(executor, cmd_logger):
    """回報今日 + 本月 PnL。"""
    try:
        # 今日
        today_key = datetime.utcnow().strftime("%Y-%m-%d")
        today = executor.daily_stats.get(today_key, {})
        today_pnl = today.get("pnl", 0.0)
        today_trades = today.get("trades_closed", 0)
        today_wins = today.get("wins", 0)
        today_losses = today.get("losses", 0)
        today_wr = (today_wins / today_trades * 100) if today_trades > 0 else 0

        # 本月
        l_pnl = executor.monthly_pnl.get("L", 0.0)
        s_pnl = executor.monthly_pnl.get("S", 0.0)
        month_total = l_pnl + s_pnl
        l_entries = executor.monthly_entries.get("L", 0)
        s_entries = executor.monthly_entries.get("S", 0)

        # 最近 7 天彙總
        week_pnl = 0.0
        week_trades = 0
        for key in sorted(executor.daily_stats.keys(), reverse=True)[:7]:
            d = executor.daily_stats[key]
            week_pnl += d.get("pnl", 0.0)
            week_trades += d.get("trades_closed", 0)

        lines = [
            "<b>📊 損益報表</b>",
            "━━━━━━━━━━━━━━━",
            f"<b>今日</b>（{today_key}）",
            f"  💵 PnL：${today_pnl:+.2f}",
            f"  📝 交易：{today_trades} 筆（{today_wins}W {today_losses}L，WR {today_wr:.0f}%）",
            "",
            f"<b>本月</b>（{executor.monthly_key or 'N/A'}）",
            f"  💵 合計：${month_total:+.2f}",
            f"  📈 L 做多：${l_pnl:+.2f}（{l_entries} 筆）",
            f"  📉 S 做空：${s_pnl:+.2f}（{s_entries} 筆）",
            "",
            f"<b>近 7 天</b>",
            f"  💵 PnL：${week_pnl:+.2f}（{week_trades} 筆）",
        ]
        send_telegram_message("\n".join(lines))
    except Exception as e:
        cmd_logger.error(f"PnL error: {e}")
        send_telegram_message(f"❌ 查詢失敗：{str(e)[:200]}")


def _handle_trades(executor, cmd_logger):
    """回報最近 5 筆交易。"""
    try:
        import csv
        data_dir = os.path.join(os.path.dirname(__file__),
                                "data" if PAPER_TRADING else "data_live")
        trades_path = os.path.join(data_dir, "trades.csv")
        if not os.path.exists(trades_path):
            send_telegram_message("📭 尚無交易記錄")
            return

        # 讀取最後 5 筆已平倉交易
        rows = []
        with open(trades_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("exit_type"):  # 已平倉
                    rows.append(row)
        recent = rows[-5:]

        if not recent:
            send_telegram_message("📭 尚無已平倉交易")
            return

        lines = ["<b>📋 最近交易</b>", "━━━━━━━━━━━━━━━"]
        for t in reversed(recent):
            side = "🟢L" if t.get("sub_strategy") == "L" else "🔴S"
            pnl = float(t.get("net_pnl_usd", 0))
            wl = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
            entry_time = t.get("entry_time_utc8", "")[:16]
            exit_type = t.get("exit_type", "")
            hold = t.get("hold_hours", "?")
            lines.append(
                f"{wl} {side} ${pnl:+.2f} | {exit_type} | {hold}h | {entry_time}"
            )

        send_telegram_message("\n".join(lines))
    except Exception as e:
        cmd_logger.error(f"Trades error: {e}")
        send_telegram_message(f"❌ 查詢失敗：{str(e)[:200]}")


def _handle_circuit_breaker(executor, cmd_logger):
    """回報風控熔斷狀態。"""
    try:
        l_pnl = executor.monthly_pnl.get("L", 0.0)
        s_pnl = executor.monthly_pnl.get("S", 0.0)
        l_entries = executor.monthly_entries.get("L", 0)
        s_entries = executor.monthly_entries.get("S", 0)

        lines = [
            "<b>🛡 風控熔斷狀態</b>",
            "━━━━━━━━━━━━━━━",
            f"<b>日虧限額</b>",
            f"  今日 PnL：${executor.daily_pnl:+.2f} / ${strategy.DAILY_LOSS_LIMIT}",
            f"  {'🔴 已觸發！' if executor.daily_pnl <= strategy.DAILY_LOSS_LIMIT else '🟢 正常'}",
            "",
            f"<b>月虧限額</b>",
            f"  L：${l_pnl:+.2f} / ${strategy.L_MONTHLY_LOSS_CAP}",
            f"  {'🔴 已觸發！' if l_pnl <= strategy.L_MONTHLY_LOSS_CAP else '🟢 正常'}",
            f"  S：${s_pnl:+.2f} / ${strategy.S_MONTHLY_LOSS_CAP}",
            f"  {'🔴 已觸發！' if s_pnl <= strategy.S_MONTHLY_LOSS_CAP else '🟢 正常'}",
            "",
            f"<b>月進場</b>",
            f"  L：{l_entries} / {strategy.L_MONTHLY_ENTRY_CAP}",
            f"  S：{s_entries} / {strategy.S_MONTHLY_ENTRY_CAP}",
            "",
            f"<b>連虧</b>",
            f"  連虧筆數：{executor.consec_losses} / {strategy.CONSEC_LOSS_PAUSE}",
        ]

        if executor.consec_losses >= strategy.CONSEC_LOSS_PAUSE:
            remaining = executor.consec_loss_cooldown_until - executor.bar_counter
            if remaining > 0:
                lines.append(f"  🔴 冷卻中，剩餘 {remaining} bar")
            else:
                lines.append("  🟢 冷卻已結束")

        # 暫停狀態
        if getattr(executor, "paused", False):
            lines.append("\n⏸ <b>手動暫停中</b>（/resume 恢復）")

        send_telegram_message("\n".join(lines))
    except Exception as e:
        cmd_logger.error(f"CB error: {e}")
        send_telegram_message(f"❌ 查詢失敗：{str(e)[:200]}")


def _handle_pause(executor, cmd_logger):
    """暫停開新倉（持倉出場不受影響）。"""
    executor.paused = True
    executor.save_state()
    cmd_logger.info("Trading PAUSED by Telegram command")
    send_telegram_message(
        "<b>⏸ 已暫停開新倉</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "🔒 不再開新倉位\n"
        "✅ 現有持倉照常監控出場\n"
        "▶️ 輸入 /resume 恢復交易"
    )


def _handle_resume(executor, cmd_logger):
    """恢復交易。"""
    executor.paused = False
    executor.save_state()
    cmd_logger.info("Trading RESUMED by Telegram command")
    send_telegram_message(
        "<b>▶️ 已恢復交易</b>\n"
        "🟢 開新倉功能已啟用"
    )


def _handle_alerts(cmd_logger):
    """回傳今日 alerts.log 內容（最多 10 筆）。"""
    alerts_path = os.path.join(LOGS_DIR, "alerts.log")
    if not os.path.exists(alerts_path):
        send_telegram_message("✅ 無告警日誌檔")
        return
    today_prefix = now_utc8().strftime("%Y-%m-%d")
    hits = []
    try:
        with open(alerts_path, "r", encoding="utf-8") as af:
            for line in af:
                if len(line) >= 10 and line[:10] == today_prefix:
                    hits.append(line.rstrip())
    except Exception as e:
        cmd_logger.error(f"Alerts read error: {e}")
        send_telegram_message(f"❌ 讀取失敗：{str(e)[:200]}")
        return
    if not hits:
        send_telegram_message(f"✅ 今日（{today_prefix}）無 ERROR/WARNING")
        return
    # 只留最近 10 筆，每筆精簡顯示
    recent = hits[-10:]
    lines = [f"<b>⚠️ 今日告警（{len(hits)} 筆，顯示最新 {len(recent)}）</b>",
             "━━━━━━━━━━━━━━━"]
    for h in recent:
        # 格式：時間 [module] LEVEL  訊息 → 縮成「HH:MM LEVEL msg」
        try:
            ts = h[11:16]  # "HH:MM"
            # 切 level 與 msg
            after = h[20:]  # 跳過完整時間戳
            if "]" in after:
                lvl_msg = after.split("]", 1)[1].strip()
            else:
                lvl_msg = after
            # 截斷過長訊息
            if len(lvl_msg) > 80:
                lvl_msg = lvl_msg[:77] + "..."
            lines.append(f"<code>{ts}</code> {lvl_msg}")
        except Exception:
            lines.append(h[:100])
    send_telegram_message("\n".join(lines))


def _handle_help():
    """回傳可用指令列表。"""
    send_telegram_message(
        "<b>🤖 可用指令</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "/status — 倉位同步狀態（內部 vs Binance）\n"
        "/bal — 帳戶餘額 + 未實現損益\n"
        "/pnl — 今日 / 本月損益報表\n"
        "/trades — 最近 5 筆交易\n"
        "/alerts — 今日告警日誌\n"
        "/cb — 風控熔斷狀態\n"
        "/pause — 暫停開新倉\n"
        "/resume — 恢復交易\n"
        "/cleanup — 清理孤兒倉位\n"
        "/help — 顯示此說明"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 主循環
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    setup_logging()
    logger = logging.getLogger("main")
    sig_logger = logging.getLogger("signal")

    mode = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info(f"=" * 60)
    logger.info(f"  ETH 1h V14 雙策略 L+S ({mode})")
    logger.info(f"  L: GK<{strategy.L_GK_THRESH} BRK{strategy.BRK_LOOK} TP{strategy.L_TP_PCT*100:.1f}% MH{strategy.L_MAX_HOLD} SN{strategy.L_SAFENET_PCT*100:.1f}%")
    logger.info(f"  S: GK<{strategy.S_GK_THRESH} BRK{strategy.BRK_LOOK} TP{strategy.S_TP_PCT*100:.1f}% MH{strategy.S_MAX_HOLD} SN{strategy.S_SAFENET_PCT*100:.1f}%")
    logger.info(f"  Symbol: {SYMBOL} | Notional: ${strategy.NOTIONAL} | Fee: ${strategy.FEE}")
    logger.info(f"=" * 60)

    # 初始化 Executor
    executor = Executor()
    logger.info(f"Executor loaded: {len(executor.positions)} positions, "
                f"balance=${executor.account_balance:.2f}")

    # 設定槓桿 + 確認 Hedge Mode（Paper=testnet, Live=production）
    try:
        import binance_trade
        binance_trade.set_leverage(SYMBOL, strategy.LEVERAGE)
        logger.info(f"Leverage set to {strategy.LEVERAGE}x")
        # 確保 Hedge Mode（雙向持倉），L/S 才能獨立運作
        mode = binance_trade.client.get_position_mode()
        if not mode.get("dualSidePosition"):
            binance_trade.client.change_position_mode(dualSidePosition="true")
            logger.info("Switched to Hedge Mode (dual side)")
        else:
            logger.info("Hedge Mode confirmed")
    except Exception as e:
        logger.error(f"Failed to set leverage/mode: {e}")

    # 啟動倉位同步檢查：偵測 Binance 孤兒倉位
    try:
        import binance_trade
        bn_positions = binance_trade.get_positions(SYMBOL)
        internal_sides = set()
        for p in executor.positions.values():
            internal_sides.add("LONG" if p["side"] == "long" else "SHORT")
        for bp in bn_positions:
            ps = bp.get("position_side")
            if bp["size"] > 0 and ps not in internal_sides:
                logger.error(
                    f"ORPHAN DETECTED: Binance {ps} position "
                    f"(size={bp['size']}, entry=${bp['entry_price']:.2f}) "
                    f"not in internal state!"
                )
                send_telegram_message(
                    f"<b>🚨 啟動偵測到孤兒倉位！</b>\n"
                    f"📍 Binance {ps}：{bp['size']} ETH @ ${bp['entry_price']:.2f}\n"
                    f"⚠️ 內部狀態無此倉位\n"
                    f"🔧 請手動清理（取消該方向訂單 + 市價平倉）"
                )
    except Exception as e:
        logger.warning(f"Startup position sync check failed: {e}")

    # 啟動通知
    env = "模擬" if PAPER_TRADING else "實戰"
    l_count = sum(1 for p in executor.positions.values() if p.get("sub_strategy") == "L")
    s_count = sum(1 for p in executor.positions.values() if p.get("sub_strategy") == "S")
    pos_text = f"L:{l_count} S:{s_count}" if (l_count + s_count) > 0 else "空手待命"
    startup_msg = (
        f"<b>🖨 印鈔機開機！V14（{env}）</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔧 配方：L 做多 + S 做空（各最多1筆）\n"
        f"💼 口袋：${executor.account_balance:.2f}\n"
        f"📊 持倉：{pos_text}\n"
        f"⏱ 已印：{executor.bar_counter} 張（K棒）"
    )
    send_telegram_message(startup_msg)

    # ── Telegram 指令監聽（背景執行緒）──
    skip_old_updates()  # 跳過啟動前的舊訊息

    def telegram_command_listener():
        """每 10 秒輪詢 Telegram 指令。"""
        cmd_logger = logging.getLogger("telegram_cmd")
        while True:
            try:
                commands = get_pending_commands()
                for cmd in commands:
                    cmd_lower = cmd.lower().split()[0]  # 取第一個字（忽略參數）
                    cmd_logger.info(f"Received command: {cmd}")

                    if cmd_lower in ("/cleanup", "/clean"):
                        _handle_cleanup(executor, cmd_logger)
                    elif cmd_lower in ("/status", "/pos"):
                        _handle_status(executor, cmd_logger)
                    elif cmd_lower in ("/bal", "/balance"):
                        _handle_balance(executor, cmd_logger)
                    elif cmd_lower == "/pnl":
                        _handle_pnl(executor, cmd_logger)
                    elif cmd_lower == "/trades":
                        _handle_trades(executor, cmd_logger)
                    elif cmd_lower in ("/alerts", "/warn"):
                        _handle_alerts(cmd_logger)
                    elif cmd_lower == "/cb":
                        _handle_circuit_breaker(executor, cmd_logger)
                    elif cmd_lower == "/pause":
                        _handle_pause(executor, cmd_logger)
                    elif cmd_lower == "/resume":
                        _handle_resume(executor, cmd_logger)
                    elif cmd_lower == "/help":
                        _handle_help()
                    else:
                        send_telegram_message(f"❓ 未知指令：{cmd}\n輸入 /help 查看可用指令")
            except Exception as e:
                cmd_logger.debug(f"Command listener error: {e}")
            time.sleep(10)

    cmd_thread = threading.Thread(target=telegram_command_listener, daemon=True)
    cmd_thread.start()
    logger.info("Telegram command listener started (10s polling)")

    last_heartbeat_bar = executor.bar_counter
    last_daily_date = None

    # ── 主循環 ──
    while True:
        try:
            sleep_until_next_hour(offset_seconds=10)

            cycle_start = time.time()
            t_utc8 = now_utc8()
            logger.info(f"── Cycle {executor.bar_counter + 1} | {t_utc8.strftime('%Y-%m-%d %H:%M')} UTC+8 ──")

            # ── 1. 取資料 ──
            eth_df, btc_df = data_feed.fetch_eth_and_btc()
            df = strategy.compute_indicators(eth_df)
            idx = len(df) - 2  # 最新已收盤 bar

            bar_time = df.iloc[idx]["datetime"]
            bar_time_str = str(bar_time)

            # 防止重複處理同一根 bar
            if executor.last_bar_time == bar_time_str:
                logger.warning(f"Duplicate bar: {bar_time_str}, skipping")
                continue

            # 遞增 bar counter
            executor.bar_counter += 1
            executor.last_bar_time = bar_time_str

            bar_data = bar_to_dict(df.iloc[idx])
            ind = indicators_to_dict(df.iloc[idx])
            btc_context = data_feed.get_btc_context(btc_df)

            logger.info(f"Bar: {bar_time_str} | C={bar_data['close']:.2f} | "
                        f"GK={ind['gk_pctile']:.1f}" if ind['gk_pctile'] else
                        f"Bar: {bar_time_str} | C={bar_data['close']:.2f} | GK=NaN")

            # ── 2. 同步幣安餘額 + 更新風控熔斷 ──
            executor._sync_balance()
            executor.update_period_keys(t_utc8)

            # ── 2.5 每小時倉位同步巡檢 ──
            try:
                import binance_trade as _bt
                bn_pos = _bt.get_positions(SYMBOL)
                # 建立內部狀態的方向集合
                internal_sides = {}
                for p in executor.positions.values():
                    ps = "LONG" if p["side"] == "long" else "SHORT"
                    internal_sides[ps] = internal_sides.get(ps, 0) + p.get("qty", 0)
                # 檢查 Binance 有但內部沒有的倉位（孤兒）
                for bp in bn_pos:
                    ps = bp.get("position_side")
                    if bp["size"] > 0 and ps not in internal_sides:
                        logger.error(
                            f"ORPHAN: Binance {ps} {bp['size']} ETH "
                            f"@ ${bp['entry_price']:.2f} not in internal state!"
                        )
                        send_telegram_message(
                            f"<b>🚨 孤兒倉位！</b>\n"
                            f"📍 Binance {ps}：{bp['size']} ETH @ ${bp['entry_price']:.2f}\n"
                            f"⚠️ 內部狀態無此倉位\n"
                            f"🔧 請手動清理"
                        )
                # 檢查內部有但 Binance 沒有的倉位（幽靈）
                bn_sides = {bp.get("position_side") for bp in bn_pos if bp["size"] > 0}
                for ps, qty in internal_sides.items():
                    if ps not in bn_sides:
                        logger.error(
                            f"GHOST: Internal {ps} position (qty={qty:.4f}) "
                            f"not found on Binance!"
                        )
                        send_telegram_message(
                            f"<b>🚨 幽靈倉位！</b>\n"
                            f"📍 內部 {ps}：{qty:.4f} ETH\n"
                            f"⚠️ Binance 無此倉位\n"
                            f"🔧 可能已被清算或手動平倉"
                        )
            except Exception as e:
                logger.warning(f"Position sync check failed: {e}")

            # ── 3. 檢查持倉出場 ──
            exits_this_bar = []
            ema20 = float(df.iloc[idx]["ema20"])
            for pos in list(executor.get_open_positions()):
                trade_id = pos["trade_id"]
                side = pos["side"]
                sub = pos.get("sub_strategy", "L")

                # 跳過無效持倉（entry_price=0，testnet 異常）
                if not pos.get("entry_price") or pos["entry_price"] <= 0:
                    logger.error(f"Skipping invalid position {trade_id}: entry_price={pos.get('entry_price')}")
                    continue

                # 更新追蹤（MAE/MFE）
                executor.update_tracking(trade_id, bar_data, executor.bar_counter)

                # 上 bar 有掛著的 pending_exit（前次下單 Binance 408 等失敗但倉位仍在）
                # → 本 bar 直接以 MARKET 強制平倉，避免因價格已離開 TP 區間而漏接出場
                if pos.get("pending_exit"):
                    pending_reason = pos["pending_exit"]
                    logger.warning(f"Retrying pending close for {trade_id} (reason: {pending_reason})")
                    result = executor.close_position(
                        trade_id=trade_id,
                        exit_price=bar_data["close"],  # 重試用本根收盤價
                        exit_reason=pending_reason,
                        bar_counter=executor.bar_counter,
                        bar_data=bar_data,
                        btc_context=btc_context,
                    )
                    if result:
                        executor.record_close(
                            result["pnl_usd"],
                            result["exit_reason"],
                            result["bars_held"],
                            commission=result.get("commission", 0.0),
                        )
                        exits_this_bar.append(result)
                        sig_logger.info(
                            f"EXIT {sub} {side.upper()} | {pending_reason} (retry) "
                            f"@ ${bar_data['close']:.2f} | PnL ${result['pnl_usd']:.2f}"
                        )
                    # 無論重試成功與否，本 bar 這筆倉位就不再跑策略出場檢查
                    continue

                # 按策略分派出場檢查（V14: 傳入 extension 狀態）
                ext_active = pos.get("extension_active", False)
                ext_start = pos.get("extension_start_bar", 0)

                if sub == "L":
                    exit_result = strategy.check_exit_long(
                        entry_price=pos["entry_price"],
                        entry_bar_counter=pos["entry_bar_counter"],
                        current_bar_counter=executor.bar_counter,
                        bar_high=bar_data["high"],
                        bar_low=bar_data["low"],
                        bar_close=bar_data["close"],
                        extension_active=ext_active,
                        extension_start_bar=ext_start,
                        running_mfe=pos.get("running_mfe", 0.0),
                        mh_reduced=pos.get("mh_reduced", False),
                        entry_regime=pos.get("entry_regime", "NA"),
                    )
                    # V14: 更新 running_mfe 和 mh_reduced 到持倉狀態
                    executor.positions[trade_id]["running_mfe"] = exit_result.get("running_mfe", 0.0)
                    executor.positions[trade_id]["mh_reduced"] = exit_result.get("mh_reduced", False)
                else:  # S
                    exit_result = strategy.check_exit_short(
                        entry_price=pos["entry_price"],
                        entry_bar_counter=pos["entry_bar_counter"],
                        current_bar_counter=executor.bar_counter,
                        bar_high=bar_data["high"],
                        bar_low=bar_data["low"],
                        bar_close=bar_data["close"],
                        extension_active=ext_active,
                        extension_start_bar=ext_start,
                        entry_regime=pos.get("entry_regime", "NA"),
                    )

                # V14: 處理延長期啟動
                if exit_result.get("start_extension") and not ext_active:
                    executor.positions[trade_id]["extension_active"] = True
                    executor.positions[trade_id]["extension_start_bar"] = executor.bar_counter
                    logger.info(f"Extension started for {trade_id} ({sub})")

                # 記錄 lifecycle
                recorder.record_position_bar(
                    trade_id=trade_id,
                    position=pos,
                    bar_data=bar_data,
                    ema20=ema20,
                    exit_result=exit_result if exit_result["exit"] else None,
                )

                if exit_result["exit"]:
                    result = executor.close_position(
                        trade_id=trade_id,
                        exit_price=exit_result["exit_price"],
                        exit_reason=exit_result["reason"],
                        bar_counter=executor.bar_counter,
                        bar_data=bar_data,
                        btc_context=btc_context,
                    )
                    if result:
                        executor.record_close(
                            result["pnl_usd"],
                            result["exit_reason"],
                            result["bars_held"],
                            commission=result.get("commission", 0.0),
                        )
                        exits_this_bar.append(result)
                        sig_logger.info(
                            f"EXIT {sub} {side.upper()} | {exit_result['reason']} "
                            f"@ ${exit_result['exit_price']:.2f} | "
                            f"PnL ${result['pnl_usd']:.2f}"
                        )

            # ── 4. 評估進場信號 ──
            bar_data_for_entry = dict(bar_data)
            bar_data_for_entry["eth_24h_change_pct"] = calc_eth_24h_change(df, idx)
            any_signal = False

            # 暫停檢查（/pause 指令）
            trading_paused = getattr(executor, "paused", False)
            if trading_paused:
                logger.info("Trading PAUSED — skipping entry signals")

            # L 信號
            l_cb_ok, l_cb_reason = executor.check_circuit_breaker("L")
            long_sig = None
            if trading_paused:
                pass
            elif l_cb_ok:
                long_sig = strategy.evaluate_long_signal(
                    df=df, idx=idx,
                    open_positions=executor.positions,
                    last_exits=executor.last_exits,
                    bar_counter=executor.bar_counter,
                    monthly_pnl_l=executor.monthly_pnl.get("L", 0.0),
                    monthly_entries_l=executor.monthly_entries.get("L", 0),
                )
            elif l_cb_reason:
                logger.debug(f"L blocked by circuit breaker: {l_cb_reason}")

            if long_sig:
                any_signal = True
                executor.record_signal(fired=True)
                sig_logger.info(f"SIGNAL BUY L | {long_sig['reason']} | GK={ind.get('gk_pctile')}")
                try:
                    fill_price = bar_data["close"]
                    trade_id = executor.open_position(
                        side="long",
                        sub_strategy="L",
                        entry_price=fill_price,
                        bar_counter=executor.bar_counter,
                        signal_indicators=long_sig["indicators"],
                        bar_data=bar_data_for_entry,
                        btc_context=btc_context,
                    )
                    if trade_id:
                        executor.record_open()
                        sig_logger.info(f"ENTRY LONG L @ ${fill_price:.2f} | {trade_id}")
                except Exception as e:
                    logger.error(f"L entry failed: {e}")

            # S 信號
            s_cb_ok, s_cb_reason = executor.check_circuit_breaker("S")
            short_sig = None
            if trading_paused:
                pass
            elif s_cb_ok:
                short_sig = strategy.evaluate_short_signal(
                    df=df, idx=idx,
                    open_positions=executor.positions,
                    last_exits=executor.last_exits,
                    bar_counter=executor.bar_counter,
                    monthly_pnl_s=executor.monthly_pnl.get("S", 0.0),
                    monthly_entries_s=executor.monthly_entries.get("S", 0),
                )
            elif s_cb_reason:
                logger.debug(f"S blocked by circuit breaker: {s_cb_reason}")

            if short_sig:
                any_signal = True
                executor.record_signal(fired=True)
                sig_logger.info(f"SIGNAL SELL S | {short_sig['reason']} | GK_S={ind.get('gk_pctile_s')}")
                try:
                    fill_price = bar_data["close"]
                    trade_id = executor.open_position(
                        side="short",
                        sub_strategy="S",
                        entry_price=fill_price,
                        bar_counter=executor.bar_counter,
                        signal_indicators=short_sig["indicators"],
                        bar_data=bar_data_for_entry,
                        btc_context=btc_context,
                    )
                    if trade_id:
                        executor.record_open()
                        sig_logger.info(f"ENTRY SHORT S @ ${fill_price:.2f} | {trade_id}")
                except Exception as e:
                    logger.error(f"S entry failed: {e}")

            if not any_signal:
                executor.record_signal(fired=False)
                logger.debug("HOLD: no L or S signals")

            # ── 5. 記錄 bar snapshot ──
            pos_state = get_position_state(executor)
            total_unr = 0.0
            for pos in executor.get_open_positions():
                ep = pos["entry_price"]
                if not ep or ep <= 0:
                    continue
                if pos["side"] == "long":
                    unr_pct = (bar_data["close"] - ep) / ep
                else:
                    unr_pct = (ep - bar_data["close"]) / ep
                total_unr += unr_pct * strategy.NOTIONAL
            pos_state["total_unrealized_pnl"] = total_unr

            long_sig_str = long_sig["reason"] if long_sig else "HOLD"
            short_sig_str = short_sig["reason"] if short_sig else "HOLD"
            detail_parts = []
            if long_sig:
                detail_parts.append(f"L:{long_sig['reason']}")
            if short_sig:
                detail_parts.append(f"S:{short_sig['reason']}")

            recorder.record_bar_snapshot(
                bar_data=bar_data,
                indicators=ind,
                signal_result={
                    "long_signal": long_sig_str,
                    "short_signals": short_sig_str,
                    "signal_detail": "|".join(detail_parts),
                },
                position_state=pos_state,
            )

            # ── 6. 日結統計 ──
            today_str = t_utc8.strftime("%Y-%m-%d")
            if last_daily_date is not None and last_daily_date != today_str:
                executor.flush_daily_summary(last_daily_date)
                logger.info(f"Daily summary flushed for {last_daily_date}")
            last_daily_date = today_str

            # ── 7. 狀態持久化 ──
            executor.save_state()

            # ── 8. 心跳 ──
            if executor.bar_counter - last_heartbeat_bar >= HEARTBEAT_INTERVAL:
                last_heartbeat_bar = executor.bar_counter
                positions = executor.get_open_positions()
                l_pos = [p for p in positions if p.get("sub_strategy") == "L"]
                s_pos = [p for p in positions if p.get("sub_strategy") == "S"]

                if positions:
                    lines = []
                    if l_pos:
                        for p in l_pos:
                            if p["entry_price"] > 0:
                                unr = (bar_data["close"] - p["entry_price"]) / p["entry_price"] * 100
                                lines.append(f"📈 L 多單（{unr:+.1f}%，抱{p['bars_held']}h）")
                    if s_pos:
                        for p in s_pos:
                            if p["entry_price"] > 0:
                                unr = (p["entry_price"] - bar_data["close"]) / p["entry_price"] * 100
                                lines.append(f"📉 S 空單（{unr:+.1f}%，抱{p['bars_held']}h）")
                    pos_text = "\n".join(lines)
                else:
                    pos_text = "空手中 🏖"

                gk_val_l = ind.get('gk_pctile')
                gk_val_s = ind.get('gk_pctile_s')

                gk_parts = []
                if gk_val_l is not None:
                    if gk_val_l < 25:
                        gk_parts.append(f"L🔥{gk_val_l:.0f}")
                    elif gk_val_l < 50:
                        gk_parts.append(f"L👀{gk_val_l:.0f}")
                    else:
                        gk_parts.append(f"L😴{gk_val_l:.0f}")
                if gk_val_s is not None:
                    if gk_val_s < 35:
                        gk_parts.append(f"S🔥{gk_val_s:.0f}")
                    elif gk_val_s < 50:
                        gk_parts.append(f"S👀{gk_val_s:.0f}")
                    else:
                        gk_parts.append(f"S😴{gk_val_s:.0f}")
                gk_status = " | ".join(gk_parts) if gk_parts else "N/A"

                # 風控狀態
                cb_info = ""
                if executor.consec_losses >= 2:
                    cb_info = f"\n⚠️ 連虧 {executor.consec_losses} 筆"
                if executor.daily_pnl < 0:
                    cb_info += f"\n📊 今日：${executor.daily_pnl:.0f}"

                # ── V14 自檢 ──
                checks = []

                # 1. 日誌健康：今日（00:00 起）無 ERROR/WARNING
                alerts_path = os.path.join(LOGS_DIR, "alerts.log")
                alert_count = 0
                try:
                    if os.path.exists(alerts_path):
                        today_prefix = t_utc8.strftime("%Y-%m-%d")
                        with open(alerts_path, "r", encoding="utf-8") as af:
                            for line in af:
                                # 只計算以日期開頭的行（跳過 Traceback/堆疊等續行）
                                if len(line) >= 10 and line[:10] == today_prefix:
                                    alert_count += 1
                except Exception:
                    pass
                if alert_count == 0:
                    checks.append("✅ 今日無 ERROR/WARNING")
                else:
                    checks.append(f"⚠️ 今日 {alert_count} 筆告警（/alerts 查詳情）")

                # 2. 持倉數正確（L ≤ 1，S ≤ 1）
                if len(l_pos) <= 1 and len(s_pos) <= 1:
                    checks.append(f"✅ 持倉正常 L:{len(l_pos)} S:{len(s_pos)}")
                else:
                    checks.append(f"🚨 持倉異常 L:{len(l_pos)} S:{len(s_pos)}")

                # 3. GK L/S 值不同（確認兩套窗口生效）
                if gk_val_l is not None and gk_val_s is not None:
                    if abs(gk_val_l - gk_val_s) > 0.01:
                        checks.append(f"✅ GK 雙窗口 L={gk_val_l:.1f} S={gk_val_s:.1f}")
                    else:
                        checks.append(f"⚠️ GK L=S={gk_val_l:.1f}（窗口可能異常）")
                else:
                    checks.append("⏳ GK 暖機中")

                # 4. V14 出場機制：檢查歷史出場是否出現 MFE-trail/MH-ext/BE
                v14_reasons = set()
                for ds in executor.daily_stats.values():
                    if ds.get("mfe_trail_count", 0) > 0:
                        v14_reasons.add("MFE-trail")
                    if ds.get("mh_ext_count", 0) > 0:
                        v14_reasons.add("MH-ext")
                    if ds.get("be_count", 0) > 0:
                        v14_reasons.add("BE")
                if v14_reasons:
                    checks.append(f"✅ V14 出場已驗證：{','.join(sorted(v14_reasons))}")
                else:
                    checks.append("⏳ V14 出場待驗證（尚無 MFE-trail/MH-ext/BE）")

                check_text = "\n".join(checks)

                hb_msg = (
                    f"<b>🖨 V14 運轉中…（第 {executor.bar_counter} 張）</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💵 ETH：${bar_data['close']:.2f}\n"
                    f"🔋 壓縮能量：{gk_status}\n"
                    f"🎰 持倉：\n{pos_text}\n"
                    f"💰 金庫：${executor.account_balance:.2f}{cb_info}\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🩺 自檢：\n{check_text}"
                )
                send_telegram_message(hb_msg)

            elapsed = time.time() - cycle_start
            logger.info(f"Cycle done in {elapsed:.1f}s | Balance: ${executor.account_balance:.2f}")

        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            executor.save_state()
            env = "模擬" if PAPER_TRADING else "實戰"
            send_telegram_message(f"<b>🖨 V14 下班了（{env}）</b>\n💰 金庫：${executor.account_balance:.2f}\n🛏 明天繼續印！")
            break

        except Exception as e:
            logger.error(f"Cycle error: {e}\n{traceback.format_exc()}")
            send_telegram_message(f"<b>🚨 V14 卡紙了！</b>\n🔧 故障原因：{str(e)[:200]}\n⏳ 60 秒後自動維修...")
            # 等 60 秒再試，避免瘋狂重試
            time.sleep(60)


if __name__ == "__main__":
    main()
