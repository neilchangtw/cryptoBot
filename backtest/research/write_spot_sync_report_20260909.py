"""由結果產生報告，並用 Decimal 原始價格獨立核對同步判斷。"""
from decimal import Decimal
import json
import shutil
import pandas as pd
from spot_futures_sync_20260909 import OUT,ROOT,RULES,cost

def main():
    result=json.loads((OUT/'results.json').read_text());manifest=json.loads((OUT/'manifest.json').read_text())
    rows={(r['name'],r['slip'],r['historical']):r for r in result['rows']};b=rows['base',0,False]
    labels=pd.read_csv(OUT/'baseline_spot_labels.csv');raw=[]
    for r in manifest['requests']:raw.extend(json.loads((OUT/r['file']).read_text()))
    closes=[Decimal(r[4]) for r in raw];independent={'L':[],'S':[]}
    for i,c in enumerate(closes):
        independent['L'].append(i>=15 and c>max(closes[i-15:i]))
        independent['S'].append(i>=15 and c<min(closes[i-15:i]))
    for t in labels.itertuples():assert independent[t.side][int(t.entry_bar)]==t.spot_sync
    recent=labels[labels.entry_dt>='2026-06-01']
    assert len(recent)==35 and recent.spot_sync.all()
    for name in RULES:
        trades=pd.read_csv(OUT/f'{name}_flat_0_trades.csv')
        expected=labels[labels.exit_dt>='2026-06-01'][trades.columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(trades[trades.exit_dt>='2026-06-01'].reset_index(drop=True),expected)
        ev=pd.read_csv(OUT/f'{name}_flat_0_events.csv')
        for t in ev.itertuples():
            check=name=='both' or (name=='long' and t.side=='L') or (name=='short' and t.side=='S')
            assert t.allowed==(independent[t.side][int(t.bar)] if check else True)
    audit={'decimal_baseline_labels':len(labels),'decimal_full_rule_events':'PASS',
        'baseline_mh_sync':int(labels.loc[labels.reason_code=='MH','spot_sync'].sum()),
        'all_baseline_mh':int(labels.reason_code.eq('MH').sum()),'unaltered_inputs':True}
    for p,h in result['hashes'].items():assert cost.sha(ROOT/p)==h
    (OUT/'independent_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    lines=['# ETH 現貨／合約同步突破研究結果','',
        '**NO PROMOTION：本輪固定 15h 同步突破條件幾乎沒有新增辨識能力，不能改善 MaxHold。** 原策略 269 筆中 266 筆（98.88%）已有現貨同步突破；75 筆 MH 全部同步。只對多單套用的全期小幅收益增加，來自兩筆原交易被替換，後期變差且樣本不足。','',
        '## 定義與資料','',
        '- 合約符合原 V14+R+V25-D 進場條件時，L 要求現貨 close 嚴格大於現貨自己前 15 根 close 最大值，S 反向。當根不算入界線，不能用合約價代替現貨。兩市場都等 1h 收盤。',
        '- base 原策略；both 雙向過濾為主假說；long／short 是預先固定方向診斷，不改出場或增加部位。不等待現貨確認，被拒絕當下不開單；往後若原策略重新發出訊號仍可進場。',
        '- 行情開盤時間：2024-09-08 11:00～2026-09-08 09:00，台北時間，最後收盤 09-08 10:00。合約與現貨各 17,519 根，原引擎暖機 310 根。',
        f"- Binance 公開現貨 API 下載 {len(manifest['requests'])} 次，原始回應 {manifest['download_bytes']:,} bytes（{manifest['download_bytes']/1e6:.2f} MB），CSV {(OUT/'spot_1h.csv').stat().st_size/1e6:.2f} MB，下載程式耗時 {manifest['wall_seconds']:.2f} 秒（不含權限等待與環境啟動）。",
        '- 首次沙箱代理連線失敗；透過標準網路權限審查後成功。無 API key，不下單。原始 JSON、請求參數與 SHA256 均留本機。',
        '- 已驗證開／收盤毫秒、UTC→台北、逐小時完全對齊、無重複缺漏、OHLC／成交量合法及全部已收盤；沒有補值。先存預登記，再產生候選結果。','',
        '## 固定部位完整回測','',
        '全程 200U 保證金、20x、每筆 $4 原成本、真實 funding；TP／BE 收盤市價假設，SafeNet 沿用原模型。表中額外滑價 0bp 並非零成本。勝率使用扣 funding 的 net>0。MDD 為 1h mark 淨值，非棒內極端回撤。','',
        '|方案|交易數|淨收益 USD|相對基準|淨勝率|1h mark MDD|MH 筆數|MH 淨損益|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    names={'base':'原策略','both':'雙向同步','long':'只過濾多單','short':'只過濾空單'}
    for name in RULES:
        r=rows[name,0,False]['full'];base=b['full']
        lines.append(f"|{names[name]}|{r['n']}|{r['net_pnl']:,.2f}|{r['net_pnl']-base['net_pnl']:+.2f}|{r['wr']:.2f}%|{r['mdd']:.2f}|{r['mh']}|{r['mh_net']:,.2f}|")
    lines+=['','交易數都仍是 269，是因為拒絕後出現其他原策略訊號可成交；不能以從原清單直接刪單的方式估計收益。所有情境期末皆無持倉，完整計入占倉、冷卻、月上限、熔斷與新增交易。','',
        '## 原交易的同步分類','',
        '|方向|同步|筆數|淨收益|淨勝率|MH|','|---|---|---:|---:|---:|---:|']
    for q in result['buckets']:
        if q['period']=='full':lines.append(f"|{q['side']}|{'是' if q['sync'] else '否'}|{q['n']}|{q['net']:+.2f}|{q['wr']:.2f}%|{q['mh']}|")
    lines+=['','不同步只有 3 筆，合計仍 +$40.07；其中 1 筆贏家 +$60.30、2 筆輸家共 -$20.23。這種樣本量不能用 33.33% 勝率推論穩定差異。沒有任何 MH 落在不同步組。','',
        '|不同步原交易進場（台北實際收盤時刻）|方向|現貨收盤|現貨前 15h 界線|沿交易方向離界線 bp|原出場|原淨收益|',
        '|---|---|---:|---:|---:|---|---:|']
    for t in labels[~labels.spot_sync].itertuples():
        lines.append(f'|{t.entry_dt}|{t.side}|{t.spot_close:.2f}|{t.spot_boundary:.2f}|{t.spot_distance_bp:.3f}|{t.reason_code}|{t.net:+.2f}|')
    lines+=['','三筆都只差不到 2.3bp，屬本資料下接近界線的少數事件；沒有根據稱為操縱、主力出貨或普遍假突破。使用原始 JSON 字串的 Decimal 獨立重算，269 個標籤與全部四組基準成本 gate 事件皆一致，排除浮點比較造成分類。','',
        '## 收益差異分解','',
        '|方案|移除原交易|新增交易|放棄原贏家收益|避開原虧損的收益貢獻|新增交易淨收益|共同交易變化|合計差異|',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,a in result['diagnostics'].items():
        p=a['pair'];d=a['decomposition'];delta=rows[name,0,False]['full']['net_pnl']-b['full']['net_pnl']
        lines.append(f"|{names[name]}|{p['removed']}|{p['new']}|{-d['removed_winner_net']:+.2f}|{-d['removed_loss_net']:+.2f}|{d['added_net']:+.2f}|{d['common_delta']:+.2f}|{delta:+.2f}|")
    lines+=['','雙向：避開 $20.23 虧損，但放棄 $60.30 贏家；新交易只補回 $9.86，合計 -$30.20。多單：避開兩筆合計 $20.23 虧損，新交易合計 -$8.57，所以僅 +$11.66；完全不是改善 MH。','',
        '## 分期、成本與樣本','',
        '前期：2026-01-01 前；後期：2026-01-01 起。既有研究用過這些歷史，不能稱真正未見 OOS。期間損益以 mark 淨值變化計，勝率按期內出場交易計；跨期浮盈使期間已平倉總額與淨值變化可能不同。','',
        '|方案|前期 Δ收益|後期 Δ收益|前期 Δ勝率 pp|後期 Δ勝率 pp|全期／後期受影響交易|',
        '|---|---:|---:|---:|---:|---:|']
    for name,a in result['diagnostics'].items():
        r=rows[name,0,False]
        lines.append(f"|{names[name]}|{r['early']['net_pnl']-b['early']['net_pnl']:+.2f}|{r['late']['net_pnl']-b['late']['net_pnl']:+.2f}|{r['early']['wr']-b['early']['wr']:+.2f}|{r['late']['wr']-b['late']['wr']:+.2f}|{a['pair']['affected']} / {a['late_affected']}|")
    lines+=['','「受影響」為移除＋新增＋改變共同單的配對計數，不代表同樣多的獨立市場事件。主規則只有三筆原進場被拒絕，不能把 6 筆配對當六個獨立原訊號。','',
        '2026-06-01 起對應的 35 筆基準回測進場全部同步，四方案交易與結果相同；這是歷史回測重現，不是新增 35 筆前瞻實盤驗證。固定 200U 此段淨收益 $543.66，並非實際歷史保證金／帳戶損益。','',
        '|方案 Δ淨收益|固定0bp|固定2bp|固定5bp|歷史排程0bp|歷史排程2bp|歷史排程5bp|',
        '|---|---:|---:|---:|---:|---:|---:|']
    for name in RULES[1:]:
        values=[rows[name,slip,hist]['full']['net_pnl']-rows['base',slip,hist]['full']['net_pnl'] for hist in [False,True] for slip in [0,2,5]]
        lines.append('|'+names[name]+'|'+'|'.join(f'{v:+.2f}' for v in values)+'|')
    lines+=['','三方案均未通過預登記收益／淨勝率前後期同升及樣本門檻。多單全期提升約 0.15%，但後期 -$21.09，後期 MDD $335.81→$356.90；全期 MDD 相同不代表每段風險相同。','',
        '|方案|7 日區塊 bootstrap 全期 Δ收益 95% 區間|Holm p（三候選）|','|---|---|---:|']
    for name,a in result['diagnostics'].items():
        ci=a['uncertainty']['block7_ci95'];lines.append(f"|{names[name]}|[{ci[0]:+.2f}, {ci[1]:+.2f}]|{a['holm_p']:.3f}|")
    lines+=['','2000 次 bootstrap，三者區間都跨零；稀少差異事件使推論尤其有限，不能視為排除所有微小優勢。全期多次歷史研究的偏誤也不能由本輪三候選校正消除。','',
        '## 驗證與研究決策','',
        f"- 24 組全狀態回測；6 組基準逐欄與既有成本帳本 parity；2 個截斷點 × 4 規則的指標、已平倉交易及 gate 事件前綴測試，全部通過（結果檔 {len(result['checks'])} 項）。",
        '- 7 項單元測試：現貨自己界線與嚴格比較、方向隔離、未來污染、缺漏／重複／錯位、REST 時間與 OHLC 合法性、前 15 根界線及三筆接近界線的原始價格 fixture。另 5 項共用進場與 6 項成本測試通過，合計 18 項。',
        '- 原始行情 Decimal 獨立核對通過；研究前後策略、引擎、成本資料與凍結行情 SHA256 未變。',
        '- 基本門檻已失敗，依預登記停止，未執行六段 WF、10bp、14/16 窗口或移除最佳月後續測試；不把未跑的檢查標示為通過。',
        '- 建議不加這個條件。這只否定本次「同時突破各自前 15h 收盤界線」作為可採用改善，不代表現貨資訊、棒內先後或成交量差異永遠沒有價值；那些是新假說，不能沿本結果事後調門檻。',
        '- 現貨歷史資料可重用。若後續新假說歷史通過，仍需 received_ts 與前瞻 shadow，確認現貨在原整點 +10 秒決策前已可取得。',
        '- 沒有修改實盤策略、環境設定或 VPS。本輪研究尚未 commit／push。','',
        '## 重現與來源','',
        '```powershell',
        '.venv/Scripts/python.exe backtest/research/fetch_spot_sync_20260909.py',
        '.venv/Scripts/python.exe -m unittest discover -s tests -p test_spot_futures_sync.py',
        '.venv/Scripts/python.exe backtest/research/spot_futures_sync_20260909.py',
        '.venv/Scripts/python.exe backtest/research/write_spot_sync_report_20260909.py','```','',
        '下載器已驗證存在的原始頁面可重用；本機完整輸入／交易／淨值／funding／事件位於 data/spot_futures_sync_20260909。可版本管理的摘要位於 doc/research_results/20260909_spot_sync。data/ 被 gitignore 排除；其他電腦重現尚需既有凍結合約、funding、mark 與依賴腳本。',
        '', '[Binance 現貨 Kline API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)；[公開資料及歷史修訂說明](https://github.com/binance/binance-public-data)。原始歷史檔無當年接收時間，今天下載的歷史價格不等於保存當時實際接收版本。']
    (ROOT/'doc/spot_futures_sync_results_20260909.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    target=ROOT/'doc/research_results/20260909_spot_sync';target.mkdir(parents=True,exist_ok=True)
    for name in ['registration.json','results.json','summary.csv','buckets.csv','baseline_spot_labels.csv','manifest.json','independent_audit.json']:
        shutil.copyfile(OUT/name,target/name)
    (target/'README.md').write_text('# 現貨／合約同步突破研究\n\nNO PROMOTION。266/269 原交易已有現貨同步，75/75 MH 全部同步。\n\n計畫：../../spot_futures_sync_plan_20260909.md；結果：../../spot_futures_sync_results_20260909.md。完整本機行情與交易帳本位於 data/spot_futures_sync_20260909，未納入 git。\n',encoding='utf-8')
    print(json.dumps(audit),flush=True)

if __name__=='__main__':main()
