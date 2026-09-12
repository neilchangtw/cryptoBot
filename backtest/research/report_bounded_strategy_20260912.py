"""Verify saved baseline independently, close all three pre-return rounds."""
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import bounded_strategy_20260912 as b

ROOT=b.ROOT;DOC=b.DOC;OUT=b.OUT

def load(slip,suffix):
    p=OUT/f'base_{slip}bp_{suffix}.csv'
    dates={'trades':['entry_dt','exit_dt'],'equity':['time'],'funding':['time']}.get(suffix)
    return pd.read_csv(p,parse_dates=dates)

def audit_saved(base):
    mark=pd.read_csv(ROOT/'data/public_cost_history_20260908/mark_1h_full.csv').close.to_numpy()
    results=[]
    for row in base['runs']:
        slip=row['slip'];t=load(slip,'trades');eq=load(slip,'equity');fund=load(slip,'funding')
        assert not row['terminal_positions']
        times=pd.DatetimeIndex(eq.time);n=len(eq)
        cash=np.zeros(n);fc=np.zeros(n);flt=np.zeros(n);count=np.zeros(n,dtype=int)
        for tr in t.itertuples():
            first,last=int(tr.entry_bar),int(tr.exit_bar);sign=1 if tr.side=='L' else -1
            assert abs(tr.qty_exact*tr.entry_exact-4000)<1e-8 and tr.margin==200
            cash[first]-=tr.fee_exact/2;cash[last]+=tr.pnl+tr.fee_exact/2
            flt[first:last]+=sign*tr.qty_exact*(mark[first:last]-tr.entry_exact)
            count[first:last]+=1
        for f in fund.itertuples():
            tr=t.iloc[int(f.trade_id)];sign=1 if tr.side=='L' else -1
            expected=-sign*tr.qty_exact*f.markPrice*f.rate if f.included else 0.
            assert abs(expected-f.cashflow)<1e-8
            fc[times.get_loc(f.time)]+=f.cashflow
        rebuilt=cash.cumsum()+fc.cumsum()+flt
        error=float(np.abs(rebuilt-eq.equity).max());assert error<1e-8
        v=np.r_[0.,rebuilt]
        measured={'net_pnl':float(rebuilt[-1]),'loss_total':float(-t.loc[t.net<0,'net'].sum()),
            'mdd':float((np.maximum.accumulate(v)-v).max()),
            'worst30':float((pd.Series(rebuilt)-pd.Series(rebuilt).shift(720)).min()),
            'pf':float(t.loc[t.net>0,'net'].sum()/-t.loc[t.net<0,'net'].sum()),'wr':float(t.net.gt(0).mean()*100)}
        for k,v in measured.items():assert abs(v-row['full'][k])<1e-7,(slip,k)
        assert count.max()<=2 and abs(row['early']['net_pnl']+row['late']['net_pnl']-row['full']['net_pnl'])<1e-7
        results.append({'slip':slip,'equity_max_error':error,'all_metrics_rebuilt':'PASS','max_simultaneous':int(count.max()),
                        'fixed_notional_all_positions':'PASS','position_hours':int(count.sum())})
    prefix=[]
    t=load(0,'trades');eq=load(0,'equity')
    for p in base['prefix']:
        expected=t.loc[t.exit_bar<p['cut'],'net'].sum()+sum(x['funding']-x['entry_fee']+x['unrealized_mark'] for x in p['terminal_positions'])
        error=float(abs(expected-eq.equity.iloc[p['cut']-1]));assert error<.011
        prefix.append({'cut':p['cut'],'open_positions':len(p['terminal_positions']),'terminal_identity_error':error})
    return {'baseline_cost_ledgers':results,'prefix_terminal_identity':prefix}

