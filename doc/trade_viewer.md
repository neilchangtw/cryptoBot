# ETH 交易對照檢視器

## 啟動

Windows 直接雙擊專案根目錄的 `trade_viewer.bat`。

也可以在 PowerShell 執行：

```powershell
.\.venv\Scripts\python.exe trade_viewer.py
```

瀏覽器會開啟 `http://127.0.0.1:8765`。保留終端機視窗；結束時按 `Ctrl+C`。

## 資料來源

程式依下列順序自動尋找：

- 正式盤：`data_live/trades.csv`，若不存在則讀取 `Downloads/實戰ALL.txt`。
- 回測：`Downloads/回測ALL.txt`。
- 模擬盤：`data/trades.csv`。
- K 線：`data/ETHUSDT_1h_latest730d.csv`。

CSV 的進出場時間會依現行 `analysis_report.py` 規則加一小時，顯示實際成交時刻；文字報表已是成交時刻，不會再加。

## 功能

- 正式盤、回測、模擬盤切換。
- 日期與 L/S 方向篩選。
- ETHUSDT 1h 收盤價折線。
- L/S 進場、獲利／虧損出場標記。
- 點圖上標記或交易表格列，雙向定位同一筆交易。
- 顯示區間交易數、勝率、PnL 與 K 線資料點。

本工具只讀本機檔案，不載入 `.env`、不呼叫 Binance API，也不會下單或修改正式盤資料。
