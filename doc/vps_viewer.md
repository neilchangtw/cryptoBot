# VPS 唯讀交易視覺化

`vps_viewer.py` 與 `vps_viewer.html` 是獨立的唯讀服務，不會載入 `.env`，不會匯入策略、執行器或 Binance 下單模組，也不會提供任何寫入 API。

## 顯示內容

- ETHUSDT 1h OHLC K 線
- 已收盤／形成中的 K 線區分
- Long／Short 進場標記
- 平倉標記、出場原因、PnL、持倉時間與 regime
- 目前 `eth_state_live.json` 內的策略持倉
- 最近交易與服務／資料 freshness
- Asia/Taipei 顯示，API 同時提供 UTC 時間
- 實戰／回測資料模式切換
- K 線縮放、拖曳平移與全覽復原（桌面滑鼠及手機觸控）

實戰模式優先讀取 Binance Futures 公開端點；網路失敗時退回本機快取。回測模式優先讀取
`data/ETHUSDT_1h_latest730d.csv`，讓圖表可與回測使用的 K 線期間對齊。公開 K 線不需要 API key。

## 回測資料快照

Viewer 不會自行執行回測，也不會修改策略或實盤資料。要讓頁面出現可切換的「回測」來源，
在 VPS 以 `cryptobot` 使用者執行：

```bash
cd ~/cryptoBot
mkdir -p data
.venv/bin/python run_backtest.py -t > data/backtest_trades.txt
```

Viewer 預設讀取 `data/backtest_trades.txt`；也可以在 `cryptoviewer.service` 設定
`VIEWER_BACKTEST_PATH` 指向其他唯讀回測明細檔。若檔案不存在，頁面會把「回測」標為不可用，
不會把實戰資料冒充成回測。回測模式沒有目前持倉資料。

## VPS 單實例啟用

以下指令應在 VPS 執行。Viewer 先綁定 localhost，不直接公開 port：

```bash
cd ~/cryptoBot
git pull
sudo cp deploy/cryptoviewer.service /etc/systemd/system/cryptoviewer.service
sudo systemctl daemon-reload
sudo systemctl enable --now cryptoviewer
sudo systemctl status cryptoviewer --no-pager
curl -fsS http://127.0.0.1:8765/api/health
```

若要從本機查看，使用 SSH tunnel：

```bash
ssh -L 8765:127.0.0.1:8765 cryptobot@<VPS_IP>
```

然後在本機瀏覽器開啟 `http://127.0.0.1:8765/`。

## 多實例

多實例時不要直接使用單實例 unit，應將 `VIEWER_INSTANCE_DIR` 改成目標實例目錄，例如：

```ini
Environment=VIEWER_INSTANCE_DIR=/home/cryptobot/instances/alice
```

Viewer 只需要讀取該目錄的 `data_live/`、`logs/` 與 `eth_state_live.json`。

## 唯讀驗證

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/meta
curl -fsS 'http://127.0.0.1:8765/api/data?source=live&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/data?source=backtest&days=30&side=ALL' | head -c 500
curl -i -X POST http://127.0.0.1:8765/api/data
systemctl is-active cryptobot
```

最後一個 POST 應回傳 HTTP 405；`cryptobot` 應維持 active。

## 停用與回滾

```bash
sudo systemctl disable --now cryptoviewer
sudo rm /etc/systemd/system/cryptoviewer.service
sudo systemctl daemon-reload
```

這些操作不會停止或修改 `cryptobot.service`，也不會刪除交易資料。
