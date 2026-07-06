# V30 Research: Funding Rate Overlay（最後一個未測的免費數據源）

> V30 goal: 在 V14+R+V25-D 之上測試 funding rate（資金費率）作為 per-side 進場 overlay。
> 動機：V19 當年想用的 OI / 多空比只有 30 天歷史而放棄，但 **funding rate 的完整歷史可免費取得**
> （`/fapi/v1/fundingRate`，每 8h 一筆），且從未在 V14 框架上測過——
> 早期否決的是「資金費率**套利**」（獨立策略，BTC 時代），不是「funding 條件下的 V14 交易品質」。
>
> **最終結論：REJECTED（R1 診斷階段即關閉，未進入配置掃描）。**
> **進場當下的 funding（水位/均值/percentile）完全無法區分 V14 交易好壞——**
> **30 個分桶 PnL 全為正（block 任何一桶都虧），最壞桶 permutation p=0.30~0.94，**
> **最佳桶經多重比較校正 p=0.159 / WR p=0.476，且 IS/OOS 不一致（IS n=7 WR 57% vs OOS 90%）。**

---

## 研究設計

### 數據（R0, `v30_r0_download_funding.py`）

| 項目 | 值 |
|------|-----|
| 端點 | `/fapi/v1/fundingRate`（公開，免 key） |
| 筆數 | 2,371 筆（每 8h 結算：00/08/16 UTC） |
| 範圍 | 2024-05-03 ~ 2026-07-02（K 線窗口前多抓 60 天暖機） |
| 輸出 | `data/ETHUSDT_funding.csv` |

對齊：嚴格用「bar 收盤時刻（UTC+8 open + 1h，轉 UTC）之前已結算」的 funding，無前瞻。
（實作雷：pandas 讀出的 datetime 是 `datetime64[us]`，`astype('int64')//10**6` 會差 1000 倍，
需明確 `astype('datetime64[ms]')`。）

### 特徵（進場時已知）

| 特徵 | 定義 | 假說 |
|------|------|------|
| `fr_last` | 最近一次結算費率 | 正=多頭擁擠（L 逆風？）、負=空頭擁擠（S 逆風？） |
| `fr_ma21` | 近 7 天（21 次結算）平均 | 平滑版擁擠度 |
| `fr_pct` | `fr_last` 近 90 天 rolling percentile | 相對水位（去趨勢） |

### 基準

V14+R+V25-D（`v25_engine.py`，R gate + regime-conditional exits，研究基準 200U/$4,000/fee $4）：
全期間 n=263、PnL $7,205、WR 62.0%、MDD $334。

---

## R1: 分桶診斷（`v30_r1_funding_diag.py`）

每側 × 每特徵五分位分桶（L n=127 / S n=136）：

### L side

| 特徵 | 分桶結果 | 最壞桶 perm p |
|------|----------|---------------|
| fr_last | Q1(負funding) avg $54.7 WR 80.8% ← 最亮；其餘 Q2~Q5 avg $5.7~$55.9 全正 | 0.300 |
| fr_ma21 | avg $12.9~$39.7 全正，無單調性 | 0.718 |
| fr_pct | avg $19.0~$35.4 全正，無單調性 | 0.944 |

### S side

| 特徵 | 分桶結果 | 最壞桶 perm p |
|------|----------|---------------|
| fr_last | avg $13.6~$60.0 全正；「空頭擁擠時 S 差」假說不成立（Q1 avg $13.6 仍正） | 0.340 |
| fr_ma21 | avg $14.2~$45.6 全正，U 型無意義 | 0.375 |
| fr_pct | avg $20.0~$45.5 全正；高 funding（多頭擁擠）S 較好方向性存在但不顯著 | 0.687 |

**關鍵事實：30 桶（2 側 × 3 特徵 × 5 分位）PnL 全部為正。**
→ 任何 funding block-gate 數學上必然移除正收益交易（與 V15/V27 同一教訓：
被過濾掉的永遠是賺錢的）。**不存在可以 block 的桶，配置掃描沒有意義。**

---

## R2: 最佳桶顯著性（`v30_r2_best_bucket_check.py`）

R1 唯一亮點是 L × fr_last Q1（負 funding）WR 80.8% avg $54.7。但 30 桶挑最亮的一桶本來就會亮：

| 檢驗 | 結果 |
|------|------|
| Permutation「30 桶最佳 avg」 | 實際 $60.0，**p=0.159** — 不顯著 |
| Permutation「30 桶最佳 WR」 | 實際 80.8%，**p=0.476** — 純運氣水準 |
| L fr_last<0 IS/OOS | IS n=**7** avg $22 WR 57.1% / OOS n=20 avg $70 WR **90.0%** — 效果幾乎全在後半段，前半段不存在 |
| 對照 L fr_last>=0 | IS avg $15.6 WR 54.2% / OOS avg $15.8 WR 51.9% — 穩定 |

負 funding 時段（ETH 2 年中僅 ~20% 時間）L 交易本來就稀少（n=27），
且即使效果為真也是「boost」信號——加碼方向已被 V23 Path V 否決（binary edge，
soft scaling 全劣化），選擇性加碼等同變相過濾其餘交易。

---

## V30 結論

1. **Funding rate overlay REJECTED** — 診斷階段即關閉，不進入配置掃描/10-Gate。
2. 這是**最後一個「歷史夠長 + 免費」的未測外部數據源**（V19 已排除宏觀/FGI/HMM，
   OI/LSR/Taker 只有 30 天）。至此：OHLCV（V17/V18）、跨市場（V19）、
   其他標的（V20/V24C）、古典 TA（V22）、ML（V27）、funding（V30）全數清空。
3. **V14+R+V25-D 在可取得數據下已是全域最佳**，不是局部最佳。
4. 「獲利更高」剩下的可行槓桿不在策略層而在**帳戶層**：
   V28 人肉複利 SOP（PROMOTED 未部署，等實盤滿 50 筆貼合回測後啟用）＋
   V29 健康度監控 edge 存續。「勝率更高」方向 V25-D 已取走可取的
   （V26/V27 證明再推 WR 必以 PnL 為代價）。

### 加入「不要做的事」

- Funding rate 進場過濾/overlay on V14（V30：30 桶 PnL 全正、最壞桶 p>0.3、
  最佳桶多重比較 p=0.159、IS/OOS 不一致——block 任何 funding 桶都在移除正收益交易）
- 負 funding 時 L 加碼（V30 R2：IS n=7 不存在效果，OOS n=20 WR 90% 是後半段小樣本；
  且 sizing 方向已被 V23 Path V 否決）

---

## 腳本

| 腳本 | 內容 |
|------|------|
| `backtest/research/v30_r0_download_funding.py` | 抓 funding 完整歷史 → data/ETHUSDT_funding.csv |
| `backtest/research/v30_r1_funding_diag.py` | 基準交易 × funding 特徵五分位分桶 + 最壞桶 permutation |
| `backtest/research/v30_r2_best_bucket_check.py` | 最佳桶多重比較 permutation + IS/OOS 一致性 |
