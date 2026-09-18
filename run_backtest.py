"""
終端機回測 CLI — 在 VPS 或本機直接跑回測，可選日期範圍。

使用現行共用引擎 backtest/research/v14_export_trades.py（V14+R + V25-D），
參數預設與線上實盤一致，日期過濾邏輯是唯一現行回測定義。

用法：
    .venv/bin/python run_backtest.py                          # 全期間
    .venv/bin/python run_backtest.py --start 2025-01-01       # 從該日起（熔斷從零，與實盤啟動一致）
    .venv/bin/python run_backtest.py --start 2025-01-01 --end 2025-06-30
    .venv/bin/python run_backtest.py --end 2026-05-31         # 對齊某個結算日
    .venv/bin/python run_backtest.py --refresh                # 先抓最新 K 線再跑
    .venv/bin/python run_backtest.py --symbol ETHUSDT
    .venv/bin/python run_backtest.py --live-replay 實戰ALL.txt \
        --compare-backtest 回測ALL.txt -t                    # 實戰風控重播與明細比對
"""
import os
import sys
import argparse
import importlib.util
from pathlib import Path
from collections import defaultdict

import pandas as pd

# Windows 終端預設 cp950 無法輸出 emoji（🟢/🔴）→ 強制 UTF-8，避免 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # 讓引擎內 `from strategy import ...` 找得到（單一來源 V25-D）

import labels  # 中文(英文)詞彙對照 + 全形對齊
import analysis_report  # to_exec_time：成交時刻 = bar 開盤 + 1h（對齊幣安/實盤）

# ── 實盤保證金調整歷史（對齊 .env MARGIN_PER_TRADE 的實際變更；之後再調就往下加一行）──
# 每筆交易名目 = 進場日當時保證金 × 20；FEE 與熔斷線也依當時保證金等比（= 線上動態風控）。
# 用 --flat 可切回「全程 200U」研究基準（= V14~V28 文件裡的數字）。
MARGIN_SCHEDULE = [
    ("2000-01-01", 200),   # 起始：200U（$4,000 名目）
    ("2026-07-03", 300),   # 2026-07-03 起：300U（$6,000 名目）
    ("2026-08-01", 500),   # 2026-08-01 起：500U（$10,000 名目）
]


