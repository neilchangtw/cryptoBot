# VPS 唯讀交易視覺化

`vps_viewer.py` 與 `vps_viewer.html` 是獨立的唯讀服務，不會載入 `.env`，不會匯入策略、執行器或 Binance 下單模組，也不會提供任何寫入 API。

## 公開網址

目前 Viewer 以公開網址提供：<https://srv1722575.hstgr.cloud/>（VPS `srv1722575`）。服務本身仍綁
`127.0.0.1:8765`，對外 HTTPS 由 VPS 上的反向代理轉送，代理設定不在本 repo。

公開網址代表任何知道網址的人都能看到：

- 實戰逐筆交易、PnL、保證金、目前持倉與即時開單條件
- 策略健康度、證偽檢查與回測對照

頁面不顯示錢包餘額、API key 或 `.env` 內容，也沒有任何寫入／下單入口（非 GET 一律 405）。
若之後要限制觀看者，請在反向代理層加帳號密碼（例如 Basic Auth），Viewer 程式不需修改。

## 顯示內容

- ETHUSDT 1h OHLC K 線
- 已收盤／形成中的 K 線區分
- Long／Short 進場標記
- 平倉標記、出場原因、PnL、持倉時間與 regime
- 目前 `eth_state_live.json` 內的策略持倉
- 最近交易與服務／資料 freshness
- Asia/Taipei 顯示，API 同時提供 UTC 時間
- 實戰／回測資料模式切換
- 可自由選擇起訖日期，或使用 7／30／90／180（半年）／365 天快捷期間；快捷期間以資料來源最新日期往前計算。日期以 Asia/Taipei 計算，最早可選日依各資料來源可用 K 線／交易紀錄動態顯示，不能選到更早日期
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
- **策略健康度（2026-10-08 新增）**：V29 健康度（實戰讀 `eth_state_live.json` 的 `edge_health.cusum`，
  曲線為依交易紀錄回放）、證偽檢查三項（Edge 強度／突破延續／尾部風險，判讀函式直接沿用
  `edge_falsify.py`，視窗為最近 30 筆）、近 6 個月的月度燈號與「連續非綠燈月數」，對應
  「連續兩個月 🟡 → 凍結加碼」規則。不受頁面日期／方向篩選影響。實盤貼合項需要回測引擎，
  Viewer 不執行，請看下方「實戰 vs 回測」卡片或 VPS 上的 `analyze.py`
- **金額基準切換（實際金額／200U 基準）**：200U 基準 = 實際 PnL × $4,000 ÷ 當筆名目，排除
  200→300→500U 保證金放大效果；總覽、圖表、月度、交易列表都跟著切換。交易列表新增「保證金」欄
- **近 20 筆滾動 MFE／MAE**：每點是到該筆為止最近 20 筆的平均（以全歷史計算，區間開頭也有完整窗口），
  虛線為全歷史中位數，用來提早看出突破延續轉弱
- **實戰 vs 回測（同期間）**：只在實戰模式顯示，與回測快照在相同日期／方向下比較累積 PnL、
  逐筆吻合數（方向＋進場成交時刻）、吻合交易的成交差與出場原因差異，並列出只出現在單邊的交易
- **回測模式 MAE／MFE／進場 GK**：由更新器另外輸出的逐筆指標檔補上，回測模式的交易風險圖不再空白

實戰模式優先讀取 Binance Futures 公開端點；網路失敗時退回本機快取。回測模式優先讀取
`data/ETHUSDT_1h_latest730d.csv`，讓圖表可與回測使用的 K 線期間對齊。公開 K 線不需要 API key。

## 回測資料快照

Viewer 本身仍是唯讀：它不執行回測、不寫交易資料，也不會載入下單模組。獨立的
`cryptoviewer-backtest-refresh.timer` 每小時在新 K 線收盤後執行一次更新器；更新器先抓最新
730 天已收盤 K 線，再以現行回測引擎重算，驗證明細格式後以原子替換更新快照。失敗時保留
上一份可讀快照，不會留下半寫入檔案。頁面顯示回測快照更新時間；超過 90 分鐘會標示可能缺少
最近已收盤 K 線。

