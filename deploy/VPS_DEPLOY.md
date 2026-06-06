# CryptoBot — Hostinger VPS 部署紀錄（Live mainnet）

2026-06-01 go-live：從家用 Windows（paper/Testnet）遷移到 Hostinger KVM VPS 跑正式盤。
本文件記錄完整步驟與踩過的雷，供日後重部署 / 遷移參考。

## 背景：為什麼要上 VPS

| 原始問題 | 根因 | VPS 解法 |
|----------|------|----------|
| Binance `-2015` Invalid API-key/IP | 家用 HiNet **浮動 IP**（PPPoE 重撥就變），白名單對不上 | VPS **固定 IP** `187.127.108.237`，白名單設一次就好 |
| Binance `-1021` Timestamp ahead | 本機系統時鐘快 ~2.4 秒 | Linux 內建 NTP（`timedatectl set-ntp true`）自動校時 |

> 共享主機（Single Web Hosting）不行：無 SSH/Python/常駐/固定 IP。必須用 **VPS（KVM）**。

## 環境

- VPS：Hostinger KVM 1（Ubuntu 24.04），固定 IP `187.127.108.237`
- 帳號：`cryptobot`（非 root），專案 `/home/cryptobot/cryptoBot`
- 服務：systemd unit `cryptobot`（`deploy/cryptobot.service`），enabled = 開機自啟 + 崩潰自動重啟
- 模式：LIVE mainnet（`PAPER_TRADING=false` / `BINANCE_TESTNET=false`），資料寫 `data_live/`

---

## 部署步驟

### 0. 買 VPS
Hostinger → VPS → KVM 1 → Ubuntu 24.04（純淨、不要含面板模板）。記下固定 IPv4。

### 1. Binance API key 設定
API 管理 → 編輯 key：
- IP 存取限制 → 填 **VPS 固定 IP**（不要填家裡浮動 IP）
- 勾選 **啟用合約 / Enable Futures**（新 key 預設沒開期貨，會 -2015）

### 2. VPS 基礎設定（root）
```bash
ssh root@187.127.108.237
apt update && apt -y upgrade
apt -y install python3 python3-venv python3-pip git curl
timedatectl set-ntp true
timedatectl                       # 確認 System clock synchronized: yes
adduser --gecos "" cryptobot      # 設 cryptobot 密碼
usermod -aG sudo cryptobot
reboot                            # 載入新核心（若提示 restart required）
```

### 3. 打包並上傳（本機 CMD / PowerShell）
```cmd
cd C:\Users\wei\IdeaProjects\cryptoBot
tar --exclude=.venv --exclude=.git --exclude=data --exclude=logs --exclude=data_live --exclude=__pycache__ -czf %USERPROFILE%\cryptobot.tgz .
scp %USERPROFILE%\cryptobot.tgz cryptobot@187.127.108.237:~/
```
> `.env`（含正式盤 key）有被打包，scp 走 SSH 加密。`data/`、`logs/` 不傳，會重建/重抓。

### 4. 建 venv + 裝依賴（cryptobot）
```bash
ssh cryptobot@187.127.108.237
mkdir -p ~/cryptoBot && tar -xzf ~/cryptobot.tgz -C ~/cryptoBot
cd ~/cryptoBot
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-vps.txt
mkdir -p data logs data_live
chmod 600 .env
```

⚠️ **依賴雷（重要）**：
- binance 套件是 **`binance-futures-connector`（本機鎖 4.1.0）**，提供 `binance.um_futures.UMFutures`。
- **不要**裝 `binance-connector` —— 那是不同套件，其 3.13.0 **移除了 futures 模組**（只剩 spot/websocket），會 `No module named 'binance.um_futures'`。
- repo 根目錄的 `requirements.txt` 是舊 Anaconda freeze（含 Windows-only 套件），**Linux 請用 `requirements-vps.txt`**。
- pandas 3.0 / numpy 2.4 與策略相容（已驗證）。

### 5. 唯讀連線驗證
```bash
.venv/bin/python verify_mainnet.py
```
要看到帳戶 USDT 餘額讀出（= -2015 解除）、Hedge Mode、無持倉、K 線抓取 OK。

### 6. 設 systemd 常駐服務
```bash
sudo cp ~/cryptoBot/deploy/cryptobot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cryptobot
sudo systemctl start cryptobot
sudo systemctl status cryptobot --no-pager
```

### 7. 重開機驗證自動啟動
```bash
sudo reboot
# 等 ~40s 重連
ssh cryptobot@187.127.108.237
sudo systemctl status cryptobot --no-pager     # Active: active (running)
journalctl -u cryptobot -n 20 --no-pager       # 看到 -- Boot -- 後自動 Started
```

---

## 維運指令

完整速查見 **`deploy/cheatsheet.txt`**（可 scp 到 VPS：
`scp deploy\cheatsheet.txt cryptobot@187.127.108.237:~/cryptoBot/deploy/`）。

