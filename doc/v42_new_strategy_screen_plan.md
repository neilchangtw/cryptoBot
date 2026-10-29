# V42 預登記：新策略初步篩選（A 多幣套用現行策略、B 橫截面動能，2026-10-29）

**狀態：REJECTED（開發期即未過 G1，2026-10-29 VPS 執行；未動用保留期）。** 不修改 `strategy.py`、`executor.py`、`.env`、VPS 設定；只在 VPS 跑獨立研究腳本 `backtest/research/v42_data.py`、`backtest/research/v42_screen.py`。公司電腦不下載行情（只用合成資料跑 `tests/test_v42_screen.py`）。

## 目標與原則

- 目標：**現行策略＋新策略**合計每個日曆年 ≥ 0（2021～2026）。
- 年度結果只當最後的檢核（G7），**不得當調參目標**；看到年度數字後不得回頭改規則、幣池或參數。
- 新策略是**獨立帳戶層的加法**，不改現行 ETH 策略任何一行。

## 期間切分（台北日期 UTC+8，左閉右開）

| 段 | 期間 | 用途 |
|---|---|---|
| 開發 dev | 2021-01-01 ～ 2023-12-31 | B 選 L；G1、G4、G5 |
| 驗證 val | 2024-01-01 ～ 2024-12-31 | G2；A 的納入判斷 |
| 保留 holdout | 2025-01-01 ～ 2026-10-28 | G3、G6；**只跑一次** |

- 2020-11～12 只作指標暖機與 B 回看期，不計績效。
- 每段以「進場時間（A）／週一建倉時間（B）」歸屬；跨段最後一筆讀至多 3 天（A）／7 天（B）後續 K 線平倉。
- 保留期需加 `--i-understand-holdout-once`，執行即寫入 `doc/research_results/v42/holdout_log.txt`；同一候選第二次執行一律拒絕。A 只在至少一幣 dev、val 皆 > 0 時、B 只在 G1、G2 皆過時才動用保留期，否則不寫紀錄、直接結案。

## 候選 A：現行策略原封不動套到其他幣

- 標的：BTCUSDT、SOLUSDT、BNBUSDT、XRPUSDT、DOGEUSDT（USDⓈ-M 永續 1h）。
- 規則：**完全沿用現行 V14+R+V25-D**——直接呼叫研究引擎 `v14_export_trades.compute_indicators` 與 `simulate_v14_detailed`（V25-D 查表取自 `strategy.py`，與 `run_backtest.py` 同一引擎），GK 門檻、突破 15 根、時段、Path R、TP／SafeNet／MFE trail／MaxHold／延長、冷卻、月上限、熔斷全部不改。**自由參數 0。**
- 成交與成本：`realistic=True`；滑價 BTC 2bp、其他 4bp（每次市價成交逆向）；引擎 FEE $4＋每筆 +$5；再加實際歷史 funding（持有區間 (進場, 出場] 內每次結算 `−方向 × 數量 × 結算時收盤價 × 費率`）。固定 200U×20x＝$4,000 名目。
- 納入規則：**開發期與驗證期淨 PnL 都 > 0 才納入**，才動用保留期。
- 現行策略基準（G4 相關、G7／G8 組合）：同引擎跑 ETHUSDT，`realistic=True`、+$5、滑價 0（同 V39～V41）、加實際 funding。
- 先驗：[V20](v20_research.md) 曾以 V14（2 年資料、無 R／V25-D）測 9 幣 9/9 IS<0；[V24](v24_research.md) Direction C 多標的分散 REJECTED。A 是在 5.5 年、現行版本上的重新確認，預期通過率低；不因此放寬門檻。

## 候選 B：10 幣橫截面動能（週頻）

