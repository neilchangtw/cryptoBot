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

import strategy
import data_feed
import recorder
from executor import Executor
from telegram_notify import send_telegram_message

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
# 主循環
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    setup_logging()
    logger = logging.getLogger("main")
    sig_logger = logging.getLogger("signal")

    mode = "PAPER" if PAPER_TRADING else "LIVE"
    logger.info(f"=" * 60)
    logger.info(f"  ETH 1h V14 雙策略 L+S ({mode})")
    logger.info(f"  L: GK<{strategy.L_GK_THRESH} BRK{strategy.BRK_LOOK} TP{strategy.L_TP_PCT*100}% MH{strategy.L_MAX_HOLD} SN{strategy.L_SAFENET_PCT*100}%")
    logger.info(f"  S: GK<{strategy.S_GK_THRESH} BRK{strategy.BRK_LOOK} TP{strategy.S_TP_PCT*100}% MH{strategy.S_MAX_HOLD} SN{strategy.S_SAFENET_PCT*100}%")
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

            # ── 2. 更新風控熔斷 ──
            executor.update_period_keys(t_utc8)

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

            # L 信號
            l_cb_ok, l_cb_reason = executor.check_circuit_breaker("L")
            long_sig = None
            if l_cb_ok:
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
            if s_cb_ok:
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

                # 1. 日誌健康：最近 24h 無 ERROR/WARNING
                alerts_path = os.path.join(LOGS_DIR, "alerts.log")
                alert_count = 0
                try:
                    if os.path.exists(alerts_path):
                        cutoff = (t_utc8 - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
                        with open(alerts_path, "r", encoding="utf-8") as af:
                            for line in af:
                                if line[:16] >= cutoff:
                                    alert_count += 1
                except Exception:
                    pass
                if alert_count == 0:
                    checks.append("✅ 無 ERROR/WARNING")
                else:
                    checks.append(f"⚠️ 24h 內 {alert_count} 筆告警")

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
