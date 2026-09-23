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
- K 線縮放、拖曳平移與全覽復原（桌面滑鼠及手機觸控）；手機圖表允許頁面垂直滑動
- 績效分析：累積 PnL、月度 PnL、方向勝率、出場原因、regime、持倉時間勝率
- 交易品質分析：累積 PnL 對齊每筆 MAE／MFE、最大回撤、PF、連勝連敗
- GK 壓縮熱圖可點擊，主 K 線會聚焦該時段，直接檢查壓縮後是否突破與交易結果
- 若資料檔存在，顯示 GK 百分位與持倉生命週期未實現 PnL；缺資料時明確顯示資料不足
- 窄螢幕將分析圖表改為單欄，並在手機上加大篩選與刷新控件的觸控尺寸
- 快速切換篩選時會依序載入最新選項；載入失敗時標明頁面仍是上次成功資料
- 顯示統計日期範圍與目前 K 線涵蓋範圍，提醒長區間統計可能超出圖表 K 線範圍
- 未保存的 MAE／MFE／GK 值顯示為缺值，不會畫成 0；生命週期摘要列出首尾樣本數
- 持倉卡距離 TP／SafeNet 以同卡顯示的現價計算；浮動 PnL 明確標示為未計費用與資金費的估算

實戰模式優先讀取 Binance Futures 公開端點；網路失敗時退回本機快取。回測模式優先讀取
`data/ETHUSDT_1h_latest730d.csv`，讓圖表可與回測使用的 K 線期間對齊。公開 K 線不需要 API key。

## 回測資料快照

Viewer 本身仍是唯讀：它不執行回測、不寫交易資料，也不會載入下單模組。獨立的
`cryptoviewer-backtest-refresh.timer` 每小時在新 K 線收盤後執行一次更新器；更新器先抓最新
730 天已收盤 K 線，再以現行回測引擎重算，驗證明細格式後以原子替換更新快照。失敗時保留
上一份可讀快照，不會留下半寫入檔案。頁面顯示回測快照更新時間；超過 90 分鐘會標示可能缺少
最近已收盤 K 線。

手動更新或安裝排程：

```bash
cd ~/cryptoBot
mkdir -p data
.venv/bin/python refresh_viewer_backtest.py
sudo cp deploy/cryptoviewer-backtest-refresh.service /etc/systemd/system/
sudo cp deploy/cryptoviewer-backtest-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cryptoviewer-backtest-refresh.timer
sudo systemctl start cryptoviewer-backtest-refresh.service
systemctl list-timers cryptoviewer-backtest-refresh.timer --no-pager
```

回測資料來源預設是 `data/backtest_trades.txt`；若 Viewer 設定了自訂
`VIEWER_BACKTEST_PATH`，更新服務也需使用相同路徑。若快照不存在或更新失敗，頁面不會把實戰
資料冒充成回測。回測模式沒有目前持倉狀態。

## 分析資料與限制

分析資料透過唯讀 `GET /api/analysis?source=live|backtest&days=30&side=ALL` 提供，
頁面每次載入、切換來源／區間／方向、按重新整理，以及每 60 秒會同步更新。
`live` 模式會讀取 `data_live/bar_snapshots.csv` 與 `data_live/position_lifecycle.csv`。
回測模式若要顯示 GK／breakout／生命週期圖，需另外產生與回測同一批資料對應的 CSV，
預設檔名為：

```text
data/backtest_bar_snapshots.csv
data/backtest_position_lifecycle.csv
```

也可以在 `cryptoviewer.service` 以 `VIEWER_BACKTEST_SNAPSHOTS_PATH`、
`VIEWER_BACKTEST_LIFECYCLE_PATH` 指向其他唯讀檔案。現有回測文字明細沒有逐根保存
TP／SafeNet／MaxHold 價格線，因此 viewer 不會自行推算或從策略程式重建這些線，避免把推測
當成實際紀錄。

實戰公開 K 線每次最多讀取最近 1,000 根 1h K 線。若選擇 90 天或 1 年，交易統計和明細仍按所選
天數篩選，但 K 線可能只覆蓋區間後段；頁面會標示兩者的涵蓋範圍，圖表不會顯示 K 線範圍外的
成交標記。縮窄區間可讓圖表與績效統計更接近同一段期間。

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
curl -fsS 'http://127.0.0.1:8765/api/analysis?source=live&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/analysis?source=backtest&days=30&side=ALL' | head -c 500
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