def _load_engine():
    path = os.path.join(ROOT, "backtest", "research", "v14_export_trades.py")
    spec = importlib.util.spec_from_file_location("v14_export_trades", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_replay_rows(path_text):
    """讀取實戰 CSV 或交易列表 TXT；時間統一為 UTC+8 實際成交時間。"""
    from trade_viewer import load_trades

    path = Path(path_text).expanduser()
    if not path.is_file():
        raise ValueError(f"找不到實戰重播檔案：{path}")
    return load_trades(path)


def _fmt_replay_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def _run_live_replay(args):
    from live_replay import (
        compare_trade_entries,
        margin_for_time,
        parse_replay_time,
        replay_trades,
        state_at,
    )

    try:
        live_rows = _load_replay_rows(args.live_replay)
        result = replay_trades(live_rows, MARGIN_SCHEDULE)
    except (OSError, ValueError, KeyError) as exc:
        print(f"❌ 實戰重播失敗：{exc}")
        return 2

    print("═" * 92)
    print(" 實戰重播模式（歷史風控狀態稽核，不是純 K 棒預測回測）")
    print(f" 實戰來源：{Path(args.live_replay).resolve()}")
    print(f" 交易範圍：{_fmt_replay_time(result.first_entry)} ~ {_fmt_replay_time(result.last_exit)}（UTC+8 實際成交時間）")
    print(f" 交易數量：{len(result.rows)}")

    if args.trades:
        print("\n 進場風控稽核")
        print(" #   Dir Entry (UTC+8)      PnL($)   月L/月S(進場前)       連虧  判定")
        print("-" * 92)
        for audit in result.entry_audits:
            row = audit["row"]
            monthly = audit["monthly_pnl"]
            verdict = "✅ 允許" if audit["risk_allowed"] else "⚠️ " + "；".join(audit["risk_reasons"])
            print(
                f"{str(row['number']):>3} {row['side']:<3} {row['entry_time']:<16} "
                f"{float(row['pnl']):>8.2f} "
                f"{monthly.get('L', 0.0):>7.2f}/{monthly.get('S', 0.0):>7.2f} "
                f"{audit['consec_losses']:>5}  {verdict}"
            )

    # 依實際出場月份統計，因為 executor 是在平倉時更新日/月風控帳本。
    monthly = defaultdict(float)
    for row in result.rows:
        monthly[parse_replay_time(row["exit_time"]).strftime("%Y-%m")] += float(row["pnl"])
    print("\n 風控帳本（依實際出場月份）")
    for month in sorted(monthly):
        print(f"   {month}：${monthly[month]:+.2f}")

    state = result.state
    last_dt = result.last_exit
    margin = margin_for_time(last_dt, MARGIN_SCHEDULE)
    l_cap = -75.0 * margin / 200.0
    s_cap = -150.0 * margin / 200.0
    cd = _fmt_replay_time(state.cooldown_until) if state.cooldown_until else "無"
    print("\n 最後風控狀態")
    print(f"   保證金基準：{margin:.0f}U")
    print(f"   L 月虧：${state.monthly_pnl.get('L', 0.0):+.2f} / ${l_cap:+.2f} "
          f"{'🔴 已阻擋' if state.monthly_pnl.get('L', 0.0) <= l_cap else '🟢 可通過'}")
    print(f"   S 月虧：${state.monthly_pnl.get('S', 0.0):+.2f} / ${s_cap:+.2f} "
          f"{'🔴 已阻擋' if state.monthly_pnl.get('S', 0.0) <= s_cap else '🟢 可通過'}")
    print(f"   連虧：{state.consec_losses} 筆；冷卻至：{cd}")

    if args.compare_backtest:
        try:
            back_rows = _load_replay_rows(args.compare_backtest)
        except (OSError, ValueError, KeyError) as exc:
            print(f"❌ 回測比對失敗：{exc}")
            return 2
        comparison = compare_trade_entries(live_rows, back_rows)
        print("\n 明細比對（方向＋實際進場時間）")
        print(f"   共同比對：{len(comparison['common'])} 筆")
        print(f"   實戰有、回測無：{len(comparison['live_only'])} 筆")
        print(f"   回測有、實戰無：{len(comparison['backtest_only'])} 筆")

        if comparison["backtest_only"]:
            print("\n 回測多出的交易：")
            outside_before = 0
            for row in comparison["backtest_only"]:
                entry_dt = parse_replay_time(row["entry_time"])
                if entry_dt < result.first_entry:
                    outside_before += 1
                    continue
                replay_state = state_at(live_rows, entry_dt, MARGIN_SCHEDULE)
                reasons = replay_state.block_reasons(row["side"], entry_dt)
                if reasons:
                    reason = "；".join(reasons)
                    print(f"   {row['side']} {row['entry_time']} PnL ${float(row['pnl']):+.2f}："
                          f"🚫 實戰重播會阻擋（{reason}）")
                else:
                    print(f"   {row['side']} {row['entry_time']} PnL ${float(row['pnl']):+.2f}："
                          "⚠️ 非風控阻擋，需再比對信號／持倉／資料")
            if outside_before:
                print(f"   （另有 {outside_before} 筆早於實戰檔案起始時間，未納入逐筆判定）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="終端機回測（V14+R+V25-D，可選日期）")
    ap.add_argument("--start", default="", metavar="YYYY-MM-DD", help="開始日期（空=最早）")
    ap.add_argument("--end", default="", metavar="YYYY-MM-DD", help="結束日期（空=最新）")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--refresh", action="store_true", help="跑之前先抓最新 730 天 K 線")
    ap.add_argument("-t", "--trades", action="store_true", help="印每筆進出場明細表")
    ap.add_argument("--ideal", action="store_true",
                    help="用理想化成交（TP 鎖理論價）；預設貼近實盤（TP/BE 用市價收盤成交）")
    ap.add_argument("--slip", type=float, default=0.0, metavar="BPS",
                    help="每次市價成交逆向滑價 bp（1bp=0.01%%），預設 0；高波動可設 2~5 壓測")
    ap.add_argument("--flat", action="store_true",
                    help="忽略保證金歷史，全程 200U/$4,000（= 歷史研究基準數字）")
    ap.add_argument("--live-replay", metavar="PATH",
                    help="重播實戰 CSV/TXT，重建日/月風控與連虧冷卻")
    ap.add_argument("--compare-backtest", metavar="PATH",
                    help="搭配 --live-replay，比對回測明細多出的交易")
    args = ap.parse_args()
    if args.compare_backtest and not args.live_replay:
        ap.error("--compare-backtest 必須搭配 --live-replay")
    if args.live_replay:
        return _run_live_replay(args)
    realistic = not args.ideal
    schedule = None if args.flat else MARGIN_SCHEDULE

    csv_path = os.path.join(ROOT, "data", f"{args.symbol}_1h_latest730d.csv")

    if args.refresh:
        print(f"抓取最新 {args.symbol} K 線中…")
        import fetch_backtest_data as fbd
        df_new = fbd.fetch_history(args.symbol, "1h", 730)
        os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
        df_new.to_csv(csv_path, index=False)
        print(f"  ✓ {len(df_new)} 根\n")

    if not os.path.exists(csv_path):
        print(f"❌ 找不到 {csv_path}\n   先跑：.venv/bin/python fetch_backtest_data.py")
        return

    df = pd.read_csv(csv_path)
    if df.empty or "datetime" not in df.columns:
        print(f"❌ K 線資料為空或缺少 datetime 欄位：{csv_path}")
        return
    dt_index = pd.to_datetime(df["datetime"], errors="coerce")
    if dt_index.isna().all():
        print(f"❌ K 線 datetime 格式無效：{csv_path}")
        return

    data_start = dt_index.min()
    data_end = dt_index.max()
    try:
        req_start = pd.Timestamp(args.start) if args.start else data_start
        req_end = pd.Timestamp(args.end) if args.end else data_end
    except (ValueError, TypeError):
        print("❌ 日期格式錯誤，請使用 YYYY-MM-DD")
        return
    if args.start and args.end and req_start > req_end:
        print(f"❌ 日期範圍錯誤：start {args.start} 晚於 end {args.end}")
        return
    if args.start and req_start.normalize() > data_end.normalize():
        print(f"❌ 指定起始日 {req_start:%Y-%m-%d} 晚於 K 線資料終點")
        print(f"   資料：{data_start:%Y-%m-%d %H:%M} ~ {data_end:%Y-%m-%d %H:%M}")
        print("   若要回測最新日期，請加 --refresh 更新 K 線後再執行")
        return
    if args.end and req_end.normalize() < data_start.normalize():
        print(f"❌ 指定結束日 {req_end:%Y-%m-%d} 早於 K 線資料起點")
        print(f"   資料：{data_start:%Y-%m-%d %H:%M} ~ {data_end:%Y-%m-%d %H:%M}")
        print("   請調整日期範圍後再執行")
        return

    effective_start = max(req_start, data_start)
    requested_end_of_day = req_end.normalize() + pd.Timedelta(hours=23, minutes=59)
    effective_end = min(requested_end_of_day, data_end) if args.end else data_end
    coverage_warnings = []
    if args.start and req_start.normalize() < data_start.normalize():
        coverage_warnings.append(
            f"⚠️ 指定起始日早於資料範圍，結果從 {data_start:%Y-%m-%d %H:%M} 開始"
        )
    if args.end and req_end.normalize() > data_end.normalize():
        coverage_warnings.append(
            f"⚠️ 指定結束日超出資料範圍，結果只計至 {data_end:%Y-%m-%d %H:%M}；"
            "可加 --refresh 更新"
        )

    eng = _load_engine()
    ind = eng.compute_indicators(df)
    datetimes = df["datetime"].values

    # ── 日期過濾（現行唯一回測定義）──
    start_bar = None
    if args.start:
        for j, dt in enumerate(datetimes):
            if str(dt) >= args.start:
                start_bar = j
                break
    if args.start and start_bar is None:
        print(f"❌ 找不到 {args.start} 之後的 K 線；資料只到 {data_end:%Y-%m-%d %H:%M}")
        print("   請加 --refresh 更新 K 線後再執行")
        return
    trades = eng.simulate_v14_detailed(ind, datetimes, start_bar=start_bar,
                                       realistic=realistic, slip_bps=args.slip,
                                       margin_schedule=schedule)
    if args.end:
        trades = [t for t in trades if str(t["entry_dt"]) <= args.end + " 23:59:59"]

    dr_start = args.start or f"{data_start:%Y-%m-%d %H:%M}"
    dr_end = args.end or f"{data_end:%Y-%m-%d %H:%M}"

    if realistic:
        mode_str = f"貼近實盤（TP/BE 市價收盤成交，滑價 {args.slip:.0f}bp）"
    else:
        mode_str = "理想化（TP 鎖理論價，= 研究基準）"
    if schedule:
        sched_str = " → ".join(
            f"{m}U" if d == "2000-01-01" else f"{m}U@{d}" for d, m in schedule)
        sched_str += "（--flat 可切回全程 200U 基準）"
    else:
        sched_str = "全程 200U/$4,000（研究基準）"
    heading = [
        f" 回測 {args.symbol}  V14+R + V25-D（策略邏輯 = 線上實盤）",
        f" 成交假設：{mode_str}",
        f" 保證金　：{sched_str}",
        f" 指定範圍：{dr_start[:16]} ~ {dr_end[:16]}",
        f" 實際資料：{effective_start:%Y-%m-%d %H:%M} ~ {effective_end:%Y-%m-%d %H:%M}"
        f"（完整快取 {len(df)} 根）",
    ]
    border = "═" * max(labels.disp_width(line) for line in heading)
    print(border)
    print("\n".join(heading))
    for warning in coverage_warnings:
        print(f" {warning}")
    print(border)

    if not trades:
        print(" 此範圍無交易")
        return

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    total = float(tdf["pnl_usd"].sum())
    wins = tdf[tdf["pnl_usd"] > 0]
    losses = tdf[tdf["pnl_usd"] < 0]
    wr = len(wins) / n * 100
    gw = float(wins["pnl_usd"].sum())
    gl = abs(float(losses["pnl_usd"].sum()))
    pf = gw / gl if gl > 0 else 999
    cum = tdf["pnl_usd"].cumsum()
    mdd = abs(float((cum - cum.cummax()).min()))
    avg_hold = float(tdf["bars_held"].mean())
    l = tdf[tdf["side"] == "L"]
    s = tdf[tdf["side"] == "S"]
    l_wr = (len(l[l["pnl_usd"] > 0]) / len(l) * 100) if len(l) else 0
    s_wr = (len(s[s["pnl_usd"] > 0]) / len(s) * 100) if len(s) else 0

    print(f" 總 PnL    : ${total:+.2f}")
    print(f" 交易數    : {n}（L {len(l)} / S {len(s)}）")
    print(f" 勝率      : {wr:.1f}%")
    print(f" 獲利因子  : {pf:.2f}")
    print(f" 最大回撤  : ${mdd:.2f}")
    print(f" 平均持倉  : {avg_hold:.1f}h")
    print(f" 最佳/最差 : ${float(tdf['pnl_usd'].max()):+.2f} / ${float(tdf['pnl_usd'].min()):+.2f}")
    print(f" L 做多    : ${float(l['pnl_usd'].sum()):+.2f}（{len(l)} 筆，WR {l_wr:.0f}%）")
    print(f" S 做空    : ${float(s['pnl_usd'].sum()):+.2f}（{len(s)} 筆，WR {s_wr:.0f}%）")

    # 每筆進出場明細（-t）
    if args.trades:
        print("\n 進出場明細（時間=實際成交時刻 K 棒收盤，對齊實戰）")
        RSN_W, RG_W = 22, 16
        hdr = (f"{'#':>4} {'Dir':<3} {'Entry (UTC+8)':<16} {'EntryPx':>9} "
               f"{'Exit (UTC+8)':<16} {'ExitPx':>9} "
               f"{labels.ljust_disp('出場 (Reason)', RSN_W)} {'Hold(h)':>7} {'Mgn(U)':>6} "
               f"{'PnL($)':>9} {'Move%':>7} {'PnL%':>7} "
               f"{labels.ljust_disp('進場趨勢 (Regime)', RG_W)}")
        print(" " + hdr)
        print(" " + "-" * labels.disp_width(hdr))
        for k, t in enumerate(trades, 1):
            # 引擎 entry_dt/exit_dt = 訊號 bar 開盤時刻；機器人收盤後才下單，
            # 成交時刻 = 開盤 + 1h → 用 to_exec_time 對齊幣安後台與 analyze.py
            edt = analysis_report.to_exec_time(str(t["entry_dt"]).replace("T", " "))
            xdt = analysis_report.to_exec_time(str(t["exit_dt"]).replace("T", " "))
            rsn = labels.ljust_disp(labels.exit_label(t["exit_reason"]), RSN_W)
            rg = labels.ljust_disp(labels.regime_label(t.get("entry_regime", "NA")), RG_W)
            margin = float(t.get("margin", 200))
            margin_pnl_pct = float(t["pnl_usd"]) / margin * 100 if margin else 0.0
            print(f" {k:>4} {t['side']:<3} {edt:<16} {t['entry_price']:>9.2f} "
                  f"{xdt:<16} {t['exit_price']:>9.2f} "
                  f"{rsn} {t['bars_held']:>7} {margin:>6.0f} "
                  f"{t['pnl_usd']:>+9.2f} {t['pnl_pct']:>+7.2f} {margin_pnl_pct:>+7.2f} "
                  f"{rg}")
        print(" Move% = 方向性價格變動（L 上漲／S 下跌為正）；PnL% = 淨損益 ÷ 保證金（與實盤通知一致）")

    # 出場分佈
    print("\n 出場分佈：")
    for reason, cnt in tdf["exit_reason"].value_counts().items():
        sub = tdf[tdf["exit_reason"] == reason]["pnl_usd"].sum()
        print(f"   {labels.ljust_disp(labels.exit_label(reason), 22)}: {cnt:3d} 筆（${float(sub):+.2f}）")

    # 月度
    tdf["_month"] = tdf["entry_dt"].astype(str).str[:7]
    print("\n 月度 PnL：")
    monthly = tdf.groupby("_month")["pnl_usd"].agg(["sum", "count"])
    pos_months = (monthly["sum"] > 0).sum()
    for mth, row in monthly.iterrows():
        bar = "🟢" if row["sum"] > 0 else "🔴"
        print(f"   {mth}  {bar} ${float(row['sum']):+8.2f}（{int(row['count'])} 筆）")
    print(f"\n 正報酬月份：{pos_months}/{len(monthly)}")

    # 策略證偽檢查（四項理論檢核；貼合項僅實盤 analyze.py 適用）
    import edge_falsify
    print(edge_falsify.build_check_backtest(trades))


if __name__ == "__main__":
    main()