更新器在快照成功後，會用與 `run_backtest.py` 預設相同的參數（貼近實盤、0bp 滑價、保證金排程、
每筆額外成本 $5）重跑同一個引擎，輸出 `data/backtest_trade_metrics.csv`（每筆 MAE／MFE／進場 GK）。
筆數或進場時間與快照不一致時不寫檔、保留舊檔；這一步失敗不影響主快照。可用
`VIEWER_BACKTEST_METRICS_PATH` 指定其他路徑（Viewer 與更新服務需一致）。

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

分析資料透過唯讀 `GET /api/analysis?source=live|backtest&start=YYYY-MM-DD&end=YYYY-MM-DD&side=ALL&basis=actual|200u` 提供，
頁面每次載入、切換來源／區間／方向、按重新整理，以及每 60 秒會同步更新。
起訖日為含首尾的台北日曆日期；交易統計、交易明細、GK／未開單原因、生命週期與 K 線都使用相同日期範圍。
已平倉績效依實際出場日歸屬；尚未平倉的交易依進場日顯示。
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

實戰公開 K 線依所選日期向 Binance 分頁讀取；單次最多 30,000 根 1h K 線，超過時 API 會要求縮短區間。
回測日期受本機回測 K 線快取與交易資料可用範圍限制。頁面會顯示最早可選日期與實際 K 線涵蓋範圍；
若 K 線來源缺少部分區間，交易績效仍按所選日期計算，並明確提醒圖表可能缺少範圍外的成交標記。

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

## 策略健康度 API

`GET /api/edge?source=live|backtest` 回傳 V29 健康度、證偽檢查與月度燈號。常數來源：

- 保證金排程：`run_backtest.py` 的 `MARGIN_SCHEDULE`
- V29 門檻：`strategy.py` 的 `EDGE_CUSUM_K`、`EDGE_CUSUM_YELLOW`、`EDGE_CUSUM_RED`

以上都用 `ast` 讀字面值，不執行也不匯入這兩個模組；`edge_falsify.py` 只使用其純計算函式。
實戰 trades.csv 沒有保證金欄，Viewer 以「毛損益 ÷ 價格變動」推回名目（與機器人 V29 用的
「qty × 進場價」等價），進出場同價無法推算時才退回保證金排程。

## 唯讀驗證

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/meta
curl -fsS 'http://127.0.0.1:8765/api/data?source=live&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/data?source=backtest&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/analysis?source=live&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/analysis?source=backtest&days=30&side=ALL' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/analysis?source=live&days=90&side=ALL&basis=200u' | head -c 500
curl -fsS 'http://127.0.0.1:8765/api/edge?source=live' | head -c 500
curl -i -X POST http://127.0.0.1:8765/api/data
systemctl is-active cryptobot
```

最後一個 POST 應回傳 HTTP 405；`cryptobot` 應維持 active。

## 更新既有 VPS（2026-10-08 版）

Viewer 與更新器是獨立服務，更新不需要重啟交易機器人：

```bash
cd ~/cryptoBot
git pull
.venv/bin/python refresh_viewer_backtest.py      # 立即產生回測快照與逐筆指標檔
sudo systemctl restart cryptoviewer
curl -fsS 'http://127.0.0.1:8765/api/edge?source=live' | head -c 300
systemctl is-active cryptobot                     # 應維持 active，未被重啟
```

## 停用與回滾

```bash
sudo systemctl disable --now cryptoviewer
sudo rm /etc/systemd/system/cryptoviewer.service
sudo systemctl daemon-reload
```

這些操作不會停止或修改 `cryptobot.service`，也不會刪除交易資料。
