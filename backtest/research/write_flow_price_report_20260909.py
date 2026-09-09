"""獨立核對量差／價格條件，產生可追溯研究報告。"""
from decimal import Decimal
from functools import lru_cache
import csv
import json
import shutil
import pandas as pd
from flow_price_20260909 import ROOT,OUT,SUB,RULES,MAIN,cost

def main():
    result=json.loads((OUT/'results.json').read_text())
    rows={(r['name'],r['slip'],r['historical']):r for r in result['rows']};base=rows['base',0,False]
    labels=pd.read_csv(OUT/'baseline_flow_labels.csv')
    with (SUB/'ETHUSDT_5m_full.csv').open(encoding='utf-8',newline='') as handle:raw=list(csv.DictReader(handle))
    q=pd.read_csv(SUB/'hour_quality.csv').valid_5m_alignment.tolist()
    @lru_cache(None)
    def independent(i,side):
        bars=raw[i*12:(i+1)*12];sign=1 if side=='L' else -1
        v45=sum(Decimal(b['volume']) for b in bars[:9]);v15=sum(Decimal(b['volume']) for b in bars[9:])
        b45=sum(Decimal(b['taker_buy_volume']) for b in bars[:9]);b15=sum(Decimal(b['taker_buy_volume']) for b in bars[9:])
        valid=i>=15 and all(q[i-15:i+1]) and v45>0 and v15>0
        strong=sign*(2*b15-v15)>0 and sign*(2*b15-v15)*v45>sign*(2*b45-v45)*v15
        weak=sign*(Decimal(bars[11]['close'])-Decimal(bars[8]['close']))<=0
        hour=sign*(2*(b45+b15)-(v45+v15))>0 and weak
        return bool(valid),bool(strong),bool(weak),bool(hour)
    for t in labels.itertuples():
        valid,A,P,_=independent(int(t.entry_bar),t.side)
        assert valid==t.valid and A==t.strong and P==t.weak and (A and P)==t.divergent
    event_count=0
    for name in RULES:
        for hist in [False,True]:
            for slip in [0,2,5]:
                ev=pd.read_csv(OUT/f'{name}_{"hist" if hist else "flat"}_{slip}_events.csv')
                for t in ev.itertuples():
                    valid,A,P,H=independent(int(t.bar),t.side)
                    pair=name=='pair' or (name=='pair_long' and t.side=='L') or (name=='pair_short' and t.side=='S')
                    block=A and P if pair else P if name=='price' else A if name=='flow' else H if name=='hour' else False
                    allowed=True if name=='base' else valid and not block
                    assert allowed==t.allowed,(name,t.bar,t.side)
                    event_count+=1
    for p,h in result['hashes'].items():assert cost.sha(ROOT/p)==h
    audit={'decimal_baseline_labels':len(labels),'decimal_gate_events':event_count,'unique_bar_side_checks':independent.cache_info().currsize,
           'status':'PASS','source_hashes_unchanged':True}
    (OUT/'independent_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    names={'base':'原策略','quality':'品質對照','pair':'多空交互過濾','pair_long':'只過濾多單','pair_short':'只過濾空單',
           'price':'只看價格未推進','flow':'只看不平衡增強','hour':'整小時量差＋價格'}
    lines=['# 5m 買賣力道與價格反應研究結果','',
        '**NO PROMOTION；空單規則為 INSUFFICIENT_SAMPLE（證據不足），保留作前瞻驗證假說。** 雙向交互過濾全期收益與勝率增加，但前期收益下降；只過濾多單全期少賺。只過濾空單的歷史前後期與成本壓測均改善，但只有 3 筆原訊號被拒絕，不能當作穩定 edge。','',
        '## 規則與因果','',
        '- 1h 進場不變，只用訊號小時內已完成的 12 根 5m。前九根為 45m，後三根為最後 15m；整小時收盤後才決定是否過濾。沒有讀進場後價格。',
        '- 順向不平衡 F = 方向 × (2×taker 買入量／總量−1)，多單方向 +1，空單 -1。分別加總前 45m 與後 15m 的量再計算，不平均比例。',
        '- A：F15>0 且 F15>F45，代表主動買賣不平衡占比更偏向開單方向，並不代表絕對成交量增加。P：最後 15m 順向價格報酬<=0。A 與 P 同時成立才是主研究標籤 D。',
        '- pair 雙向遇 D 不開；pair_long／pair_short 只套指定方向。price／flow／hour 是預先固定對照，用來拆解價格、不平衡與整小時 TBR 的差別，不是事後新增冠軍候選。',
        '- 被拒絕不產生持倉、冷卻或月開單數；後續訊號、替代交易、原出場、熔斷與最大部位全部沿用完整引擎，不等待或延後原訊號。',
        '- V13／V16／9/8 研究已用過 1h TBR 類資訊；本輪的新定義是同一小時內 45／15m 的變化與價格交互，不能宣稱發現完全獨立的新市場資訊。不能從此標籤推斷主力、吸收或操縱。','',
        '## 資料與驗證','',
        '- 合約 1h 開盤時間 2024-09-08 11:00～2026-09-08 09:00（台北），17,519 根；沿用 210,228 根既有 5m、完整 funding 和 mark。本輪無新增下載。',
        '- 原始來源 manifest、CSV、凍結行情與研究前後 SHA256 全部核對。重算 5m 聚合到 1h 的 OHLC／volume／taker 買量，重現原有 2024-10-29 04／05 時兩根差異。',
        '- 品質 gate 要求當根與前 15 小時皆合格，以及前／後兩段成交量大於零。共有 32 個無效特徵小時，包含最初 15 小時暖機及差異傳播 17 小時；沒有把未知標成正常。',
        f"- 基準交易未知標籤 {result['unknown_baseline_labels']} 筆；quality 在六種成本／部位情境均與 base 交易配對完全相同。本研究沒有用刪除壞資料提高收益。原 1h 差異對更長 GK 窗口的影響仍未由本輪修正。",
        '- 48 組完整回測；6 組原引擎逐欄 parity、6 組品質對照、2 截斷點 × 8 規則的特徵／已平倉交易／事件前綴檢查，全部通過。',
        f"- 8 項單元測試通過，包含六種合成情境 fixture。另以 CSV 原始字串 Decimal 加總、交叉相乘核對 269 筆原交易標籤及全部 {event_count:,} 次 gate 事件，與主程式一致。",'',
        '## 固定 200U 全期結果','',
        '固定 20x、每筆 $4 原成本、funding 入帳；額外 0bp 並非免手續費。TP／BE 使用原收盤市價模型，SafeNet 沿用原模型。淨勝率=net>0，MDD 使用每小時 mark 淨值，非棒內最大回撤。','',
        '|方案|交易數|淨收益 USD|相對原策略|淨勝率|1h mark MDD|MH 筆數|MH 淨損益|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in RULES:
        r=rows[name,0,False]['full'];b=base['full']
        lines.append(f"|{names[name]}|{r['n']}|{r['net_pnl']:,.2f}|{r['net_pnl']-b['net_pnl']:+.2f}|{r['wr']:.2f}%|{r['mdd']:.2f}|{r['mh']}|{r['mh_net']:,.2f}|")
    lines+=['','## 原交易四格診斷','',
        '|順向不平衡增強 A|價格未推進 P|筆數|淨收益|淨勝率|MH|','|---|---|---:|---:|---:|---:|']
    for q in result['buckets']:
        if q['period']=='full' and q['side']=='ALL':lines.append(f"|{'是' if q['strong'] else '否'}|{'是' if q['weak'] else '否'}|{q['n']}|{q['net']:+.2f}|{q['wr']:.2f}%|{q['mh']}|")
    lines+=['','D 組只有 6 筆，5 虧 1 贏，4 筆 MH，合計 -$37.70。這是值得追蹤的描述性線索，不能把 16.67% 勝率當真實穩定勝率。僅價格未推進、但不平衡沒有增強的 75 筆反而 +$3,262.95，說明不能看到進場前最後 15m 沒有延續就全面放棄。','',
        '|D 原交易進場時刻（台北）|方向|F45 %|F15 %|最後15m順向報酬 bp|原出場|原淨收益|',
        '|---|---|---:|---:|---:|---|---:|']
    selected=labels[labels.valid&labels.divergent]
    for t in selected.itertuples():
        lines.append(f'|{t.entry_dt}|{t.side}|{t.F45*100:.3f}|{t.F15*100:.3f}|{t.R15*10000:.3f}|{t.reason_code}|{t.net:+.2f}|')
    lines+=['','TP 標籤不保證淨獲利：原回測先判斷棒內觸及 TP，再以收盤市價成交，可能收回至進場價內。2025-07-30 那筆屬這個既有模型行為，本輪沒有改出場或按出場名稱取勝率。','',
        '## 完整交易變化與收益分解','',
        '|方案|直接拒絕原訊號|移除原交易／新增交易|放棄原贏家|避開原虧損貢獻|新增交易淨收益|合計 Δ收益|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in MAIN:
        a=result['diagnostics'][name];p=a['pair'];d=a['decomposition']
        lines.append(f"|{names[name]}|{a['original_rejects']}|{p['removed']} / {p['new']}|{-d['removed_winner_net']:+.2f}|{-d['removed_loss_net']:+.2f}|{d['added_net']:+.2f}|{rows[name,0,False]['full']['net_pnl']-base['full']['net_pnl']:+.2f}|")
    lines+=['','三個主方案共同交易出場／損益變化皆為零。雙向避開 4 筆原 MH 共 $182.41，但同時放棄一筆 $150.87 贏家；其全期 +$151.88 包含新交易的 +$114.18，不能全解釋成避開 MH。','',
        '空單只拒絕 3 筆原交易，原本共 -$93.91，新增一筆 +$70.16，合計 +$164.07。其中原 MH 只有 2 筆，共 -$87.74。這是三個原事件，不是 267 筆交易共同驗證出過濾效果。','',
        '直接拒絕原訊號與實際候選 gate 拒絕數不同：pair 候選流程拒絕 7 次、基準原交易標籤命中 6 次，因先前拒絕會讓不同時刻重新有進場資格；price／flow／hour 更有狀態連鎖，必須看完整配對。','',
        '## 前後期、最近時段與成本','',
        '前期為 2026-01-01 前，後期為其後，最近為 2026-06-01 起。這些歷史在先前研究已使用，不能稱真正未見 OOS。分期收益依 mark 淨值變化，勝率依該期出場交易計；跨期浮盈與已平倉總額可能不同。','',
        '|主方案|前期 Δ收益|後期 Δ收益|前期 Δ勝率 pp|後期 Δ勝率 pp|全期／後期受影響配對|',
        '|---|---:|---:|---:|---:|---:|']
    for name in MAIN:
        r=rows[name,0,False];a=result['diagnostics'][name]
        lines.append(f"|{names[name]}|{r['early']['net_pnl']-base['early']['net_pnl']:+.2f}|{r['late']['net_pnl']-base['late']['net_pnl']:+.2f}|{r['early']['wr']-base['early']['wr']:+.2f}|{r['late']['wr']-base['late']['wr']:+.2f}|{a['pair']['affected']} / {a['late_affected']}|")
    lines+=['','受影響配對=移除＋新增＋共同單變化，不是獨立事件數。預登記全期／後期至少各 30 筆配對，三方案都不足；空單只有 4／2，距門檻很遠。','',
        '|最近時段固定200U|交易數|淨收益|淨勝率|MH|','|---|---:|---:|---:|---:|']
    for name in ['base']+MAIN:
        r=rows[name,0,False]['recent'];lines.append(f"|{names[name]}|{r['n']}|{r['net_pnl']:.2f}|{r['wr']:.2f}%|{r['mh']}|")
    lines+=['','最近結果是歷史回測反事實，不是已實際獲得的收益；200U 固定研究部位也不同於實盤保證金排程。近期同時改善不能抵銷雙向前期收益下降與樣本不足。','',
        '|Δ淨收益|固定0bp|固定2bp|固定5bp|歷史排程0bp|歷史排程2bp|歷史排程5bp|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in MAIN:
        v=[rows[name,slip,hist]['full']['net_pnl']-rows['base',slip,hist]['full']['net_pnl'] for hist in [False,True] for slip in [0,2,5]]
        lines.append('|'+names[name]+'|'+'|'.join(f'{x:+.2f}' for x in v)+'|')
    lines+=['','空單三種固定部位成本下，全期與前後期收益、淨勝率皆高於 base／quality，且風險門檻通過；其失敗項只有樣本門檻。雙向三種成本的前期收益都下降，只過濾多單全期和前期收益下降。','',
        '## 額外資訊對照與不確定性','',
        'pair 相對三個對照，在 0／2／5bp 下全期及前後期淨收益都較高，全期勝率也不低。但對照會拒絕遠多於 pair 的交易，這只能顯示本次粗糙的一因子／整小時替代規則更差，不能單靠這點證明交互效應穩定，也沒有做同拒絕數隨機對照。','',
        '|主方案|7日區塊 bootstrap Δ收益95%區間|單尾 p|Holm p（六規則）|','|---|---|---:|---:|']
    for name in MAIN:
        a=result['diagnostics'][name];u=a['uncertainty'];lo,hi=u['block7_ci95']
        lines.append(f"|{names[name]}|[{lo:+.2f}, {hi:+.2f}]|{u['one_sided_centered_block_p']:.3f}|{a['holm_p']:.3f}|")
    lines+=['','空單區間下緣數值約 2e-12，應視為零，不是嚴格正的收益下限。只有少數正向差異事件，重抽樣不能創造新的獨立證據；單尾 p=0.068、六規則 Holm p=0.411，也不足以聲稱顯著。多輪已知歷史研究偏誤仍在。','',
        '## 決策與下一步','',
        '- pair：REJECTED，前期收益未過且樣本不足；pair_long：REJECTED，全期／前期收益未過且樣本不足。',
        '- pair_short：INSUFFICIENT_SAMPLE。與前輪現貨同步不同，這輪有收益、勝率、MH 同時改善的歷史線索；但只有 3 筆原事件，應保留固定定義做前瞻驗證，不應直接部署。',
        '- 依預登記，沒有主方案通過基本經濟與樣本門檻，停止後續 WF、10bp、10／20m 鄰域、移除最佳月及單方向對照；這些未跑，不是通過。',
        '- 不改 15m 窗口、不放寬「價格未推進」定義增加樣本，也不改成只放行反向條件救結果。未來若追蹤空單，應記錄所有合格原訊號的完整 5m 量價與 received_ts，以及未過濾基準的完整生命週期；先以 shadow 觀察，不能先避開交易再失去反事實紀錄。',
        '- 本輪僅完成本機歷史研究，未啟動自動蒐集／排程，未修改實盤策略、.env 或 VPS，未 commit／push。','',
        '## 重現','',
        '```powershell',
        '.venv/Scripts/python.exe -m unittest discover -s tests -p test_flow_price.py',
        '.venv/Scripts/python.exe backtest/research/flow_price_20260909.py',
        '.venv/Scripts/python.exe backtest/research/write_flow_price_report_20260909.py','```','',
        '完整本機輸入／交易／funding／淨值／gate 事件：data/flow_price_20260909。可版本管理摘要：doc/research_results/20260909_flow_price。data/ 被 gitignore 排除，跨機重現需既有凍結 1h、5m、mark、funding、manifest 及依賴研究腳本。',
        '', '欄位依 [Binance 公開 Futures Kline 格式](https://github.com/binance/binance-public-data)。規則見 [預登記計畫](flow_price_plan_20260909.md)；既有兩根差異見 [5m 來源稽核](eth_5m_data_audit_20260908.md)。']
    report=ROOT/'doc/flow_price_results_20260909.md';report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    target=ROOT/'doc/research_results/20260909_flow_price';target.mkdir(parents=True,exist_ok=True)
    for name in ['registration.json','results.json','summary.csv','buckets.csv','baseline_flow_labels.csv','independent_audit.json']:
        shutil.copyfile(OUT/name,target/name)
    (target/'README.md').write_text('# 5m 買賣力道與價格反應\n\nNO PROMOTION。空單交互過濾 INSUFFICIENT_SAMPLE：3 筆原事件、全期 +$164.07。\n\n計畫：../../flow_price_plan_20260909.md；報告：../../flow_price_results_20260909.md。完整本機資料未納入 git，見 data/flow_price_20260909。\n',encoding='utf-8')
    print(json.dumps(audit),flush=True)

if __name__=='__main__':main()
