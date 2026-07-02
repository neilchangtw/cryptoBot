# 新增使用者（多人 / 方案 A）

多實例架構下，每個人 = 一個資料夾 + 一份自己的 `.env`：
`/home/cryptobot/instances/<名字>/`。彼此完全隔離（各自 Binance 帳號、Telegram、資料、狀態）。

---

## Part 1 — 請對方準備（可直接複製轉發給對方）

> 要用交易機器人，請給我以下 **4 樣**：
>
> **1) 幣安 API Key 與 Secret**
> - 幣安 App/網頁 →「API 管理」→ 建立 API
> - 權限**只勾「啟用合約交易 / Enable Futures」**；**「提款 / Withdraw」千萬不要開**
> - IP 存取限制填：`187.127.108.237`（我的伺服器固定 IP）
> - 把 **API Key** 和 **Secret Key** 私訊給我（Secret 只會顯示一次，記得複製）
>
> **2) 你自己的 Telegram Bot Token**
> - Telegram 搜尋 `@BotFather` → 輸入 `/newbot` → 照指示取名
> - 它會給你一串 token（像 `123456789:AAxxxxxxxx`），傳給我
> - ⚠️ 每人要各自一支 bot，不能共用
>
> **3) 你的 Telegram Chat ID**
> - 先對你剛剛建立的那支 bot 隨便說一句話（讓它有你的對話）
> - 再搜尋 `@userinfobot` → 對它 `/start` → 它會回你的 **Id**（一串數字），傳給我
>
> **4) 交易設定**
> - 想用的槓桿與每筆保證金（例如 `20x` / `$200`）
> - 先跑「模擬」還是直接「真錢」

---

## Part 2 — 拿到後，你在 VPS 新增這個人（以 alice 為例）

```bash
ssh cryptobot@187.127.108.237

# 1. 建實例資料夾 + 從範本複製 .env
mkdir -p /home/cryptobot/instances/alice
cp ~/cryptoBot/deploy/instance.env.example /home/cryptobot/instances/alice/.env

# 2. 填入對方給的資料
nano /home/cryptobot/instances/alice/.env
#    BINANCE_API_KEY / BINANCE_API_SECRET     ← 對方的 key
#    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID     ← 對方自己的 bot + chat id
#    LEVERAGE / MARGIN_PER_TRADE               ← 對方要的部位大小
#    PAPER_TRADING=false（真錢）或 true（模擬）
#    INSTANCE_NAME=Alice                        ← Telegram 訊息開頭會顯示
chmod 600 /home/cryptobot/instances/alice/.env   # 保護 key

# 3. 上線前唯讀體檢（用該實例的設定跑，不下單）
cd /home/cryptobot/instances/alice
INSTANCE_DIR=$PWD /home/cryptobot/cryptoBot/.venv/bin/python \
    /home/cryptobot/cryptoBot/verify_mainnet.py
#    要看到：帳戶 USDT 餘額讀出、Hedge Mode、無持倉、K 線 OK

# 4. 啟動（開機自啟 + 立即啟動）
sudo systemctl enable --now cryptobot@alice
journalctl -u cryptobot@alice -f          # 看有沒有正常跑（Ctrl+C 離開）
```

> 前提：template unit 已裝過一次（見 `new_vps_setup.md` 第 4 步）。
> 之後每新增一人，只重複上面 4 步、把 `alice` 換成新名字即可。

---

## 每實例日常操作（把 alice 換成對應名字）

```bash
systemctl is-active cryptobot@alice          # 是否在跑
sudo systemctl restart cryptobot@alice       # 重啟（改 .env 或程式後）
sudo systemctl stop cryptobot@alice          # 停止
journalctl -u cryptobot@alice -f             # 即時日誌
tail -30 /home/cryptobot/instances/alice/logs/alerts.log   # 程式告警

# 看該使用者績效 / 開單條件（要帶 INSTANCE_DIR）
cd /home/cryptobot/instances/alice
INSTANCE_DIR=$PWD /home/cryptobot/cryptoBot/.venv/bin/python /home/cryptobot/cryptoBot/analyze.py -t
INSTANCE_DIR=$PWD /home/cryptobot/cryptoBot/.venv/bin/python /home/cryptobot/cryptoBot/check_signal.py

# 看全部實例
systemctl list-units 'cryptobot@*'
```

---

## 安全與注意

- **API key**：你拿到別人的 Secret = 能在他帳戶下單。務必要求對方「**關提款 + 綁本 VPS IP**」，
  萬一 VPS 被入侵損失才可控。
- **每人一支 Telegram bot**：同一 token 兩個實例會互搶更新（getUpdates 409）。
- **Telegram 訊息開頭 👤 名字**：每則都標，讓對方一眼確認是自己的（`INSTANCE_NAME`）。
- **共用 K 線**：多實例每小時只有一個實例真的打 Binance、其他讀共用檔（`cache/`），自動、免設定。
- **合規**：替他人操作真錢可能涉及代操 / 理財規範，依所在地確認。
