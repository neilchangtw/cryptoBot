# v5 策略詳細規格

## 目前運行版本：v5（Testnet）

### 進場條件（7 個全滿足）

```
做多：
  1. 5m RSI(14) < 30
  2. 5m close < BB Lower(20,2)
  3. ATR 百分位 <= 75
  4. |price vs EMA21| < 2%
  5. 1h RSI(上一根已收盤) >= 前一根
  6. ATR(14) <= ATR MA50（波動沒在放大）
  7. BB 寬度百分位 < 50（盤整環境）

做空：鏡像（RSI>70, close>BB Upper, 1h RSI<=前一根）
```

### 出場

```
安全網 SL：±3%（STOP_MARKET，只防爆倉）
TP1：1.5x ATR → 全平 100%（程式監控，市價單）
時間止損：8h（96根5m bar）→ 全平
無 Phase 2 Trail
```

### 風控

```
每單保證金：100 USDT
槓桿：20x（名義 $2,000）
Max 同方向：2
手續費：~0.04%
```

### 技術指標計算

```python
RSI(14): ewm(alpha=1/14)
BB(20,2): rolling(20).mean() ± 2 × rolling(20).std()
ATR(14): max(H-L, |H-C[-1]|, |L-C[-1]|).rolling(14).mean()
ATR百分位: ATR在rolling(100)中的percentile
ATR MA50: ATR.rolling(50).mean()
BB寬度百分位: BB_width在rolling(100)中的percentile
EMA21: close.ewm(span=21).mean()
所有指標用 iloc[-2]（已收盤 bar）
1h 指標延遲一根（hour_key = floor("h") + 1h）
```

### 持倉管理

```
Runner（每5分鐘）：信號偵測 + 開倉
Monitor（每5分鐘）：TP1/TimeStop/SafeNet 偵測 + 平倉
持倉計數：用 journal get_open_trades()，不用 Binance API
State：monitor_state.json（key = symbol_side_entryPrice）
```

## v5.2 回測最佳參數（待部署）

與 v5 差異：TP1 改 1.25x ATR（目前是 1.5x）

1年績效：133筆 +$229, PF2.24, 滾動+$264(10/10)
