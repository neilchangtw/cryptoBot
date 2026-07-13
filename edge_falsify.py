"""策略證偽檢查 (Falsification Check) — 四個「可能推翻策略的理論」的量化檢核。

理論依據（詳見 doc/ 研究記錄與 2026-07 討論）：
  1. Edge 強度 (edge decay)   — AMH 適應性市場假說：edge 被套利/擁擠而衰減
                                → 近期平均每筆 PnL（200U 正規化）vs 2 年基準
  2. 實盤貼合 (fidelity)      — 執行環境劣化（滑價/延遲/API）會先於 edge 死亡出現
                                → 實盤 vs 回測引擎同窗口逐筆比對（僅實盤模式）
  3. 突破延續 (regime shift)  — G8 已證實策略 regime-dependent：突破延續性消失＝結構改變
                                → TP:(TP+MH) 比率 vs 基準（延續性死亡時 TP 掉、MH 升）
  4. 尾部風險 (fat tail)      — SafeNet 穿透模型用常態市場校準，肥尾會先在這現形
                                → SafeNet 頻率 + 最差單筆 vs 模型上限

顯示一律「大=好」HP%（🟢 ≥60 / 🟡 25~60 / 🔴 <25），與 V29 策略健康度同慣例。
Edge 強度項「獲利中不觸紅」：窗口平均 >0 時最低 🟡（弱≠死），轉虧才可能 🔴——
歷史 2 年滾動回放校準：🔴 率 0.4~2%（僅真虧損窗口）、🟡 率 15~21%，
與 V29（2 年零紅燈、誤報 24%）同哲學；🟡 的行動本來就只是「觀察/連續兩月才凍結加碼」。
掛載點：analyze.py（實盤/模擬 trades.csv）與 run_backtest.py（回測明細）輸出尾端。
所有入口 fail-open：檢查失敗只印一行原因，不影響主報告。

基準常數來自 2 年貼近實盤回測（--flat 200U 基準），重算方式：
    .venv/bin/python run_backtest.py --flat
    （總 PnL / 交易數 / 出場分佈 → 更新下方 BASE_* 常數並註記日期）
"""
import os
import csv
from datetime import datetime, timedelta

import labels

# ── 2 年基準（2026-07-09 重算，run_backtest --flat 貼近實盤：n=271, $+8031.57）──
BASE_AVG_R = 29.6          # 平均每筆 PnL（200U 基準 $）
BASE_TP_CONT = 0.588       # TP/(TP+MH) = 107/(107+75)
BASE_SN_RATE = 0.0074      # SafeNet 2/271
MODEL_MAX_LOSS_R = 205.0   # 模型單筆最大虧損（200U 基準：S SN 4%×1.25穿透×$4000+fee）

# 實盤貼合檢查的起點：V14+R+V25-D 版本一致的乾淨窗口（之前有版本轉換，對不上是正常）
FIDELITY_SINCE = "2026-06-01"

_GREEN, _YELLOW, _RED = "🟢", "🟡", "🔴"


def _norm_exit(code) -> str:
    """實盤 exit_type 全名 → 回測引擎短碼（TP/SN/MFE/MH/MHx/BE）。"""
    m = {"SafeNet": "SN", "MFE-trail": "MFE", "MaxHold": "MH", "MH-ext": "MHx"}
    c = str(code or "").strip()
    return m.get(c, c)


def _hp_pct(hp: float) -> str:
    hp = max(0.0, min(100.0, hp))
    return f"{hp:3.0f}%"


def _light(hp: float) -> str:
    if hp >= 60:
        return _GREEN
    if hp >= 25:
        return _YELLOW
    return _RED


def _margin_at(date_str: str) -> float:
    """依 run_backtest.MARGIN_SCHEDULE 取進場日保證金（單一來源；lazy import 防循環）。"""
    from run_backtest import MARGIN_SCHEDULE
    m = MARGIN_SCHEDULE[0][1]
    for d, v in MARGIN_SCHEDULE:
        if str(date_str)[:10] >= d:
            m = v
    return float(m)


def _check_edge(pnls_R):
    """賺得比基準少=弱（最低 🟡 觀察）；窗口平均轉虧才觸 🔴。
    校準（2026-07-09，2 年滾動窗口回放）：線性刻度在 23 筆窗口誤報率 6.8%（全為假警報，
    含 2024Q3 +$117 獲利季），改「獲利中不觸紅」後 🔴 率 0.4~2%（僅真虧損窗口），
    🟡 率 15~21% ≈ V29 誤報率 24%/2年，與其「連續兩月才行動」的 SOP 相容。"""
    n = len(pnls_R)
    avg = sum(pnls_R) / n
    if avg > 0:
        hp = max(25.0, min(100.0, avg / BASE_AVG_R * 100))
        extra = "（仍獲利，弱於基準最低顯示 🟡）" if avg / BASE_AVG_R < 0.25 else ""
    else:
        hp = max(0.0, 25.0 * (1 + avg / BASE_AVG_R))
        if avg > -BASE_AVG_R * 0.1:  # 貼近損益兩平（>-$3/筆）屬統計噪音帶 → 視同 🟡
            hp = max(hp, 25.0)
        extra = "（窗口合計轉虧）"
    note = f"平均 ${avg:+.1f}/筆 vs 基準 ${BASE_AVG_R:.1f}（200U 正規化，n={n}）{extra}"
    return hp, note


