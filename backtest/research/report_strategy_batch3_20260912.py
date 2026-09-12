"""Independent saved-ledger audit and final bounded-batch report."""
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch3_20260912 as b

def load(name,slip,suffix):
    dates={'trades':['entry_dt','exit_dt'],'equity':['time'],'funding':['time']}.get(suffix)
    return pd.read_csv(b.OUT/f'{name}_{slip}bp_{suffix}.csv',parse_dates=dates)

def audit(result):
    mark=pd.read_csv(b.ROOT/'data/public_cost_history_20260908/mark_1h_full.csv').close.to_numpy()
    checks=[]
    for row in result['runs']:
        name,slip=row['name'],row['slip'];t=load(name,slip,'trades');eq=load(name,slip,'equity');fund=load(name,slip,'funding')
        assert not row['terminal_positions']
        times=pd.DatetimeIndex(eq.time);cash=np.zeros(len(eq));fc=np.zeros(len(eq));flt=np.zeros(len(eq));positions=np.zeros(len(eq),dtype=int)
        for tr in t.itertuples():
            first,last=int(tr.entry_bar),int(tr.exit_bar);sign=1 if tr.side=='L' else -1
            assert abs(tr.qty_exact*tr.entry_exact-4000)<1e-8 and tr.margin==200
            cash[first]-=tr.fee_exact/2;cash[last]+=tr.pnl+tr.fee_exact/2
            flt[first:last]+=sign*tr.qty_exact*(mark[first:last]-tr.entry_exact)
            positions[first:last]+=1
        for f in fund.itertuples():
            tr=t.iloc[int(f.trade_id)];sign=1 if tr.side=='L' else -1
            expected=-sign*tr.qty_exact*f.markPrice*f.rate if f.included else 0.
            assert abs(expected-f.cashflow)<1e-8
            fc[times.get_loc(f.time)]+=f.cashflow
        rebuilt=cash.cumsum()+fc.cumsum()+flt
        error=float(np.abs(rebuilt-eq.equity).max());assert error<1e-8
        v=np.r_[0.,rebuilt]
        metrics={'n':len(t),'net_pnl':float(rebuilt[-1]),'loss_total':float(-t.loc[t.net<0,'net'].sum()),
            'pf':float(t.loc[t.net>0,'net'].sum()/-t.loc[t.net<0,'net'].sum()),'wr':float(t.net.gt(0).mean()*100),
            'mdd':float((np.maximum.accumulate(v)-v).max()),'worst30':float((pd.Series(rebuilt)-pd.Series(rebuilt).shift(720)).min())}
        for k,value in metrics.items():assert abs(value-row['full'][k])<1e-7,(name,slip,k)
        assert abs(row['early']['net_pnl']+row['late']['net_pnl']-row['full']['net_pnl'])<1e-7
        assert positions.max()<=2
        checks.append({'name':name,'slip':slip,'equity_error':error,'metrics':'PASS','fixed4000_each':'PASS','max_positions':int(positions.max())})
    prefixes=[]
    for p in result['prefix']:
        t=load(p['name'],0,'trades');eq=load(p['name'],0,'equity')
        terminal=sum(x['funding']-x['entry_fee']+x['unrealized_mark'] for x in p['terminal_positions'])
        error=float(abs(t.loc[t.exit_bar<p['cut'],'net'].sum()+terminal-eq.equity.iloc[p['cut']-1]))
        assert error<.011
        prefixes.append({'name':p['name'],'cut':p['cut'],'open_positions':len(p['terminal_positions']),'identity_error':error})
    return {'full_cost_ledgers':checks,'prefix_open_position_identity':prefixes}

