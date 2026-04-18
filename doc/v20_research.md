# V20 研究：多標的 V14 框架測試（2026/04/16）

> V20 目標：測試 V14 GK 壓縮突破框架在其他加密貨幣上的可行性。
> 核心規則：V14 參數完全鎖定（LOCKED），不允許重新優化。

---

## 背景

V6-V19 共 26,000+ 配置證明了：
1. ETH 1h 的唯一 alpha = 15-bar close breakout
2. 非 breakout bars 是 random walk（MFE 對稱性證明）
3. 所有可取得的免費數據源（宏觀/情緒/HMM）無預測力

V20 的核心問題：V14 框架在其他加密貨幣上是否有效？如果有，可以多標的分散部署。

---

## R0: 候選標的盤點與數據下載

### 候選標的（10 個）

選擇標準：Binance Futures 主流幣、730 天數據可取得、流動性充足。

| Symbol | 市值排名 | 特性 |
|--------|----------|------|
| SOLUSDT | Top 5 | 高 beta、Solana 生態 |
| BNBUSDT | Top 4 | 低波動、交易所幣 |
| XRPUSDT | Top 4 | 高爆發性、法規敏感 |
| DOGEUSDT | Top 10 | Meme 幣、高波動 |
| ADAUSDT | Top 10 | 低波動、Cardano 生態 |
| AVAXUSDT | Top 15 | DeFi、高 beta |
| LINKUSDT | Top 15 | Oracle、與 DeFi 相關 |
| MATICUSDT | Top 20 | L2、已遷移至 POL |
| LTCUSDT | Top 20 | 老牌幣、低波動 |
| BCHUSDT | Top 20 | BTC 分叉、低波動 |

### 數據下載結果 (`v20_r0_download_multi.py`)

| Symbol | Bars | 天數 | 備註 |
|--------|------|------|------|
| SOLUSDT | 17,520 | 730 | ✓ |
| BNBUSDT | 17,520 | 730 | ✓ |
| XRPUSDT | 17,520 | 730 | ✓ |
| DOGEUSDT | 17,520 | 730 | ✓ |
| ADAUSDT | 17,520 | 730 | ✓ |
| AVAXUSDT | 17,520 | 730 | ✓ |
| LINKUSDT | 17,520 | 730 | ✓ |
| MATICUSDT | 3,562 | 148 | 不足（2024/09 遷移 POL） |
| LTCUSDT | 17,520 | 730 | ✓ |
| BCHUSDT | 17,520 | 730 | ✓ |

MATICUSDT 因數據不足（<1000 bars）自動跳過。最終篩選 9 個標的。

---

## R0: V14 Locked-Parameter Screening (`v20_r0_screening.py`)

### V14 鎖定參數

```
L: GK(5/20) pctile<25, breakout 15, block {0,1,2,12}h+{Sat,Sun}
   SafeNet 3.5%, TP 3.5%, MFE trail(1.0%/0.8%), Cond MH(1.0%→5), MH6+ext2+BE
   Cooldown 6, Monthly cap 20, Circuit: daily-$200/month-$75/consec4→24bar

S: GK(10/30) pctile<35, breakout 15, block {0,1,2,12}h+{Mon,Sat,Sun}
   SafeNet 4.0%, TP 2.0%, MH10+ext2+BE
   Cooldown 8, Monthly cap 20, Circuit: daily-$200/month-$150/consec4→24bar

Account: $1,000 / $200 margin / 20x / $4,000 notional / $4 fee
```

### 篩選標準

必須全部通過：
1. **IS > 0**：前 50% 數據正收益
2. **OOS > 0**：後 50% 數據正收益
3. **Fee% < 40**：手續費佔比合理
4. **WF6 >= 3**：6-fold Walk-Forward 至少 3 折正收益

### 篩選結果

