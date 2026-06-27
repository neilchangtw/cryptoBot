"""
收益分析報表（共用模組）。

同一套計算同時供：
  - Telegram /analysis 指令（main_eth._handle_analysis，html=True）
  - VPS 終端機 CLI（analyze.py，html=False）

只讀 trades.csv（+ bar_snapshots.csv 做 regime join），無副作用、不碰 API/executor。
指標對齊 dashboard 收益分析：總損益 / WR / PF / 最大回撤 / 出場分佈 / L vs S / regime。
"""
import os
import csv
from datetime import datetime, timedelta

import labels  # 中文(英文)詞彙對照 + 全形對齊


def _parse_dt(s):
    s = str(s)
    if " " in s:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def to_exec_time(s, fmt="%Y-%m-%d %H:%M"):
    """trades.csv 存的是訊號 bar 的「開盤時刻」（data_feed datetime = open_time）。
    但機器人是在該 bar「收盤」後（整點 +10s）才下單，幣安實際成交時間 = 開盤 + 1h。
    交易列表顯示成成交時刻，才會與幣安後台 / 回測明細對齊（回測已用收盤時刻標記）。
    解析失敗則原樣回傳前 16 字元。"""
    dt = _parse_dt(s)
    if dt is None:
        return str(s)[:16]
    return (dt + timedelta(hours=1)).strftime(fmt)


