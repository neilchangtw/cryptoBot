"""由已完成的研究結果輸出繁體中文報告，不重跑回測。"""
from pathlib import Path
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/optimization_execution_20260908'
data=json.loads((OUT/'results.json').read_text(encoding='utf-8'))
names={'base':'現行基準','tp25':'S/DOWN TP2.5%','tp30':'S/DOWN TP3%','tp35':'S/DOWN TP3.5%',
       'mh2':'S MH+2h','mh3':'S MH+3h','mh4':'S MH+4h','vol10':'S量比≥1.0','vol15':'S量比≥1.5',
       'adx20':'S ADX≥20＋−DI>＋DI','adx25':'S ADX≥25＋−DI>＋DI',
       'retest1':'S回測確認最多1h','retest3':'S回測確認最多3h','delay1':'S單純延後1h','delay3':'S單純延後3h'}
runs={(r['name'],r['slip'],r['historical']):r for r in data['runs']}
analysis={a['name']:a for a in data['analysis']}
def val(x):return f'{x:,.2f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,row))+' |' for row in rows])

text=['# 策略優化計畫執行結果（2026-09-08）',
'''結論：**NO PROMOTION，正式策略維持V14+R+V25-D。** S/DOWN TP3%保留為值得固定向前觀察的假說，正式判定為INCONCLUSIVE；MH延長及本輪成交量／ADX／回測確認都未通過預先設定的歷史門檻。没有因看到結果再追加條件或改選其他家族。

本次執行15組單項／對照（含基準），固定200U與歷史排程各測0／2／5bp，共90組完整引擎模擬；另做6個家族×3成本的18組連續狀態walk-forward，以及基準＋3個TP候選的10bp壓測。原計畫的組合試驗以兩個參數家族各自合格為前提，MH家族未過，故不執行组合。獨立新策略、訂單流資料及V37不屬本輪加測範圍。

## 1. 資料、成本及風險口徑

- 凍結兩年ETHUSDT 1h窗口：台灣時間開盤2024-09-08 11:00～2026-09-08 09:00，共17,519根；未使用未收盤K線。
- 新取得2,190筆funding（含結算markPrice）與17,519根mark-price K線，下載manifest及固定回測行情SHA256一致。沒有再下載額外歷史。
- 主表一律固定200U／20x，避免加碼日期放大某個候選；歷史200→300→500U另表對照。
- 原引擎4美元／200U每筆全成本不變，開平各分攤一半，額外0／2／5bp是每次市價成交的逆向滑價。不是把實際手續費率宣稱為固定每邊2美元。
- Funding以未四捨五入的進場數量×公開結算markPrice×rate計入現金流；正率多付空收、負率相反。使用的是回測持倉估算收付，並非私人帳戶收入實單對帳。
- 以名義整點對齊小於60秒的funding timestamp偏移。一般市價出場採entry < settlement <= exit；SN於棒內觸發時，不計出場收盤整點的funding。所有進出場邊界另列可能計入／不計入上下界，不把時間次序假裝成已知。
- Funding進入帳本、未進入交易信號或熔斷計數。這沿用現行executor以交易出場PnL更新風控的語意；改成funding-aware熔斷須另做規則研究。
- 回撤以每小時mark-price收盤估值，包含L/S同時持有的浮動損益、費用及funding。仍未涵蓋棒內最深浮虧、私人帳戶入出金、維持保證金與強平，不是最大可能虧損上限。
- 分期PnL是該段首尾淨值差，跨期持倉按mark price估值、funding在實際結算時進帳；不能與舊報告依出場日期歸屬整筆損益的分期數字混用。全期兩端沒有倉位，淨值差等於交易PnL＋funding。

官方依據：[Binance Funding說明](https://www.binance.com/en/support/faq/detail/360033525031)。''',
'## 2. 全部單項結果',
'金額為USD，全部已計入公開funding估算及原交易成本；本表額外滑價0bp。']
b=runs[('base',0,False)]['full']
rows=[]
for name in names:
    r=runs[(name,0,False)]['full']
    rows.append([names[name],r['n'],val(r['net_pnl']),val(r['net_pnl']-b['net_pnl']),val(r['mdd']),val(r['worst30']),r['mh'],r['sn'], '基準' if name=='base' else analysis[name]['status']])
text.append(table(['規則','筆數','PnL','相對基準','淨值MDD','最差30日','MH','SN','判定'],rows))
text.extend(['''MH減少不代表虧損被救回：例如ADX25將MH75→52，卻少賺約2,998美元；回測確認3h回撤較小，但總PnL少約2,090美元。這些取捨不符合本輪「收益改善且風險不明顯惡化」的目標，不能據此宣稱各種方法在所有市場都無效。

量比1.0：基準39筆交易消失，其中28筆原為贏家；新策略另新增31筆。ADX25：93筆消失，其中60筆原為贏家，另新增18筆。這些數字是完整策略的進場序列差異，包含熔斷與冷卻的連帶影響，不全是直接被該指標拒絕的單。

回測確認3h：基準132筆原進場消失、候選新增70筆不同進場時刻；不能把延後的同一個市場事件簡單當成獨立新機會。配對表以方向＋進場時間定義，延後進場在表中列移除／新增。

## 3. 出場候選：收益較好，但未取得升級證據'''])
rows=[]
for name in ['base','tp25','tp30','tp35','mh2','mh3','mh4']:
    r=runs[(name,0,False)]; m=r['full']
    rows.append([names[name],val(r['pre2026']['net_pnl']),val(r['2026']['net_pnl']),val(r['recent']['net_pnl']),val(m['pf']),val(m['wr'])+'%',m['hours']])
