# MaxHold 延續研究第一批十輪：事前登記（2026-09-23）

本檔先於這十條候選的收益計算建立。沿用本機凍結 ETHUSDT 1h V14+R + V25-D、L15/S15、2024-09-08～2026-09-08 17,519 根、200→300→500U 保證金排程。原費用及每筆額外 $5、funding、逐時 mark equity，另跑 0／2／5bp 額外逆向滑價。資料以台北時間標記。這是已反覆使用的歷史，2026-01 後只稱固定晚期切片，**不稱真正 OOS**。所有規則只可做歷史淘汰或 prospective shadow 假說，不部署。

## 查重及已知限制

已測 V26、V31、9/12、9/23 共約 50 條 MaxHold 規則涵蓋固定／淺虧寬限、常見 OHLCV 技術指標、到期那小時 5m、整筆持倉 5m 價格路徑、現貨、OI 總量／大戶部位／全市場帳戶、最近 funding 正負／斜率、mark 差。本批不重設已拒閾值。9/10 premium 的進場過濾失敗；本批的決策點是**已持倉且虧損的原 MaxHold**，並測 premium K 線波幅，不能把它當獨立來源。大戶 account count、premium 波幅、整段 5m flow 是本機先前未見相同 MaxHold 定義；同資料家族的候選高度相關，十輪不等於十份獨立統計證據。

外部 archive 沒有當年 `received_ts`／未修訂快照。OI 指標只取 `D-75min` 的 5m metrics（假設於事件後10m可用），premium 指標取 `D-2h` 開盤的1h premium K（其收盤 `D-1h`，再留1h緩衝）。這些是保守的**模型假設**，不能證明當年實盤可取得同值。5m futures OHLCV 只取進場成交後至 D 的已完成子棒；兩個聚合無效 1h 小時路徑不觸發。funding clock 只用 `D` **之前**三次已結算時間估計下一次，不讀實際未來結算時間或費率。

本輪沿用已更正的**總持倉上限**：只在原 MH 棒收盤且方向性浮盈≤0 時可延長，最多到原 MH +2 個 1h bar。寬限期間若轉盈，原 BE／MH-ext 的計數也包含已消耗的寬限 bar；SafeNet、TP、MFE-trail 仍優先。原本在 MH 盈利的路徑不變。完整狀態引擎重算 L/S、占用、冷卻、替代交易、日月／連敗風控、funding 後收益與 mark MDD；funding 現金流用於績效及 equity，依原 executor 語意不回灌成交 PnL 熔斷。

先做三成本完整逐筆欄位、funding 帳本及 mark equity no-op parity，並做資料與交易 11,000／16,000 根截斷前綴檢查。每條中心與鄰域皆固定，不用鄰域挑獲利版本。驗證須全期 +5% PnL／+1pp WR、0／2／5bp 均雙增、30 個 24h 獨立直接事件／晚期15個、MDD及最差30日不重大惡化、晚期與參數鄰域同向。歷史通過最多 `SHADOW_ONLY`；真 OOS 尚不存在。若中心未同時改善淨利與勝率，`REJECTED` 後照表繼續；資料時間不可核則 `DATA_LIMITED`。

## 鎖定十條規則

`s=+1` 為 Long、`s=-1` 為 Short；`D` 為 MaxHold 已完成 1h 棒收盤。所有候選只在原本虧損 MH 將出場時評估，觸發則給總計最多2h寬限；值無效或欠完整窗就照原規則出場。鄰域僅測穩定性。

| 輪 | 到期可觀測指標，主閾值（鄰域） | 與既有機制差別及失敗風險 |
|---:|---|---|
| 1 | `s×premium_level ≥0`（±0.5bp）；level 為延遲 premium close 減其前24h中位 | 到期時擁擠方向，非進場 veto；延長可能是在擁擠方向繼續逆行。 |
| 2 | `s×(premium_change_now−premium_change_prev) ≥0`（±0.5bp） | premium 變化的**加速度**，非上次已結算 funding 斜率或進場時 level/change；與輪1相關。 |
| 3 | premium 當根 high−low／前24根完整 premium range 中位 `≤0.8`（0.65／0.95） | premium 市場內部分歧收斂，非 ETH 價格波動收縮；無交易量因果含義。 |
| 4 | `s×log(top_account_count_ratio_now / prior_1h) ≥0`（±0.01） | 大戶**帳戶個數**方向變動，舊輪用大戶部位名目比率；可能與全市場帳戶高度相關。 |
| 5 | `s×Δlog(top_position_ratio / top_account_count_ratio) ≥0`（±0.01） | 同群體**倉位大小相對帳戶數**差異，不是單獨 top position 趨勢；受帳戶比率口徑限制。 |
| 6 | `s×Δlog(top_account_count_ratio / global_account_ratio) ≥0`（±0.01） | 大戶帳戶相對整體帳戶集中度；與輪4／5同資料家族。 |
| 7 | `s×(2ΣT/ΣV−1) ≥0.04`（0／0.08）；T/V 取入場後整段5m taker_buy/volume | 整筆持倉的主動成交流，非前批只看最後一小時；整段流可能被早期成交主導。 |
| 8 | `s×(imbalance_after_MAE−imbalance_before_MAE) ≥0.05`（0／0.10），MAE 為當下已知整段5m不利極值；前後各至少6根 | **不利極值後**的流向恢復，非單小時前後量；極值自身可能是噪音。 |
| 9 | 在方向性5m收盤變動不利的子棒，`s×(2ΣT_adverse/ΣV_adverse−1) ≥0.02`（0／0.04）；至少6根不利子棒 | 逆向價格下的主動流吸收，不以全段買賣總量或未來反彈定義；與輪7／8相關。 |
| 10 | 以決策前最近三個等間隔已結算 funding 時刻推估下一時刻，距 D `>2h`（>1h／>3h） | 僅測可預估的**結算時間風險**，非未來實際 funding 或最近費率正負；間隔更動時預測會錯，須標 `DATA_LIMITED`。 |

每輪輸出完整策略 PnL、WR、PF、期望值、筆數、MH 數／淨損、直接事件勝敗、慢熱贏家、替代單、持倉時數、funding、MDD、最差30日、L/S及regime拆分、三成本、晚期、鄰域及前綴。第一批若無合格方法，再依真正未測機制評估第二批；不能以閾值重命名湊輪。最多三批。
