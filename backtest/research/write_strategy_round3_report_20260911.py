"""Rebuild round-3 closure from saved evidence only. No market request or backtest."""
from pathlib import Path
import json
import sys
import pandas as pd
import strategy_round3_20260911 as r


def main():
    r.register()
    prior=r.read(r.PREV/'results.json')
    diagnostic=r.read(r.DOC/'diagnostic_status.json')
    failure=r.read(r.DOC/'source_failure.json')
    requests=r.read(r.DOC/'source_requests.json')
    assert diagnostic['status']=='DATA_CONSTRAINED'
    assert diagnostic['candidate_pnl_trials']==0 and diagnostic['event_counts'] is None
    assert failure['error_type']=='ProxyError' and failure['retry_count']==0
    assert len(requests)==1 and sum(x['bytes'] for x in requests)==0
    baseline=[x for x in prior['runs'] if x['name']=='base']
    assert [x['slip'] for x in baseline]==[0,2,5]
    assert baseline[0]['full']['n']==269
    reg=r.read(r.DOC/'registration.json'); revision=r.read(r.DOC/'implementation_revision.json')
    r.verify_map(revision['sha256']); r.verify_map(revision['original_sources_sha256'])
    preserved=r.verify_map(reg['protected_sha256'])
    source_urls=['https://docs.deribit.com/api-reference/market-data/public-get_volatility_index_data',
        'https://insights.deribit.com/exchange-updates/dvol-deribit-implied-volatility-index/']
    sources={'official_pages_checked_utc_date':'2026-09-11','urls':source_urls,
        'verified_documentation':{'public_method':'public/get_volatility_index_data','currency':'ETH',
            'resolution_seconds':'3600','query':['currency','resolution','start_timestamp','end_timestamp'],
            'response':['timestamp_ms','open','high','low','close'],'pagination':'continuation becomes next end_timestamp',
            'methodology':'30-day annualised implied volatility inferred from option prices'},
        'documentation_caveats':['No guaranteed complete historical coverage or historical received timestamps.',
            'No documented point-in-time revision/version guarantee established.',
            'Timestamp label start/end convention still requires the planned aggregation check.',
            'The historical API numeric example is not used to infer ETH DVOL units; the formula uses a ratio.'],
        'local_market_requests':requests,'downloaded_market_bytes':0,'rows_obtained':0,
        'prior_failed_Binance_endpoint_retried':False,'network_bypass':False,'account_keys_used':False,
        'common_coverage_verified':False,'historical_received_ts':None,'historical_unseen':False,
        'source_availability_verdict':'Official public endpoint documented; actual common coverage unverified because local probe failed.'}
    r.dump(r.DOC/'sources.json',sources)
    dedup={'parent_index':'doc/research_results/20260910_new_information/history_index.json',
        'parent_index_sha256':r.sha(r.ROOT/'doc/research_results/20260910_new_information/history_index.json'),
        'new_mechanisms_considered':1,'selected':'D_dvol_shock','main_pnl_trials_added':0,
        'records':[
            {'path':'doc/v19_research.md','lines':[27,185],'status':'OPTIONS_SKIPPED_NO_DATA','sha256':r.sha(r.ROOT/'doc/v19_research.md')},
            {'path':'doc/v36_multitrack_research.md','lines':[62,73],'status':'OPTIONS_PROSPECTIVE_ONLY','sha256':r.sha(r.ROOT/'doc/v36_multitrack_research.md')},
            {'path':'doc/new_information_plan_20260910.md','lines':[43,43],'status':'OPTIONS_COVERAGE_NOT_VERIFIED','sha256':r.sha(r.ROOT/'doc/new_information_plan_20260910.md')},
            {'path':'doc/backtest_history.md','lines':[2426,2470],'status':'TRADE_COUNT_AND_AVERAGE_SIZE_ALREADY_STUDIED','sha256':r.sha(r.ROOT/'doc/backtest_history.md')},
            {'path':'doc/strategy_research_results_20260911_round2.md','status':'C_REJECTED_AB_NOT_REOPENED','sha256':r.sha(r.ROOT/'doc/strategy_research_results_20260911_round2.md')},
            {'path':'doc/strategy_research_results_20260911_round3.md','status':'D_DATA_CONSTRAINED_NO_PNL'}],
        'reopening_basis':'Official ETH hourly DVOL endpoint now identified; earlier options work had no candidate PnL. Actual data still unavailable in this run.',
        'distinction':'Option-implied forward volatility shock; no directional taker flow, OI net position change, realised-volume or leverage overlay.',
        'not_an_exhaustiveness_claim':True}
    r.dump(r.DOC/'history_index_delta.json',dedup)
    tests={'command':'.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_strategy_round3_20260911.py',
        'executed_before_first_source_request':True,'tests_run':6,'failures':0,'errors':0,'status':'PASS',
        'stdout':'......\nRan 6 tests in 0.028s\nOK',
        'python':sys.version,'test_sha256':r.sha(r.ROOT/'tests/test_strategy_round3_20260911.py'),
        'covered':['Hand-calculated 10% boundary and 55-minute assumed availability buffer',
                   'Missing intervening hour cannot be bridged','Physical prefix and future-value perturbation',
                   '24-hour chained event clusters and period head attribution','Invalid source OHLC/schema rejection',
                   'Saved source failure prevents any request session'],
        'earlier_fixture_errors':'Two filesystem fixture attempts failed with Windows TemporaryDirectory PermissionError; no candidate calculation or market request had occurred. Original source/test saved; explicit implementation revision switches guard fixture to mocked recorded-failure presence.',
        'not_validated':['Actual market feature prefix','Candidate stateful adapter','Profitability or economic gates']}
    r.dump(r.DOC/'test_results.json',tests)
    result={'status':'NO_PROMOTION','candidate_status':'DATA_CONSTRAINED','candidate':'D_dvol_shock',
        'stop_reason':'First new Deribit source probe failed with ProxyError/WinError10061. No retry or alternate channel; common coverage and feature quality not verified.',
        'registration_utc':reg['registered_utc'],'implementation_revision_utc':revision['registered_utc'],
        'candidate_pnl_trials':0,'candidate_cost_scenarios_evaluated':0,'neighborhoods_evaluated':0,'controls_evaluated':0,
        'direct_event_counts':None,'candidate_metrics':None,
        'baseline_runs':baseline,'baseline_provenance':'Second-round immutable saved outputs, current hashes match.',
        'data':{'rows':17519,'last_close_taipei':'2026-09-08 10:00:00','margin':200,'leverage':20,'notional_per_side':4000,
                'simultaneous_cap':2,'market_bytes_added':0,'market_requests':1,'source_rows':0},
        'attribution':{'status':'NOT_COMPUTED','reason':'Candidate quality gate not passed; no candidate sequence exists.',
            'avoided_losses':None,'missed_winners':None,'common_delta':None,'added_net':None,
            'funding_delta':None,'fees_delta':None,'terminal_delta':None,'net_delta':None},
        'checks':{'previous_hash_checks':r.read(r.DOC/'initial_hash_audit.json')['checks'],
                  'end_preserved_files':preserved,'new_tests':6,'new_tests_status':'PASS',
                  'baseline_noop_prefix_ledger':'REUSED_BY_HASH','D_stateful_noop':'NOT_RUN','D_actual_prefix':'NOT_RUN'},
        'unperformed':['Common DVOL coverage and raw-data reconstruction','60s/3600s timestamp convention',
            'Actual historical reception/revision audit','Quality and 30/15 independent-event gates',
            'Full stateful candidate replay, terminal positions and attribution',
            'Candidate 0/2/5bp costs, new no-op and actual-prefix checks','Neighborhood and delay stress',
            'Continuous-state walk-forward, block bootstrap, multiplicity and concentration',
            'Rule-locked genuine prospective validation'],
        'carried_status':{'A':{'status':'DATA_CONSTRAINED','provisional_clusters':42,'late_clusters':10,'pnl_trials':0},
            'B':{'status':'INSUFFICIENT_SAMPLE','clusters':47,'late_clusters':14,'pnl_trials':0},
            'C':{'status':'REJECTED','clusters':93,'late_clusters':31,'net_0bp':5344.216617353569},
            '20260910_six_OI_premium':'REJECTED'},
        'effects_too_small':[],'historical_candidates':[],
        'claims_not_supported':['D is ineffective','A/B are ineffective','Free DVOL history does not exist','Original strategy is globally optimal'],
        'production_changes':False,'preserved_existing_uncommitted_work':True,'background_collection':False}
    # Carry exact C from its own verified output, avoiding a hand-copied number.
    c=next(x for x in prior['runs'] if x['name']=='candidate' and x['slip']==0) if any(x['name']=='candidate' for x in prior['runs']) else None
    if c is None:
        c=next(x for x in prior['runs'] if x['name'] not in ['base','quality','futures_control'] and x['slip']==0)
    result['carried_status']['C']['net_0bp']=c['full']['net_pnl']
    r.dump(r.DOC/'results.json',result)
    rows=[{'name':'base','slip_bp_per_fill':b['slip'],**b['full'],'status':'REUSED_VERIFIED'} for b in baseline]
    rows += [{'name':'D_dvol_shock','slip_bp_per_fill':x,'status':'DATA_CONSTRAINED_NOT_COMPUTED'} for x in [0,2,5]]
    pd.DataFrame(rows).to_csv(r.DOC/'summary.csv',index=False)
    b=baseline[0]['full']
    table=('|規則|交易數|事件群 全期／後期|0bp淨收益$|勝率|PF|mark MDD$|最差30日$|虧損總額$|2／5bp淨收益$|判定|\n'
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|\n'
           f"|基準|{b['n']}|—|{b['net_pnl']:,.2f}|{b['wr']:.2f}%|{b['pf']:.2f}|{b['mdd']:.2f}|{b['worst30']:.2f}|{b['loss_total']:,.2f}|{baseline[1]['full']['net_pnl']:,.2f}／{baseline[2]['full']['net_pnl']:,.2f}|保留|\n"
           '|D 選擇權隱含波動突升|未計算|未取得資料|未計算|—|—|—|—|—|未計算|資料受限|')
    report=f'''# 2026-09-11 第三輪結果：選擇權隱含波動突升

**NO PROMOTION。本輪已執行來源探測、測試與雜湊稽核，D因資料受限結案；收益實驗0次，沒有歷史候選。** 不能把未取得資料說成D已被證明無效，也不能據此宣稱免費長歷史不存在或原策略全域最佳。

{table}

固定200U、20倍、每倉4,000U、L/S各最多一倉；最後收盤台北2026-09-08 10:00。基準為含手續費與funding的已驗證結果，mark MDD含浮盈虧，最差30日為完整720h窗口。2bp基準269筆、5bp267筆；全部沿用狀態重放結果，不是事後扣固定總額。D欄位的「未計算」不是0，也不是無交易。

## 選擇與停止證據

本輪只列並選一個新機制：GK壓縮下，選擇權隱含波動仍明顯上升可能增加突破失敗風險。規則為名義決策D使用q=D−2h的DVOL收盤，`V(q)/V(q−24h)−1>10%`時拒絕新L/S倉位；中間25小時須全數有效，至少留55分鐘假設接收緩衝。這個風險關係尚未獲得實證。

V19／V36／9/10只因資料未核實而跳過options，沒有此規則的歷史收益。此次辨識到[官方公開歷史端點]({source_urls[0]})支援ETH、小時解析度及時間／OHLC欄位；[官方方法說明]({source_urls[1]})把DVOL定義為30日年化隱含波動。它新增選擇權價格資訊，與C現貨主動量、A/B持倉存量、永續溢價及已實現波動部位overlay不同。完整共同覆蓋、歷史接收與修訂版本仍未證實。

預登記時間UTC {reg['registered_utc']}，在事件或候選收益查看之前。第一次市場資料請求UTC {failure['requested_utc']}，查詢ETH、resolution=3600、start_timestamp={failure['params']['start_timestamp']}、end_timestamp={failure['params']['end_timestamp']}。結果是ProxyError及WinError10061；只有1次請求、0 bytes、0資料列，立即停止。沒有重試先前Binance端點，也沒有以瀏覽器／其他主機／代理設定繞過限制。

因此共同覆蓋與品質門檻未通過，無法取得30／15群計數；尚不能分類成「樣本不足」「效果太小」或「淘汰」。沒有把新下載、換分期或舊歷史稱為真正未見資料。

## 損益歸因

D未形成可評估的完整策略序列。避開虧損、錯失獲利、共同單變化、替代單、funding／手續費及期末持倉差額均為未計算；不宣稱有降低虧損或提高收益。基準數據來自前輪凍結檔雜湊核對，並非本輪新回測。

## 已完成驗證

- 前轮807個保護檔、18項有效registration與61個artifact全部一致；原registration唯一程式差異符合已保存的implementation_revision，因此沿用既有逐筆、no-op、前綴及獨立帳本驗證，不重跑舊輪次。
- 新增6項fixture全部通過：10%邊界、資料可用時間、缺值不橋接、物理前綴／未來值擾動、24h群聚及後期歸屬、無效OHLC拒絕、已存失敗禁止重試（前兩项同一測試）。這些是合成資料測試，不冒充真實DVOL品質或策略收益驗證。
- 初次測試的TemporaryDirectory受Windows權限限制，兩次失敗後改用模擬已存在失敗檔條件；原始程式／測試保留在registration_sources，修訂在市場請求及候選收益前另行登記。公式、資料、門檻與切分未變。
- 本輪末次核對{preserved}個既有檔案雜湊不變，保留既有未提交工作。實際`.venv`為Python {sys.version.split()[0]}。只新增本機研究檔案。

## 未完成驗證與既有狀態

尚未完成：完整DVOL共同覆蓋／原始資料品質、60秒與小時標籤核對、歷史接收／修訂、實際事件數、D完整狀態回測與期末持倉、D的0／2／5bp成本與損益歸因、新接線no-op／實際前綴、鄰域、延遲、連續狀態WF、區塊重抽樣、多重比較、集中度，以及真正前瞻驗證。因資料門檻失敗而未執行，均不是PASS；沒有啟動背景蒐集。

A仍為資料受限（暫42／10群，另有後期樣本不足）；B仍為樣本不足（47／14群）；A/B候選收益均0次，沒有證明無效。C與9/10六個OI／溢價候選維持已淘汰。只有資料或來源權限實質改變後才能另開登記繼續D；本輪不重試、不換候選湊結果。

## 檔案

- [預登記](strategy_research_plan_20260911_round3.md)
- [完整JSON結果](research_results/20260911_strategy_round3/results.json)
- [來源與失敗證據](research_results/20260911_strategy_round3/sources.json)
- [離線重現與檔案清單](research_results/20260911_strategy_round3/README.md)
'''
    (r.ROOT/'doc/strategy_research_results_20260911_round3.md').write_text(report,encoding='utf-8')
    readme='''# 2026-09-11 第三輪本機重現

結論：D選擇權隱含波動突升「資料受限」，候選收益0次。首次Deribit探測ProxyError／WinError10061，0 bytes；失敗記錄禁止重試。本輪完成至預登記停止線，不包含合格資料下才允許的回測。

在專案根目錄執行以下離線指令，不連網、不重跑前輪、不修改正式策略：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/strategy_round3_20260911.py --phase audit
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_strategy_round3_20260911.py
```

雜湊一致時直接沿用保存結果，無需重跑測試。需要重建本輪JSON／CSV／報告時才執行：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/write_strategy_round3_report_20260911.py
```

此重建指令只讀已保存來源停止證據及前輪結果；不取得候選收益、不請求市場資料。`--phase probe`是第一次探測用程式入口，已執行並保存失敗；不列作重現步驟。請保留source_failure.json與source_requests.json，不刪檔重試。取得新增有效資料須另輪登記，不能把此README當繞過限制的授權。

- registration.json：原始計畫、程式、測試、history_index與869項既有保護雜湊。
- registration_sources/、implementation_revision.json：原始程式／測試副本；Windows暫存fixture調整及有效雜湊，均在來源請求前登記。
- initial_hash_audit.json：807項前輪保護、18項有效registration、61項artifact核對。
- sources.json、source_requests.json、source_failure.json、source_status.json：官方欄位、唯一小量請求、錯誤類型、未取得覆蓋及不可重試證據。
- diagnostic_status.json：品質前停止，事件數與收益均未計算。
- history_index_delta.json：以原history_index為父索引的本輪增量，不覆寫舊索引。
- test_results.json：6項新增合成fixture通過，真實來源與策略驗證未執行。
- results.json、summary.csv：基準三成本完整指標、D未計算欄位、損益歸因缺值及所有未完成項目。
- artifact_manifest.json：本輪研究程式、測試、計畫、報告及證據檔SHA256，不含manifest自身。

依賴前輪manifest列出的本機檔案；data/受gitignore管理，fresh clone不保證具備原始輸入。原始基準驗證按雜湊沿用，若不一致，audit將失敗；不得直接重跑舊README整輪命令修補。
'''
    (r.DOC/'README.md').write_text(readme,encoding='utf-8')
    files=[p for p in r.DOC.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    files += [r.PLAN,r.ROOT/'doc/strategy_research_results_20260911_round3.md',
              Path(r.__file__),Path(__file__),r.ROOT/'tests/test_strategy_round3_20260911.py']
    r.dump(r.DOC/'artifact_manifest.json',{'files':[{'path':r.rel(p),'bytes':p.stat().st_size,'sha256':r.sha(p)} for p in sorted(set(files))]})
    print(json.dumps({'status':result['candidate_status'],'pnl_trials':0,'preserved_files':preserved,'artifact_files':len(files)}))


if __name__=='__main__': main()