text.append(table(['規則','2026前PnL','2026 PnL','6月起PnL','PF','勝率','合計持倉小時'],rows))
text.extend(['''**S/DOWN TP3%：** 全期+516.94美元，2026前+337.53、2026+179.41；PF由2.99→3.06，淨值MDD維持368.53。但勝率63.20%→62.45%、MH75→76。269筆進場相同，29筆結果變動，19筆改善、10筆變差，其中3筆原先淨盈利轉成非正。原75筆MH全部仍是MH，沒有救回原MH。

**TP3.5%：** 比TP3%只多約17.05美元，PF反而較低，SN由2筆變3筆。不能因它總數略高就更改原先TP3%的優先候選。

**S MH+4h：** 全期+201.93美元，但2026前−104.48；淨值MDD368.53→385.58，0bp下雖未超過10%，成本壓測中的風險門檻仍失敗。原MH能配對73筆，其中62仍MH、4 BE、3 TP、3 SN、1 MH-ext，只有4筆轉為淨獲利。實際進場總數268，共通267、新增1、移除2；共通交易27改善、24變差。

**MH+3h**同樣因早期收益退化被淘汰；**MH+2h**全期亦低於基準。沒有把MH候選再切更多regime來救結果。

## 4. 成本與保證金敏感度'''])
rows=[]
for hist in [False,True]:
    for slip in [0,2,5]:
        br=runs[('base',slip,hist)]['full']; cr=runs[('tp30',slip,hist)]['full']
        rows.append(['歷史排程' if hist else '固定200U',slip,val(br['net_pnl']),val(cr['net_pnl']),val(cr['net_pnl']-br['net_pnl']),val(br['mdd']),val(cr['mdd'])])
text.append(table(['保證金','額外bp/成交','基準PnL','TP3% PnL','增量','基準MDD','TP3% MDD'],rows))
stress={r['name']:r for r in data['stress10']}
text.append(f"固定200U額外每次10bp下：基準{val(stress['base']['full']['net_pnl'])}、TP3% {val(stress['tp30']['full']['net_pnl'])}，增量{val(stress['tp30']['full']['net_pnl']-stress['base']['full']['net_pnl'])}。成本沒有吃掉歷史改善，但不能代替樣本外證據。")
text.append('''完整funding使固定200U基準7,934.09→7,926.70（淨支出7.39）；歷史排程7,878.15→7,869.65（淨支出8.50）。這與前一階段只覆蓋248筆、成交價代理funding的−6.85不可直接混用。

## 5. 不確定性與真正向前驗證

7日區塊bootstrap固定2,000次，保留沒有交易的日期，使用每日日終現金流增量；不重建市場路徑，因此是历史差額敏感度估計，不是未來收益保證。14個非基準規則的單尾中心化bootstrap p值作Holm校正。''')
rows=[]
for name in ['tp25','tp30','tp35','mh3','mh4']:
    a=analysis[name]; u=a['bootstrap']; ci=u['block7_ci95']
    rows.append([names[name],a['pair']['affected'],val(u['delta']),f'{val(ci[0])} ～ {val(ci[1])}',f"{u['holm_p']:.3f}"])
text.append(table(['規則','全期受影響筆數','增量','7日區塊95%區間','Holm調整p'],rows))
text.extend(['''TP3%增量區間跨零，不足以拒絕「改善只是樣本波動」的可能。只有29筆實際變動也少於計畫30筆門檻；即使再多1筆，也不自動等於統計可靠。

WF每段兩個月（2025-09～2026-08），只用段前已出場且早於48小時embargo的交易挑同家族參數；以扣funding後的訓練交易PnL排名，不能使用驗證段選參數。可用訓練基準交易至少100筆。

原計畫沒有把「訓練受影響筆數不足」寫成精確數字，本次實作在看結果前採同計畫的30筆門檻，且只有訓練收益高於基準才允許切換。這個保守設定是本輪明確化的研究選擇，不能被解讀為唯一正確的WF設定，也不能事後放寬來製造通過。

每個家族以一條完整引擎時間序列執行，段間不重置占倉、熔斷、冷卻；TP／MH鎖在實際進場時，pending確認事件保留原訊號的規則。L不被強制平倉重開。最後已選規則延用至9/8，不用9月資料重選。'''])
rows=[]
for w in data['walk_forward']:
    ds=w['decisions']; r=w['results'][0]
    rows.append([w['family'],'、'.join(d['winner'] for d in ds),sum(d['sample_valid'] for d in ds),
                 sum(f['delta']>1e-8 and d['sample_valid'] for f,d in zip(r['folds'],ds)),
                 val(sum(f['delta'] for f in r['folds'])),sum(f['affected'] for f in r['folds'])])