```
Symbol       Bars   avg$  Fee%  BRK%   IS_PnL  OOS_PnL OOS_WR    OOS_L    OOS_S  +Mon   Worst    MDD  WF6  Grade
----------------------------------------------------------------------------------------------------------------------------------
ETHUSDT     17520    19   21% 24.3%   +1913   +4180  60.1%   +2067   +2113 11/13   -225   480   5/6    A *BASE
SOLUSDT     17520    24   16% 26.1%    -857     +38  50.7%     +73     -35  6/13   -516  1128   1/6    F
BNBUSDT     17520    16   26% 25.4%    -245   -1037  38.9%    -164    -874  6/13   -394  1830   3/6    F
XRPUSDT     17520    22   18% 24.1%   -1377    +107  51.6%     -92    +199  9/13   -385   956   1/6    F
DOGEUSDT    17520    26   15% 25.3%    -798     +74  51.5%    -599    +673  7/12   -437  1239   3/6    C
ADAUSDT     17520    26   15% 25.7%   -1661    -414  48.3%    -673    +259  5/12   -427  1769   2/6    F
AVAXUSDT    17520    26   15% 26.7%   -1980   +1045  51.1%    +228    +817  6/13   -362  1002   2/6    F
LINKUSDT    17520    26   15% 26.8%   -1010   +1751  59.3%    +339   +1413  9/13   -304   490   4/6    C
LTCUSDT     17520    22   18% 25.5%   -1221     -80  46.7%     +56    -136  6/13   -322  1627   2/6    F
BCHUSDT     17520    22   18% 25.2%    -295    -445  45.4%    +276    -721  6/13   -416  1211   2/6    F
```

### 篩選判定

| Symbol | IS>0 | OOS>0 | Fee<40% | WF>=3 | 結果 |
|--------|------|-------|---------|-------|------|
| SOLUSDT | ✗ | ✓ | ✓ | ✗ | **FAIL** |
| BNBUSDT | ✗ | ✗ | ✓ | ✓ | **FAIL** |
| XRPUSDT | ✗ | ✓ | ✓ | ✗ | **FAIL** |
| DOGEUSDT | ✗ | ✓ | ✓ | ✓ | **FAIL** |
| ADAUSDT | ✗ | ✗ | ✓ | ✗ | **FAIL** |
| AVAXUSDT | ✗ | ✓ | ✓ | ✗ | **FAIL** |
| LINKUSDT | ✗ | ✓ | ✓ | ✓ | **FAIL** |
| LTCUSDT | ✗ | ✗ | ✓ | ✗ | **FAIL** |
| BCHUSDT | ✗ | ✗ | ✓ | ✗ | **FAIL** |

**9/9 全部 FAIL。** Universal failure point: **IS < 0**。

---

## 分析

### 1. IS < 0 是 Universal Failure

所有 9 個標的的 IS PnL 為負。這代表 V14 參數（GK 5/20 pctile<25、breakout 15）在前 365 天完全無法在其他幣種上獲利。

### 2. OOS 正收益是 Regime 運氣

LINK OOS +$1,751（IS -$1,010）和 AVAX OOS +$1,045（IS -$1,980）看似有潛力，但：
- IS 為負 → OOS 正收益只是特定市場環境（regime）的運氣
- 如果 IS 也正，才代表 alpha 穩定存在
- IS 負 + OOS 正 = 經典的 regime-dependent artifact

### 3. Fee% 不是瓶頸

| Symbol | Fee% | avg|move| |
|--------|------|----------|
| ETHUSDT | 21% | $19 |
| SOLUSDT | 16% | $24 |
| LINKUSDT | 15% | $26 |
| DOGEUSDT | 15% | $26 |

大部分 altcoins 的 avg|move| 比 ETH 高（$24-26 vs $19），fee% 更低（15-18% vs 21%）。問題不是手續費太高，而是 breakout 後的方向預測力不存在。

### 4. Breakout 頻率相似但質量不同

所有標的的 breakout 頻率（BRK%）在 24-27% 之間，與 ETH（24.3%）相似。但 ETH 的 breakout 有方向性 alpha（WR 60%），其他幣的 breakout WR ≈ 50%（隨機）。

這說明 GK 壓縮突破的 alpha 不是來自「壓縮後突破」這個力學結構（所有幣都有），而是來自 **ETH 特有的微觀結構**：
- ETH 的 breakout 後續方向持續性（momentum）比其他幣強
- 可能與 ETH 的市場參與者結構、流動性分布、或與 BTC 的聯動有關
- V14 的出場參數（TP/MH/MFE trail）也是針對 ETH 的分布校準的

---

## 最終結論

**V14 GK 壓縮突破框架是 ETH-specific 的。**

- 9 個主流加密貨幣全部未通過 R0 篩選
- Universal failure: IS < 0（前 365 天全虧）
- 不進入 R1 deep testing（按 V20 prompt 規則：ALL FAIL → V14 on ETH = globally optimal）
- 多標的分散部署不可行

**V14 on ETH 是在所有可取得免費數據和所有可測試市場下的 globally optimal solution。**

---

## 研究腳本

| 腳本 | 說明 |
|------|------|
| `v20_r0_download_multi.py` | 下載 10 個標的 730 天 1h K 線 |
| `v20_r0_screening.py` | V14 locked-parameter 篩選（9 標的 + ETH baseline） |
