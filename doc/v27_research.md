# V27 Research — Meta-labeling 進場過濾（預測 MH 結局）

**研究日期**：2026-07-02
**基底**：V14+R + V25-D（線上實盤版；本輪資料 2024-07-02 ~ 2026-07-02，17,528 根，
realistic 成交 baseline：273 筆 / PnL $7,809 / MH 76 筆 -$3,557 —— 與 VPS `run_backtest.py` 完全一致）
**觸發**：V26 三面封死「持倉中」砍 MH 後，唯一方法論上未試過的類別 ——
**進場當下**用多特徵分類器預測「這筆會不會以 MH 虧損收場」（López de Prado meta-labeling），
預測到就不進場。
**結果**：**REJECTED（R1 提前終止）** —— 進場時特徵對 MH 結局無預測力
（WF AUC 最佳 0.614 但 perm p=0.130；OOS 單次確認 AUC 0.44~0.58；
靜態過濾移除的全是正 PnL）。MH 方向就此徹底關閉。

---

## 為什麼 V26 之後還值得試這一輪

V26 證明「持倉到第 N 根時贏家輸家尚未分化」→ 持倉中的任何選擇性動作必然兩邊都砍。
但這不自動推出「進場當下也無法分辨」：進場時的市場狀態（壓縮深度、突破力度、動能、
量能結構、BTC 同步性…）是另一組資訊。歷史上唯一真正縮小過 MH 桶的 V23 Path R
也正是進場端 gate。Meta-labeling = 把單變數進場過濾（V15 已敗）升級為多特徵分類器，
是文件中唯一沒出現過的方法類別。

---

## 方法

### 引擎（v27_engine.py）

不複製引擎（V24/V25 複製法會漂移），改為**讀線上引擎 `v14_export_trades.py` 原始碼、
文字 patch 注入 `veto(i, side)` hook 後 exec**。Parity assert：veto=None 時與原引擎
**逐筆完全一致**（realistic / ideal 兩模式都驗證）。

### 特徵（23 個，全部進場 bar 收盤當下已知，無前瞻）

| 類別 | 特徵 |
|---|---|
| 信號自身 | gk_pctile（該側）、brk_margin（突破幅度）、compress_len（壓縮持續 bar 數） |
| Regime | slope（SMA200 100-bar 斜率，shift(1) 同引擎）、dist_sma50/200 |
| 動能 | ret4 / ret24 / ret72、streak（連續同向收盤）、rpos48 |
| 波動 | atr14_pct、rv24 |
| 量能 | vol_ratio20、taker_imb（單 bar）、taker_imb6 |
| K 棒形態 | body_ratio、upper_wick、lower_wick |
| 時間 | hour、dow |
| 跨市場 | btc_ret24、ethbtc_spread24 |

標籤兩種：`y_mh`（exit_reason==MH）、`y_loss`（pnl<0）。

### 檢定設計（預先登記判準，防事後挑選）

- **IS = 前 50% bars 的交易（123 筆：L 63 / S 60），OOS 完全不碰**
- R1a：univariate rank-AUC + **walk-forward CV**（IS 內 expanding window、24-bar embargo、
  logistic C=0.5 / GBM depth-2）取 pooled out-of-fold AUC
- R1a 附 **permutation test**：標籤洗牌 200 次重跑同一 CV → null AUC 分佈 → p-value
- **判準：任一 (side, label) WF AUC ≥ 0.60 且 perm p < 0.05 才進 R2（動態注入回測 + gates）**
- R1b（關門確認）：模型在 IS 全量定案 → 對 OOS **單次**預測（無挑選偏誤），
  看 OOS AUC 與靜態過濾的經濟效果

---

## R1a 結果：無信號

**Walk-forward CV AUC（IS 內，out-of-fold）：**

| side | label | logit | gbm |
|---|---|---:|---:|
| L | y_mh | **0.614** | 0.536 |
| L | y_loss | 0.565 | 0.392 |
| S | y_mh | 0.451 | 0.482 |
| S | y_loss | 0.451 | 0.578 |
| L+S 合併 | y_mh | 0.508 | 0.475 |
| L+S 合併 | y_loss | 0.489 | 0.537 |