- 幣池（固定，不得事後增減）：BTC、ETH、BNB、XRP、ADA、DOGE、SOL、DOT、LTC、LINK（USDⓈ-M 永續）。
- 每週一 00:00 UTC 以收盤價算過去 L 天報酬（t 與 t−L 天兩個時點需有真實 K 線）；**多前 3、空後 3**，等權，多空各 $2,000，總名目 $4,000，持有一週。有效幣 < 6 則該週空手。
- 每週調回目標權重，只對「|目標 − 漂移後部位|」收 taker 0.04%＋滑價（BTC 2bp、其他 4bp）；期末全平。加實際 funding（持有期間每次結算 `−名目 × 費率`）。
- 參數：L ∈ {14, 28, 56}。開發期以「自己＋相鄰 L 的 PnL 中位數」最高者為平台中心（同分取較短），**鎖定後**用於 val 與 holdout。

## 門檻（看資料前鎖定）

| Gate | 條件 |
|---|---|
| G1 | 開發期平台中心 PnL > 0 且相鄰 L 皆 > 0（A 無參數，只看開發期 > 0） |
| G2 | 2024 驗證期 PnL > 0 |
| G3 | 保留期 PnL > 0 |
| G4 | 開發期月 PnL 與現行策略月 PnL 相關 < 0.3 |
| G5 | 開發期 PnL ≥ 隨機進場 200 次的第 95 百分位（A：同數量隨機小時取代「GK＋突破」，其餘時段／Path R／冷卻／熔斷／出場不變；B：每週隨機選 3 多 3 空，成本相同） |
| G6 | 保留期去掉最佳月仍 > 0 |
| G7 | 組合（現行＋通過 G1～G6 者）2021～2026 每個日曆年 ≥ 0（依出場／週結算的台北日期；最後報告） |
| G8 | 組合 5.5 年已實現日 PnL 最大回撤 ≤ $2,281 |

通過 G1～G6 的候選才進組合；G7、G8 皆過 → **SHADOW 候選**（以 2026-10-29 之後的新資料前推，再由使用者決定）；否則 **REJECTED**。

## 已排除方向（不在本輪重做）

- 2026-10-08 大行情外掛 10 輪 462 組全 REJECTED：單邊趨勢／Donchian／波動擴張／日線 TSMOM、ETH/BTC 配對、4h／日線 GK、funding 極端反向、30m GK、BTC 領先、爆量反轉、SIDE 均值回歸、funding 結算時段、順勢日內時段（[V35](v35_research.md)）。
- [V38](v38_signal_aggtrades_plan.md)／[V39](v39_s_exhaustion_oos_plan.md)：小時內訂單流（aggTrades 主動量、大單、突破承接）REJECTED，結案。
- [V40](v40_dvol_bookdepth_plan.md)：Deribit DVOL 突升、bookDepth 盤口深度 REJECTED。
- 現行策略本身的進出場微調（V15～V34、V36～V37 見 AGENTS.md 索引）不屬本輪。

## 資料（VPS）

`v42_data.py fetch` 從 data.binance.vision 抓 11 幣（10 幣池＋ETH 基準重疊）2020-11～2026-10 的 UM 1h K 線月檔與 fundingRate 月檔；最後一個月 K 線用每日檔，funding 用 Binance 公開 `/fapi/v1/fundingRate` 補（不需 API key）。可續傳，`status` 檢查月份、K 線跳格、funding 筆數。輸出 `data/v42/{SYMBOL}_1h.csv`（欄位同 `fetch_backtest_data.py`，UTC+8）與 `data/v42/{SYMBOL}_funding.csv`（同 `data/ETHUSDT_funding.csv`）。

## 執行順序（VPS，cryptobot，~/cryptoBot）

```
.venv/bin/python backtest/research/v42_data.py fetch
.venv/bin/python backtest/research/v42_data.py status
.venv/bin/python backtest/research/v42_screen.py a --period dev
.venv/bin/python backtest/research/v42_screen.py b --period dev
.venv/bin/python backtest/research/v42_screen.py a --period val
.venv/bin/python backtest/research/v42_screen.py b --period val
.venv/bin/python backtest/research/v42_screen.py report
# 以下各只准一次：
.venv/bin/python backtest/research/v42_screen.py a --period holdout --i-understand-holdout-once
.venv/bin/python backtest/research/v42_screen.py b --period holdout --i-understand-holdout-once
.venv/bin/python backtest/research/v42_screen.py report
```

