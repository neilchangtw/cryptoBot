"""成本研究的條件式結論與實作可行性。"""
import json
import shutil
import pandas as pd
from execution_cost_20260909 import ROOT,OUT,FEES


def main():
    result=json.loads((OUT/'results.json').read_text(encoding='utf-8'))
    rows={(r['fee'],r['slip'],r['historical']):r for r in result['rows']}
    base=rows[4.,0,False]['full'];live=result['live']
    sources={
        'fees':'https://www.binance.com/en/support/faq/detail/360033544231',
        'account':'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account',
        'fills':'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade'}
    lines=['# 成交成本改善研究結果','',
           '**CONDITIONAL SENSITIVITY ONLY，未提出可部署成交方案。** 若成本確實能降低，完整模擬可同時提高淨收益與勝率；但本機沒有實際佣金／逐筆 fills，尚不能證明能取得這些省費幅度。','',
           '研究先預登記六種成本與驗證方式，再完成 36 組全狀態模擬。原策略、槓桿、部位與風控美元門檻不變。費用改變會回饋熔斷、连敗與替代交易，不是只在事後逐筆加錢。','',
           '## 固定 200U、額外滑價 0bp 的完整結果','',
           '每笔 $4 是原全成本模型，並非帳戶已核實佣金。0bp 是無額外滑價，仍扣表列成本與公開 funding。','',
           '|每筆模型成本|筆數|淨收益|相對原策略|淨勝率|1h mark MDD|MH|',
           '|---|---:|---:|---:|---:|---:|---:|']
    for fee in FEES:
        r=rows[fee,0,False]['full']
        lines.append(f"|${fee:g}|{r['n']}|${r['net_pnl']:,.2f}|${r['net_pnl']-base['net_pnl']:+,.2f}|{r['wr']:.2f}%|${r['mdd']:.2f}|{r['mh']}|")
    lines+=['','費用 $0 是原模型費用完全消失的極端敏感度，不是可取得費率，也不是所有執行改善的上限。每筆 $3 的回測多出 4 筆交易，$2 多出 8 筆，所以不能直接用原 269 筆乘省費金額當完整策略結果。','',
            '## 原交易清單不變的算術對照','',
            '|每筆成本|固定清單淨收益|勝率|原未獲利轉正|原MH轉正|','|---|---:|---:|---:|---:|']
    for r in result['static']:
        lines.append(f"|${r['fee']:g}|${r['net']:,.2f}|{r['wr']:.2f}%|{r['flipped']}|{r['mh_flipped']}|")
    lines+=['','只對原 269 筆而言，把模型每筆 $4 全部拿掉，勝率也僅 63.20%→65.06%，原 75 筆 MH 沒有一筆因此轉正。成本改善可增加淨收益，但不是原 MH 虧損的主要解法。此結論不把其他交易清單或價格改善包含在內。','',
            '## 分期與額外成本壓測','',
            '2026 年後資料先前研究已使用，列作歷史驗證，不宣稱真正未見 OOS。表內為相對同成本／同部位基準的淨收益差及勝率差（百分點）。','',
            '|每筆成本|額外bp|固定全期 Δ收益／Δ勝率|固定前期 Δ收益／Δ勝率|固定後期 Δ收益／Δ勝率|歷史全期 Δ收益／Δ勝率|','|---|---:|---|---|---|---|']
    for fee in FEES[1:]:
        for slip in [0,2,5]:
            values=[]
            for hist,period in [(False,'full'),(False,'early'),(False,'late'),(True,'full')]:
                r=rows[fee,slip,hist][period];b=rows[4.,slip,hist][period]
                values.append(f"{r['net_pnl']-b['net_pnl']:+.2f} / {r['wr']-b['wr']:+.2f}")
            lines.append('|'+f'${fee:g}|{slip}|'+'|'.join(values)+'|')
    lines+=['','模型結果不代表省費一定可行，也不因某一費用情境勝率最高而把它選成最佳費率。降低費用後多開的交易可能使勝率非單調；交易成本便宜不是新 alpha。','',
            '## 35 筆實戰的成交價偏差','',
            f"- 附件實戰 ${live['live_pnl']:.2f}，同期附件回測 ${live['backtest_pnl']:.2f}，實戰多 ${live['net_difference']:.2f}。",
            f"- 勝率實戰 {live['live_wr']:.2f}%，同期回測 {live['backtest_wr']:.2f}%；相同進出場時刻、方向、原因、Hold 與 regime 均核對一致。",
            '- 實戰摘要與同期附件回測的 PnL 比較沿用附件口徑，沒有把公開 funding 加進其中一邊。',
            '- 價格偏差正值為不利、負值為有利。多單買貴／賣便宜為不利；空單相反。價格僅到小數兩位、時間僅策略小時標籤，不能稱精確滑價或延遲測量。','',
            '|價格偏差|平均bp|中位bp|平均絕對bp|不利筆數|移除最大進場偏差後平均bp|','|---|---:|---:|---:|---:|---:|']
    for key,title in [('entry_adverse_bp','進場'),('exit_adverse_bp','出場'),('sum_adverse_bp','兩端近似合計')]:
        r=live[key]
        lines.append(f"|{title}|{r['mean']:.3f}|{r['median']:.3f}|{r['mean_absolute']:.3f}|{r['adverse_n']}|{r['mean_without_largest_entry']:.3f}|")
    lines+=['',f"最大進場偏差是附件 #{live['largest_entry_deviation_trade']}；移除只作穩健描述，不拿來制定過濾規則。合計 bp 是兩端基準百分比的近似加總，並非美元損益分解。",'',
            '沒有證據顯示整體成交價明顯劣於回測；不能因此證明延遲無害，也不能把 $40.21 全歸因於滑價或佣金。缺少 qty、實際費率、commissionAsset 及 exchange fill 時間，故未反推真實費率。','',
            '## 哪些省費方式可以落地','',
            f"1. **既有市價方式的費率折扣**：官方說明 USDⓈ-M 使用 BNB 支付手續費可享標準費用 10% 折扣，需符合其餘額與設定條件。使用者是否已啟用未知；只作用於實際佣金，不能把模型全部 $4 當佣金。[Binance 費用說明]({sources['fees']})",
            f"2. **先核對佣金幣別**：本機 `binance_trade.py:184` 的 get_order_commission 直接加總 commission，未讀取 commissionAsset；`executor.py:730` 再把它當美元從 gross_pnl 扣除。若收到非 USDT 佣金，必須先按實際扣款幣別估值，否則淨利與風控記帳會失真。官方 userTrades 同時提供 commission 與 commissionAsset。[成交欄位]({sources['fills']}#account-trade-list-user_data)",
            '   這是已確認的程式條件缺口，不代表已確認現行帳戶正在用 BNB 或帳本已出錯。本輪未改程式，也未啟用 BNB 抵扣；查佣金失敗時現有 fallback 為 0，仍需原始紀錄才能稽核是否實際發生。',
            f"3. **Maker／限價**：只有掛在簿上、未立即成交的限價單才可能是 maker；市價單是 taker，立即成交的限價單也不能假定 maker。[官方定義]({sources['fees']}) 本機 5m OHLC 沒有排隊／部分成交資訊，不做『觸價即全額成交』的 maker 回測。",
            '4. **縮短送單延遲**：目前沒有 signal-close→order-sent→exchange-fill 的精確事件資料，不能從小時成交摘要估算降低輪詢延遲的收益。','',
            '## 後續需要的最小資料','',
            '- VPS 既有 `data_live/trades.csv`：gross_pnl_usd、commission_usd、net_pnl_usd 與成交價，可先核對實際總佣金；本機不存在此檔。',
            f"- 帳戶 ETHUSDT maker／taker 費率與 BNB 折扣狀態：官方提供 `commissionRate` 與 burn-status 查詢。[帳戶 API]({sources['account']})",
            f"- 逐筆 fills：orderId、side／positionSide、price、qty、commission、commissionAsset、maker、time；若需非 USDT 換算還要該時點匯價。官方 userTrades 的可查歷史有期限，完整六月資料可能需歷史匯出或既有檔案。[成交 API]({sources['fills']}#account-trade-list-user_data)",
            '- 欄位取得後再把真正可省佣金代入；沒有排隊資料仍不宣稱 maker 可成交。這是剩餘證據缺口，不要求新增大量 K 線下載。','',
            '## 驗證與重現','',
            '- 原成本六情境逐筆進出場索引／原因／淨收益 parity 通過。',
            '- 10,000／16,000 根 × 六成本共 12 組截斷前綴一致，截斷時重新計算指標。',
            '- 六個合成測試：價格不變而成本減少、原引擎常數隔離、部位費用縮放、月熔斷重開、零費用仍可虧損、多空價格偏差符號。',
            '- 36 組無期末未平倉，現金與逐筆 net 帳本一致；正式策略與來源 SHA256 不變。',
            '- 尚未用私人 API、VPS 或原始 fills 驗證實際可省成本；結果不能標成策略升級。','',
            '```powershell',
            '.venv/Scripts/python.exe backtest/research/execution_cost_20260909.py',
            '.venv/Scripts/python.exe -m unittest discover -s tests -p test_execution_cost.py',
            '.venv/Scripts/python.exe backtest/research/write_execution_cost_report_20260909.py','```','',
            '本機完整輸出 `data/execution_cost_20260909/`；精簡研究結果 `doc/research_results/20260909_execution_cost/`。']
    text='\n'.join(lines).replace('连敗','連敗').replace('每笔','每筆')+'\n'
    (ROOT/'doc/execution_cost_results_20260909.md').write_text(text,encoding='utf-8')
    (OUT/'sources.json').write_text(json.dumps({'checked_on':'2026-09-09','sources':sources},indent=2),encoding='utf-8')
    dest=ROOT/'doc/research_results/20260909_execution_cost';dest.mkdir(exist_ok=True)
    for name in ['registration.json','results.json','summary.csv','sources.json']:shutil.copy2(OUT/name,dest/name)
    (dest/'README.md').write_text('# 成交成本研究\n\n條件式成本敏感度，非可部署成果。見 doc/execution_cost_results_20260909.md。\n逐筆私人附件衍生資料僅存本機 data/，本目錄為彙總。\n',encoding='utf-8')
    print('Report written')


if __name__=='__main__':main()