常用：
```bash
systemctl is-active cryptobot                 # 是否在跑
sudo systemctl restart cryptobot              # 重啟
journalctl -u cryptobot -f                    # 即時日誌
journalctl -u cryptobot -p err --no-pager     # 只看錯誤
tail -30 ~/cryptoBot/logs/alerts.log          # 程式告警
tail -5  ~/cryptoBot/data_live/bar_snapshots.csv   # 最近 bar 快照
.venv/bin/python verify_mainnet.py            # 唯讀體檢
```

### 看績效 / 交易記錄（終端機，免開 dashboard）

`trades.csv` 有 50+ 欄，`cat`/`tail` 直接看是亂碼。用 `analyze.py`（只挑關鍵欄位、對齊輸出；
依 `.env` 的 `PAPER_TRADING` 自動選 `data_live/`）：

```bash
cd ~/cryptoBot
.venv/bin/python analyze.py            # 收益分析彙總（全期間）：總損益/WR/PF/最大回撤/出場分佈/L vs S/regime
.venv/bin/python analyze.py 30         # 收益分析（最近 30 天）
.venv/bin/python analyze.py -t         # 對齊好讀的交易列表（最近 20 筆）
.venv/bin/python analyze.py -t 50      # 交易列表最近 50 筆
.venv/bin/python analyze.py --paper    # 強制看模擬盤 data/（若有帶資料上來）
```

> Telegram 也有對應指令：`/analysis`（彙總）/ `/analysis 30`（近 30 天）/ `/trades`（近 5 筆）/ `/pnl`。
> `analyze.py` 與 `/analysis` 共用 `analysis_report.py` 同一套計算，數字一致。
> `analyze.py` 只讀 CSV、不碰 bot，**不用重啟服務**即可使用。

### 在 VPS 跑回測

回測腳本（`backtest/research/`）要讀 730 天 K 線快取，但 `data/` 整個被 gitignore / 沒打包上來，
所以 fresh VPS 上沒有快取。用 `fetch_backtest_data.py` 從 Binance Futures 公開端點補齊（不需 API key）：

```bash
cd ~/cryptoBot
.venv/bin/python fetch_backtest_data.py                 # 抓 ETH+BTC 1h 730 天 → data/
.venv/bin/python fetch_backtest_data.py --days 365      # 只抓 365 天
.venv/bin/python fetch_backtest_data.py --interval 4h   # 換時框
# 補齊後即可跑研究腳本：
.venv/bin/python backtest/research/v14_r5_champion_validation.py
```

> 少數 `btc_*` 腳本會用 matplotlib 畫圖，headless VPS 跑前先 `export MPLBACKEND=Agg`；
> 核心 ETH 腳本（`v14_*`/`v23_*`/`v25_*`）多半只 print 數字，不受影響。

---

## 更新已部署的檔案（修 bug / 加功能）

VPS 是用 tar/scp 部署（**不含 `.git`，不能 `git pull`**）。改了本機檔案後，scp 覆蓋對應檔即可：

```cmd
:: 本機 CMD / PowerShell（在專案根目錄）
cd C:\Users\wei\IdeaProjects\cryptoBot
scp main_eth.py analysis_report.py analyze.py fetch_backtest_data.py cryptobot@187.127.108.237:~/cryptoBot/
```

```bash
# VPS：只有改到 bot 主程式（main_eth.py / strategy.py / executor.py / binance_trade.py）才需重啟
sudo systemctl restart cryptobot
sudo systemctl status cryptobot --no-pager
```

- 純工具（`analyze.py` / `analysis_report.py` / `fetch_backtest_data.py`）只讀 CSV → scp 完直接用，**免重啟**。
- `.env` 不要 scp 覆蓋（VPS 的才是正式 key）；要改參數用 `nano ~/cryptoBot/.env` 直接編輯，改完重啟。

---

## 常見雷

1. **Hostinger 網頁終端預設是 root**（人在 `/root`），跑 `.venv/bin/python ...` 會 `No such file`。
   先切帳號再操作：`su - cryptobot` → `cd ~/cryptoBot`（root su 不需密碼）。或一律用絕對路徑 `/home/cryptobot/cryptoBot/...`。
2. **Live 模式資料在 `data_live/`，不是 `data/`**（paper 才用 `data/`）。
3. **網頁終端貼多行 / 長指令會被自動縮排或截斷** → 用本機 CMD 的 scp 傳檔，或用 `nano` 編輯，避免 inline 多行貼上。
4. **只能有一個機器人跑**：家裡 Windows 的 `start.bat` 別再開（同帳戶重複下單）。
5. **槓桿有兩個來源，必須一致**（2026-06-06 修正先前的誤解）：
   - `strategy.py` 寫死 `LEVERAGE=20` → 開機 `set_leverage()` 設**幣安帳戶槓桿**。
   - `binance_trade.py` 讀 **`.env` 的 `LEVERAGE`** → 算**下單名目**（`notional = MARGIN_PER_TRADE × LEVERAGE`，line 402）。
   - ⚠️ 所以 `.env` 的 `LEVERAGE` **確實會影響實際下單大小**。曾經 `.env=10` 但 `strategy.py=20`，
     結果帳戶 20x、卻只開 $2,000 名目（半倉）。**改槓桿要同時確保兩邊一致**，改 `.env` 後 `systemctl restart`。
