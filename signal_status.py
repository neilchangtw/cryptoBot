"""
即時開單條件檢查（共用模組）。

同一套邏輯供：
  - Telegram /signal 指令（main_eth._handle_signal，html=True）
  - VPS 終端機 CLI（signal.py，html=False）

顯示 L/S 每個進場 gate 的當下 ✅/❌（GK 壓縮 / 15-bar 突破 / 時段 / Regime gate /
冷卻 / 持倉 / 月進場上限 / 月虧上限 / 連虧冷卻 / 暫停），以及 L/S 可開單時段。
gate 順序與 strategy.evaluate_long/short_signal 完全一致，結論 = bot 會不會在這根進場。
"""
import pandas as pd

import strategy

WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]


def _hours_text(block_h):
    """把封鎖時段反算成允許時段的精簡字串，如 '03–11, 13–23'。"""
    allowed = [h for h in range(24) if h not in block_h]
    if not allowed:
        return "（無）"
    ranges = []
    start = prev = allowed[0]
    for h in allowed[1:]:
        if h == prev + 1:
            prev = h
        else:
            ranges.append((start, prev))
            start = prev = h
    ranges.append((start, prev))
    return ", ".join(f"{a:02d}–{b:02d}" if a != b else f"{a:02d}" for a, b in ranges)


def _days_text(block_d):
    allowed = [d for d in range(7) if d not in block_d]
    return "、".join(f"週{WEEKDAY_ZH[d]}" for d in allowed)


def session_windows():
    """回傳 L/S 可開單時段的人類可讀字串（UTC+8）。"""
    return {
        "L": f"{_days_text(strategy.L_BLOCK_D)}　{_hours_text(strategy.BLOCK_H)} 點",
        "S": f"{_days_text(strategy.S_BLOCK_D)}　{_hours_text(strategy.BLOCK_H)} 點",
    }


def _side_gates(side, row, st):
    """回傳該側 [(ok, label, detail), ...] 與 fire(bool)。順序對齊 strategy 評估。"""
    close = float(row["close"])
    bar_counter = st.get("bar_counter", 0)
    positions = st.get("positions", {}) or {}
    last_exits = st.get("last_exits", {}) or {}

    if side == "L":
        gk = row.get("gk_pctile")
        gk_thr = strategy.L_GK_THRESH
        brk_ok = strategy._safe_bool(row.get("breakout_long"))
        brk_lvl = row.get("breakout_15bar_max")
        brk_detail_ok = f"C={close:.2f} > 15bar高 {float(brk_lvl):.2f}" if pd.notna(brk_lvl) else "—"
        gap = ((float(brk_lvl) - close) / close * 100) if pd.notna(brk_lvl) else None
        brk_detail_no = (f"C={close:.2f} 距 15bar高 {float(brk_lvl):.2f} 還差 {gap:.2f}%"
                         if gap is not None else "—")
        session_ok = strategy._safe_bool(row.get("session_ok_l"))
        regime_blocked = strategy._safe_bool(row.get("regime_block_l"))
        slope = row.get("sma_slope")
        regime_detail = (f"slope={float(slope)*100:+.2f}%（需 ≤ +{strategy.R_TH_UP*100:.1f}%，超過=強多頭擋 L）"
                         if pd.notna(slope) else "slope=NaN")
        exit_cd = strategy.L_EXIT_CD
        max_total = strategy.L_MAX_TOTAL
        cap = strategy.L_MONTHLY_ENTRY_CAP
        loss_cap = strategy.L_MONTHLY_LOSS_CAP
    else:
        gk = row.get("gk_pctile_s")
        gk_thr = strategy.S_GK_THRESH
        brk_ok = strategy._safe_bool(row.get("breakout_short"))
        brk_lvl = row.get("breakout_15bar_min")
        brk_detail_ok = f"C={close:.2f} < 15bar低 {float(brk_lvl):.2f}" if pd.notna(brk_lvl) else "—"
        gap = ((close - float(brk_lvl)) / close * 100) if pd.notna(brk_lvl) else None
        brk_detail_no = (f"C={close:.2f} 距 15bar低 {float(brk_lvl):.2f} 還差 {gap:.2f}%"
                         if gap is not None else "—")
        session_ok = strategy._safe_bool(row.get("session_ok_s"))
        regime_blocked = strategy._safe_bool(row.get("regime_block_s"))
        slope = row.get("sma_slope")
        regime_detail = (f"|slope|={abs(float(slope))*100:.2f}%（需 ≥ {strategy.R_TH_SIDE*100:.1f}%，太小=橫盤擋 S）"
                         if pd.notna(slope) else "slope=NaN")
        exit_cd = strategy.S_EXIT_CD
        max_total = strategy.S_MAX_TOTAL
        cap = strategy.S_MONTHLY_ENTRY_CAP
        loss_cap = strategy.S_MONTHLY_LOSS_CAP

    monthly_pnl = (st.get("monthly_pnl", {}) or {}).get(side, 0.0)
    monthly_entries = (st.get("monthly_entries", {}) or {}).get(side, 0)
    pos_count = sum(1 for p in positions.values() if p.get("sub_strategy") == side)
    last = last_exits.get(side, -9999)
    cd_remain = max(0, exit_cd - (bar_counter - last)) if last and last > -9999 else 0

    gk_ok = (gk is not None) and pd.notna(gk) and (gk < gk_thr)
    gk_detail = (f"pctile={float(gk):.1f}（需 < {gk_thr}）" if (gk is not None and pd.notna(gk))
                 else "pctile=NaN（暖機中）")

    gates = [
        (gk_ok, "GK 壓縮", gk_detail),
        (brk_ok, "15-bar 突破", brk_detail_ok if brk_ok else brk_detail_no),
        (session_ok, "交易時段", "允許" if session_ok else "封鎖時段/休市日"),
        (not regime_blocked, "Regime gate", regime_detail),
        (cd_remain == 0, "出場冷卻", "已解除" if cd_remain == 0 else f"剩 {cd_remain}/{exit_cd}h"),
        (pos_count < max_total, "持倉上限", f"{pos_count}/{max_total}"),
        (monthly_entries < cap, "月進場上限", f"{monthly_entries}/{cap}"),
        (monthly_pnl > loss_cap, "月虧上限", f"${monthly_pnl:+.2f} / ${loss_cap}"),
    ]
    fire = all(ok for ok, _, _ in gates)
    return gates, fire


