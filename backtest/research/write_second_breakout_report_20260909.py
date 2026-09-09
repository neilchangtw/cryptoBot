"""再次突破報告、事件去向與原單連結。"""
import json
import shutil
import pandas as pd
from second_breakout_20260909 import ROOT,OUT,RULES,MAIN


def main():
    result=json.loads((OUT/'results.json').read_text())
    rows={(r['name'],r['slip'],r['historical']):r for r in result['rows']}
    b=rows['base',0,False]['full']
    links=[];seedrows=[]
    baseline=pd.read_csv(OUT/'base_flat_0_trades.csv')
    bm={(t.side,t.entry_bar):t for t in baseline.itertuples()}
    for r in result['rows']:
        funnel=r['funnel'];created=funnel.get('created_wait',0)+funnel.get('created_after_exit',0)
        ended=sum(funnel.get(k,0) for k in ['expired','second_entry','cross_blocked','replaced_by_normal'])
        assert created==ended+len(r['pending_at_end']),(r['name'],created,ended)
    for name in RULES[1:]:
        f=pd.read_csv(OUT/f'{name}_flat_0_trades.csv');events=pd.read_csv(OUT/f'{name}_flat_0_events.csv')
        if name.startswith('wait'):
            terminal=events[events.event.isin(['expired','cross_blocked','second_entry'])].set_index(['side','origin'])
            assert terminal.index.is_unique
            for e in events[events.event=='created_wait'].itertuples():
                key=(e.side,e.origin);seed=bm.get(key)
                outcome=terminal.loc[key,'event'] if key in terminal.index else 'pending'
                seedrows.append({'name':name,'side':e.side,'origin_bar':e.origin,'outcome':outcome,
                                 'matched_baseline':seed is not None,'baseline_net':seed.net if seed is not None else None})
        else:
            origins={(t.side,t.entry_bar):t for t in f.itertuples() if t.entry_kind=='normal'}
            retry=f[f.entry_kind=='retry'];assert not retry.duplicated(['side','origin_bar']).any()
            for t in retry.itertuples():
                origin=origins[t.side,t.origin_bar]
                assert t.entry_bar>origin.exit_bar
                assert t.locked_boundary==origin.locked_boundary
                links.append({'name':name,'side':t.side,'origin_bar':t.origin_bar,'original_exit_bar':origin.exit_bar,
                              'original_reason':origin.reason_code,'original_net':origin.net,
                              'retry_entry_bar':t.entry_bar,'retry_net':t.net,'retry_reason':t.reason_code})
    pd.DataFrame(links).to_csv(OUT/'retry_lineage.csv',index=False)
    pd.DataFrame(seedrows).to_csv(OUT/'wait_seed_outcomes.csv',index=False)
    lines=['# 固定界線再次突破研究結果','',
           '**NO PROMOTION：沒有方案通過固定部位「淨收益與淨勝率同升」門檻。** 等待第二次突破會錯失大量原有收益；原單出場後再進場的 24／48h 規則新增交易整體虧損。12h 再進場較接近基準，但全期略少賺、歷史後期未改善且樣本不足。','',
           '## 研究定義','',
           '- 先寫 second_breakout_plan_20260909.md，再執行候選；源碼、原始行情與計畫 SHA256 保存於 registration.json。',
           '- 首次合格訊號鎖定前 15 根 close 的突破界線 B。L 先 close<=B 再於後續小時 close>B；S 反向，不使用同棒 high/low 順序。',
           '- wait：首次不進場，等待 6／12／24h；12h 為主規則。re：原策略照常，原單平倉後下一根起才開始計算 inside→outside，等待 12／24／48h；24h 為主規則。re 不使用持倉期間的收回事件。',
           '- 首次觸發 GK 與原突破背景保留；二次成交時 session／Path R／月上限／冷卻／占倉／熔斷仍須全部允許。原訊號優先、每方向一張等待票；二次交易不能續發第三次交易。',
           '- 原 TP／MH／MFE 等出場不改，regime 按真正進場時重新取值；一小時收盤市價、原成本加額外逆向滑價、完整 funding 與小時末 mark 淨值。最大部位不增加，但交易量與占倉時間可改變。',
           '- outside 為機會對照：原單平倉後同期限，只要安全條件允許且在 B 外即可再進，不要求先收回。不是新的主候選。',
           '- 既有 retest 在收盤收回區間時取消，V36 episode 看前序 TP 群聚，與本輪定義不同。','',
           '## 固定 200U、原成本全期結果','',
           '行情開盤 2024-09-08 11:00～2026-09-08 09:00（UTC+8），17,519 根，暖機 310 根。0 額外 bp 仍扣每筆 $4 原全成本與 funding。','',
           '|規則|交易數|淨收益|相對原策略|淨勝率|每筆淨收益|1h mark MDD|MH|',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in RULES:
        r=rows[name,0,False]['full']
        lines.append(f"|{name}|{r['n']}|{r['net_pnl']:,.2f}|{r['net_pnl']-b['net_pnl']:+,.2f}|{r['wr']:.2f}%|{r['closed_net']/r['n']:.2f}|{r['mdd']:.2f}|{r['mh']}|")
    lines+=['','等待方案不只交易數下降，每筆平均淨收益與全期勝率也低於原策略，不能解釋成單純犧牲頻率換品質。wait12 原策略 269 筆都沒有在相同時刻進場，但它們可能以二次入場時刻出現在候選中；不能將「原時刻移除 170 筆贏單」全稱為完全錯過，須與新增交易一起計算。','',
            '## 原單平倉後的再進場表現','',
            '|規則|真正再進場數|再進場淨收益|再進場勝率|原策略分支淨收益|','|---|---:|---:|---:|---:|']
    for name in ['re12','re24','re48','outside12','outside24','outside48']:
        r=rows[name,0,False]['entry_kinds'];retry=r['retry']
        lines.append(f"|{name}|{retry['n']}|{retry['net']:+.2f}|{retry['wr']:.2f}%|{r['normal']['net']:+.2f}|")
    lines+=['','re24 的 38 筆再進場合計 -$385.10，勝率 47.37%；re48 的 44 筆合計 -$632.76，勝率 45.45%。收回再突破相較 outside 對照少虧，但仍不足以勝過原策略，不能稱為已找到額外 alpha。','',
            're12 的 12 筆再進場 +$70.80；同時原策略被替代的 12 筆淨收益原有 +$80.97，所以全期 -$10.17。其回撤由 $368.53 降至 $335.81、勝率略升，但不滿足使用者要求的收益與勝率一起提高；受影響配對 24 筆也低於預登記 30 筆。不能只呈現再進場正收益。','',
            '## 事件漏斗','',
            '|規則|建立事件|曾收回內側|再次成交|跨回但受限制取消|原訊號取代|到期未成交|期末待定|','|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in RULES[1:]:
        r=rows[name,0,False];f=r['funnel']
        values=[f.get('created_wait',0)+f.get('created_after_exit',0),f.get('returned_inside',0),f.get('second_entry',0),
                f.get('cross_blocked',0),f.get('replaced_by_normal',0),f.get('expired',0),len(r['pending_at_end'])]
        lines.append('|'+name+'|'+'|'.join(str(x) for x in values)+'|')
    lines+=['','「曾收回」是中間狀態，不能與最後去向直接相加。所有事件均满足建立數=成交+受限取消+原訊號取代+到期+期末待定。受到冷卻／session／風控等限制時，首次跨回被取消，不用事後後續好看的跨回取代它。','',
            '## 分期與成本','',
            '2026 年後已被過往研究使用，只稱歷史驗證。以下為相對同成本原策略的差值；pp 為勝率百分點。','',
            '|規則|前期 Δ收益|後期 Δ收益|前期 Δ勝率 pp|後期 Δ勝率 pp|','|---|---:|---:|---:|---:|']
    for name in MAIN:
        r=rows[name,0,False];base=rows['base',0,False]
        lines.append(f"|{name}|{r['early']['net_pnl']-base['early']['net_pnl']:+.2f}|{r['late']['net_pnl']-base['late']['net_pnl']:+.2f}|{r['early']['wr']-base['early']['wr']:+.2f}|{r['late']['wr']-base['late']['wr']:+.2f}|")
    lines+=['','|規則|固定0bp|固定2bp|固定5bp|歷史0bp|歷史2bp|歷史5bp|','|---|---:|---:|---:|---:|---:|---:|']
    for name in MAIN:
        delta=[rows[name,slip,hist]['full']['net_pnl']-rows['base',slip,hist]['full']['net_pnl'] for hist in [False,True] for slip in [0,2,5]]
        lines.append('|'+name+'|'+'|'.join(f'{x:+.2f}' for x in delta)+'|')
    lines+=['','## L/S 分開觀察（不事後改選方向）','',
            '|規則|L 筆數／淨收益／勝率|S 筆數／淨收益／勝率|','|---|---|---|']
    for name in RULES[:7]:
        values=[]
        for side in ['L','S']:
            r=rows[name,0,False]['side'][side];values.append(f"{r['n']} / {r['net']:.2f} / {r['wr']:.2f}%")
        lines.append('|'+name+'|'+'|'.join(values)+'|')
    lines+=['','## 不確定性與停止決策','',
            '|規則|淨收益差 7日區塊bootstrap 95%區間|Holm p|判定|','|---|---|---:|---|']
    for name in MAIN:
        r=result['diagnostics'][name];lo,hi=r['uncertainty']['block7_ci95']
        lines.append(f"|{name}|[{lo:.2f}, {hi:.2f}]|{r['holm_p']:.3f}|{r['status']}|")
    lines+=['','六主候選、2000 次區塊抽樣並校正多重比較。re12 區間約 [-$649,+$610]，不能說它必然較差，但也沒有改善證據；24／48h 鄰域未支持升級。六主候選基本門檻全失敗，依計畫停止 10bp／walk-forward／最佳月排除等後續加測，不改等待期限或放寬冷卻救結果。','',
            '## 驗證與重現','',
            '- base 在 2 部位 × 3 成本共六情境與原引擎所有原始交易欄位一致，淨 funding 帳本亦與既有結果相同。',
            '- 10,000／16,000 根 × 十規則共 20 組截斷重算，已平倉交易及截至當下事件日誌均與完整序列一致。',
            '- 12 個狀態測試通過：不同收盤事件、鎖定 B、空單及等號、期限含界、取消／到期不重選、原訊號優先、不用出場當棒收回、不可再進連鎖、outside 對照、雙向獨立、歷史日誌不被未來狀態改寫。',
            '- 60 組事件流量守恆、帳本期末與逐筆 net 相等、期末無未平倉；再進場逐笔連結其實際原單，B 一致且同原單最多一筆。',
            '- 配對清單顯示原單移除與新增交易的完整損益；wait_seed_outcomes.csv 另列等待種子在原基準是否有相同進場，僅作描述，不拿事後結果做規則。',
            '- 未修改 strategy.py、executor.py、行情或 VPS。仍有既有 1h 來源異常與小時 mark 無法反映棒內最大浮虧的限制；本輪不聲稱已消除這些限制。','',
            '```powershell',
            '.venv/Scripts/python.exe backtest/research/second_breakout_20260909.py',
            '.venv/Scripts/python.exe -m unittest discover -s tests -p test_second_breakout.py',
            '.venv/Scripts/python.exe backtest/research/write_second_breakout_report_20260909.py','```','',
            '完整本機資料：data/second_breakout_20260909/。精簡結果：doc/research_results/20260909_second_breakout/。']
    text='\n'.join(lines).replace('满足','滿足').replace('逐笔','逐筆')+'\n'
    (ROOT/'doc/second_breakout_results_20260909.md').write_text(text,encoding='utf-8')
    dest=ROOT/'doc/research_results/20260909_second_breakout';dest.mkdir(exist_ok=True)
    for name in ['registration.json','results.json','summary.csv','retry_lineage.csv','wait_seed_outcomes.csv']:shutil.copy2(OUT/name,dest/name)
    (dest/'README.md').write_text('# 再次突破研究\n\nNO PROMOTION。計畫及結果见 doc/second_breakout_plan_20260909.md、doc/second_breakout_results_20260909.md。\n',encoding='utf-8')
    print('Report written; event conservation and retry lineage verified')


if __name__=='__main__':main()
