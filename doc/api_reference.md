# Binance API 參考

## 使用的端點

| 端點 | 模組 | 用途 |
|------|------|------|
| `GET /fapi/v1/exchangeInfo` | binance_trade | tick_size/step_size/min_qty |
| `GET /fapi/v1/account` | binance_trade | USDT 可用餘額 |
| `GET /fapi/v1/positionRisk` | binance_trade | 持倉查詢 |
| `POST /fapi/v1/changeInitialLeverage` | binance_trade | 設定槓桿 |
| `POST /fapi/v1/order` | binance_trade | 市價單 |
| `DELETE /fapi/v1/allOpenOrders` | binance_trade | 取消掛單 |
| `POST /fapi/v1/algoOrder` | binance_trade | 條件單（安全網SL） |
| `GET /fapi/v1/openAlgoOrders` | binance_trade | 查Algo掛單 |
| `DELETE /fapi/v1/algoOrder` | binance_trade | 取消Algo單 |
| `GET /fapi/v1/markPrice` | binance_trade | 即時mark price |
| `GET /fapi/v1/time` | binance_trade | 伺服器時間 |
| `GET /fapi/v1/klines` | data_fetcher | Futures K線 |

## Algo Order API
- 2025/12 起 SL/TP 遷移至 Algo Order
- 參數：`algoType=CONDITIONAL` + `triggerPrice`
- 舊 `/fapi/v1/order` 對 STOP_MARKET 回傳 `-4120`

## closePosition 行為
- 安全網SL用 `closePosition=true`（關閉該方向所有倉位）
- TP1/TimeStop用 `qty=N, reduce_only=True`（只平特定數量）

## Session 管理
- 每30分鐘自動重建（`_SESSION_MAX_AGE=1800`）
- API錯誤時觸發重建
- 時間同步：`sync_time()` 修正本地時鐘偏移
