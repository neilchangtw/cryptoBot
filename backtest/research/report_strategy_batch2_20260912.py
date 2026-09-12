"""Read saved diagnostics, verify contracts, deliver second bounded batch."""
import json
import subprocess
from pathlib import Path
import pandas as pd
import strategy_batch2_20260912 as b

ROOT=b.ROOT;DOC=b.DOC

def main():
    registration=b.initialize();base=b.read(DOC/'baseline_reuse.json')
    rounds=[b.read(DOC/f'round{i}_diagnostic.json') for i in [1,2,3]]
    assert [r['status'] for r in rounds]==['DATA_LIMITED','INSUFFICIENT_SAMPLE','DATA_LIMITED']
    assert all(r['candidate_pnl_trials']==0 for r in rounds)
    verified=[]
    for i in [1,2,3]:verified.append(b.verify(b.read(DOC/f'round{i}_registration.json')['sha256']))
    assert b.sha(b.old.DOC/'baseline.json')==base['baseline_sha256']
    assert b.sha(b.old.DOC/'verification.json')==base['verification_sha256']
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    assert head==registration['git_head']
    gitcheck=subprocess.run(['git','diff','--check'],cwd=ROOT,text=True,capture_output=True)
    assert gitcheck.returncode==0,gitcheck.stdout+gitcheck.stderr
    tests=[{'file':'test_strategy_batch2_20260912.py','passed':4,'run':4},
           {'file':'test_strategy_batch2_extension_20260912.py','passed':5,'run':5},
           {'file':'test_strategy_batch2_quote_20260912.py','passed':2,'run':2}]
    for t in tests:t['command']=f'cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p {t["file"]}"'
    verification={'utc':b.now(),'tests':tests,'unique_tests':11,'round_input_hashes_verified':verified,
        'old_files_hash_verified':b.verify(registration['protected_sha256']),'old_git_head_unchanged':True,'git_diff_check':'PASS',
        'baseline_reuse':{'previous_artifacts':40,'previous_original_files':938,'no_new_baseline_replay':True,
           'trade_equity_funding_gate_state_noop_cost_cases_reused':3,'physical_prefixes_reused':4,
           'previous_independent_accounting_audit_reused':str(b.old.DOC/'verification.json')},
        'J_features_prefixes':3,'K_action_prefixes':3,'candidate_full_state_return_runs':0,
        'network_requests':0,'download_bytes':0}
    b.dump(DOC/'verification.json',verification)
    unperformed=['candidate full-state returns and 0/2/5bp economic gates','candidate strategy no-op/prefix tests',
        'preregistered neighborhoods','execution delay and additional slippage','continuous-state walk-forward',
        'block bootstrap','multiplicity-adjusted inference','payoff attribution','genuine prospective validation']
    result={'conclusion':'DATA_LIMITED','additional_status':'INSUFFICIENT_SAMPLE','new_rounds_completed':3,
        'prior_batch_rounds':3,'qualified_candidates':0,'best_by_return':None,'candidate_pnl_trials':0,
        'baseline':base['runs'],'rounds':rounds,'unperformed':unperformed,
        'stop_reason':'New batch reached three rounds; J/L data gates failed and K independent-event gate failed.',
        'no_efficacy_claim':'No candidate return calculated; no conclusion that candidate returns are positive or negative.'}
    b.dump(DOC/'results.json',result)
    rows=[{'name':'baseline','slip':r['slip'],'status':'BASELINE',**r['full']} for r in base['runs']]
    for r in rounds:
        rows.append({'name':r['name'],'status':r['status'],'counts_provisional':r['round']==1,
                     **(r.get('counts_provisional',r.get('counts')) or {})})
    pd.DataFrame(rows).to_csv(DOC/'summary.csv',index=False)
    b.dump(DOC/'history_index_delta.json',{'parent':'doc/research_results/20260910_new_information/history_index.json',
        'parent_sha256':b.sha(ROOT/'doc/research_results/20260910_new_information/history_index.json'),
        'previous_batch_delta_sha256':b.sha(b.old.DOC/'history_index_delta.json'),
        'new_registered_candidates':3,'new_payoff_candidates':0,
        'discarded_before_registration':{'name':'5m variance concentration','reason':'overlap with previous kurtosis/RBV research; not counted as a research round'},
        'new_directions':[{'id':r['name'],'status':r['status'],'counts':r.get('counts_provisional',r.get('counts')),'payoff_trials':0} for r in rounds],
        'reopening_requires':'J point-in-time receive/revision evidence; K new independent events; L price series with as-of evidence',
        'historical_unseen':False,'nearby_prior_payoff_trials_at_least':8})
    table=['|方案／額外bp|交易數|事件群 全／後|淨收益U|PF|勝率|mark MDD U|最差30日U|虧損總額U|',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in base['runs']:
        m=r['full'];table.append(f"|基準／{r['slip']}|{m['n']}|—|{m['net_pnl']:,.2f}|{m['pf']:.2f}|{m['wr']:.2f}%|{m['mdd']:,.2f}|{m['worst30']:,.2f}|{m['loss_total']:,.2f}|")
    table.extend(['|J 現貨／合約價格領先／0・2・5|未計算|暫88／34|—|—|—|—|—|—|',
                  '|K 扣成本後延長資格／0・2・5|未計算|8／3|—|—|—|—|—|—|',
                  '|L 穩定幣報價偏離／0・2・5|未計算|不可確認|—|—|—|—|—|—|'])
    report='''# 下一批最多三輪研究結果（2026-09-12）

**DATA LIMITED（J、L）；INSUFFICIENT SAMPLE（K）。** 新批次三輪已完成並停止，沒有合格候選或可按收益排名的最佳候選。三個方向皆在候選PnL之前停止；不宣稱有效、無效或原策略全域最佳。

'''+ '\n'.join(table)+'''

金額為USDT，0bp仍有原4U成本及funding；0／2／5bp是逐次成交額外逆向成本。固定200U／20x／每倉4000U、L/S各一倉，17,519根，末根收盤台北2026-09-08 10:00。基準來自前批全狀態回放，本批以目前檔案SHA256核對後沿用；沒有拿未核對的舊數字冒充新實驗，也沒有重跑舊候選。各成本基準交易數不同，反映冷卻／熔斷連鎖狀態，不是靜態扣款。

## 停止原因與歸因

- **J：價格領先位置。** 168小時lagged雙向相關差，原可進場gate中123次遭拒、暫88群／後期34群，數量達門檻。但來源的`historical_received_ts=unavailable`、`revision_status=unverified; historical API snapshot`；2026-09-09的下載日期不能證明2024–2026各決策當下看到的版本。1h延遲／55分鐘緩衝只是工程假設，因此資料門檻未過。沒有把前C研究曾計收益當成這次准入理由。
- **K：淨成本資格。** 只有8次原MH即將延長時，價格浮盈為正卻不足支付固定4U模型成本，形成8／3群。門檻30／15未過；不放寬到8／3，不提高費用閾值增加事件，也不跑鄰域救樣本。
- **L：USDC／USDT相對報價偏離。** 舊來源清單的2,128可讀CSV／79種schema，加本次data檔名檢索，未發現可對齊全期的USDCUSDT價格或歷史接收／修訂證據。不能用p=1補值，不能把未知視為沒有事件，亦不憑兩種穩定幣的比價就認定哪一個偏離USD。

候選收益、避開的虧損、錯失的贏單、替代交易、共同單變化、funding或期末部位損益差額都未計算。這次失敗是證據准入失敗，不是經濟收益被證偽。

## 查重、測試與保護

第一輪另考慮過5m波動集中度，但辨識其與既有峰度／RBV重疊後，在登記與事件檢查之前取消；不算第四輪或收益試驗。J不同於同時突破／premium／C主動量，K不同於直接加長MH／整體取消延長／降低費用，L新增報價貨幣資訊。每輪各一主候選及最多兩鄰域，前輪結案後才進下一輪。

本批11個測試全部通過：J已知領先方向、物理前綴與未来擾動、缺值及零variance、禁止同小時資料；K正負方向、低於／高於成本、原extension／交易根源、截點；L對數對称偏離及缺值。J真實資料3個特徵前綴、K真實狀態3個動作前綴一致，未冒充候選完整策略前綴。

前批40個產物、938原檔核對一致，保留三成本全欄no-op、4個物理前綴（包含未平倉且零已平倉交易）、獨立mark帳本及當前production純指標核對的已驗證結果。本批結尾979項既有檔案雜湊一致、HEAD不變、git diff --check通過。只新增本批程式／測試／文件／結果，沒有修改正式策略、.env、正式狀態、VPS或原未提交研究。

0網路請求、0下載；沒有下單、部署、commit、push、委派、fork、背景任務、排程或蒐集器。檔名掃描遇到的既存拒絕存取路徑是前輪測試暫存 `data/strategy_round3_20260911/test_temp/tmp1kcujkog`，未繞過，也未聲稱掃過不可讀內容；完整stderr在round3_sources.json。

## 尚未完成

候選完整狀態回測及0／2／5bp經濟門檻、候選策略no-op／前綴、鄰域、成交延遲、連續狀態WF、區塊重抽樣、多重比較、集中度、收益歸因與真正前瞻驗證，全部因前置門檻失敗而未執行。K仍承接基準1h收盤代理成交的限制，沒有逐秒成交證明。J、L不確定的歷史時點已作停止條件處理；未以「可能晚一點拿得到」放行。

下一次若再研究同方向，J須補時點／修訂證據、K須有新增獨立事件、L須有可核對的報價來源；單純換窗口、降低門檻或重跑同歷史不能解除限制。
'''
    (DOC/'REPORT.md').write_text(report,encoding='utf-8')
    readme='''# 本批離線核對與重現

PowerShell在專案根目錄執行；單一前景程序、不連網：

```powershell
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_strategy_batch2*.py"
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 backtest/research/report_strategy_batch2_20260912.py"
```

報告命令只核對原有來源／產物並重建本批報告，不回測候選。候選在資料／事件門檻停止，收益皆未計算。

首次執行次序（既存registration／diagnostic不可覆寫，勿重新執行來變更當初時間）：

1. strategy_batch2_20260912.py --phase diagnose-j
2. strategy_batch2_round2_20260912.py
3. strategy_batch2_round3_20260912.py
4. report_strategy_batch2_20260912.py

可直接呼叫lead_features或extension_events，對凍結輸入重算並與本批CSV比對；不得改公式、閾值或切分救結果。summary.csv與results.json彙整全部狀態；verification.json有實際測試命令與結果；history_index_delta.json供下一輪定向查重；artifact_manifest.json含全部本批產物SHA256（不包含自身）。

預登記在doc/strategy_batch2_round1_20260912.md、round2、round3；共同契約在round1。基準及原始資料維持本機原路徑，data被gitignore，fresh clone不等於具備全部凍結輸入。J暫計88／34群沒有通過可用時點認證，不能拿此計數宣稱可回測。
'''
    (DOC/'README.md').write_text(readme,encoding='utf-8')
    files=[p for p in DOC.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    files += [ROOT/'backtest/research'/name for name in ['strategy_batch2_20260912.py','strategy_batch2_round2_20260912.py','strategy_batch2_round3_20260912.py','report_strategy_batch2_20260912.py']]
    files += [ROOT/'tests'/name for name in ['test_strategy_batch2_20260912.py','test_strategy_batch2_extension_20260912.py','test_strategy_batch2_quote_20260912.py']]
    files += [ROOT/f'doc/strategy_batch2_round{i}_20260912.md' for i in [1,2,3]]
    manifest={b.rel(p):b.sha(p) for p in sorted(set(files))}
    b.dump(DOC/'artifact_manifest.json',{'utc':b.now(),'sha256':manifest});b.verify(manifest)
    print(json.dumps({'new_rounds':3,'status':'DATA_LIMITED / INSUFFICIENT_SAMPLE','candidate_pnl_trials':0,
        'tests_passed':11,'old_files_verified':verification['old_files_hash_verified'],'artifacts_verified':len(manifest)}))

if __name__=='__main__':main()
