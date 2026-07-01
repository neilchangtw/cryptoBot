"""
即時開單條件 CLI — 在 VPS 終端機看 L/S 現在符不符合進場（等同 Telegram /signal）。

抓最新 K 線 + 讀持倉狀態檔（eth_state_live.json / eth_state.json），算出每個 gate 的 ✅/❌。
依 .env 的 PAPER_TRADING 自動選正式 / 模擬狀態檔。

（檔名不可叫 signal.py — 會蓋掉 Python 內建的 signal 模組，導致 subprocess/pandas 載入失敗。）

用法：
    .venv/bin/python check_signal.py            # 即時開單條件
    .venv/bin/python check_signal.py --paper    # 強制讀模擬狀態
"""
import os
import json
import argparse

from dotenv import load_dotenv

import data_feed
import strategy
import signal_status
import paths  # 多實例路徑（INSTANCE_DIR 分流狀態檔）

load_dotenv()


def main():
    ap = argparse.ArgumentParser(description="即時開單條件檢查（終端機版）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--paper", action="store_true", help="讀 eth_state.json（模擬）")
    g.add_argument("--live", action="store_true", help="讀 eth_state_live.json（正式）")
    args = ap.parse_args()

    if args.paper:
        paper = True
    elif args.live:
        paper = False
    else:
        paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    state_path = paths.state_file(paper)  # 多實例：INSTANCE_DIR 下；未設則程式目錄

    st = {}
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        cb = raw.get("circuit_breaker", {})
        st = {
            "bar_counter": raw.get("bar_counter", 0),
            "last_exits": raw.get("last_exits", {}),
            "positions": raw.get("positions", {}),
            "paused": raw.get("paused", False),
            "monthly_pnl": cb.get("monthly_pnl", {}),
            "monthly_entries": cb.get("monthly_entries", {}),
            "consec_losses": cb.get("consec_losses", 0),
            "consec_loss_cooldown_until": cb.get("consec_loss_cooldown_until", 0),
        }
    else:
        print(f"⚠️ 找不到狀態檔 {os.path.basename(state_path)}，冷卻/月計數以 0 顯示\n")

    print(f"資料來源：{'模擬' if paper else '正式'} {os.path.basename(state_path)}（即時抓 K 線中…）\n")
    eth_df, _ = data_feed.fetch_eth_and_btc()
    df = strategy.compute_indicators(eth_df)
    idx = len(df) - 2  # 最新已收盤 bar
    print(signal_status.build_signal_status(df, idx, st, html=False))


if __name__ == "__main__":
    main()
