"""Generate the compact deliverable from saved numerical results."""
import json
from pathlib import Path
from datetime import datetime, timezone
import strategy_study_20260911 as study


def main():
    result=json.loads((study.DOC/'results.json').read_text(encoding='utf-8'))
    audit=json.loads((study.DOC/'independent_audit.json').read_text(encoding='utf-8'))
    rows={r['slip']:r['full'] for r in result['runs'] if r['name']=='base'}
    b=rows[0]
    c=result['counts']
    lines=[
        '# 2026-09-11 策略研究：資料與事件門檻結案', '',
        '**NO PROMOTION：保留 V14+R+V25-D、L15/S15。** 本輪兩個新方向都在候選損益計算前停止；收益效果尚未測定，沒有足夠證據支持修改策略。全程單代理，未做參數掃描。', '',
        '## 基準／候選比較', '',
        '固定每筆200U、20倍、4,000U名目，最多L/S各一倉。淨收益含原每筆$4成本及歷史funding；MDD為含未實現損益的1h mark美元回撤，最差30日為完整720h窗口。虧損總額以正數呈現。', '',
        '|規則|交易數|受影響事件群 全期／後期|淨收益$|淨勝率|PF|mark MDD$|最差30日$|虧損總額$|額外2／5bp淨收益$|判定|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|',
        f'|原策略|{b["n"]}|—|{b["net_pnl"]:,.2f}|{b["wr"]:.2f}%|{b["pf"]:.2f}|{b["mdd"]:.2f}|{b["worst30"]:.2f}|{b["loss_total"]:,.2f}|{rows[2]["net_pnl"]:,.2f}／{rows[5]["net_pnl"]:,.2f}|保留|',
        f'|A 持倉分布分歧|未回測|{c["A"]["clusters24h"]}／{c["A"]["late_clusters24h"]}（暫算）|—|—|—|—|—|—|未執行|資料受限，另有後期樣本不足|',
        f'|B OI收縮＋浮虧，bar2離場|未回測|{c["B"]["clusters24h"]}／{c["B"]["late_clusters24h"]}|—|—|—|—|—|—|未執行|樣本不足|', '',
        '候選欄位「—」表示未計算，不代表零收益。原策略沒有新增規則的受影響樣本；269筆總交易不能充作候選的獨立證據。事件間隔≤24h歸同群，只是保守近似。', '',
        '## 方向、證據與停止原因', '',
        'A：在D−15min的資料，以前60min大戶持倉long/short比率變化朝交易反向、全市場帳戶比率變化朝交易同向，作進場拒絕條件。使用比率的變化，不比較兩群體水準。與9/10的總OI或溢價規則不同。', '',
        f'暫以archive欄位計算，原交易{c["A"]["events"]}筆觸發，歸為{c["A"]["clusters24h"]}群；2026起只有{c["A"]["late_clusters24h"]}群。匿名小量API核對遇到ProxyError，立即停止且未重試；實際新增下載0 bytes。因此archive與即時欄位對應沒有通過核實，列為資料受限。即使日後對應核實，這份凍結樣本的後期數量仍未達15群。', '',
        f'[Binance官方USD-M文件]({study.SOURCE_URL}#top-trader-longshort-position-ratio-market_data)說明大戶持倉比例及期末timestamp，目前top-trader端點標示需API key；本輪沒有使用帳戶金鑰。封存數值缺少歷史接收與修訂證據，不能直接宣稱當年實盤可用。', '',
        'B：原進場後第2根收盤，先執行既有SafeNet／TP／MFE等出場；仍持倉且方向價格報酬<0、OI較進場決策時下降，才考慮市價離場。OI比較時點均留15min緩衝，區間每5min點須有效。這項新增資訊是持倉期間的存量變化，與V26純價格／running-MFE give-up及9/10進場OI過濾不同。OI下降不能識別清算或哪一方平倉。', '',
        f'原策略共有{c["quality"]["original_eligible_bar2"]}次可做bar2檢查，符合雙條件{c["B"]["events"]}筆，歸為{c["B"]["clusters24h"]}群；後期{c["B"]["late_clusters24h"]}群，低於事先要求15群。因此依預登記停止，不放寬門檻、不把bar1/bar3改成新主候選。此結果只表示樣本不足，不能推論此規則賺或賠。', '',
        '第三方向為省費／被動成交，9/9已做成本敏感度且仍缺可靠實際排隊成交資料，本輪未選，不另外湊第三個測試。', '',
        '## 資料、重現與檢查', '',
        f'- 價格：台北開盤{result["data"]["start_open_taipei"]}～{result["data"]["end_open_taipei"]}，{result["data"]["bars"]:,}根；最後收盤{result["data"]["last_close_taipei"]}。前310根暖機。本輪沒有延長到9/11，因此沒有9/8截止後的驗證。',
        '- 原版引擎与研究no-op：0／2／5bp三組所有原交易欄位一致，逐筆pnl、funding、net與9/8成本帳本重現。0bp基準交易PnL $7,934.09，加funding −$7.39＝$7,926.70。',
        '- 成本壓力的明示適配：舊引擎SafeNet沿用穿透模型但未加額外bp；本輪表內另外對SafeNet成交也加不利2／5bp。舊口徑$7,584.53／$7,018.89，完整成交壓力口徑$7,582.92／$7,014.88；0bp不變。未改原引擎。',
        '- 保留原雙向占倉、替代單、冷卻、熔斷、月上限及funding帳本；全期三個基準情境均無期末持倉，沒有丟棄未實現資產。funding不加入原引擎交易PnL風控計數，沿用現行语意。',
        f'- 重新核對全部metrics ZIP與checksum；來源{c["quality"]["source_rows"]:,}列。A暖機後特徵無效率{c["quality"]["a_invalid_fraction"]*100:.4f}%；B原策略可檢查事件無效率{c["quality"]["b_invalid_eligible_fraction"]*100:.2f}%，未補值。',
        f'- 獨立Decimal核對{audit["decimal_feature_checks"]:,}項特徵及嚴格正負門檻；以strategy.py純計算函式核對7欄指標及關鍵常數，通過。',
        '- 5個fixture測試通過：時間隔離、缺失中間點、刪除／擾動未來資料、出場嚴格邊界、24h群聚。真實資料10,000／16,000根截斷後，全部特徵、基準gate、bar2觀測與已平倉交易一致。截斷當下兩側都已平倉。',
        '- 既有索引101項已核對當前雜湊；2份變動文件（dynamic_tp_research_20260904、v37_microstructure_shadow_plan）與索引外新增文件已納入去重。這是定向核對，不是宣稱重跑所有歷史試驗。', '',
        '## 改善歸因及未完成驗證', '',
        '本輪沒有已計算的候選收益增量，因此避開原虧損、錯失原獲利、共同交易變化與新增交易收益均不作推論。沒有用原交易事後刪單偽裝完整策略收益。', '',
        '依停止條件，未執行候選完整狀態回測、單因子／同數量隨機對照、候選成本與延遲壓力、鄰域、連續WF、最佳月份／事件剔除、區塊重抽樣或Holm檢查。這些均非PASS。歷史已多輪使用；重新下載或重切分不能成為真正未見樣本。未啟動前瞻蒐集或排程。', '',
        '試驗數：沿用9/10六個已看收益的主候選歷史；本輪預登記最多2個主候選、實際做2個事前資料／事件診斷、候選收益試驗0個、鄰域0個、額外隨機對照0個。', '',
        '## 交付', '',
        '- [预登記](strategy_research_plan_20260911.md)',
        '- [重現與完整檔案](research_results/20260911_strategy_study/README.md)',
        '- [數值結果](research_results/20260911_strategy_study/results.json)',
        '- [獨立核對](research_results/20260911_strategy_study/independent_audit.json)',
        '',
        '僅新增本機研究檔案；正式strategy／executor、設定、實盤狀態及凍結資料維持原樣。沒有下單、部署、commit或push。',
    ]
    report=study.ROOT/'doc/strategy_research_results_20260911.md'
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    readme='''# 2026-09-11 可重現研究

結果：NO PROMOTION。A資料受限且後期10群；B後期14群、樣本不足。兩者均在候選收益計算前停止。

在專案根目錄以PowerShell執行：

```powershell
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_strategy_study_20260911.py
.\\.venv\\Scripts\\python.exe backtest/research/strategy_study_20260911.py
.\\.venv\\Scripts\\python.exe backtest/research/audit_strategy_study_20260911.py
.\\.venv\\Scripts\\python.exe backtest/research/write_strategy_study_report_20260911.py
```

需要本機既有：data/maxhold_review_20260908/candles.csv、data/public_cost_history_20260908/、data/optimization_execution_20260908/base_flat_{0,2,5}_trades.csv、data/new_information_20260910/（全部原ZIP、checksum、download_manifest及oi_source_merged.csv）。data被gitignore，不代表fresh clone能離線重現。重跑會重建本輪自己的研究輸出，最多重做兩個匿名3點API核對，不重抓歷史行情。

- registration.json：收益前鎖定時間、Git版本、計畫／程式／輸入雜湊、既有索引差異；後續同條件重跑另存implementation_run.json。
- sources.json：官方定義與匿名抽樣結果；本次ProxyError，0 bytes、不重試。
- pre_pnl_counts.json：候選收益前的事件數與品質。
- results.json／summary.csv：基準三成本完整指標、分期、parity及停止狀態。
- independent_audit.json：Decimal、當前strategy純函式、事件數重查。
- data/strategy_study_20260911/：三組基準逐筆、mark淨值、funding帳本、gate、bar2觀測、全特徵及B直接原事件。沒有候選逐筆檔，因未進行候選回測。

收盤t+1h為決策／成交模型時間；metrics q=決策−15min，假設q+10min可用。歷史received_ts及修訂狀態未知；此假設不是實盤可用性證明。原版收盤價成交及SafeNet穿透只作基準模型，不是已證實零延遲成交。
'''
    (study.DOC/'README.md').write_text(readme,encoding='utf-8')
    files=[p for folder in [study.DOC,study.OUT] for p in folder.glob('*') if p.is_file() and p.name!='delivery_manifest.json']
    files += [report,study.PLAN,Path(__file__),Path(study.__file__),
              study.ROOT/'backtest/research/audit_strategy_study_20260911.py',
              study.ROOT/'tests/test_strategy_study_20260911.py']
    study.dump(study.DOC/'delivery_manifest.json',{'created_utc':datetime.now(timezone.utc).isoformat(),
               'files':{str(p.relative_to(study.ROOT)).replace('\\','/'):{'sha256':study.cost.sha(p),'bytes':p.stat().st_size} for p in files}})
    assert '14群' in report.read_text(encoding='utf-8')
    print(str(report))


if __name__=='__main__':main()
