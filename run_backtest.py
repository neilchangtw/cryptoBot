"""
終端機回測 CLI — 在 VPS 上直接跑回測，可選日期範圍（等同儀表板「回測」tab）。

用的是儀表板同一個引擎 backtest/research/v14_export_trades.py（V14+R + V25-D），
參數預設與線上實盤一致，日期過濾邏輯與儀表板 _run_backtest 完全相同，數字會吻合。

用法：
    .venv/bin/python run_backtest.py                          # 全期間
    .venv/bin/python run_backtest.py --start 2025-01-01       # 從該日起（熔斷從零，與實盤啟動一致）
    .venv/bin/python run_backtest.py --start 2025-01-01 --end 2025-06-30
    .venv/bin/python run_backtest.py --end 2026-05-31         # 對齊某個結算日
    .venv/bin/python run_backtest.py --refresh                # 先抓最新 K 線再跑
    .venv/bin/python run_backtest.py --symbol ETHUSDT
"""
import os
import sys
import argparse
import importlib.util

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
]


def _load_engine():
    path = os.path.join(ROOT, "backtest", "research", "v14_export_trades.py")
    spec = importlib.util.spec_from_file_location("v14_export_trades", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
                    help="每次市價成交逆向滑價 bp（1bp=0.01%），預設 0；高波動可設 2~5 壓測")
    ap.add_argument("--flat", action="store_true",
                    help="忽略保證金歷史，全程 200U/$4,000（= 歷史研究基準數字）")
    args = ap.parse_args()
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

    eng = _load_engine()
    df = pd.read_csv(csv_path)
    ind = eng.compute_indicators(df)
    datetimes = df["datetime"].values

    # ── 日期過濾（與儀表板 _run_backtest 完全相同）──
    start_bar = None
    if args.start:
        for j, dt in enumerate(datetimes):
            if str(dt) >= args.start:
                start_bar = j
                break
    trades = eng.simulate_v14_detailed(ind, datetimes, start_bar=start_bar,
                                       realistic=realistic, slip_bps=args.slip,
                                       margin_schedule=schedule)
    if args.end:
        trades = [t for t in trades if str(t["entry_dt"]) <= args.end + " 23:59:59"]

    dr_start = args.start or str(df["datetime"].iloc[0])
    dr_end = args.end or str(df["datetime"].iloc[-1])

    if realistic:
        mode_str = f"貼近實盤（TP/BE 市價收盤成交，滑價 {args.slip:.0f}bp）"
    else:
        mode_str = "理想化（TP 鎖理論價，= 研究/儀表板基準）"
    if schedule:
        sched_str = " → ".join(
            f"{m}U" if d == "2000-01-01" else f"{m}U@{d}" for d, m in schedule)
        sched_str += "（--flat 可切回全程 200U 基準）"
    else:
        sched_str = "全程 200U/$4,000（研究基準）"
    print("══════════════════════════════════════════")
    print(f" 回測 {args.symbol}  V14+R + V25-D（= 線上實盤）")
    print(f" 成交假設：{mode_str}")
    print(f" 保證金　：{sched_str}")
    print(f" 範圍：{dr_start[:16]} ~ {dr_end[:16]}")
    print(f" 資料：{df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}（{len(df)} 根）")
    print("══════════════════════════════════════════")

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
        RSN_W, RG_W = 16, 16
        hdr = (f"{'#':>4} {'Dir':<3} {'Entry (UTC+8)':<16} {'EntryPx':>9} "
               f"{'Exit (UTC+8)':<16} {'ExitPx':>9} "
               f"{labels.ljust_disp('出場 (Reason)', RSN_W)} {'Hold':>4} {'Mgn':>4} "
               f"{'PnL($)':>9} {'PnL%':>6} {labels.ljust_disp('進場趨勢 (Regime)', RG_W)}")
        print(" " + hdr)
        print(" " + "-" * labels.disp_width(hdr))
        for k, t in enumerate(trades, 1):
            # 引擎 entry_dt/exit_dt = 訊號 bar 開盤時刻；機器人收盤後才下單，
            # 成交時刻 = 開盤 + 1h → 用 to_exec_time 對齊幣安後台與 analyze.py
            edt = analysis_report.to_exec_time(str(t["entry_dt"]).replace("T", " "))
            xdt = analysis_report.to_exec_time(str(t["exit_dt"]).replace("T", " "))
            rsn = labels.ljust_disp(labels.exit_label(t["exit_reason"]), RSN_W)
            rg = labels.ljust_disp(labels.regime_label(t.get("entry_regime", "NA")), RG_W)
            print(f" {k:>4} {t['side']:<3} {edt:<16} {t['entry_price']:>9.2f} "
                  f"{xdt:<16} {t['exit_price']:>9.2f} "
                  f"{rsn} {t['bars_held']:>4} {t.get('margin', 200):>4.0f} "
                  f"{t['pnl_usd']:>+9.2f} {t['pnl_pct']:>+6.2f} "
                  f"{rg}")

    # 出場分佈
    print("\n 出場分佈：")
    for reason, cnt in tdf["exit_reason"].value_counts().items():
        sub = tdf[tdf["exit_reason"] == reason]["pnl_usd"].sum()
        print(f"   {labels.ljust_disp(labels.exit_label(reason), 18)}: {cnt:3d} 筆（${float(sub):+.2f}）")

    # 月度
    tdf["_month"] = tdf["entry_dt"].astype(str).str[:7]
    print("\n 月度 PnL：")
    monthly = tdf.groupby("_month")["pnl_usd"].agg(["sum", "count"])
    pos_months = (monthly["sum"] > 0).sum()
    for mth, row in monthly.iterrows():
        bar = "🟢" if row["sum"] > 0 else "🔴"
        print(f"   {mth}  {bar} ${float(row['sum']):+8.2f}（{int(row['count'])} 筆）")
    print(f"\n 正報酬月份：{pos_months}/{len(monthly)}")


if __name__ == "__main__":
    main()