def build_signal_status(df, idx, st, html: bool = False) -> str:
    """產生即時開單條件報表。

    Args:
        df: 已過 strategy.compute_indicators 的 DataFrame
        idx: 評估的 bar index（通常 len(df)-2，最新已收盤 bar）
        st: dict — bar_counter / last_exits / monthly_pnl / monthly_entries /
            positions / consec_losses / consec_loss_cooldown_until / paused
        html: True → Telegram HTML；False → 終端機純文字
    """
    # html 模式用 sentinel 標記粗體，最後統一 escape 內容（避免 < > 被當成 HTML 標籤），
    # 再把 sentinel 還原成 <b></b>。這樣 "（<25）" 之類的 < 不會炸掉 Telegram parser。
    BOLD_L, BOLD_R = "\x00B\x00", "\x00b\x00"

    def b(t):
        return f"{BOLD_L}{t}{BOLD_R}" if html else t

    row = df.iloc[idx]
    bar_time = str(row["datetime"])[:16]
    close = float(row["close"])
    slope = row.get("sma_slope")
    regime = strategy.classify_regime(slope)
    gk_l = row.get("gk_pctile")
    gk_s = row.get("gk_pctile_s")
    gk_l_s = f"{float(gk_l):.1f}" if pd.notna(gk_l) else "NaN"
    gk_s_s = f"{float(gk_s):.1f}" if pd.notna(gk_s) else "NaN"

    # 全域阻擋：連虧冷卻 / 暫停
    bar_counter = st.get("bar_counter", 0)
    consec = st.get("consec_losses", 0)
    cd_until = st.get("consec_loss_cooldown_until", 0) or 0
    consec_remain = max(0, cd_until - bar_counter) if cd_until > 0 else 0
    paused = bool(st.get("paused", False))

    lines = [
        b("🎯 開單條件即時檢查"),
        f"🕐 {bar_time} UTC+8｜C=${close:.2f}",
        f"📐 Regime：{regime}（slope={float(slope)*100:+.2f}%）" if pd.notna(slope)
        else "📐 Regime：NA（暖機中）",
        f"🗜 GK 壓縮：L={gk_l_s}（<{strategy.L_GK_THRESH}）｜S={gk_s_s}（<{strategy.S_GK_THRESH}）",
        "━━━━━━━━━━━━━━━",
    ]

    global_block = []
    if paused:
        global_block.append("⛔ 已 /pause 暫停開新倉")
    if consec_remain > 0:
        global_block.append(f"🚫 連虧 {consec} 筆冷卻中，剩 {consec_remain}h（L+S 皆停）")

    for side in ("L", "S"):
        tag = "📈 L 做多" if side == "L" else "📉 S 做空"
        gates, fire = _side_gates(side, row, st)
        lines.append(b(tag))
        for ok, label, detail in gates:
            mark = "✅" if ok else "❌"
            lines.append(f"  {mark} {label}：{detail}")
        # 結論（含全域阻擋）
        if global_block:
            lines.append(f"  ➡️ {'｜'.join(global_block)} → 不開單")
        elif fire:
            lines.append("  ➡️ ✅ 符合開單條件（下一輪整點會進場）")
        else:
            fails = [label for ok, label, _ in gates if not ok]
            lines.append(f"  ➡️ ❌ 卡在：{'、'.join(fails)}")
        lines.append("")

    sw = session_windows()
    lines += [
        b("⏰ 可開單時段（UTC+8）"),
        f"  L：{sw['L']}",
        f"  S：{sw['S']}",
        "  （封鎖 00–02、12 點；冷卻/暖機另計）",
    ]
    text = "\n".join(lines)
    if html:
        import html as _html
        text = _html.escape(text, quote=False)  # & < > → 實體，避免被當標籤
        text = text.replace(BOLD_L, "<b>").replace(BOLD_R, "</b>")
    return text


