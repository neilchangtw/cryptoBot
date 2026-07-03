# 新電腦操作 + 架 VPS + 更新既有 VPS（git 時代，多實例）

程式現在都在 GitHub：`https://github.com/neilchangtw/cryptoBot.git`。
所以「換電腦」「架新 VPS」「更新舊 VPS」都用 **git**，不用再手動 scp 一堆檔。

單人歷史部署（tar/scp）見 `VPS_DEPLOY.md`；本文件是 git 時代 + 多人（方案 A）的做法。

---

## A. 換一台新電腦操作（本機）

```bash
# 1. 裝好 git + Python 3.11+，然後 clone
git clone https://github.com/neilchangtw/cryptoBot.git
cd cryptoBot

# 2. 建虛擬環境（本機開發 / 跑回測用；VPS 另外建自己的）
python -m venv .venv
.venv\Scripts\activate            # Windows；Linux/mac 用 source .venv/bin/activate
pip install -r requirements.txt   # 本機用這份（VPS 用 requirements-vps.txt）

# 3. 要跑回測先補 K 線快取（data/ 不在 git 裡）
python fetch_backtest_data.py
```

> `.env`、`data/`、`data_live/`、`.venv/`、`cache/`、`instances/` 都被 gitignore，**不會在 git 裡**。
> 新電腦不需要這些也能改程式 / 推版；要在本機實跑才需要自己的 `.env`。

改完程式推上去（新電腦 → GitHub → VPS 都靠這條）：
```bash
git add -A && git commit -m "..." && git push origin master
```

---

## B. 從零架一台新 VPS（多實例 / 給多人）

### 0. 買 VPS
Hostinger → VPS → KVM（Ubuntu 24.04，純淨）。記下固定 IPv4（本專案目前是 `187.127.108.237`）。

### 1. 基礎設定（root）
```bash
ssh root@<新VPS_IP>
apt update && apt -y upgrade
apt -y install python3 python3-venv python3-pip git curl
timedatectl set-ntp true          # 自動校時（避免 Binance -1021）
adduser --gecos "" cryptobot      # 設密碼
usermod -aG sudo cryptobot
```

### 2. clone 專案（cryptobot 帳號）
```bash
ssh cryptobot@<新VPS_IP>
git clone https://github.com/neilchangtw/cryptoBot.git ~/cryptoBot
cd ~/cryptoBot
```

### 3. 建 venv + 裝依賴
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-vps.txt      # ⚠️ Linux 用這份，不是 requirements.txt
```
> 依賴雷：binance 套件要 `binance-futures-connector`（提供 `binance.um_futures`），
> 不要裝 `binance-connector`（新版拿掉 futures 模組）。詳見 `VPS_DEPLOY.md`。

### 4. 裝多實例 systemd template（一次性）
```bash
sudo cp ~/cryptoBot/deploy/cryptobot@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 5. 新增每個使用者
照 `onboarding.md` 的 Part 2（建 `instances/<名字>/`、填 `.env`、`verify_mainnet.py`、
`systemctl enable --now cryptobot@<名字>`）。你自己也建議建一個 `instances/neil/`。

### 6. Binance API key（每個使用者各自）
每人的 key 都要：IP 白名單填 `<新VPS_IP>`、勾「啟用合約」、關「提款」。

---

## C. 更新既有 VPS 到最新版（一次性轉成 git，之後 `git pull` 就好）

> ✅ **本節已於 2026-07-03 在 srv1722575 執行完成**（VPS 已是 git 檢出，之後更新只要
> `git pull` + 重啟）。以下步驟保留給未來其他 tar/scp 時代的機器參考。
> `.env` / `data_live/` / `logs/` / 狀態檔都被 gitignore，**轉換過程不會被動到**（只覆蓋程式碼檔）。

```bash
ssh cryptobot@187.127.108.237
cd ~/cryptoBot

# 0. 保險：先備份現有目錄（萬一）
cp -a ~/cryptoBot ~/cryptoBot.bak.$(date +%F)

# 1. 就地初始化 git 並指到 GitHub
git init
git remote add origin https://github.com/neilchangtw/cryptoBot.git
git fetch origin

# 2. 強制檢出到最新 master（-f 會用 repo 版本覆蓋同名程式檔；
#    gitignore 的 .env/data_live/logs/狀態檔 不受影響）
git checkout -f -B master origin/master
git branch --set-upstream-to=origin/master master

# 3. 依賴：這次沒有新增 pip 套件（labels/paths/data_feed 只用既有套件），
#    但保險起見可重跑一次：
.venv/bin/pip install -r requirements-vps.txt

# 4. 重啟服務
sudo systemctl restart cryptobot            # 若還是單人舊 service
#   或多實例：對每個實例
#   for u in $(systemctl list-units 'cryptobot@*' --no-legend | awk '{print $1}'); do sudo systemctl restart $u; done
```

**以後更新程式（本機 push 後）：**
```bash
ssh cryptobot@187.127.108.237
cd ~/cryptoBot && git pull
sudo systemctl restart cryptobot            # 或各 cryptobot@實例
```

---

## D. 這一版有什麼新東西（✅ 已於 2026-07-03 隨 git 轉換部署到 VPS）

自上次 tar/scp 部署後新增/改動（都已在 GitHub master 並上線）：

- **回測貼近實盤成交**：`run_backtest.py` 預設 TP/BE 用市價收盤成交（`--ideal` 切回理論價）
- **交易時間對齊幣安**：`analyze` / Telegram 顯示實際成交時刻（K 棒收盤 = 開盤+1h）
- **中文(英文) 顯示**：新增 `labels.py`；`analyze` / `check_health` / Telegram 全面中文(英文)
- **多實例支援**：新增 `paths.py`（`INSTANCE_DIR` 分流 data/logs/state）、`cryptobot@.service`、
  `instance.env.example`；Telegram 訊息開頭標 👤 實例名
- **共用 K 線**：`data_feed.py` 多實例每小時只抓一次 Binance（`cache/`，flock 去重，fail-open）

> 用上面 **Part C** 的 git 轉換一次帶上全部，不用逐檔 scp。
> ⚠️ 重啟前先確認 VPS `.env` 的 `LEVERAGE` / `MARGIN_PER_TRADE` 是你要的值（= 實際下單大小）。

---

## 一鍵驗證（更新 / 部署後）

```bash
cd ~/cryptoBot
git log -1 --format='%h %s'                 # 確認 VPS 已到最新 commit
.venv/bin/python verify_mainnet.py          # 唯讀體檢（單人）
#   多實例：cd instances/<名字> && INSTANCE_DIR=$PWD ../../cryptoBot/.venv/bin/python ../../cryptoBot/verify_mainnet.py
systemctl is-active cryptobot                # 或 systemctl list-units 'cryptobot@*'
```
