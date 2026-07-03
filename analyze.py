"""
收益分析 CLI — 在 VPS 終端機直接看 dashboard 那套收益分析。

與 Telegram /analysis 共用 analysis_report.build_report（同一套計算）。
依 .env 的 PAPER_TRADING 自動選 data/（模擬）或 data_live/（正式）。

用法：
    .venv/bin/python analyze.py            # 收益分析彙總（全期間）
    .venv/bin/python analyze.py 30         # 收益分析（最近 30 天）
    .venv/bin/python analyze.py -t         # 對齊好讀的交易列表（最近 20 筆）
    .venv/bin/python analyze.py -t 50      # 交易列表最近 50 筆
    .venv/bin/python analyze.py --paper    # 強制看模擬盤 data/
    .venv/bin/python analyze.py 7 --live   # 最近 7 天，強制看正式盤 data_live/
"""
import os
import sys
import argparse

from dotenv import load_dotenv

import paths  # 多實例路徑；必須最先 import（會優先載入 INSTANCE_DIR/.env，見 paths.py）
import analysis_report

# Windows 終端預設 cp950 無法輸出 emoji（🟢/🔴）→ 強制 UTF-8，避免 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

load_dotenv()


def main():
    ap = argparse.ArgumentParser(description="收益分析（終端機版）")
    ap.add_argument("days", nargs="?", type=int, default=None,
                    help="只算最近 N 天（依出場時間）；省略 = 全期間")
    ap.add_argument("-t", "--trades", nargs="?", type=int, const=20, default=None,
                    metavar="N", help="顯示交易列表最近 N 筆（預設 20），取代彙總")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="強制讀 data/（模擬盤）")
    g.add_argument("--live", action="store_true", help="強制讀 data_live/（正式盤）")
    args = ap.parse_args()

    if args.paper:
        paper = True
    elif args.live:
        paper = False
    else:
        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    data_dir = paths.data_dir(paper)  # 多實例：INSTANCE_DIR 下；未設則程式目錄
    src = "模擬盤 data/" if paper else "正式盤 data_live/"
    print(f"資料來源：{src}\n")
    if args.trades is not None:
        print(analysis_report.build_trades_table(data_dir, days=args.days, limit=args.trades))
    else:
        print(analysis_report.build_report(data_dir, days=args.days, html=False))


if __name__ == "__main__":
    main()