def _check_continuation(exit_codes):
    tp = sum(1 for c in exit_codes if c == "TP")
    mh = sum(1 for c in exit_codes if c == "MH")
    if tp + mh < 5:
        return None, f"TP+MH 僅 {tp + mh} 筆，樣本不足跳過"
    ratio = tp / (tp + mh)
    hp = max(0.0, min(100.0, ratio / BASE_TP_CONT * 100))
    note = f"TP:(TP+MH) {ratio * 100:.0f}% vs 基準 {BASE_TP_CONT * 100:.0f}%（TP {tp} / MH {mh}）"
    return hp, note


def _check_tail(pnls_R, exit_codes):
    n = len(pnls_R)
    sn = sum(1 for c in exit_codes if c == "SN")
    worst = -min(pnls_R) if min(pnls_R) < 0 else 0.0
    hp = 100.0
    # 壓測校準（2026-07-09）：超模 1.5x（跳空級）→ 🟡、2x+（閃崩級）→ 🔴；
    # SN 群聚 10%（3/30，純運氣機率 0.16%）→ 🟡；歷史正常窗口（最壞 2SN/30=6.7%）仍 🟢
    if worst > MODEL_MAX_LOSS_R:
        hp -= min(80.0, (worst / MODEL_MAX_LOSS_R - 1) * 120)
    sn_rate = sn / n
    if sn_rate > 0.02:
        hp -= min(70.0, (sn_rate - 0.02) * 800)
    hp = max(0.0, hp)
    note = (f"SafeNet {sn} 筆（{sn_rate * 100:.1f}% vs 基準 {BASE_SN_RATE * 100:.1f}%）；"
            f"最差單筆 -${worst:.0f} vs 模型上限 -${MODEL_MAX_LOSS_R:.0f}（200U 正規化）")
    return hp, note