def _position_payload(trade_id, position, close, bar_time, bar_counter):
    """把既有持倉狀態轉成唯讀 viewer 可用的出場資訊。"""
    side = str(position.get("sub_strategy") or "").upper()
    entry_price = float(position.get("entry_price") or 0)
    if side not in {"L", "S"} or entry_price <= 0:
        return None

    regime = str(position.get("entry_regime") or "NA")
    bars_held = int(position.get("bars_held") or max(
        0, int(bar_counter or 0) - int(position.get("entry_bar_counter") or 0)
    ))
    if side == "L":
        tp_pct = strategy.get_l_tp(regime)
        stop_pct = strategy.L_SAFENET_PCT
        max_hold = strategy.L_COND_REDUCED_MH if position.get("mh_reduced") else strategy.get_l_mh(regime)
        tp_price = entry_price * (1 + tp_pct)
        stop_price = entry_price * (1 - stop_pct)
        current_pnl_pct = (close - entry_price) / entry_price * 100
        distance_tp = (tp_price - close) / close * 100 if close > 0 else None
        distance_stop = (close - stop_price) / close * 100 if close > 0 else None
        extension_bars = strategy.L_EXT_BARS
    else:
        tp_pct = strategy.S_TP_PCT
        stop_pct = strategy.S_SAFENET_PCT
        max_hold = strategy.get_s_mh(regime)
        tp_price = entry_price * (1 - tp_pct)
        stop_price = entry_price * (1 + stop_pct)
        current_pnl_pct = (entry_price - close) / entry_price * 100
        distance_tp = (close - tp_price) / close * 100 if close > 0 else None
        distance_stop = (stop_price - close) / close * 100 if close > 0 else None
        extension_bars = strategy.S_EXT_BARS

    qty = float(position.get("qty") or 0)
    unrealized_pnl = current_pnl_pct / 100 * entry_price * qty if qty > 0 else None
    extension_active = bool(position.get("extension_active"))
    if extension_active:
        elapsed = max(0, int(bar_counter or 0) - int(position.get("extension_start_bar") or 0))
        hold_status = f"延長期 {min(elapsed + 1, extension_bars)}/{extension_bars} 根｜BE 價 ${entry_price:.2f}"
        hold_remaining = max(0, extension_bars - elapsed)
    else:
        hold_remaining = max(0, max_hold - bars_held)
        hold_status = f"距 MaxHold 剩 {hold_remaining} 根（約 {hold_remaining}h）"
    try:
        max_hold_due = (pd.to_datetime(bar_time) + pd.Timedelta(hours=hold_remaining)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        max_hold_due = None

    return {
        "id": str(trade_id),
        "side": side,
        "direction": "Long" if side == "L" else "Short",
        "entry_time_display": str(position.get("entry_time_utc8") or position.get("entry_time_utc") or "")[:16],
        "entry_price": entry_price,
        "entry_regime": regime,
        "hold_bars": bars_held,
        "current_price": close,
        "price_time_display": bar_time,
        "price_is_closed": True,
        "unrealized_pnl_usd": unrealized_pnl,
        "unrealized_pnl_pct": current_pnl_pct,
        "tp_price": tp_price,
        "tp_pct": tp_pct * 100,
        "safenet_price": stop_price,
        "stop_price": stop_price,
        "stop_pct": stop_pct * 100,
        "distance_tp_pct": distance_tp,
        "distance_safenet_pct": distance_stop,
        "max_hold_bars": max_hold,
        "max_hold_remaining_bars": hold_remaining,
        "max_hold_due_display": max_hold_due,
        "hold_status": hold_status,
        "extension_active": extension_active,
        "actual_exit_time_display": None,
        "mfe_pct": position.get("mfe_pct"),
        "mae_pct": position.get("mae_pct"),
    }


def build_signal_payload(df, idx, st) -> dict:
    """回傳給唯讀 viewer 的結構化即時開單條件，不改變任何策略判斷。"""
    row = df.iloc[idx]
    bar_time = str(row["datetime"])[:16]
    close = float(row["close"])
    slope = row.get("sma_slope")
    regime = strategy.classify_regime(slope)
    bar_counter = st.get("bar_counter", 0)
    consec = st.get("consec_losses", 0)
    cd_until = st.get("consec_loss_cooldown_until", 0) or 0
    consec_remain = max(0, cd_until - bar_counter) if cd_until > 0 else 0
    paused = bool(st.get("paused", False))

    global_blocks = []
    if paused:
        global_blocks.append("已 /pause 暫停開新倉")
    if consec_remain > 0:
        global_blocks.append(f"連虧 {consec} 筆冷卻中，剩 {consec_remain}h")

    sides = {}
    for side in ("L", "S"):
        gates, fire = _side_gates(side, row, st)
        positions = st.get("positions", {}) or {}
        pos_count = sum(1 for p in positions.values() if p.get("sub_strategy") == side)
        if side == "L":
            entry_cap = strategy.L_MONTHLY_ENTRY_CAP
            loss_cap = strategy.L_MONTHLY_LOSS_CAP
            position_cap = strategy.L_MAX_TOTAL
            cooldown = strategy.L_EXIT_CD
        else:
            entry_cap = strategy.S_MONTHLY_ENTRY_CAP
            loss_cap = strategy.S_MONTHLY_LOSS_CAP
            position_cap = strategy.S_MAX_TOTAL
            cooldown = strategy.S_EXIT_CD
        monthly_entries = (st.get("monthly_entries", {}) or {}).get(side, 0)
        monthly_pnl = (st.get("monthly_pnl", {}) or {}).get(side, 0.0)
        last_exit = (st.get("last_exits", {}) or {}).get(side, -9999)
        cooldown_remaining = max(0, cooldown - (bar_counter - last_exit)) \
            if last_exit and last_exit > -9999 else 0
        sides[side] = {
            "label": "Long" if side == "L" else "Short",
            "can_open": bool(fire and not global_blocks),
            "gates": [
                {"ok": bool(ok), "label": label, "detail": detail}
                for ok, label, detail in gates
            ],
            "risk": {
                "monthly_entries": int(monthly_entries),
                "monthly_entry_cap": int(entry_cap),
                "monthly_pnl": float(monthly_pnl),
                "monthly_loss_cap": float(loss_cap),
                "positions": int(pos_count),
                "position_cap": int(position_cap),
                "cooldown_remaining": int(cooldown_remaining),
                "cooldown_bars": int(cooldown),
            },
            "blocked_by": [label for ok, label, _ in gates if not ok] + global_blocks,
        }

    position_details = []
    for trade_id, position in (st.get("positions", {}) or {}).items():
        detail = _position_payload(trade_id, position, close, bar_time, bar_counter)
        if detail:
            position_details.append(detail)

    return {
        "evaluated_bar_time": bar_time,
        "close": close,
        "regime": regime,
        "slope_pct": float(slope) * 100 if pd.notna(slope) else None,
        "gk": {
            "L": float(row["gk_pctile"]) if pd.notna(row.get("gk_pctile")) else None,
            "S": float(row["gk_pctile_s"]) if pd.notna(row.get("gk_pctile_s")) else None,
            "thresholds": {"L": float(strategy.L_GK_THRESH), "S": float(strategy.S_GK_THRESH)},
        },
        "global_blocks": global_blocks,
        "consecutive_losses": int(consec),
        "consecutive_cooldown_remaining": int(consec_remain),
        "paused": paused,
        "sides": sides,
        "positions": position_details,
        "session_windows": session_windows(),
    }