def _load_closed(data_dir: str, days: int = None):
    """讀 trades.csv 的已平倉交易（依出場時間過濾最近 N 天），回傳 list[dict]。"""
    trades_path = os.path.join(data_dir, "trades.csv")
    if not os.path.exists(trades_path):
        return None
    cutoff = None
    if days:
        cutoff = (datetime.utcnow() + timedelta(hours=8)) - timedelta(days=days)
    rows = []
    with open(trades_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pnl_raw = row.get("net_pnl_usd", "")
            if pnl_raw is None or str(pnl_raw).strip() == "":
                continue
            if cutoff is not None:
                edt = _parse_dt(row.get("exit_time_utc8", ""))
                if edt is None or edt < cutoff:
                    continue
            try:
                row["_pnl"] = float(pnl_raw)
            except (ValueError, TypeError):
                continue
            try:
                row["_hold"] = float(row.get("hold_bars", 0) or 0)
            except (ValueError, TypeError):
                row["_hold"] = 0.0
            row["_exit_dt"] = _parse_dt(row.get("exit_time_utc8", ""))
            rows.append(row)
    return rows


def build_trades_table(data_dir: str, days: int = None, limit: int = 20) -> str:
    """對齊好讀的交易列表（純 ASCII 欄位，終端機對齊不跑掉）。

    欄位：# / 方向 / 進場時間 / 進場價 / 出場時間 / 出場價 / 出場類型 / 持倉 / 損益 / regime
    """
    rows = _load_closed(data_dir, days)
    if rows is None:
        return "📭 尚無交易記錄（trades.csv 不存在）"
    if not rows:
        scope = f"最近 {days} 天" if days else "全期間"
        return f"📭 {scope}無已平倉交易"

    rows.sort(key=lambda r: r["_exit_dt"] or datetime.min)
    shown = rows[-limit:] if limit else rows

    TYPE_W, RG_W = 20, 15
    header = (f"{'#':>5} {'Dir':<4} {'Entry (UTC+8)':<16} {'EntryPx':>9} "
              f"{'Exit (UTC+8)':<16} {'ExitPx':>9} "
              f"{labels.ljust_disp('出場類型 (Type)', TYPE_W)} {'Hold':>4} "
              f"{'PnL($)':>9} {labels.ljust_disp('進場趨勢 (Regime)', RG_W)}")
    sep = "─" * labels.disp_width(header)
    out = ["交易列表（# 編號 / Dir 方向 / Entry 進場 / Exit 出場 / 出場類型 / "
           "Hold 持倉小時 / PnL 損益 / 進場趨勢）",
           "（時間=實際成交時刻 K 棒收盤，對齊幣安後台 / 回測明細）",
           header, sep]
    total = 0.0
    for r in shown:
        num = r.get("trade_number", "") or r.get("trade_id", "")[:6]
        d = str(r.get("sub_strategy", ""))
        et = to_exec_time(r.get("entry_time_utc8", ""))  # 顯示成交時刻（對齊幣安）
        ep = r.get("entry_price", "")
        xt = to_exec_time(r.get("exit_time_utc8", ""))
        xp = r.get("exit_price", "")
        typ = labels.ljust_disp(labels.exit_label(r.get("exit_type", "")), TYPE_W)
        hold = int(r["_hold"])
        pnl = r["_pnl"]
        rg = labels.ljust_disp(labels.regime_label(r.get("entry_regime", "")), RG_W)
        total += pnl
        try:
            ep_s = f"{float(ep):.2f}"
        except (ValueError, TypeError):
            ep_s = str(ep)
        try:
            xp_s = f"{float(xp):.2f}"
        except (ValueError, TypeError):
            xp_s = str(xp)
        out.append(f"{str(num):>5} {d:<4} {et:<16} {ep_s:>9} "
                   f"{xt:<16} {xp_s:>9} {typ} {hold:>4} "
                   f"{pnl:>+9.2f} {rg}")
    out.append(sep)
    n = len(shown)
    wins = sum(1 for r in shown if r["_pnl"] > 0)
    out.append(f"共 {n} 筆（{wins}W {n-wins}L），合計 ${total:+.2f}")

    # ── 出場分佈（依顯示的交易，與「共 N 筆」一致；對齊回測明細格式）──
    exit_dist = {}
    for r in shown:
        et = str(r.get("exit_type", "") or "?").strip() or "?"
        c, s = exit_dist.get(et, (0, 0.0))
        exit_dist[et] = (c + 1, s + r["_pnl"])
    out += ["", " 出場分佈："]
    for et, (c, s) in sorted(exit_dist.items(), key=lambda x: -x[1][0]):
        out.append(f"   {labels.ljust_disp(labels.exit_label(et), 20)}: {c:3d} 筆（${s:+.2f}）")

    # ── 月度 PnL（依進場成交月份）──
    monthly = {}
    for r in shown:
        mth = to_exec_time(r.get("entry_time_utc8", ""))[:7]  # YYYY-MM
        s, c = monthly.get(mth, (0.0, 0))
        monthly[mth] = (s + r["_pnl"], c + 1)
    out += ["", " 月度 PnL："]
    for mth in sorted(monthly):
        s, c = monthly[mth]
        bar = "🟢" if s > 0 else "🔴"
        out.append(f"   {mth}  {bar} ${s:+8.2f}（{c} 筆）")
    pos_months = sum(1 for s, _ in monthly.values() if s > 0)
    out.append(f"\n 正報酬月份：{pos_months}/{len(monthly)}")
    return "\n".join(out)


def build_report(data_dir: str, days: int = None, html: bool = False) -> str:
    """產生收益分析報表字串。

    Args:
        data_dir: 含 trades.csv / bar_snapshots.csv 的目錄（data/ 或 data_live/）
        days: 只算最近 N 天（依出場時間）；None = 全期間
        html: True 用 Telegram HTML 標籤；False 用純文字（終端機）
    """
    # html 模式用 sentinel 標記，最後統一 escape 內容再還原標籤（避免 < > 炸掉 Telegram parser）
    BOLD_L, BOLD_R = "\x00B\x00", "\x00b\x00"
    ITAL_L, ITAL_R = "\x00I\x00", "\x00i\x00"

    def b(t):  # 粗體
        return f"{BOLD_L}{t}{BOLD_R}" if html else t

    def i(t):  # 斜體
        return f"{ITAL_L}{t}{ITAL_R}" if html else t

    trades_path = os.path.join(data_dir, "trades.csv")
    if not os.path.exists(trades_path):
        return "📭 尚無交易記錄"

    cutoff = None
    if days:
        cutoff = (datetime.utcnow() + timedelta(hours=8)) - timedelta(days=days)

    rows = []
    with open(trades_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pnl_raw = row.get("net_pnl_usd", "")
            if pnl_raw is None or str(pnl_raw).strip() == "":
                continue  # 未平倉
            if cutoff is not None:
                edt = _parse_dt(row.get("exit_time_utc8", ""))
                if edt is None or edt < cutoff:
                    continue
            try:
                row["_pnl"] = float(pnl_raw)
            except (ValueError, TypeError):
                continue
            try:
                row["_hold"] = float(row.get("hold_bars", 0) or 0)
            except (ValueError, TypeError):
                row["_hold"] = 0.0
            row["_exit_dt"] = _parse_dt(row.get("exit_time_utc8", ""))
            rows.append(row)

    scope = f"最近 {days} 天" if days else "全期間"
    if not rows:
        return f"📭 {scope}無已平倉交易"

    # ── 整體 ──
    n = len(rows)
    pnls = [r["_pnl"] for r in rows]
    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / n * 100
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = (gross_w / gross_l) if gross_l > 0 else (999 if gross_w > 0 else 0)
    avg_hold = sum(r["_hold"] for r in rows) / n
    best, worst = max(pnls), min(pnls)
    avg_win = (gross_w / len(wins)) if wins else 0
    avg_loss = (-gross_l / len(losses)) if losses else 0

    # ── 最大回撤 ──
    ordered = sorted(rows, key=lambda r: r["_exit_dt"] or datetime.min)
    cum = peak = max_dd = 0.0
    for r in ordered:
        cum += r["_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    # ── 出場分佈 ──
    exit_dist = {}
    for r in rows:
        et = str(r.get("exit_type", "") or "?").strip() or "?"
        c, s = exit_dist.get(et, (0, 0.0))
        exit_dist[et] = (c + 1, s + r["_pnl"])

    # ── L vs S ──
    strat = {}
    for sub in ("L", "S"):
        if sub == "L":
            sub_rows = [r for r in rows if str(r.get("sub_strategy", "")) == "L"]
        else:
            sub_rows = [r for r in rows if str(r.get("sub_strategy", "")).startswith("S")]
        if sub_rows:
            sp = [r["_pnl"] for r in sub_rows]
            sw = [p for p in sp if p > 0]
            strat[sub] = (len(sub_rows), sum(sp),
                          len(sw) / len(sub_rows) * 100, sum(sp) / len(sub_rows))

    # ── Regime（join bar_snapshots 的 sma_slope）──
    regime = {}
    snap_path = os.path.join(data_dir, "bar_snapshots.csv")
    if os.path.exists(snap_path):
        slope_map = {}
        try:
            with open(snap_path, "r", encoding="utf-8") as f:
                for s in csv.DictReader(f):
                    bt = str(s.get("bar_time_utc8", ""))
                    sv = s.get("sma_slope", "")
                    if bt and sv not in ("", None):
                        try:
                            slope_map[bt] = float(sv)
                        except (ValueError, TypeError):
                            pass
            for r in rows:
                sl = slope_map.get(str(r.get("entry_time_utc8", "")))
                if sl is None:
                    continue
                if sl > 0.045:
                    rg = "UP"
                elif abs(sl) < 0.010:
                    rg = "SIDE"
                elif sl < 0:
                    rg = "DOWN"
                else:
                    rg = "MILD_UP"
                c, s_pnl, w = regime.get(rg, (0, 0.0, 0))
                regime[rg] = (c + 1, s_pnl + r["_pnl"], w + (1 if r["_pnl"] > 0 else 0))
        except Exception:
            regime = {}

    # ── 組字串 ──
    sep = "━━━━━━━━━━━━━━━"
    lines = [
        b("📊 收益分析"),
        i(scope),
        sep,
        f"💵 總損益：${total_pnl:+.2f}",
        f"📝 交易：{n} 筆（做多 L {strat.get('L', (0,))[0]} / "
        f"做空 S {strat.get('S', (0,))[0]}；{len(wins)}W {len(losses)}L）",
        f"🎯 勝率：{wr:.1f}%",
        f"⚖️ 獲利因子 PF：{pf:.2f}",
        f"📉 最大回撤：${max_dd:.2f}",
        f"⏱ 平均持倉：{avg_hold:.1f}h",
        f"📈 平均獲利：${avg_win:+.2f} / 平均虧損：${avg_loss:+.2f}",
        f"🏆 最佳：${best:+.2f} / 最差：${worst:+.2f}",
        "",
        b("出場分佈"),
    ]
    for et, (c, s) in sorted(exit_dist.items(), key=lambda x: -x[1][0]):
        lines.append(f"  {labels.exit_label(et)}：{c} 筆（${s:+.2f}）")

    lines += ["", b("L vs S")]
    for sub in ("L", "S"):
        if sub in strat:
            cnt, sp, swr, savg = strat[sub]
            tag = "📈 L 做多" if sub == "L" else "📉 S 做空"
            lines.append(f"  {tag}：{cnt} 筆 ${sp:+.2f}（WR {swr:.0f}%，均 ${savg:+.2f}）")

    if regime:
        lines += ["", b("進場趨勢（Regime，SMA 斜率）")]
        for rg in ("UP", "MILD_UP", "SIDE", "DOWN"):
            if rg in regime:
                c, s, w = regime[rg]
                rwr = w / c * 100 if c else 0
                lines.append(f"  {labels.regime_label(rg)}：{c} 筆 ${s:+.2f}（WR {rwr:.0f}%）")

    text = "\n".join(lines)
    if html:
        import html as _html
        text = _html.escape(text, quote=False)
        text = (text.replace(BOLD_L, "<b>").replace(BOLD_R, "</b>")
                    .replace(ITAL_L, "<i>").replace(ITAL_R, "</i>"))
    return text