def _check_fidelity(live_rows):
    """實盤 vs 回測引擎同窗口逐筆比對。回傳 (hp, note) 或 (None, 跳過原因)。"""
    from run_backtest import _load_engine, MARGIN_SCHEDULE
    import analysis_report
    import pandas as pd

    rows = [r for r in live_rows
            if str(r.get("entry_time_utc8", ""))[:10] >= FIDELITY_SINCE]
    if len(rows) < 3:
        return None, f"{FIDELITY_SINCE} 起實盤不足 3 筆，樣本不足跳過"

    root = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(root, "data", "ETHUSDT_1h_latest730d.csv")
    if not os.path.exists(csv_path):
        return None, "無 K 線快取（fetch_backtest_data.py 補抓後可用）"
    df = pd.read_csv(csv_path)
    last_exit = max(str(r.get("exit_time_utc8", "")) for r in rows)
    if str(df["datetime"].iloc[-1]) < last_exit:
        return None, (f"K 線快取只到 {df['datetime'].iloc[-1]}，未覆蓋最後出場 "
                      f"{last_exit[:16]}（run_backtest.py --refresh 後可用）")

    eng = _load_engine()
    ind = eng.compute_indicators(df)
    bt = eng.simulate_v14_detailed(ind, df["datetime"].values, start_bar=None,
                                   realistic=True, slip_bps=0.0,
                                   margin_schedule=MARGIN_SCHEDULE)
    # 成交時刻（= 訊號 bar 開盤 +1h）當比對鍵
    lo = min(analysis_report.to_exec_time(r["entry_time_utc8"]) for r in rows)
    hi = max(analysis_report.to_exec_time(r["entry_time_utc8"]) for r in rows)
    bt_keys = {}
    for t in bt:
        k = (pd.to_datetime(str(t["entry_dt"])) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        if lo <= k <= hi:
            bt_keys[(t["side"], k)] = t
    matched = 0
    live_pnl = 0.0
    for r in rows:
        k = (str(r.get("sub_strategy", "")).strip(),
             analysis_report.to_exec_time(r["entry_time_utc8"]))
        live_pnl += float(r.get("net_pnl_usd", 0) or 0)
        if k in bt_keys:
            matched += 1
    denom = max(len(rows), len(bt_keys)) if bt_keys else len(rows)
    match_rate = matched / denom
    bt_pnl = sum(t["pnl_usd"] for t in bt_keys.values())
    hp = match_rate * 100
    if bt_pnl > 0 and live_pnl / bt_pnl < 0.7:  # 實盤系統性少賺 → 降級
        hp = min(hp, 55.0)
    note = (f"{FIDELITY_SINCE} 起 {matched}/{len(rows)} 筆吻合（回測同窗口 {len(bt_keys)} 筆）；"
            f"PnL 實盤 ${live_pnl:+.0f} vs 回測 ${bt_pnl:+.0f}")
    return hp, note


def _render(items, n, scope_note) -> str:
    """items: list of (名稱, hp or None, note)。"""
    out = ["", "──────────────────────────────────────────",
           f" 🧪 策略證偽檢查 (Falsification Check) — {scope_note}",
           "   （四項對應可推翻策略的理論：edge 衰減 / 執行劣化 / regime 改變 / 肥尾）"]
    W = 26
    worst = None
    for name, hp, note in items:
        tag = labels.ljust_disp(name, W)
        if hp is None:
            out.append(f"   {tag} —— 跳過：{note}")
            continue
        out.append(f"   {tag} {_hp_pct(hp)} {_light(hp)}  {note}")
        worst = hp if worst is None else min(worst, hp)
    if worst is None:
        out.append("   （無可評估項目）")
    else:
        light = _light(worst)
        if light == _GREEN:
            verdict = "無證偽訊號"
        elif light == _YELLOW:
            verdict = "有弱化訊號，持續觀察（連續兩個月 🟡 → 凍結加碼）"
        else:
            verdict = "證偽警報：檢視是否退回 200U / 暫停（參考 V29 SOP）"
        small = f"；n={n} 樣本 <30 參考性有限" if n < 30 else f"；n={n}"
        out.append(f"   總評 {light} {verdict}{small}")
    out.append("──────────────────────────────────────────")
    return "\n".join(out)


def build_check_live(data_dir: str, recent: int = 30) -> str:
    """analyze.py 掛載點：讀 trades.csv 最近 N 筆已平倉交易做四項檢查。"""
    try:
        trades_path = os.path.join(data_dir, "trades.csv")
        if not os.path.exists(trades_path):
            return ""
        rows = []
        with open(trades_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if str(row.get("net_pnl_usd", "") or "").strip() == "":
                    continue
                rows.append(row)
        if not rows:
            return ""
        rows.sort(key=lambda r: str(r.get("exit_time_utc8", "")))
        recent_rows = rows[-recent:]

        pnls_R, codes = [], []
        for r in recent_rows:
            pnl = float(r["net_pnl_usd"])
            m = _margin_at(str(r.get("entry_time_utc8", ""))[:10])
            pnls_R.append(pnl * 200.0 / m)
            codes.append(_norm_exit(r.get("exit_type", "")))

        items = [("1. Edge 強度 (edge decay)", *_check_edge(pnls_R))]
        try:
            hp_f, note_f = _check_fidelity(rows)
        except Exception as e:  # 引擎/快取問題不擋主報告
            hp_f, note_f = None, f"比對失敗（{type(e).__name__}: {e}）"
        items.append(("2. 實盤貼合 (fidelity)", hp_f, note_f))
        items.append(("3. 突破延續 (regime shift)", *_check_continuation(codes)))
        items.append(("4. 尾部風險 (fat tail)", *_check_tail(pnls_R, codes)))
        return _render(items, len(recent_rows), f"最近 {len(recent_rows)} 筆 vs 2 年基準")
    except Exception as e:
        return f"\n（策略證偽檢查失敗：{type(e).__name__}: {e}）"


def build_check_backtest(trades) -> str:
    """run_backtest.py 掛載點：對本次回測窗口的明細做檢查（貼合項不適用）。"""
    try:
        if not trades:
            return ""
        pnls_R, codes = [], []
        for t in trades:
            m = float(t.get("margin", 200) or 200)
            pnls_R.append(float(t["pnl_usd"]) * 200.0 / m)
            codes.append(_norm_exit(t.get("exit_reason", "")))
        items = [("1. Edge 強度 (edge decay)", *_check_edge(pnls_R)),
                 ("2. 實盤貼合 (fidelity)", None, "回測模式不適用（用 analyze.py 看實盤版）"),
                 ("3. 突破延續 (regime shift)", *_check_continuation(codes)),
                 ("4. 尾部風險 (fat tail)", *_check_tail(pnls_R, codes))]
        return _render(items, len(trades), f"本窗口 {len(trades)} 筆 vs 2 年基準")
    except Exception as e:
        return f"\n（策略證偽檢查失敗：{type(e).__name__}: {e}）"