text.append(table(['家族','六段選擇','訓練樣本合格段','嚴格改善段','0bp六段增量','WF受影響筆數'],rows))
text.extend([r'''TP家族前五段受影響訓練交易不足，維持基準；最後一段選TP3.5%，但該段沒有產生增量交易結果。因此不能把6段零差額稱為6/6通過，也不能把樣本不足稱為已證明TP無效。MH家族只有最後一段進入MH+3h，7筆受影響、增量約63.18，仍遠不足4/6及30筆門檻。量能、ADX、回測確認及延遲對照未在訓練資料勝過基準，WF維持基準。

歷史資料已在前期研究被反覆看過。即使程式不讀未來K線、WF選擇只看過去，仍不能消除研究者已看過這些行情的選擇偏誤。真正的新樣本只能從規則鎖定後累積。

## 6. 新條件與實作核對

- 量比：signal volume / 前20棒平均，不含signal棒。V27的量比用於分類器，並非這次完全相同的兩個直接gate；本輪只跑登記的1.0／1.5，不追加掃描。
- ADX14：Wilder平滑，以索引1～14的TR、方向移動均值初始化，DX從14開始，ADX以14～27的DX平均初始化；之後逐根遞推。強度與方向分開檢查，使用−DI>＋DI，沒有用ADX本身猜方向。
- 回測確認：訊號時鎖B及ATR14，不當棒確認；之後接近B−0.1ATR並收在B下才以該棒收盤市價進。close>=B先取消。確認但風控不准時取消，不晚點挑更好的進場；等到W仍未确认則逾時。處理pending當棒不另建新事件。
- 單純延遲1／3h是對照，沒有B價格確認；照樣重查交易時段與所有風控。
- 新研究功能關閉時，在2種保證金×3成本下全部逐欄重現原引擎。
- 15個規則各做6,000／11,000／16,000根截斷，共45個已完成交易前綴核對通過；6個WF家族另各做一次16,000根前綴核對，均通過。
- 7個獨立unittest通過：包含下一棒才能確認、突破邊界失效、不可绕過風控、延遲時數、ADX方向與暖機、funding正負／邊界／mark估值，以及持倉途中切policy不更改已鎖TP。
- 首次執行遇到pandas毫秒／微秒時間dtype不同而觸發對齊assert；確認17,519個時間值差異為0後，統一為nanosecond再比對，沒有修改行情數值或放過時間差。

## 7. 實際決策與後續

1. 維持正式策略；沒有任何規則取得部署資格。
2. TP3%保留為優先固定向前觀察的假說，結果標INCONCLUSIVE，不宣稱已通過所有shadow升級門檻。未來至少6個月且至少30筆受影響事件，另看完整成本、mark風險與不確定性；本輪沒有安裝排程或啟動collector。
3. MH延長、量能、ADX、回測確認及單純延遲，本輪REJECTED。回測確認3h雖回撤小，不能為了讓它合格事後改成「只追求低回撤」的研究目標。
4. 不測TP＋MH組合：MH單項沒有合格，不以组合補救失敗家族。
5. 尚未做真正未見的未來交易驗證、私人帳戶funding對帳、毫秒級成交重建、mark棒內最大浮虧／強平模擬；因此不宣稱已證明策略全域最佳或未來一定獲利。

## 檔案與重現

```powershell
.\.venv\Scripts\python.exe backtest/research/execute_optimization_plan_20260908.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_optimization_research.py -v
.\.venv\Scripts\python.exe backtest/research/write_optimization_report_20260908.py
```

data/optimization_execution_20260908/results.json保存全部結果、候選、訓練決策、SHA256與Git HEAD；同目錄保存每個候選的逐筆交易、mark淨值、funding帳本、事件與配對差異。summary.csv提供15組主比較表。

strategy.py、executor.py、原引擎及固定行情輸入的SHA256在研究執行前後一致；未變更.env、VPS或實盤資料。Git提交只包含研究程式、文件與彙總結果，不代表策略部署。研究當時的來源SHA256保留於結果；後續文件狀態更新不回寫歷史雜湊。
'''])
report='\n\n'.join(text).replace('没有','沒有').replace('组合','組合').replace('历史','歷史').replace('确认','確認').replace('绕過','繞過')
target=ROOT/'doc/optimization_execution_20260908.md'; target.write_text(report,encoding='utf-8')
pd.DataFrame([{'name':name,'label':names[name],**runs[(name,0,False)]['full'],
               'status':'BASELINE' if name=='base' else analysis[name]['status']} for name in names]).to_csv(OUT/'summary.csv',index=False,encoding='utf-8-sig')
print(target)
