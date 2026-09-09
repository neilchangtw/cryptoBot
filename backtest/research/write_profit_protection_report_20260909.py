"""將已完成研究輸出成可追溯報告。"""
import json
import shutil
import pandas as pd
from profit_protection_20260909 import ROOT,OUT,RULES,cost


def main():
    result=json.loads((OUT/'results.json').read_text())
    rows={(r['name'],r['slip'],r['historical']):r for r in result['rows']}
    base=rows['hourly',0,False]['full']
    lines=['# 1h 進場、5m／15m 獲利保護研究結果','',
           '**NO PROMOTION：六候選均未同時提高淨收益與勝率。** 固定 200U、原成本情境中六者勝率都上升，但淨收益全下降。這否定本輪固定門檻與頻率的方案，不能推論所有獲利保護方式無效。','',
           '先寫入 `doc/profit_protection_plan_20260909.md` 再執行；計畫、研究腳本、行情及正式引擎 SHA256 記錄於 registration.json，執行前後一致。','',
           '## 規則與口徑','',
           '- 固定原 1h 進場訊號、名目與上限，持倉期間才使用已完成 5m／15m 子棒。提前平倉會改變後續占倉、冷卻、熔斷及可成交訊號，因此實際進場清單容許改變。',
           '- tp：只加快 TP；protect：只加快原 L MFE 與 extension 期間 L/S BE；both：預先指定的兩者組合。S 沒有新增 MFE。所有原百分比參數不變。',
           '- 子棒 high/low 累計到當下才可使用，15m 每三根檢查一次。MFE 可在進場後首小時內觸發。原 1h 出場仍保留。觸價後成交價採已完成子棒 close 加逆向滑價，不假設 TP／BE 理論價成交。',
           '- 同一子棒先處理 SafeNet，再 TP／MFE／MH／BE。SafeNet 仍為觸發棒穿透幅度的 25% 模型，不是實際 stop 成交保證；control 與六候選均採首個 5m 觸發棒。',
           '- 原冷卻用出場所屬小時索引計數；只有整點可以進場。MH／extension 時鐘只在整點更新。日／月記帳沿用原訊號小時語意，funding 不回灌交易熔斷。',
           '- entry_dt／exit_dt 是實際子棒收盤時刻；funding 按實際持倉時間加入。bars_held 保留已完成小時時鐘，不可拿來當細粒度實際持倉長度；實際時間應以 exit_dt-entry_dt 計算。',
           '- mark 淨值仍是每小時末估值；小時內平倉會反映在該小時末現金。本研究沒有 5m mark MDD，也不聲稱量出棒內最大風險。',
           '- 使用既有 210,228 根 5m，不下載新資料；兩根來源失配小時停用中途操作、整點回退原 1h。原小時行情未改寫。','',
           '## 固定 200U、原全成本結果','',
           '原全成本為每筆 $4，以下額外滑價 0bp 不代表零成本；另外逐次成交測 2／5bp。','',
           '|規則|筆數|淨收益|相對原策略|淨勝率|1h mark MDD|MH|',
           '|---|---:|---:|---:|---:|---:|---:|']
    for name in RULES:
        r=rows[name,0,False]['full']
        lines.append(f"|{name}|{r['n']}|{r['net_pnl']:,.2f}|{r['net_pnl']-base['net_pnl']:+,.2f}|{r['wr']:.2f}%|{r['mdd']:.2f}|{r['mh']}|")
    lines+=['','hourly 是原策略重現；control 僅細化 SafeNet。兩者淨收益差 $1.87，交易數與勝率相同。候選須同時勝過兩個對照，不能把這 $1.87 當成獲利保護成果。','',
            '## 前後期與成本','',
            '2026 年後區間曾用於先前研究，稱歷史驗證而非真正未見 OOS。以下為候選減原策略：','',
            '|規則|2026年前淨收益差|2026年後淨收益差|前期勝率差 pp|後期勝率差 pp|','|---|---:|---:|---:|---:|']
    for name in RULES[2:]:
        r=rows[name,0,False];b=rows['hourly',0,False]
        lines.append(f"|{name}|{r['early']['net_pnl']-b['early']['net_pnl']:+.2f}|{r['late']['net_pnl']-b['late']['net_pnl']:+.2f}|{r['early']['wr']-b['early']['wr']:+.2f}|{r['late']['wr']-b['late']['wr']:+.2f}|")
    lines+=['','|規則|固定0bp|固定2bp|固定5bp|歷史0bp|歷史2bp|歷史5bp|','|---|---:|---:|---:|---:|---:|---:|']
    for name in RULES[2:]:
        values=[rows[name,slip,hist]['full']['net_pnl']-rows['hourly',slip,hist]['full']['net_pnl'] for hist in [False,True] for slip in [0,2,5]]
        lines.append('|'+name+'|'+'|'.join(f'{x:+.2f}' for x in values)+'|')
    lines+=['','## 勝率變高，為何收益反而降低','',
            '下面只比較相同方向／進場時刻的交易，救回指原 net<=0、候選 net>0；犧牲指反向轉換。贏單／虧單金額變化包含所有共同進場交易，不只跨越零的交易。新進與移除交易另列，不能省略。','',
            '|規則|救回虧單|贏轉未獲利|原贏單損益變化|原虧單損益變化|新增交易|移除交易|','|---|---:|---:|---:|---:|---:|---:|']
    detail=[]
    for name in RULES[2:]:
        r=result['diagnostics'][name];p=r['paired']
        lines.append(f"|{name}|{r['rescued']}|{r['spoiled']}|{r['old_winner_delta']:+.2f}|{r['old_loss_delta']:+.2f}|{p['new']}|{p['removed']}|")
        paired=pd.read_csv(OUT/f'{name}_paired.csv');common=paired[paired['_merge']=='both']
        for group in ['TP','MFE','BE','MH','MHx','SN']:
            q=common[common.reason_code_b==group]
            detail.append({'name':name,'original_reason':group,'n':len(q),'delta':float((q.net_c-q.net_b).sum()),
                           'rescued':int(((q.net_b<=0)&(q.net_c>0)).sum()),'spoiled':int(((q.net_b>0)&(q.net_c<=0)).sum())})
        new=paired.loc[paired['_merge']=='left_only','net_c'].sum()
        removed=paired.loc[paired['_merge']=='right_only','net_b'].sum()
        delta=(common.net_c-common.net_b).sum()+new-removed
        assert abs(delta-(rows[name,0,False]['full']['net_pnl']-base['net_pnl']))<1e-7
    pd.DataFrame(detail).to_csv(OUT/'original_exit_groups.csv',index=False)
    lines+=['','例如 protect5 救回 12 筆、犧牲 6 筆；原虧單改善 $415.40，原贏單卻少 $816.52，再加替代交易影響，全期少 $408.09。這是提前出場的取捨，不是單純把虧單救回就能改善總收益。','',
            'tp15 在固定部位 2bp 情境全期淨收益多 $4.48，但 2026 年後勝率未提高，不符跨成本／前後期雙提升，不能事後只挑有利成本情境。','',
            '## 不確定性與停止決策','',
            '|規則|7日區塊bootstrap淨收益差95%區間|Holm p|判定|','|---|---|---:|---|']
    for name in RULES[2:]:
        r=result['diagnostics'][name];lo,hi=r['uncertainty']['block7_ci95']
        lines.append(f"|{name}|[{lo:.2f}, {hi:.2f}]|{r['holm_p']:.3f}|{r['status']}|")
    lines+=['','2000 次配對區塊 bootstrap，六候選 Holm 校正。區間跨零，不宣稱已證明必然變差；但沒有支持改善的證據，且基本點估計門檻未過。所有候選已在初步經濟門檻淘汰，依預登記不追加 10bp、walk-forward 或門檻掃描。','',
            '## 驗證與重現','',
            '- 原引擎 2 部位排程 × 3 成本共 6 組逐筆時間／原因／Hold／淨收益 parity 通過。',
            '- 10,000／16,000 小時兩個前綴 × 8 規則共 16 組完整已平倉交易一致；指標及子棒陣列由截短來源重建後一致。',
            '- 9 個獨立合成測試通過：首小時 TP／MFE、同棒 SN 優先、TP 優先、15m 等待三根、BE 延長後才啟動、失配回退、禁止非整點進場、未來子棒擾動與小時最高價不提前洩漏。',
            '- 48 組帳本期末與逐筆 net 加總一致、期末沒有遺留持倉；六候選配對分解與全期增量一致。',
            '- 另有 7 個既有成本／funding 研究測試通過。',
            f"- 實際持倉碰到失配小時的最大計數：{max(r['invalid_position_hours'] for r in result['rows'])}。此檢查不宣稱已解決既有 1h 指標的來源不確定性。",
            '- 未改正式策略、executor、環境或 VPS；研究沒有部署。','',
            '```powershell',
            '.venv/Scripts/python.exe backtest/research/profit_protection_20260909.py',
            '.venv/Scripts/python.exe -m unittest discover -s tests -p test_profit_protection.py',
            '.venv/Scripts/python.exe backtest/research/write_profit_protection_report_20260909.py','```','',
            '完整逐筆交易、funding、每小時 mark 淨值及配對結果：`data/profit_protection_20260909/`。精簡可追溯輸出：`doc/research_results/20260909_profit_protection/`。']
    (ROOT/'doc/profit_protection_results_20260909.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dest=ROOT/'doc/research_results/20260909_profit_protection';dest.mkdir(exist_ok=True)
    for name in ['registration.json','results.json','summary.csv','original_exit_groups.csv']:shutil.copy2(OUT/name,dest/name)
    (dest/'README.md').write_text('# 獲利保護研究\n\n計畫與結果見 doc/profit_protection_plan_20260909.md、doc/profit_protection_results_20260909.md。\n完整本機行情與逐筆輸出位於 data/，未包含私人帳戶或憑證。\n',encoding='utf-8')
    print('報告與精簡結果已寫入；配對分解全部一致。')


if __name__=='__main__':main()
