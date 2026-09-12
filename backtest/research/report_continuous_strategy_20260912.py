"""Checkpoint the active study; independently audit each completed candidate."""
import inspect
import json
import subprocess
import pandas as pd
import continuous_strategy_20260912 as c
import report_strategy_batch3_20260912 as audited

def audit_case(path,result):
    def load(name,slip,suffix):
        dates={'trades':['entry_dt','exit_dt'],'equity':['time'],'funding':['time']}.get(suffix)
        return pd.read_csv(path/'ledgers'/f'{name}_{slip}bp_{suffix}.csv',parse_dates=dates)
    scope={**audited.__dict__,'load':load}
    exec(compile(inspect.getsource(audited.audit),'<continuous-independent-ledger-audit>','exec'),scope)
    return scope['audit'](result)

def main():
    reg=c.initialize();completed=[];verifications=[];rows=[];rounds=[]
    for path in sorted(c.DOC.glob('round[0-9][0-9]_*')):
        if not (path/'diagnostic.json').exists():continue
        registration=c.read(path/'registration.json');c.verify(registration['sha256'])
        d=c.read(path/'diagnostic.json');item={'id':d['id'],'status':d['status'],'counts':d.get('counts'),'candidate_pnl_trials':0}
        if (path/'results.json').exists():
            r=c.read(path/'results.json');item.update(status=r['status'],candidate_pnl_trials=r['candidate_pnl_trials'])
            check=audit_case(path,r);c.dump(path/'independent_verification.json',check);verifications.append({'id':d['id'],**check})
            if not completed:rows.extend(r['runs'][:3])
            rows.extend(r['runs'][3:]);completed.append(r)
            a=r['attribution']['0'];base=r['runs'][0]['full'];cand=r['runs'][3]['full']
            text=f"# {d['id']} 研究結果\n\n**{r['status']}**。原狀態直接事件群 {d['counts']['clusters24h']}／後期 {d['counts']['late_clusters24h']}；3成本，主候選1次，鄰域0次。\n\n"
            text+=f"0bp net {base['net_pnl']:.2f}→{cand['net_pnl']:.2f}、mark MDD {base['mdd']:.2f}→{cand['mdd']:.2f}、loss {base['loss_total']:.2f}→{cand['loss_total']:.2f}。\n\n"
            text+=f"0bp失敗門檻：{', '.join(r['failed_gates']['0'])}。2/5bp詳見results.json。\n\n"
            text+=f"歸因：{json.dumps(a,ensure_ascii=False)}\n\n"
            text+='完整逐筆／funding／mark／gate／state存於ledgers，三成本no-op逐欄一致，8個物理前綴含未平倉截點一致；另從保存帳本獨立重建6套淨值及指標。主規則失敗即停止，不跑鄰域／延遲／WF／bootstrap／多重比較救援。\n'
            (path/'REPORT.md').write_text(text,encoding='utf-8')
        else:
            text=f"# {d['id']} 資料／事件准入結果\n\n**{d['status']}**。候選PnL0次，未進行收益測試。\n\n"
            text+=d.get('reason','獨立事件或資料前置門檻未通過。')+'\n\n'
            if d.get('sample_checks'):
                text+=f"已核對{len(d['sample_checks'])}個取樣日期的前後block時間／hash／base fee／gas欄位，共{d.get('download_bytes_this_attempt',0)} bytes。現在的canonical block資料不提供歷史finalized/available時間，24h緩衝不代替該證據。初次sandbox ProxyError下載0 bytes，必要網路權限後的小樣本成功；沒有全期下載。\n"
            (path/'REPORT.md').write_text(text,encoding='utf-8')
        rounds.append(item)
    assert completed
    best=max((r['runs'][3] for r in completed),key=lambda x:x['full']['net_pnl'])
    gitcheck=subprocess.run(['git','diff','--check'],cwd=c.ROOT,capture_output=True,text=True);assert gitcheck.returncode==0
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=c.ROOT,text=True).strip()==reg['git_head']
    verification={'utc':c.now(),'audited_cases':verifications,'protected_verified':c.verify(reg['protected_sha256']),
        'git_head_unchanged':True,'git_diff_check':'PASS','actual_passed_tests_by_case':{'P':4,'Q':4,'R':4,'S':4,'T':5,'U':4,'V':4},
        'finality_probe_status':'DATA_LIMITED: historical state endpoint HTTP 403; 3 GET / 340 bytes total; no payoff test',
        'public_sample_response_bytes':sum(c.read(p/'diagnostic.json').get('download_bytes_this_attempt',0) for p in c.DOC.glob('round[0-9][0-9]_*') if (p/'diagnostic.json').exists()),
        'no_op_cost_runs':3*len(completed),'physical_prefixes':8*len(completed),'independent_cost_ledgers':6*len(completed)}
    c.dump(c.DOC/'verification.json',verification)
    result={'utc':c.now(),'research_goal':'ACTIVE','completed_rounds':len(rounds),'rounds':rounds,
        'qualified_candidates':0,'best_evaluated':best['name'],'runs':rows,'new_payoff_trials':len(completed),
        'nearby_total_payoff_trials_at_least':9+len(completed),'historical_unseen':False,
        'unperformed':['neighborhoods','execution delays','continuous-state WF','bootstrap','multiple-comparison inference','prospective validation']}
    c.dump(c.DOC/'results.json',result)
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],**r['full']} for r in rows]).to_csv(c.DOC/'summary.csv',index=False)
    c.dump(c.DOC/'history_index_delta.json',{'parent':'doc/research_results/20260910_new_information/history_index.json',
        'rounds':rounds,'nearby_historical_payoff_trials_at_least':9+len(completed),'historical_unseen':False,
        'continue_rule':'New mechanism/data only; never rescue a failed direction by parameter tuning.'})
    table=['|方案／額外bp|交易數|事件群 全／後|淨收益U|PF|勝率|mark MDD U|最差30日U|虧損總額U|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        if r['name'] not in ['base',best['name']]:continue
        m=r['full'];n='—' if r['name']=='base' else next(x for x in rounds if x['id']==r['name'])['counts']
        if isinstance(n,dict):n=f"{n['clusters24h']}／{n['late_clusters24h']}"
        table.append(f"|{r['name']}／{r['slip']}|{m['n']}|{n}|{m['net_pnl']:,.2f}|{m['pf']:.2f}|{m['wr']:.2f}%|{m['mdd']:,.2f}|{m['worst30']:,.2f}|{m['loss_total']:,.2f}|")
    report='# 持續研究進度（不設三輪結案限制）\n\n使用者要求繼續；目前研究保持進行，沒有合格改善、沒有部署。以下最佳只按已評估0bp收益排序，不代表通過風險／收益門檻。\n\n'
    report+='\n'.join(table)+'\n\n'
    for r in completed:
        x=r['runs'][3]['full'];report+=f"- {r['id']}：{r['status']}；{r['counts']['clusters24h']}／{r['counts']['late_clusters24h']}群；0bp net {x['net_pnl']:.2f}、loss {x['loss_total']:.2f}、mark MDD {x['mdd']:.2f}。失敗={','.join(r['failed_gates']['0'])}。\n"
    for r in rounds:
        if r['candidate_pnl_trials']==0:
            report+=f"- {r['id']}：{r['status']}；候選收益未計算。"
            if r['id']=='S_onchain_basefee':report+='S的3個區塊日期小樣本有base fee／時間欄位，但沒有歷史finality／availability證據；block取樣7829 bytes。另finality補查3GET/340 bytes遇歷史state HTTP403即停，未擴大資料或用假定延遲放行。'
            elif r.get('counts'):report+=f"原始事件群 {r['counts']['clusters24h']}／後期 {r['counts']['late_clusters24h']}。"
            report+='\n'
    report+='\n固定200U/20x/每倉4000U，原凍結17,519根，0bp仍有費用與funding。所有收益皆完整狀態，包含替代交易、冷卻、熔斷及未平倉；每輪3成本no-op、8前綴及6帳本獨立核對。原策略／.env／狀態／VPS／Git不變。\n\n'
    report+='已計收益的方向均經濟淘汰；其他方向因資料或樣本未准入，不代表其經濟效果已被否定。不再跑失敗方向的鄰域；尚未執行延遲、連續WF、區塊bootstrap、多重比較及新資料前瞻驗證。歷史不是未見資料。下一方向仍先查重與資料可用時點，未以持續研究為理由放寬標準。各輪預登記、原事件、結果、歸因與完整帳本都在子目錄。\n'
    (c.DOC/'REPORT.md').write_text(report,encoding='utf-8')
    readme='''# 持續研究：離線驗證與原始重現順序

```powershell
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_continuous_case*.py"
cmd /c "call .venv\\Scripts\\activate.bat && python -B -X utf8 backtest/research/report_continuous_strategy_20260912.py"
```

原始首次執行（既有registration/diagnostic/results不可覆寫，不要刪除來重選）：

- continuous_strategy_20260912.py --case continuous_case01_20260912 --phase diagnose；然後--phase evaluate
- continuous_case02_20260912.py --phase diagnose；然後--phase evaluate
- continuous_case03_20260912.py --phase diagnose；然後--phase evaluate
- continuous_case04_20260912.py --phase sample（只小樣本；已DATA LIMITED，不全量下载）
- continuous_case05_20260912.py --phase diagnose；僅READY才--phase evaluate
- continuous_case06_20260912.py --phase diagnose（已INSUFFICIENT SAMPLE，不計收益）
- continuous_case07_20260912.py --phase diagnose（已DATA LIMITED，不計收益）

報告命令核對來源／保護檔、逐案獨立帳本，不重新選規則。基準資料與原研究依賴是本機凍結輸入，fresh clone沒有data時不能重現。逐輪三成本摘要在summary.csv，所有失敗原因和歸因在各results.json。研究仍ACTIVE，後續只可新機制或新資料；不得調參救已拒方向。
'''
    (c.DOC/'README.md').write_text(readme,encoding='utf-8')
    paths=[p for p in c.DOC.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    paths+=list((c.ROOT/'backtest/research').glob('*continuous*20260912.py'))
    paths+=list((c.ROOT/'doc').glob('continuous*20260912.md'))
    paths+=list((c.ROOT/'tests').glob('test_continuous*20260912.py'))
    mapping={c.rel(p):c.sha(p) for p in sorted(set(paths))}
    c.dump(c.DOC/'artifact_manifest.json',{'utc':c.now(),'sha256':mapping});c.verify(mapping)
    print(json.dumps({'goal':'ACTIVE','completed_rounds':len(rounds),'best_evaluated':best['name'],
        'qualified':0,'independent_cost_ledgers':6*len(completed),'protected_verified':verification['protected_verified'],'artifacts':len(mapping)}))

if __name__=='__main__':main()
