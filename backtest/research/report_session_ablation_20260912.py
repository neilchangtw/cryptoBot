"""Independent saved-output audit and combined E/F report. Offline, no candidate replay."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import session_ablation_20260912 as e
import gk_ablation_20260912 as f


def load(name,slip,suffix,dates=None):
    return pd.read_csv(f.OUT/f'{name}_{slip}bp_{suffix}.csv',parse_dates=dates)


def audit(result):
    audits=[]; mark=pd.read_csv(e.ROOT/'data/public_cost_history_20260908/mark_1h_full.csv',usecols=['close']).close.to_numpy()
    for row in result['runs']:
        name,slip=row['name'],row['slip'];t=load(name,slip,'trades',['entry_dt','exit_dt']);q=load(name,slip,'equity',['time'])
        fund=load(name,slip,'funding',['time']);g=load(name,slip,'gates',['decision_ts']);st=load(name,slip,'states')
        assert not row['terminal_positions'],'Explicit terminal audit needed if full period has an open position'
        cash=np.zeros(len(q));flt=np.zeros(len(q));fc=np.zeros(len(q));count=np.zeros(len(q),int)
        times=pd.DatetimeIndex(q.time)
        for tr in t.itertuples():
            a,b=int(tr.entry_bar),int(tr.exit_bar);sgn=1 if tr.side=='L' else -1
            assert tr.margin==200 and abs(tr.qty_exact*tr.entry_exact-4000)<1e-7
            assert tr.bars_held==b-a and tr.entry_dt==times[a] and tr.exit_dt==times[b]
            cash[a]-=tr.fee_exact/2;cash[b]+=tr.pnl+tr.fee_exact/2
            flt[a:b]+=sgn*tr.qty_exact*(mark[a:b]-tr.entry_exact);count[a:b]+=1
            assert ((g.bar==a)&g.side.eq(tr.side)&g.allowed).sum()==1
            p='lp' if tr.side=='L' else 'sp';assert st.set_index('bar').loc[a:b-1,p+'_active'].all()
        for side in ['L','S']:
            part=t[t.side==side].sort_values('entry_bar')
            assert (part.entry_bar.to_numpy()[1:]>part.exit_bar.to_numpy()[:-1]).all()
        for pay in fund.itertuples():
            tr=t.iloc[int(pay.trade_id)];sgn=1 if tr.side=='L' else -1
            expected=-sgn*tr.qty_exact*pay.markPrice*pay.rate if pay.included else 0.
            assert abs(pay.cashflow-expected)<1e-8
            fc[times.get_loc(pay.time)]+=pay.cashflow
        equity=cash.cumsum()+fc.cumsum()+flt
        error=float(np.max(np.abs(equity-q.equity)));assert error<1e-8
        assert count.max()<=2 and abs(t.net.sum()-row['full']['net_pnl'])<1e-7
        assert abs(row['early']['net_pnl']+row['late']['net_pnl']-row['full']['net_pnl'])<1e-7
        loss=float(-t.loc[t.net<0,'net'].sum());v=np.r_[0.,equity]
        mdd=float((np.maximum.accumulate(v)-v).max());w=float((pd.Series(equity)-pd.Series(equity).shift(720)).min())
        for key,value in [('loss_total',loss),('mdd',mdd),('worst30',w)]:assert abs(value-row['full'][key])<1e-7
        audits.append({'name':name,'slip':slip,'status':'PASS','equity_max_abs_error':error,'trades':len(t),'max_simultaneous':int(count.max())})
    prefixes=[]
    for p in result['prefix']:
        t=load(p['name'],0,'trades',['entry_dt','exit_dt']);q=load(p['name'],0,'equity',['time'])
        value=t.loc[t.exit_bar<p['cut'],'net'].sum()+sum(x['funding']-x['entry_fee']+x['unrealized_mark'] for x in p['terminal_positions'])
        error=float(abs(value-q.equity.iloc[p['cut']-1]));assert error<1e-8
        prefixes.append({'name':p['name'],'cut':p['cut'],'open_positions':len(p['terminal_positions']),'identity_error':error,'status':'PASS'})
    unchanged=e.prior_audit.verify_map(f.read(f.DOC/'full_outputs_before_prefix_fix.json')['sha256'])
    reg=f.read(f.DOC/'implementation_revision.json');protected=e.prior_audit.verify_map(reg['protected_sha256'])
    e.prior_audit.verify_map(reg['sha256'])
    result_audit={'runs':audits,'prefix_terminal_identity':prefixes,'full_outputs_unchanged_after_empty_prefix_fix':unchanged,
                  'protected_files':protected,'new_downloads':0,'candidate_replays_in_report':0}
    f.dump(f.DOC/'independent_output_audit.json',result_audit)
    return result_audit


def main():
    e.register();f.register();result=f.read(f.DOC/'results.json');diag=e.read(e.DOC/'diagnostic_status.json')
    assert result['status']=='REJECTED' and diag['status']=='INSUFFICIENT_SAMPLE'
    audited=audit(result)
    baseline=[x for x in result['runs'] if x['name']=='base']
    er={'status':'INSUFFICIENT_SAMPLE','counts':diag['counts'],'quality':diag['quality'],'candidate_pnl_trials':0,
        'candidate_metrics':None,'attribution':None,'baseline_runs':baseline,'noop':e.read(e.DOC/'noop.json'),
        'unperformed':['E_candidate_replay','E_cost_comparison','E_candidate_prefix','E_attribution','advanced_validation','prospective'],
        'stop_reason':'21/7 event clusters below registered 30/15; no candidate returns computed.',
        'later_separate_experiment':'F separately preregistered under the latest user ablation request; E thresholds and stopped status unchanged.'}
    e.dump(e.DOC/'results.json',er)
    evidence={'E_tests':{'run':3,'passed':3,'command':'.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_session_ablation_20260912.py'},
        'F_tests':{'run':5,'passed':5,'unique_additional_tests':2,'includes_E_tests':True,
            'command':'.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_gk_ablation_20260912.py'},
        'integration':{'no_op_cost_scenarios':3,'actual_prefix_checks':8,'real_open_position_prefixes':2,
            'saved_full_period_independent_ledgers':6,'empty_closed_trade_prefix_regression':'PASS'},
        'fixes':[{'case':'Literal regime NA parsed as CSV missing','candidate_trials_before':0,'revision':'E implementation_revision.json'},
            {'case':'First-position prefix has no completed-trade columns','F_main_trials_already_computed':1,
             'full_cost_scenarios_already_computed':3,'revision':'E implementation_revision_02.json and F implementation_revision.json',
             'full_saved_csv_hashes_unchanged':31}]}
    f.dump(f.DOC/'verification.json',evidence)
    events=pd.read_csv(f.OUT/'direct_opportunities_pre_pnl.csv',parse_dates=['decision_ts'])
    candidate=load('no_gk',0,'trades',['entry_dt','exit_dt']);base=load('base',0,'trades',['entry_dt','exit_dt'])
    pair=candidate.merge(base[['side','entry_dt']],on=['side','entry_dt'],how='left',indicator=True)
    new=pair[pair._merge=='left_only'].drop(columns='_merge').copy()
    direct_keys=set(zip(events.side,events.decision_ts))
    new['baseline_direct_opportunity']=[(s,t) in direct_keys for s,t in zip(new.side,new.entry_dt)]
    new['signal_hour_taipei']=(new.entry_dt-pd.Timedelta(hours=1)).dt.hour
    assert not new.signal_hour_taipei.isin([0,1,2,12]).any()
    new.to_csv(f.OUT/'added_trade_attribution_0bp.csv',index=False)
    diagnostics={str(bool(k)):{'n':len(v),'net':float(v.net.sum())} for k,v in new.groupby('baseline_direct_opportunity')}
    f.dump(f.DOC/'new_trade_diagnostics.json',{'by_baseline_direct_event':diagnostics,
        'sample_counting':'All added/cascade trades excluded from independent-event count.'})
    history={'parent':'doc/research_results/20260910_new_information/history_index.json',
        'parent_sha256':e.sha(e.ROOT/'doc/research_results/20260910_new_information/history_index.json'),
        'mechanisms_attempted_this_request':2,'main_return_trials':1,'cost_scenarios':3,'prior_nearby_main_return_trials':7,
        'historical_unseen':False,'scope_disclosure':'E initial plan was one direction; F separately preregistered after E pre-return sample failure under the new user instruction. No E payoff was computed. Report both.',
        'records':[{'id':'E','status':'INSUFFICIENT_SAMPLE','clusters':[21,7],'pnl_trials':0,
            'reopen_basis':'V24 session audit resets state across IS/OOS; lacks V25-D and current cost ledger.'},
            {'id':'F','status':'REJECTED','clusters':[206,74],'pnl_trials':1,
             'reopen_basis':'V16/V17 pure-breakout model lacks current R/V25-D/funding/mark; V35 adds L confirmation instead of unconditional both-side GK removal.'}],
        'related_files_sha256':{n:e.sha(e.ROOT/n) for n in ['backtest/research/v24_session_filter_audit.py','backtest/research/v24_engine.py',
            'backtest/research/v16_r2_skeptical_validation.py','doc/v17_research.md','doc/v35_high_gk_acceleration_research.md']}}
    f.dump(f.DOC/'history_index_delta.json',history)
    by={(x['name'],x['slip']):x for x in result['runs']};b=by['base',0]['full'];c=by['no_gk',0]['full'];parts=result['attribution']['0']['parts']
    table=['|規則|交易數|直接事件群 全期／後期|淨收益$|勝率|PF|mark MDD$|最差30日$|虧損總額$|2／5bp淨收益$|判定|',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|']
    for name,label,n,status in [('base','原策略','—','保留'),('no_gk','F 完整移除GK','206／74','淘汰')]:
        x=by[name,0]['full'];stress='／'.join(f"{by[name,s]['full']['net_pnl']:,.2f}" for s in [2,5])
        table.append(f"|{label}|{x['n']}|{n}|{x['net_pnl']:,.2f}|{x['wr']:.2f}%|{x['pf']:.2f}|{x['mdd']:,.2f}|{x['worst30']:,.2f}|{x['loss_total']:,.2f}|{stress}|{status}|")
    table.append('|E 取消小時限制|未計算|21／7|未計算|—|—|—|—|—|未計算|樣本不足|')
    attribution=result['attribution']['0']
    report=f'''# 2026-09-12 條件移除研究結果

**NO PROMOTION。F完整移除GK淘汰；E取消小時限制樣本不足。** 本次已實際執行F三成本完整狀態回測，沒有找到在同資金／曝險上限下減少虧損且維持收益的方案。維持V14+R+V25-D、L15/S15；不把「未找到」擴大成所有改良都不可能。

{chr(10).join(table)}

固定200U／20倍／每倉4,000U，L/S各一倉，完整17,519根，最後收盤台北2026-09-08 10:00。0bp主表含手續費及funding；MDD是1h mark含浮盈虧，最差30日為720h完整窗口。三成本均完整重放，候選0／2／5bp交易數422／415／411；基準269／269／267。沒有靠事後刪單或簡單扣固定成本代替。

## 結果含意

移除GK後0bp少賺${-attribution['net_delta']:,.2f}（{-attribution['net_delta']/b['net_pnl']*100:.2f}%），虧損總額增加${c['loss_total']-b['loss_total']:,.2f}，MDD增加${c['mdd']-b['mdd']:,.2f}。持倉小時{b['position_hours']}→{c['position_hours']}，最大同時持倉仍為2；增加交易機會造成更差的單筆品質及狀態替代，沒有形成改善。MaxHold {b['mh']}→{c['mh']}，SafeNet {b['sn']}→{c['sn']}。

前期淨收益${by['base',0]['early']['net_pnl']:,.2f}→${by['no_gk',0]['early']['net_pnl']:,.2f}；後期${by['base',0]['late']['net_pnl']:,.2f}→${by['no_gk',0]['late']['net_pnl']:,.2f}。0／2／5bp的收益+5%、虧損下降、MDD、最差30日、前後期收益門檻全數失敗，故停止，不追加L-only／S-only對照找冠軍。完整分期及近期數字在JSON。

## 0bp損益歸因

共同145筆（交易變化0）、移除124筆（其中75筆原獲利）、新增277筆。這些交易數不當作206群之外的新獨立樣本。

- 避開原虧損：+${parts['avoided_losses']:,.2f}。
- 錯失原獲利：−${-parts['missed_winners']:,.2f}。
- 新增交易合計：−${-parts['added_net']:,.2f}。
- 共同單變化及期末倉位差額：$0（浮點尾差<1e−8）。
- 總增量：−${-attribution['net_delta']:,.2f}。

交易PnL增量${attribution['trading_delta']:,.2f}、funding差額+${attribution['funding_delta']:.2f}，加總一致。多付手續費${attribution['closed_fee_delta']:.2f}已含在交易PnL，不能再扣一次。新增單的直接機會／狀態替代分類另外存檔；不是從基準交易表删掉幾笔的静態估算。

## 查重、預登記與範圍

E與F是兩項分開預登記的消融，不是假裝全新指標。E先依一方向計畫執行，品質通過，但只找到26個直接機會、21群／後期7群，所以收益0次。依本次使用者要求移除指標的廣泛授權，再另立F預登記；沒有修改E門檻或評估其收益。F有872個原狀態下只被GK擋下的直接機會，合206群／後期74群，才執行收益評估。本次到這兩項為止。

舊V24 session audit每段重新初始化持倉與風控，沒有現行V25-D/funding/mark；舊V16/V17 pure breakout也不是目前R/V25-D成本模型。V35則是L高GK再加確認。這些適用差異是重新核對的原因，不代表舊研究完全錯誤，更不是用改名／換窗口湊新方向。GK公式和shift仍保留作稽核，F只移除其進場閾值；星期、時段、R、出場與所有風控均未變。

## 驗證與限制

- 當前檔案雜湊支持沿用前輪基準。新包裝器三成本no-op的交易／淨值／funding／gate／完整狀態逐欄一致。
- E測試3項通過；F執行5項通過，其中含E原3項，共5項不同fixture。F與基準合計8個真實資料前綴通過，各含一個有倉位且零已平倉交易的截點，保留入場費、funding及mark浮盈虧，不造假平倉。
- 6個全期情境從保存逐筆/funding獨立重建淨值、風險與持倉上限，最大誤差<1e−8；8個截點資產恆等式同樣通過，全期最後均無持倉。
- 實作遇到兩個核對問題：CSV將字面值NA誤讀缺值，以及零已平倉交易缺schema。原始程式、第一次registration及修訂均保存；第二個問題是在三成本結果已計算後修正，沒有聲稱仍是收益前修訂。修正前後31個完整CSV雜湊完全一致，僅使真正未平倉前綴可以核對。
- {audited['protected_files']}項有效保護檔案核對通過；本次只修改本次新增研究包裝器並記錄修訂，既有未提交工作與正式策略／設定／實盤狀態均保留。0網路請求、0下載；A/B/D未重啟。

基本門檻已失敗，未執行因子對照、額外成交延遲、連續狀態WF、區塊重抽樣、多重比較顯著性、集中度與真正前瞻驗證，均不是PASS。E沒有候選收益／歸因結果，不能說已證明無效；F是效果明顯退步而淘汰，不是資料不足或效果太小。

本研究的歷史已多輪看過，未見資料驗證仍缺；1h收盤代理成交及bar內路徑假設也保留。結論只支持在本輪條件下保留GK，而非保證未來表現。

## 檔案與重現

- [E預登記](strategy_research_plan_20260912.md)、[F預登記](gk_ablation_plan_20260912.md)
- [完整F結果](research_results/20260912_gk_ablation/results.json)、[E樣本停止结果](research_results/20260912_session_ablation/results.json)
- [獨立帳本核對](research_results/20260912_gk_ablation/independent_output_audit.json)
- [離線重現指令與明細](research_results/20260912_gk_ablation/README.md)
'''
    (e.ROOT/'doc/strategy_research_results_20260912.md').write_text(report,encoding='utf-8')
    readme='''# 2026-09-12 離線研究重現

F完整移除GK淘汰（206／74群）；E取消小時限制樣本不足（21／7群，收益0次）。只新增本機研究；不連網、不改正式策略或既有資料。以專案根目錄PowerShell執行。

優先核對保存結果與重建報告，不重跑策略：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/report_session_ablation_20260912.py
```

必要fixture（F包含E三項原測試，合計5項不同測試）：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_gk_ablation_20260912.py
```

只有需要重新產生F結果時，才依序執行以下指令；不會重跑9/10或9/11候選：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/gk_ablation_20260912.py --phase diagnose
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/gk_ablation_20260912.py --phase evaluate
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/report_session_ablation_20260912.py
```

E樣本不足，禁止執行E evaluate。保存E的diagnostic_status／noop作F登記依賴，勿重跑E診斷覆寫原事件登記時間；也不要重跑舊README整輪命令。

- E registration／implementation_revision／implementation_revision_02及registration_sources：初登記、CSV NA解析修正、零已平倉前綴修正，原始來源保留。
- F registration／implementation_revision／registration_sources：F原登記及前綴修正；修正時1主候選、3成本已計算，完整CSV31項雜湊未變。
- diagnostic_status、direct_opportunities_pre_pnl.csv：收益前原狀態事件，無候選未來損益。
- results、summary、noop、independent_output_audit、verification、new_trade_diagnostics、history_index_delta：全部結果、成本／風險、歸因、必要驗證、範圍與試驗數。
- data/gk_ablation_20260912：6情境完整逐筆、每bar狀態、gate、funding、mark淨值、3成本配對及新增單分類。
- data/session_ablation_20260912：E直接機會與三成本基準no-op輸出，沒有E候選交易。
- artifact_manifest：本次程式、測試、计划、報告、JSON及CSV SHA256；不含manifest自身。

需要既有凍結candles、funding/mark、第二/三輪保存資料與登記的研究程式。data受gitignore管理，fresh clone不保證可離線重現。以有效修訂核對自身程式；第一次registration不覆寫。若保護雜湊不一致應停止定位，不用重新跑舊策略掩蓋漂移。
'''
    (f.DOC/'README.md').write_text(readme,encoding='utf-8')
    (e.DOC/'README.md').write_text('# E：取消小時限制\n\n樣本不足：26事件、21／7群，候選收益0次。三成本基準no-op已通過。\n\n完整交付與離線核對指令見[同次F研究README](../20260912_gk_ablation/README.md)。不要執行E evaluate或覆寫收益前診斷紀錄。\n',encoding='utf-8')
    files=[]
    for folder in [e.DOC,f.DOC,e.OUT,f.OUT]:files.extend(p for p in folder.rglob('*') if p.is_file() and p.name!='artifact_manifest.json')
    files += [e.PLAN,f.PLAN,Path(e.__file__),Path(f.__file__),Path(__file__),e.ROOT/'tests/test_session_ablation_20260912.py',
              e.ROOT/'tests/test_gk_ablation_20260912.py',e.ROOT/'doc/strategy_research_results_20260912.md']
    f.dump(f.DOC/'artifact_manifest.json',{'files':[{'path':e.relative(p),'bytes':p.stat().st_size,'sha256':e.sha(p)} for p in sorted(set(files))]})
    print(json.dumps({'status':'NO_PROMOTION','E':'INSUFFICIENT_SAMPLE','F':'REJECTED','independent_ledger_checks':len(audited['runs']),
                     'prefix_checks':len(audited['prefix_terminal_identity']),'artifacts':len(set(files))}))


if __name__=='__main__':main()