def main():
    reg=b.initialize();m=b.read(b.DOC/'round1_results.json')
    n=b.read(b.DOC/'round2_diagnostic.json');o=b.read(b.DOC/'round3_diagnostic.json')
    assert m['status']=='REJECTED' and n['status']=='INSUFFICIENT_SAMPLE' and o['status']=='DATA_LIMITED'
    assert m['candidate_pnl_trials']==1 and n['candidate_pnl_trials']==o['candidate_pnl_trials']==0
    registration_checks=[b.verify(b.read(b.DOC/f'round{i}_registration.json')['sha256']) for i in [1,2,3]]
    b.verify(b.read(b.DOC/'round3_implementation_revision.json')['sha256'])
    independent=audit(m)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=b.ROOT,text=True).strip();assert head==reg['git_head']
    check=subprocess.run(['git','diff','--check'],cwd=b.ROOT,capture_output=True,text=True);assert check.returncode==0
    verification={'utc':b.now(),'independent_saved_ledger_audit':independent,'round_input_hashes':registration_checks,
        'tests':{'unique_passed':11,'clock':4,'funding':3,'mark':3,'timestamp_resolution':1},
        'new_baseline_noop_cost_cases':3,'base_and_M_physical_prefixes':8,'diagnostic_feature_prefixes':9,
        'protected_files_verified':b.verify(reg['protected_sha256']),'head_unchanged':True,'git_diff_check':'PASS',
        'documentation_web_calls':5,'market_data_download_bytes':0,'orders_deploy_commit_push':0,
        'subagents_background_jobs_schedules':0,'previous_artifacts':reg['previous_checks']}
    b.dump(b.DOC/'verification.json',verification)
    rows=[]
    for row in m['runs']:rows.append({'name':row['name'],'slip':row['slip'],'status':'BASELINE' if row['name']=='base' else 'REJECTED',**row['full']})
    pd.DataFrame(rows).to_csv(b.DOC/'summary.csv',index=False)
    remaining=['preregistered neighborhoods','execution delays','continuous-state walk-forward',
        'block bootstrap','multiple-comparison correction','concentration controls','genuine prospective validation',
        'N/O candidate full-state returns and candidate no-op/prefix tests']
    result={'utc':b.now(),'conclusion':'REJECTED','secondary_statuses':['INSUFFICIENT_SAMPLE','DATA_LIMITED'],
        'rounds_completed':3,'qualified_candidates':0,'best_evaluated':'M (only evaluated candidate, rejected)',
        'candidate_pnl_trials':1,'cost_scenarios':3,'baseline_and_candidate':m['runs'],
        'rounds':[{'name':'M','status':m['status'],'counts':m['counts']},n,o],
        'failed_gates':m['failed_gates'],'attribution':m['attribution'],'unperformed':remaining,
        'stop_reason':'Three new rounds completed. No qualified candidate; economic failure is terminal, no parameter rescue.'}
    b.dump(b.DOC/'results.json',result)
    b.dump(b.DOC/'history_index_delta.json',{'parent':'doc/research_results/20260910_new_information/history_index.json',
        'parent_sha256':b.sha(b.ROOT/'doc/research_results/20260910_new_information/history_index.json'),
        'prior_batch_delta_sha256':b.sha(b.parent.DOC/'history_index_delta.json'),
        'new_main_candidates':3,'new_main_payoff_trials':1,'nearby_historical_payoff_trials_at_least':9,
        'historical_unseen':False,'directions':[{'id':'M_ny_day_clock','status':'REJECTED','clusters':'60/21','payoff_trials':1},
            {'id':'N_funding_debit_budget','status':'INSUFFICIENT_SAMPLE + DATA_LIMITED','provisional_clusters':'0/0','payoff_trials':0},
            {'id':'O_add_mark_safenet','status':'DATA_LIMITED','potential_hours':'2/0','payoff_trials':0}],
        'reopen_only_with':'M new independent mechanism or experiment flaw; N new events and receipt evidence; O synchronized point-in-time mark/contract/fill data. No threshold rescue.'})
    table=['|方案／額外bp|交易數|事件群 全／後|淨收益U|PF|勝率|mark MDD U|最差30日U|虧損總額U|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in m['runs']:
        x=row['full'];name='基準' if row['name']=='base' else 'M 紐約日間gate';counts='—' if row['name']=='base' else '60／21'
        table.append(f"|{name}／{row['slip']}|{x['n']}|{counts}|{x['net_pnl']:,.2f}|{x['pf']:.2f}|{x['wr']:.2f}%|{x['mdd']:,.2f}|{x['worst30']:,.2f}|{x['loss_total']:,.2f}|")
    report='''# 第三批最多三輪研究結果（2026-09-12）

**REJECTED（M）；INSUFFICIENT SAMPLE＋DATA LIMITED（N）；DATA LIMITED（O）。** 三輪已完成，沒有合格候選，不升級。M是本批唯一完成收益回放的候選，稱為最佳僅表示唯一可比較者，不表示推薦。

'''+ '\n'.join(table)+'''

U=USDT，0bp仍含原模型費用及funding；2/5bp是逐次成交額外逆向成本。固定200U/20x/每倉4000U，L/S各最多一倉；17,519根，台北開盤2024-09-08 11:00至2026-09-08 09:00、最後收盤10:00。60/21是原狀態65次直接拒絕形成的24h傳遞群，全／後門檻30/15通過；不是218筆候選交易或新增替代單數。後期從2026-01-01起。

## 各輪結論與歸因

1. **M：紐約工作日08:30–16:00拒絕進場。** 純時鐘、隨DST切換；包含假日／提早收市，並非交易所實際交易日曆。相比固定台北小時掃描是不同已凍結機制，沒有根据舊掃描收益選時間。主候選三成本均未達淨收益+5%，前期／後期也都低於基準；即使mark MDD與虧損總額下降仍淘汰。

   0bp淨收益少**2,205.81U（-27.83%）**。完整狀態共同204筆、移除65筆、新增14筆；避開原虧損+1,254.65，錯失40筆原贏單-3,372.98，替代交易-87.49，共同單0，期末差額約0，合計精確重建損益差。少交易使手續費少204U已含在結果，funding差僅+0.43U。前期5782.19→3882.60、後期2144.51→1838.29，不是只差一段時間。2bp少2124.00U，5bp少2041.45U；未用減少交易數換取較漂亮MDD冒充改善。

2. **N：已結算負funding增加風控預留。** 原所有CB保持，再將嚴格早於決策的已支付負現金流列入日／分方向月度gate；credit不擴大額度，沒有預測未來funding。模型中**0直接事件／0群／後期0群**，不准許候選收益。也缺真實歷史user-data入帳接收／修訂證據；模擬帳本的結算時間與1h緩衝不能冒充可用時點證明。沒有靠放大費用權重救樣本。

3. **O：保留contract SafeNet，增加同閾值mark參考。** 只找到**2個potential bar，皆contract與mark同小時觸價，後期0**；不能從小時OHLC判定哪個先觸發、當時可成交價格或取消／平倉競態。資料缺口使真實受影響獨立事件不可確認，記為DATA LIMITED，不是「2個已驗證事件」，也不是0收益。沒有將mark當成交價或套用contract穿透公式生成候選PnL。

M只有一次主候選收益試驗（三成本）；N/O各0次。三輪全部停止，鄰域0次，沒有第四輪或參數救援。

## 驗證與可追溯性

- 前批基準、產物及原檔先hash核對；本批基準0/2/5bp重新no-op，逐欄交易、gate、全部狀態、funding與mark淨值一致，重現269筆／7926.70U／mark MDD 368.53U錨點。
- 先no-op，再候選0bp狀態trace作物理前綴，8個基準／M前綴全部一致，包含仍持倉且尚無已平倉交易的截點；通過後才評估候選收益摘要與經濟門檻。保留佔倉、替代單、冷卻、連敗、日月風控、funding及期末倉位，沒有刪原單當回測。
- 11項測試通過：DST春秋邊界、決策收盤時刻、時段端點／週末、前綴／缺值、funding時間／credit／月日重置／方向、mark持倉時點／雙觸價不確定性、timestamp解析度。M/N/O另各3個診斷前綴一致；N/O不是候選完整策略前綴。
- 報告從6套已存交易／funding獨立重建逐時mark淨值及全部主表指標，誤差<1e-8；逐筆固定名目4000U、最多雙倉、early+late=full；8個截點另核對未平倉funding／入場費／浮盈虧帳本。
- O初次檢查在事件前因datetime毫秒／微秒dtype差異停止。新增獨立compat適配器，只將兩邊timestamp解析度正規化後逐個時間精確比對；測試證明1毫秒位移仍失敗。原登記／程式／公式／資料不改，補充雜湊與失敗原因在round3_implementation_revision.json。

基準仍承接已收盤1h資料／模型成交與历史mark/funding帳本假設，沒有歷史逐秒收到時間，也不是未見樣本。M沒有加入待接收的新市場特徵；N/O新增時點疑義已作停止理由。正式參數、.env、狀態、VPS、既有未提交研究保留，HEAD不變；保護檔數與逐一雜湊見verification及batch_registration。

本批5次web工具呼叫只查NYSE/BLS/Binance官方規格（來源見各預登記），0市場資料下載；無下單、部署、commit、push、subagent、fork、背景任务或排程。既有資料schema盤點只涵蓋可讀2128 CSV/79 schema；拒絕存取的舊合成測試暫存未繞過，沒有宣稱掃遍不可讀路徑或網路不存在資料。

## 尚未完成

主候選經濟門檻失敗，所以未做預登記鄰域、成交延遲、連續狀態WF、區塊重抽樣、多重比較／集中度及真正前瞻驗證。N/O未做候選收益、完整策略no-op／前綴或三成本經濟檢查。不可稱PROMOTE或SHADOW ONLY；沒有證據把N/O判成經濟無效，也不宣稱基準全域最佳。
'''
    (b.DOC/'REPORT.md').write_text(report,encoding='utf-8')
    readme='''# 第三批離線核對／重現

在專案根目錄PowerShell執行，單一前景程序，不連網：

```powershell
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_strategy_batch3*.py"
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 backtest/research/report_strategy_batch3_20260912.py"
```

報告只讀saved ledgers，獨立核對並重建本批報告，不重跑候選。results.json／summary.csv列結果；registration與artifact_manifest列來源／產物hash；verification記錄驗證結果；history_index_delta供下批定向查重。

首次執行次序（registration／diagnostic／results不可覆寫，勿刪除重選）：

1. strategy_batch3_20260912.py --phase diagnose-m
2. strategy_batch3_20260912.py --phase evaluate-m
3. strategy_batch3_round2_20260912.py
4. strategy_batch3_mark_compat_20260912.py（保留原round3原始程式，使用補充登記的timestamp適配）
5. report_strategy_batch3_20260912.py

可呼叫Study.run('M', slip, save=False)離線重算已登記的0/2/5bp並比对saved ledger；不改公式或新增掃描。M已經濟淘汰，不執行鄰域；N/O前置門檻未過，不實作收益回放。

依賴本機凍結data、舊研究引擎／帳本及本批全部檔案；data被gitignore，fresh clone不等於有輸入。所有Python經.venv activation。若驗證到來源hash漂移應先定位，不能拿原數字冒充新基準。
'''
    (b.DOC/'README.md').write_text(readme,encoding='utf-8')
    paths=[p for p in b.DOC.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    paths+=list((b.ROOT/'backtest/research').glob('*strategy_batch3*20260912.py'))
    paths+=list((b.ROOT/'tests').glob('test_strategy_batch3*20260912.py'))
    paths+=list((b.ROOT/'doc').glob('strategy_batch3_round*20260912.md'))
    mapping={b.rel(p):b.sha(p) for p in sorted(set(paths))}
    b.dump(b.DOC/'artifact_manifest.json',{'utc':b.now(),'sha256':mapping});b.verify(mapping)
    print(json.dumps({'rounds':3,'status':'REJECTED / INSUFFICIENT_SAMPLE / DATA_LIMITED',
        'tests_passed':11,'independent_cost_ledgers':6,'protected_verified':verification['protected_files_verified'],
        'artifacts_verified':len(mapping),'new_payoff_candidates':1}))

if __name__=='__main__':main()
