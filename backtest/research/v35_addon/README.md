# V35 外掛（sleeve）研究腳本（2026-10-08）

研究結論與數字見 [doc/v35_research.md](../../../doc/v35_research.md)。全部 REJECTED，不影響實盤。

所有腳本的第一個參數是資料夾 `WORK`（放 K 線、funding 與中間結果）；`data/` 被 gitignore，需先下載：

```bash
python backtest/research/v35_addon/fetch_data.py data/v35
python backtest/research/v35_addon/base.py data/v35          # 現行策略 5.5 年基準 → base_trades.pkl（其他腳本需要）
```

| 腳本 | 內容 |
|---|---|
| `sleeve.py` / `chk.py` / `chk2.py` / `chk3.py` | 初探：通道趨勢外掛 144×2 組、鄰域與近 2 年篩選 |
| `gate.py` | 120 根突破做多外掛 10-Gate（近 2 年 IS/OOS 各一年）→ 3P/1C/6F |
| `strict.py` + `stage1.py` + `stage2.py` | 第 1 輪嚴格模式：通道突破／波動擴張／日線動能 L/S |
| `pair.py` + `p_stage1.py` + `p_stage2.py` | 第 2 輪：ETH/BTC 配對 |
| `htf.py` + `h_stage1.py` + `h_stage2.py` | 第 3 輪：4h／日線 GK 壓縮突破（`h_stage2.py WORK 98.3 h_sel.json`） |
| `fund.py` + `f_stage1.py` | 第 4 輪：資金費率極端反向 |
| `m30.py` + `m_stage1.py` | 第 5 輪：30m GK 壓縮突破 |
| `gen.py` + `rounds.py` | 第 6～10 輪通用引擎與 10-Gate（`rounds.py WORK 6..10`） |

嚴格模式共用設定：收盤訊號→次根開盤成交 ±2bp、停損 25% 穿透、每筆 $9＋funding、200U×20x；開發期 2021-02-16～2024-10-07 選平台中心，保留期 2024-10-08～2026-10-08 只測一次；G5 隨機對照門檻第 k 輪 ≥ 100−5/k 百分位。
