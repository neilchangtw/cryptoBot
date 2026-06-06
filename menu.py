"""
終端機指令清單（等同 Telegram /help）。

在 VPS 上忘記能下哪些指令時跑這支：
    .venv/bin/python menu.py
純文字輸出、無相依，只是把常用指令印出來。
"""

MENU = r"""
══════════════════════════════════════════════════════════════
 CryptoBot 終端機指令清單           （依 .env PAPER_TRADING 自動選 data/ 或 data_live/）
══════════════════════════════════════════════════════════════

 查詢績效 / 狀態（只讀檔，免重啟）                        對應 Telegram
 ──────────────────────────────────────────────────────────────
 .venv/bin/python analyze.py [天數]      收益分析彙總          /analysis [天數]
 .venv/bin/python analyze.py -t [N]      交易列表（預設 20 筆） /trades
 .venv/bin/python check_signal.py        即時開單條件 L/S      /signal
 .venv/bin/python verify_mainnet.py      帳戶/連線/持倉體檢     /status + /bal
 .venv/bin/python check_health.py --days 30   策略健康報告
 cat eth_state_live.json | python3 -m json.tool   持倉/計數器/餘額原始狀態

 旗標：--paper / --live 強制資料來源；analyze.py 第一個數字 = 最近 N 天

 回測
 ──────────────────────────────────────────────────────────────
 .venv/bin/python fetch_backtest_data.py      補 730 天 K 線快取 → data/
 .venv/bin/python backtest/research/<腳本>.py  跑研究腳本（btc_* 先 export MPLBACKEND=Agg）

 服務控制（systemd）
 ──────────────────────────────────────────────────────────────
 systemctl is-active cryptobot           秒看是否在跑
 sudo systemctl status  cryptobot --no-pager    詳細狀態 + 最近日誌
 sudo systemctl restart cryptobot        重啟（改 main_eth/strategy/executor/.env 後）
 sudo systemctl stop / start cryptobot    停止 / 啟動

 日誌
 ──────────────────────────────────────────────────────────────
 journalctl -u cryptobot -f              即時跟（Ctrl+C 離開）
 journalctl -u cryptobot -p err --no-pager    只看錯誤
 tail -f logs/system.log | logs/signal.log | logs/alerts.log

 Telegram 指令（手機輸入）
 ──────────────────────────────────────────────────────────────
 /status /bal /pnl /analysis [天數] /signal /trades /alerts /cb
 /pause /resume /cleanup /help

 詳細維運見 deploy/cheatsheet.txt、部署見 deploy/VPS_DEPLOY.md
══════════════════════════════════════════════════════════════
"""


def main():
    print(MENU)


if __name__ == "__main__":
    main()