def main():
    reg=b.register();base=b.read(DOC/'baseline.json');diagnostics=[b.read(DOC/f'round{i}_diagnostic.json') for i in [1,2,3]]
    for i in [2,3]:b.verify(b.read(DOC/f'round{i}_registration.json')['sha256'])
    assert [d['status'] for d in diagnostics]==['INSUFFICIENT_SAMPLE','INSUFFICIENT_SAMPLE','DATA_LIMITED']
    assert all(d['candidate_pnl_trials']==0 for d in diagnostics)
    audited=audit_saved(base)
    # The only inaccessible path surfaced by rg is a pre-existing synthetic test temp.
    # Explicitly disclose inventory scope rather than claiming all local/network data was searched.
    scope={'inventory':'2128 readable CSV headers / 79 distinct schemas, plus filenames in available data/data_live/logs/cache roots',
           'inaccessible_observed':['data/strategy_round3_20260911/test_temp/tmp1kcujkog'],
           'not_claimed':['Exhaustive inspection of inaccessible paths','Absence of usable data on the internet','True historical receive or revision proof']}
    tests={'commands':[
        {'command':'cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_bounded_strategy_20260912.py"','run':3,'passed':3},
        {'command':'cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_bounded_diagnostics_20260912.py"','run':7,'passed':7}],
        'unique_tests':10,'baseline_current_pure_indicators':'PASS','baseline_noop_cost_cases':3,'baseline_physical_prefixes':4,
        'G_direct_decision_prefixes':3,'H_direct_decision_prefixes':3,'candidate_return_backtests':0}
    b.dump(DOC/'verification.json',{'tests':tests,'saved_baseline_independent_audit':audited,'inventory_scope':scope,
        'protected_files_verified':b.verify(reg['protected_sha256']),'git_head_unchanged':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==reg['git_head']})
    advanced=['candidate full-state returns and 0/2/5bp economic gates','candidate full-strategy prefixes',
        'registered neighborhoods','execution delay/slippage validation','continuous-state walk-forward',
        'block bootstrap','multiple-comparison adjusted inference','payoff attribution','genuine prospective validation']
    summary={'conclusion':'INSUFFICIENT_SAMPLE','additional_status':'DATA_LIMITED','rounds_completed':3,
        'qualified_candidates':0,'best_evaluated_candidate':None,'candidate_return_trials':0,'cost_scenarios_candidate':0,
        'base':base['runs'],'rounds':diagnostics,'unperformed':advanced,'download_bytes':0,'network_requests':0,
        'stop_reason':'Maximum three rounds reached; G/H event gates fail, I lacks auditable execution data.',
        'no_alpha_claim':'Not evidence G/H/I have negative returns; returns were never evaluated.'}
    b.dump(DOC/'results.json',summary)
    records=[]
    for row in base['runs']:records.append({'name':'baseline','slip':row['slip'],'status':'BASELINE',**row['full']})
    for d in diagnostics:records.append({'name':d['name'],'slip':None,'status':d['status'],**(d['counts'] or {})})
    pd.DataFrame(records).to_csv(DOC/'summary.csv',index=False)
    b.dump(DOC/'history_index_delta.json',{'parent':'doc/research_results/20260910_new_information/history_index.json',
        'parent_sha256':b.sha(ROOT/'doc/research_results/20260910_new_information/history_index.json'),
        'rounds':[{'id':d['name'],'status':d['status'],'counts':d['counts'],'pnl_trials':0} for d in diagnostics],
        'nearby_prior_payoff_candidates_at_least':8,'new_registered_main_hypotheses':3,'new_main_payoff_trials':0,
        'historical_unseen':False,'do_not_reopen':'G requires >=15 late direct clusters on fresh coverage; H >=30/15; I needs received/queue/fill evidence. No threshold relaxation.'})
    table=['|方案／額外bp|交易數|事件群 全／後|淨收益U|PF|勝率|mark MDD U|最差30日U|虧損總額U|',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in base['runs']:
        m=row['full'];table.append(f"|基準／{row['slip']}|{m['n']}|—|{m['net_pnl']:,.2f}|{m['pf']:.2f}|{m['wr']:.2f}%|{m['mdd']:,.2f}|{m['worst30']:,.2f}|{m['loss_total']:,.2f}|")
    for d in diagnostics:
        counts=d['counts'];n=f"{counts['clusters24h']}／{counts['late_clusters24h']}" if counts else '不可確認'
        table.append(f"|{d['name']}／0・2・5|未計算|{n}|—|—|—|—|—|—|")
    report='''# 2026-09-12 最多三輪本機研究結果

結論：**INSUFFICIENT SAMPLE（G、H）；DATA LIMITED（I）**。三輪已完成並停止；沒有合格候選，也沒有按收益評選的最佳候選。三個候選全部在收益前的門檻停止，候選PnL計算0次；不能把樣本／資料受限寫成REJECTED，更不能宣稱其無效或基準為全域最佳。

'''+ '\n'.join(table)+'''

U為USDT。額外bp為每次成交不利成本；0bp仍含原4U模型成本及funding。各成本基準均重新完整回放，不是靜態扣款。固定200U／20x／每倉4000U、L/S各一倉；17,519根、2024-09-08 11:00至2026-09-08 09:00台北開盤時間，末根收盤為10:00。基準錨點全部一致。候選空白不是零損益；全期／後期群數不是總交易數。

## 停止歸因

- G：原TP觸及但收盤未確認，且取消TP後不會由原其他出場規則同價退出，共46個直接動作差異、44群／後期14群。後期門檻少1群；不得放寬到14，不跑候選收益、不從TP結果選輸家。這是收盤資格規則，沒有提高TP距離或新增MFE／MAE判斷。
- H：原新進場遇到反向倉位仍在，只有6事件／6群、後期3群。原L/S重疊太少，未達30／15，不拿269筆原交易或後續替代交易補樣本。
- I：已讀2,128份可讀CSV的79種schema及相關檔名，没有具有歷史接收、連續book/queue與訂單取消／成交時序的合格來源。不能以1h或5m碰價推定被動成交、不能假設maker費率優惠。因此群數及收益均不可計算，未下載或啟動收集。這是本機證據限制，並非宣稱網路上不存在資料。

沒有候選收益，因此避開原虧損、錯失原贏單、共同單變化、新增交易、funding及終端部位差額歸因均未計算；不可捏造收益改善或經濟失敗。

## 已完成驗證

1. 初始核對前轮有效修訂及938項保護檔案；當前production純指標與研究引擎在暖機後GK、突破、slope與regime逐欄一致；原策略、executor、設定與未提交研究保留。
2. 基準0／2／5bp重新回放，交易／funding／mark淨值／gate／全部狀態與已驗證凍結帳本一致。四個物理截斷前綴一致，包括第383根的真實未平倉且尚無已平倉交易截點；未造假平倉，保留入場費、funding及浮盈虧。
3. G和H各三個直接決策前綴一致；10個測試通過，包括TP界值、SafeNet優先、原MH/MFE同價出場排除、狀態時點、24h連結與跨年群首歸屬。只測決策，沒有候選PnL。
4. 報告再從保存逐筆與funding獨立重建三個基準mark淨值，核對收益、PF、勝率、虧損總額、MDD、最差720h、固定4000U及兩倉上限，誤差小於1e-8。未重跑旧候選。
5. 未改既有938項保護檔，HEAD未變；0網路請求、0下載、無委派／背景收集／排程／下單／部署／commit／push。

## 限制與未完成驗證

候選完整狀態回測、三成本經濟檢查、完整策略前綴、兩個鄰域、成交延遲、連續狀態walk-forward、區塊重抽樣、多重比較、集中度與真正前瞻驗證均因前置門檻未通過而未執行，不能標PASS。G/H使用與基準相同的已收盤OHLC可用假設，沒有歷史HTTP接收存檔，真實逐秒可成交性未證明；多輪看過的歷史不是未見樣本。

檔案盤點有一個既存且拒絕存取的舊測試暫存路徑 `data/strategy_round3_20260911/test_temp/tmp1kcujkog`，未繞過權限；盤點只宣稱上述可讀schema，未宣稱掃遍所有不可讀路徑。這不提供可用的真實L2或訂單證據，I保持資料受限。

三輪各有獨立預登記與停止紀錄：共同契約/G、H、I；詳細雜湊、baseline、diagnostics、verification、history_index_delta及summary.csv在本目錄。重現方式見README.md。
'''
    (DOC/'REPORT.md').write_text(report,encoding='utf-8')
    readme='''# 離線核對與重現

目前結果：G 44／14群、H 6／3群（樣本不足）；I缺可稽核執行資料（資料受限）。候選收益0次。不得執行候選收益或放寬門檻。

PowerShell於專案根目錄執行，全部單一前景程序：

```powershell
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_bounded*.py"
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 backtest/research/report_bounded_strategy_20260912.py"
```

以上只核對並重建本輪報告，不重算候選或舊研究。若需重算本輪基準no-op（通常無需）：

```powershell
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 backtest/research/bounded_strategy_20260912.py --phase baseline"
```

首次研究原始次序為baseline、bounded_strategy --phase diagnose-g、bounded_round2、bounded_round3、report。收益前的registration與diagnostic採不可覆寫；不要刪除它們或原事件檔來重新選規則。如需重核G/H事件，可在Python中呼叫direct_tp_events／direct並與保存events_pre_pnl.csv逐欄比對，不必生成另一輪。

完整本機凍結data、前輪引擎／修訂和帳本是必要依賴；data被gitignore，fresh clone不代表可重現。registration記錄有效來源雜湊、原HEAD及原未提交狀態；verification含測試命令與獨立帳本驗證。artifact_manifest記錄本輪全部程式、測試、預登記、結果及baseline帳本雜湊（不含manifest自身）。
'''
    (DOC/'README.md').write_text(readme,encoding='utf-8')
    paths=[p for p in DOC.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    paths += [ROOT/'backtest/research'/name for name in ['bounded_strategy_20260912.py','bounded_round2_20260912.py','bounded_round3_20260912.py','report_bounded_strategy_20260912.py']]
    paths += [ROOT/'tests'/name for name in ['test_bounded_strategy_20260912.py','test_bounded_diagnostics_20260912.py']]
    paths += [ROOT/'doc'/name for name in ['strategy_bounded_plan_20260912.md','strategy_bounded_round2_20260912.md','strategy_bounded_round3_20260912.md']]
    b.dump(DOC/'artifact_manifest.json',{'created_utc':b.now(),'sha256':{b.rel(p):b.sha(p) for p in sorted(set(paths))}})
    print(json.dumps({'rounds':3,'status':'INSUFFICIENT_SAMPLE / DATA_LIMITED','candidate_pnl_trials':0,
        'baseline':[{'slip':r['slip'],**{k:r['full'][k] for k in ['n','net_pnl','pf','wr','mdd','worst30','loss_total']}} for r in base['runs']],
        'protected_files':b.verify(reg['protected_sha256']),'artifacts':len(paths)}))

if __name__=='__main__':main()