輸出：`doc/research_results/v42/`（`a_{period}_trades.csv`、`a_{period}_monthly.csv`、`b_{period}_weekly.csv`、`b_{period}_positions.csv`、`*_summary.json`、`b_selection.json`、`gates.json`、`holdout_log.txt`）。

## 限制

- 2021～2024 的 ETH K 線已被歷次研究反覆使用；A 的其他幣、B 的橫截面排名對本假說而言為未見資料。保留期 2025 起與 V35 保留期（2024-10～2026-10）大幅重疊，B 的 G3 只是一次性確認，不能取代 2026-10-29 後的前推。
- A 的 funding 在引擎外加計，不進熔斷帳本（引擎日虧／月虧只看價格 PnL＋FEE＋$5）。
- B 以小時收盤價成交，未模擬盤口深度；DOT、SOL 等小幣的 4bp 滑價在極端行情可能偏樂觀。
- 幣池是今天看得到的主流幣，含存活者偏差（2021 年時的排名未必相同）。

## 結果（2026-10-29，VPS，開發期 2021-01-01～2023-12-31）

**V42 REJECTED：A 五幣、B 皆未過 G1，依預登記不跑保留期。** 現行策略不變。

### A：現行策略套到其他幣——5/5 開發期虧損

| 幣 | 筆 | 勝率 | L 淨 | S 淨 | funding | 淨 PnL | G5 隨機分位 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ETHUSDT（現行基準） | 328 | 51.5% | -482 | +743 | -4 | **+261** | — |
| BTCUSDT | 287 | 40.4% | -1,319 | -2,393 | +4 | **-3,712** | 70% |
| SOLUSDT | 274 | 51.1% | -1,263 | -1,812 | -43 | **-3,075** | 90% |
| BNBUSDT | 292 | 42.5% | -1,144 | -1,812 | -15 | **-2,956** | 92% |
| XRPUSDT | 284 | 46.5% | -968 | -274 | +10 | **-1,242** | 98% |
| DOGEUSDT | 298 | 47.7% | -1,601 | -1,004 | +28 | **-2,605** | 69% |

- 5 幣 L、S 兩邊全部為負；沒有任何幣開發期 > 0 → 無幣可納入，A 結案。與 [V20](v20_research.md)（V14、2 年、9/9 IS<0）結論一致：**GK 壓縮突破是 ETH 專屬**，延長至 3 年開發期、換成現行 V14+R+V25-D 仍成立。
- 首次執行時 funding 數量誤用引擎捨入到 2 位小數的 entry_price（DOGE 2021 年價格捨入為 0，G5 出現 nan）；已改用未捨入收盤價（commit 見下）。funding 合計僅 ±$50 級，不影響判定。

### B：10 幣橫截面動能——G1 FAIL、G5 FAIL

| L（天） | 開發淨 PnL | 平台分數（自己＋鄰居中位數） |
|---:|---:|---:|
| 14 | -1,597 | +15 |
| 28 | +1,627 | -236 |
| 56 | -236 | +695 |

- 依預登記選 L=56（平台分數最高），其開發期淨 -$236（毛 +$110、funding -$54、成本 $291）、週勝率 48.7%、去最佳月 -$2,315 → **G1 FAIL**。
- 唯一為正的 L=28（+$1,627）兩側鄰居皆負，是孤峰，不符平台要求；不得改選。
- G5：隨機 3 多 3 空 200 次的第 95 百分位 +$5,610，L=56 只在 44% 分位 → **FAIL**。等權多空組合本身變異很大，+$1,627 也遠低於隨機 95%（橫截面排名無可辨識的訊號）。

### 未執行

- 驗證期、保留期：A 無幣可納入、B 未過 G1 → 依規則不動用保留期（`holdout_log.txt` 無紀錄），2025-01-01 起資料對 V42 類假說仍未使用。