**Permutation test（對最佳組合 L/y_mh/logit 0.614）**：
null AUC mean 0.477、**P95 0.652**、max 0.797（200 次洗牌）→ **p = 0.130**。
在 oof n=38 的樣本量下，0.614 完全落在洗牌就能得到的範圍內。

Univariate 有幾個 |AUC-0.5| 較大的（L: gk_pctile 0.248 = MH 單壓縮更深；
S: ret24 0.636 / gk_pctile_S 0.610 / dist_sma50 0.618），但**全部無法在 walk-forward
下存活** —— 與 V15 的教訓一致：IS 上看得到的單變數分離是事後選擇。

---

## R1b 結果：OOS 關門確認

Train on IS 全量（meta-labeling 能拿到的最好情境），單次預測 OOS：

| side | label | logit OOS AUC | gbm OOS AUC |
|---|---|---:|---:|
| L（OOS 73 筆，MH 18） | y_mh | 0.477 | 0.534 |
| L | y_loss | 0.459 | 0.482 |
| S（OOS 77 筆，MH 25） | y_mh | 0.576 | 0.497 |
| S | y_loss | 0.512 | 0.440 |

**靜態過濾經濟測試**（移除 OOS 中 P(MH) 最高的 K%，ceiling 示意，未重跑序列）：

| side | 過濾 | 移除筆數 | 其中真 MH | 被移除 PnL | OOS PnL 變化 |
|---|---|---:|---:|---:|---|
| L | top 20% | 15 | 3 | **+$306** | $2,430 → $2,124 |
| L | top 30% | 22 | 7 | **+$478** | $2,430 → $1,952 |
| S | top 20% | 16 | 4 | **+$472** | $2,301 → $1,829 |
| S | top 30% | 23 | 7 | **+$702** | $2,301 → $1,600 |

**每個配置移除掉的桶都是正 PnL** —— 分類器標記為「最像 MH」的進場，實際上多數是贏家。
連 oracle 方向都不對，動態注入（R2）與 gates（R3）無需進行。

---

## 結論：REJECTED，MH 方向徹底關閉

1. **進場時特徵對 MH 結局無預測力**：WF AUC 全部 ≤ 0.614 且不顯著（p=0.13），
   OOS 確認 0.44~0.58 ≈ 擲硬幣。
2. **與 V26 合併後的完整圖像**：
   - 進場當下 → 分不出誰會變 MH（V27）
   - 持倉到第 N 根 → 贏家輸家仍未分化（V26 R1/R2/R3）
   - 出場後 → MH 桶恆 ≤0 是選擇效應（正報酬單已被送進延長/TP）
   → **在 OHLCV + 量能 + BTC 可取得資訊下，MH 桶是不可壓縮的入場費**。
3. 樣本結構也不支持 ML：IS 只有 33 個 MH 正樣本 / 23 特徵，任何更複雜的模型
   只會過擬合得更漂亮、死得更難看（GBM 多數組合 AUC < logit 即為證）。
4. 若嫌 MH 絕對金額痛，唯一正確的旋鈕是 **V24 Direction B 的槓桿線性調整**
   （降槓桿等比縮小所有桶），不是動策略。

---

## 不要做的事（追加）

- **Meta-labeling / ML 分類器進場過濾**（V27：23 特徵 × logit/GBM × y_mh/y_loss，
  WF AUC 最佳 0.614 perm p=0.130，OOS 單次確認 0.44~0.58，靜態過濾移除的全是正 PnL
  +$306~+$702 —— 進場時特徵分不出誰會變 MH，被標記的反而多是贏家）
- 任何「預測單筆交易結局」的進場端 ML（同上根因：273 筆 / IS 33 個 MH 正樣本，
  訊號不存在且樣本量不支持）

---

## 研究腳本

| 腳本 | 內容 |
|---|---|
| `backtest/research/v27_engine.py` | 引擎載入器（文字 patch 注入 veto hook + parity assert）+ 特徵建構 |
| `backtest/research/v27_r1_predictability.py` | R1a：univariate + WF-CV AUC + permutation test |
| `backtest/research/v27_r1b_oos_confirm.py` | R1b：IS 全量訓練 → OOS 單次確認 + 靜態過濾經濟測試 |
