"""Audit persisted research outputs and render the final report without rerunning candidates."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_round2_20260911 as r


def load(name,slip,suffix,dates=None):
    return pd.read_csv(r.OUT/f'{name}_{slip}bp_{suffix}.csv',parse_dates=dates)


def main():
    result=r.read(r.DOC/'results.json'); audits=[]
    for row in result['runs']:
        name,slip=row['name'],row['slip']
        f=load(name,slip,'trades',['entry_dt','exit_dt']); eq=load(name,slip,'equity',['time'])
        ledger=load(name,slip,'funding',['time']); st=load(name,slip,'states'); gates=load(name,slip,'gates',['decision_ts'])
        assert not row['terminal_positions'], 'Audit current full-period case explicitly before reporting'
        cash=np.zeros(len(eq)); floating=np.zeros(len(eq)); counts=np.zeros(len(eq),int)
        times=pd.DatetimeIndex(eq.time)
        for t in f.itertuples():
            a,b=int(t.entry_bar),int(t.exit_bar); sign=1 if t.side=='L' else -1
            assert t.margin==200 and abs(t.qty_exact*t.entry_exact-4000)<1e-7
            assert t.bars_held==b-a and t.entry_dt==times[a] and t.exit_dt==times[b]
            cash[a]-=t.fee_exact/2; cash[b]+=t.pnl+t.fee_exact/2
            counts[a:b]+=1
            # Read frozen mark directly rather than calling the ledger being audited.
            mark=pd.read_csv(r.ROOT/'data/public_cost_history_20260908/mark_1h_full.csv',usecols=['close']).close.to_numpy() if 'mark' not in locals() else mark
            floating[a:b]+=sign*t.qty_exact*(mark[a:b]-t.entry_exact)
            assert ((gates.bar==a)&gates.side.eq(t.side)&gates.allowed).sum()==1
            prefix='lp' if t.side=='L' else 'sp'
            active=st.set_index('bar').loc[a:b-1,prefix+'_active']
            assert active.all()
        assert counts.max()<=2
        for side in ['L','S']:
            part=f[f.side==side].sort_values('entry_bar')
            assert (part.entry_bar.to_numpy()[1:]>part.exit_bar.to_numpy()[:-1]).all()
        funding=np.zeros(len(eq))
        for t in ledger.itertuples():
            if t.included:
                trade=f.iloc[int(t.trade_id)]; sign=1 if trade.side=='L' else -1
                expected=-sign*trade.qty_exact*t.markPrice*t.rate
                assert abs(t.cashflow-expected)<1e-8
            else: assert t.cashflow==0
            funding[times.get_loc(t.time)]+=t.cashflow
        independent=cash.cumsum()+funding.cumsum()+floating
        diff=float(np.max(np.abs(independent-eq.equity)))
        assert diff<1e-8
        assert abs(f.net.sum()-row['full']['net_pnl'])<1e-7
        assert abs(row['early']['net_pnl']+row['late']['net_pnl']-row['full']['net_pnl'])<1e-7
        loss=-f.loc[f.net<0,'net'].sum()
        assert abs(loss-row['full']['loss_total'])<1e-7
        peak=np.maximum.accumulate(np.r_[0.,independent]); mdd=float(np.max(peak-np.r_[0.,independent]))
        assert abs(mdd-row['full']['mdd'])<1e-7
        assert abs(float((pd.Series(independent)-pd.Series(independent).shift(720)).min())-row['full']['worst30'])<1e-7
        audits.append({'name':name,'slip':slip,'status':'PASS','equity_max_abs_error':diff,
                       'trades':len(f),'max_simultaneous':int(counts.max()),'stateful_entries_and_ledger':'PASS'})
    prefixes=[]
    for p in result['prefix']:
        f=load(p['name'],0,'trades',['entry_dt','exit_dt']); eq=load(p['name'],0,'equity',['time'])
        value=f.loc[f.exit_bar<p['cut'],'net'].sum()
        value+=sum(x['funding']-x['entry_fee']+x['unrealized_mark'] for x in p['terminal_positions'])
        error=abs(value-eq.equity.iloc[p['cut']-1]); assert error<1e-8
        prefixes.append({'name':p['name'],'cut':p['cut'],'open_positions':len(p['terminal_positions']),
                         'terminal_account_identity_abs_error':float(error),'status':'PASS'})
    reg=r.read(r.DOC/'registration.json')
    changed=[p for p,h in reg['protected_sha256'].items() if r.cost.sha(r.ROOT/p)!=h]
    assert not changed
    audit={'runs':audits,'prefix_terminal_identity':prefixes,'protected_files_checked':len(reg['protected_sha256']),
           'changed_protected_files':changed,'no_candidate_reruns':True}
    r.dump(r.DOC/'independent_output_audit.json',audit)
    by={(x['name'],x['slip']):x for x in result['runs']}; b=by['base',0]['full']; c=by['spot_prior',0]['full']
    attribution=result['attribution']['0']; parts=attribution['parts']
    text='''# 2026-09-11 第二輪：現貨先行主動量研究結果

**NO PROMOTION，C 淘汰，保留 V14+R+V25-D、L15/S15。** C有足夠樣本且資料檢查通過，但三種成本下均少賺，前後期也退步；不是資料受限、樣本不足或僅效果太小。本輪只選一個新機制，沒有為湊方向數重跑A/B。

## 基準／候選比較

固定200U、20倍、每倉4,000U名目，L/S各最多一倉。沿用凍結17,519根行情，最後收盤台北2026-09-08 10:00；全期暖機310根。淨收益含手續費與funding；MDD為含浮盈虧的1h mark美元回撤，最差30日為完整720h窗口。虧損總額為淨虧損交易的絕對值總和。表列0bp；最後一欄為每次成交額外2／5bp（SafeNet也適用）。

|規則|交易數|直接事件群 全期／後期|淨收益$|淨勝率|PF|mark MDD$|最差30日$|虧損總額$|2／5bp淨收益$|判定|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
'''
    for name,label,events,verdict in [('base','原策略','—','保留'),('spot_prior','C 現貨先行主動量','93／31','淘汰')]:
        x=by[name,0]['full']; stress='／'.join(f"{by[name,s]['full']['net_pnl']:,.2f}" for s in [2,5])
        text+=f"|{label}|{x['n']}|{events}|{x['net_pnl']:,.2f}|{x['wr']:.2f}%|{x['pf']:.2f}|{x['mdd']:,.2f}|{x['worst30']:,.2f}|{x['loss_total']:,.2f}|{stress}|{verdict}|\n"
    text+=f'''
C在0bp少賺 **${b['net_pnl']-c['net_pnl']:,.2f}（{(1-c['net_pnl']/b['net_pnl'])*100:.2f}%）**，虧損總額減少${b['loss_total']-c['loss_total']:,.2f}、MDD減少${b['mdd']-c['mdd']:,.2f}，但未達本輪要求的收益至少+5%。部位小時{b['hours']}→{c['hours']}，最大同時持倉仍為2；風險下降伴隨減少交易及持倉，不能稱作同資金收益改善。2bp候選197筆、5bp198筆；成本會經既有熔斷／冷卻改變後續交易，全部為狀態重放，沒有只扣固定成本。

## 規則與新資訊

訊號K棒開盤t、決策D=t+1h；讀取現貨開盤t−1h、收盤t的完整K棒。`I=(2×taker買入ETH量−總ETH成交量)/總ETH成交量`。方向s=多+1／空−1，原進場條件通過後若`s×I<0`便拒絕，等於0通過。新增資訊為另一市場的先前主動成交方向；9/9現貨同步只用價格，F15/F45與V13/V16用合約主動量，A/B則是存量／分布及bar2出場。本輪不使用它們的參數鄰域冒充新方向。

[Binance官方K線欄位](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)確認總量與taker買入基礎資產量。資料時點、假設接收時點（收盤+5min）及決策時間均存檔；特徵至少提前55min可用的說法是接收假設，歷史received_ts未保存，不能宣稱已證明當年即時可用。原引擎仍以訊號收盤價代理成交，額外滑價同樣施加於基準與候選；沒有新增提前成交。

## 歸因與對照

0bp完整序列：移除118筆原單、新增50筆、共同151筆；直接拒絕原事件108筆，差別来自後續占倉、冷卻與熔斷。下列值均含funding，總增量核對誤差小於$1e−7：

- 避開原虧損：+${parts['avoided_losses']:,.2f}。
- 錯失原獲利：−${-parts['missed_winners']:,.2f}（71筆原獲利單）。
- 新增交易：+${parts['added_net']:,.2f}。
- 共同交易變化：$0；期末持倉差額：$0（浮點尾差<1e−8）。
- 合計：${attribution['net_delta']:,.2f}。

交易PnL增量${attribution['trading_delta']:,.2f}，funding差額+${attribution['funding_delta']:.2f}，合為淨增量。原手續費少付${-attribution['closed_fee_delta']:.2f}，已包含於交易PnL，不重複加回。錯失獲利遠大於避開虧損及替代單貢獻。

前期（2026前）基準${by['base',0]['early']['net_pnl']:,.2f}→候選${by['spot_prior',0]['early']['net_pnl']:,.2f}；後期（2026起）${by['base',0]['late']['net_pnl']:,.2f}→${by['spot_prior',0]['late']['net_pnl']:,.2f}。完整分期、三種成本與逐筆對照在JSON／CSV。

品質only對照与基準逐筆一致；合約同公式對照0bp淨收益${by['futures_control',0]['full']['net_pnl']:,.2f}、MDD${by['futures_control',0]['full']['mdd']:.2f}。C雖勝過這個對照，仍大幅輸給原策略。兩者均不升格為候選。

## 驗證與停止

- 既有13項registration雜湊一致，沿用先前完整基準驗證，不重跑9/10六個候選或A/B。
- 現貨18個原始JSON逐檔SHA256通過，17,519列重建所有欄位一致，17,518項Decimal前小時特徵核對通過。暖機後及原進場的特徵無效率均為0%。新增市場資料0 bytes、API請求0次，未重試ProxyError。
- 候選收益前保存108筆直接原事件，按≤24h群聚為93群、後期31群。未用50筆替代單或168筆總受影響單膨脹樣本。群聚只是保守近似，不保證統計獨立。
- 五個手算fixture通過；新增適配器三種成本no-op逐欄交易及整條淨值與前輪一致，所有基本指標一致。
- 原策略及C各3個真實前綴核對：特徵、gate、已平倉交易、持倉與冷卻／熔斷內部狀態一致。各含一次仍持倉的截點（基準L、C為S）；沒有人工結束倉位。另由已平倉淨值及未平倉費用／funding／mark重建6個截點，誤差均<1e−8。
- 8個保存情境（3基準、3候選、2對照）重新從逐筆／funding重建淨值，最大誤差<1e−8；成本、持倉上限、gate對應、分期加總、MDD、最差30日及虧損總額通過。全期情境最後均無持倉。
- 首次前綴測試遇到舊統計工具對空的未來時段求max錯誤，在尚未計算C收益前修正，只跳過截斷資料不存在的分期摘要。原registration及implementation_revision均保留，公式／切分／資料／門檻未改。
- {len(reg['protected_sha256'])}個既有程式／文件／資料檔案末次雜湊不變；既有未提交工作完整保留。未修改策略、.env、實盤狀態、未下單／部署／commit／push／背景蒐集。

依基本收益門檻失敗停止，未執行鄰域、額外延遲、連續狀態WF、區塊重抽樣、隨機對照、多重比較顯著性或集中度門檻；它們不是PASS。試驗數：本輪一個主方向、3個固定成本情境、2個不得升格的對照；歷史9/10另有6個已看收益主候選，A/B收益試驗仍為0。

## A/B與未完成事項

A維持「資料受限，另有後期樣本不足」（暫42／10群）；B維持「樣本不足」（47／14群）。它們没有新有效資料、可定位缺陷或另定機制，因此不重啟，也不能說已測出收益無效。C是已回測淘汰；本輪沒有「效果太小」或「歷史候選」。

未延長共同历史，沒有9/8截止後的行情驗證；更長共同覆蓋未核實，不能據此斷言免費來源不存在。現有資料已多輪使用，下載日期及重新切分都不會變成真正未見資料。歷史接收／修訂證據、實際成交延遲及鎖定後前瞻驗證仍未完成；本輪沒有啟動蒐集。

## 交付

- [預登記](strategy_research_plan_20260911_round2.md)
- [重現指令與檔案](research_results/20260911_strategy_round2/README.md)
- [完整結果](research_results/20260911_strategy_round2/results.json)
- [保存結果的獨立稽核](research_results/20260911_strategy_round2/independent_output_audit.json)
'''
    report=r.ROOT/'doc/strategy_research_results_20260911_round2.md'; report.write_text(text,encoding='utf-8')
    readme='''# 2026-09-11 第二輪重現

結論：C現貨先行主動量淘汰。0bp淨收益$5,344.22，原策略$7,926.70；事件93／31群充足。A/B未重啟。

在專案根目錄以PowerShell執行（不連網、不重跑舊研究）：

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_strategy_round2_20260911.py
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/strategy_round2_20260911.py --phase diagnose
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/strategy_round2_20260911.py --phase evaluate
.\\.venv\\Scripts\\python.exe -X utf8 backtest/research/audit_report_strategy_round2_20260911.py
```

只想檢查已保存結果並重建報告時，執行最後一條即可；不重跑任何候選。

需要本機已存在的原始輸入：data/maxhold_review_20260908/candles.csv、data/public_cost_history_20260908全部manifest列出的檔案、data/spot_futures_sync_20260909的manifest／CSV／18個raw JSON、data/strategy_study_20260911/base_{0,2,5}bp_{trades,equity}.csv、前輪registration列出的程式／文件。data被gitignore，fresh clone不能保證離線重現。

- registration.json：第一次收益前公式／程式／輸入／807項保護雜湊。implementation_revision.json：僅修正截斷資料空時段摘要，C收益計算前記錄；原登記不覆寫。
- initial_hash_audit.json：先前13項registration核對與已讀JSON雜湊。
- sources.json：API欄位、原JSON逐檔雜湊、重建／Decimal核對、接收與修訂限制；本輪沒有發API請求。
- pre_pnl_counts.json、diagnostic_status.json：收益前品質及108個直接事件、93／31群。
- results.json、summary.csv：三成本基準與主候選、兩個0bp對照、分期／完整風險指標／歸因／no-op／前綴／期末倉位及停止狀態。
- independent_output_audit.json：不使用回測引擎重新計算收益，從保存逐筆與funding重建8條淨值並核對6個截點資產。
- data/strategy_round2_20260911/：完整特徵、原事件、8情境逐筆／mark淨值／funding／gate／每bar策略狀態、3成本配對歸因。所有全期情境末倉為空；真實前綴測試保留L/S未平倉倉位。
- artifact_manifest.json：新程式、測試、報告、JSON／CSV的交付雜湊與大小；產生manifest時不包含它自己。

重跑僅覆寫第二輪自己產出的結果；舊輸入、前輪資料、原registration及事件第一次記錄時間不覆寫。不得把本檔重現命令理解成允許修改正式策略、下載新資料或部署。
'''
    (r.DOC/'README.md').write_text(readme,encoding='utf-8')
    artifacts=[r.PLAN,Path(r.__file__),Path(__file__),r.ROOT/'tests/test_strategy_round2_20260911.py',report]
    artifacts += [p for folder in [r.DOC,r.OUT] for p in folder.rglob('*') if p.is_file() and p.name!='artifact_manifest.json']
    r.dump(r.DOC/'artifact_manifest.json',{'files':[{'path':r.rel(p),'bytes':p.stat().st_size,'sha256':r.cost.sha(p)} for p in artifacts]})
    # Reload report and JSON exports, then verify paths and final numbers.
    assert '5,344.22' in report.read_text(encoding='utf-8')
    assert r.read(r.DOC/'results.json')['status']=='REJECTED'
    assert all((r.ROOT/x['path']).exists() and r.cost.sha(r.ROOT/x['path'])==x['sha256'] for x in r.read(r.DOC/'artifact_manifest.json')['files'])
    print(json.dumps({'report':str(report),'audited_scenarios':len(audits),'exact_prefix_identities':len(prefixes),
                      'changed_protected_files':changed,'new_artifact_bytes':sum(x['bytes'] for x in r.read(r.DOC/'artifact_manifest.json')['files'])}))


if __name__=='__main__': main()
