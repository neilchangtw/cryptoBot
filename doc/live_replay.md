# 實戰重播模式

`run_backtest.py --live-replay` 是歷史實戰風控稽核模式，不會改變預設的
K 棒純回測，也不會連線 Binance 或下單。

## 用法

```powershell
python -B -X utf8 run_backtest.py `
  --live-replay "C:\path\實戰ALL.txt" `
  --compare-backtest "C:\path\回測ALL.txt" `
  -t
```

輸入可為：

- 實戰交易列表 TXT（例如 `實戰ALL.txt`）；
- `trades.csv`，支援 recorder 的 `entry_time_utc8` / `exit_time_utc8`、`sub_strategy`、`net_pnl_usd` 欄位。

## 模式語意

模式會使用已發生的實戰進出場時間與淨損益，依時間重建：

- 日虧、L/S 分方向月虧；
- L/S 月進場數；
- 連虧 4 筆後 24 小時冷卻；
- 每筆實戰進場當下的風控判定。

搭配 `--compare-backtest` 時，明細以「方向＋實際進場時間」配對，不使用交易編號。
回測多出的交易會再用實戰重播狀態判斷是月虧、日虧或連虧冷卻阻擋，早於實戰檔案起始時間的回測交易只列入數量、不做逐筆判定。

## 限制

實戰重播是歷史稽核，不是未來績效預測。它能回答「實戰當時為什麼不能開單」，
不能用已發生的成交結果替代未知期間的純回測。要分析未來或未覆蓋期間，仍使用
預設的 `run_backtest.py`；要做逐小時的信號阻擋原因，需另外提供
`bar_snapshots.csv` 或 signal log，因交易明細本身只記錄已成交交易。
