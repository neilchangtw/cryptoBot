"""Build the Chinese report and compact, reload-verified evidence package."""
from pathlib import Path
import hashlib
import json
import shutil
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/new_information_20260910'
OUT = ROOT / 'doc/research_results/20260910_new_information'
REPORT = ROOT / 'doc/new_information_results_20260910.md'
NAMES = {'base': '原策略', 'oi_quality': 'OI 僅品質', 'oi_both': 'OI 雙向', 'oi_long': 'OI 僅多單',
         'oi_short': 'OI 僅空單', 'premium_quality': '溢價僅品質', 'premium_both': '溢價交互雙向',
         'premium_long': '溢價交互僅多單', 'premium_short': '溢價交互僅空單',
         'premium_level': '溢價僅偏離（對照）', 'premium_change': '溢價僅變化（對照）'}


def money(n):
    return '—' if n is None else f'{n:,.2f}'


def table(headers, rows):
    return ['|' + '|'.join(headers) + '|', '|' + '|'.join(['---'] * len(headers)) + '|'] + [
        '|' + '|'.join(str(v) for v in row) + '|' for row in rows]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = {family: json.loads((DATA / f'{family}_results.json').read_text()) for family in ['oi', 'premium']}
    audits = {family: json.loads((DATA / f'{family}_independent_audit.json').read_text()) for family in results}
    assert all(a['status'] == 'PASS' for a in audits.values())
    assert all(not r['advanced_pending'] for r in results.values()), 'Finish advanced validation before closing task'
    primary = [f + '_' + s for f in results for s in ['both', 'long', 'short']]
    diagnostics = {n: results[n.split('_')[0]]['diagnostics'][n] for n in primary}
    peak = 0
    for rank, n in enumerate(sorted(primary, key=lambda n: diagnostics[n]['uncertainty7']['p'])):
        peak = max(peak, diagnostics[n]['uncertainty7']['p'] * (len(primary) - rank))
        diagnostics[n]['holm_p_6_main'] = min(1, peak)
    peak = 0
    for rank, n in enumerate(sorted(primary, key=lambda n: diagnostics[n]['random_control']['p_random_at_least_actual'])):
        peak = max(peak, diagnostics[n]['random_control']['p_random_at_least_actual'] * (len(primary) - rank))
        diagnostics[n]['holm_random_p_6_main'] = min(1, peak)
    lookup = {}
    for r in results.values():
        for row in r['rows']:
            key = row['name'], row['slip'], row['historical']
            if key in lookup:
                assert lookup[key] == row
            lookup[key] = row
    for family, result in results.items():
        for p, expected in result['hashes'].items():
            assert hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == expected, p
    combined = {'status': 'NO_PROMOTION', 'main_candidate_count': 6, 'diagnostic_single_factor_count': 2,
                'saved_scenarios': sum(r['scenario_count'] for r in results.values()),
                'unique_scenarios': len(lookup), 'random_scenarios': 600,
                'diagnostics': diagnostics, 'results': results, 'independent_audits': audits,
                'true_unseen_data': False, 'historical_receipts_verified': False}
    (OUT / 'results.json').write_text(json.dumps(combined, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    summary = pd.DataFrame([{'name': r['name'], 'slip': r['slip'], 'historical': r['historical'], **r['full']}
                            for r in lookup.values()])
    summary.to_csv(OUT / 'summary.csv', index=False)
    for family in results:
        for suffix in ['registration.json', 'data_audit.json', 'pre_pnl_counts.json', 'independent_audit.json']:
            shutil.copyfile(DATA / f'{family}_{suffix}', OUT / f'{family}_{suffix}')
        for name in primary:
            if name.startswith(family + '_'):
                for suffix in ['matched_random.csv', 'original_affected.csv', 'monthly_delta.csv']:
                    shutil.copyfile(DATA / f'{name}_{suffix}', OUT / f'{name}_{suffix}')
    shutil.copyfile(DATA / 'download_manifest.json', OUT / 'download_manifest.json')
    source = json.loads((DATA / 'download_manifest.json').read_text())
    total_bytes = sum(x.get('bytes', 0) for x in source.values())
    base = lookup['base', 0, False]['full']
    lines = ['# 2026-09-10 新資訊研究結果：OI 與永續溢價', '',
             '**結論：NO PROMOTION。六個預登記主候選全部 REJECTED，維持 V14+R+V25-D、L15/S15。**', '',
             '本輪不是沒有免費資料：Binance 官方 daily metrics 的 OI 長歷史實際可取得，舊 V19/V36「只剩近期 API」的資料限制已被本次下載查證更新。但本輪固定定義的 OI 與溢價過濾都降低淨收益，不能採用。', '',
             '沒有修改實盤策略、executor、.env、VPS；沒有下單、commit 或 push。先前未提交的 AGENTS、9/4研究與viewer工作保留。', '',
             '## 比較口徑', '',
             '- ETHUSDT USD-M 永續合約，1h；台北 K 棒開盤 2024-09-08 11:00～2026-09-08 09:00，共17,519根，最後收盤/決策2026-09-08 10:00。前310根為原引擎暖機。',
             '- 主比較每筆200U保證金、20倍、4,000U名目；原每筆$4成本加歷史funding，realistic=True。表中淨收益為策略累計美元PnL，不是帳戶報酬率；改善%分母為基準淨收益$7,926.70。淨勝率為net>0的已平倉筆數/全部已平倉筆數。',
             '- 1h mark MDD為策略損益淨值回撤美元，不是逐筆tick回撤或錢包強平風險。最差30日使用完整720h窗口。原引擎日/月/連敗風控由交易PnL更新，funding另入帳，保留executor現行語意。',
             '- 表一為每次成交額外0bp；另完整重跑2/5bp及歷史200→300→500U排程。TP/BE等用收盤市價，SafeNet沿用原stop穿透模型，沒有把限價觸價當成交。',
             '- **這兩年資料已用於多輪研究；所有前後期、重抽樣與本輪結果均非真正未見資料。** 新特徵來自現在下載的封存版本，歷史received_ts及未修訂性尚未證實。', '',
             '## 基準及六個主候選', '']
    rows = []
    for n in ['base'] + primary:
        m = lookup[n, 0, False]['full']
        diag = diagnostics.get(n)
        rows.append([NAMES[n], m['n'], money(m['net_pnl']), money(m['net_pnl'] - base['net_pnl']),
                     f"{m['wr']:.2f}%", money(m['mdd']), money(m['worst30']), m['mh'], m['sn'],
                     '保留基準' if not diag else diag['status']])
    lines += table(['規則', '交易數', '淨收益$', '增量$', '淨勝率', 'mark MDD$', '最差30日$', 'MH', 'SN', '判定'], rows)
    lines += ['', '實用門檻事前固定為淨收益至少+5%、淨勝率至少+1百分點、MDD及最差30日不惡化。六個候選的全期收益全部下降，沒有事後放寬門檻。OI雙向提高勝率但少賺29.43%，正是本輪明確排除的取捨。', '',
              '## 新資訊、公式與查重', '',
              '完整去重矩陣與排名見 [預登記計畫](new_information_plan_20260910.md)，101份文件/結果的SHA256、章節、程式引用見 [歷史索引](research_results/20260910_new_information/history_index.json)。這是文件盤點與相關定義精讀，不宣稱重新跑完所有歷史研究。', '',
              '1. **OI**：决策D=t+1h，取q=D−15min的未平倉數，`OI(q)/OI(q−60min)−1 ≤ 0`時拒絕進場；中間13個5m點都須有效。只用合約數，不用含價格變動的持倉價值。分雙向、僅多、僅空三個事前候選。這是存量資訊，不能把它稱作新資金、主力或買賣力道。V19程式只試近期openInterestHist，V36/V37也未完成此長歷史回測。',
              '2. **溢價**：決策D=t+1h，取t−1h的premium bar（t已收盤），留整小時接收緩衝。`A=方向×(p−前24個更早值中位數)>0`，`B=方向×(p−前一值)>0`，拒絕A且B；相等通過。分雙向/僅多/僅空；另跑A與B單因子。與V30的最近結算funding、7日均值、90日percentile不同，也不是9/9現貨同步或F15/F45改名。',
              '3. **持倉分布差異**：第三順位，metrics含大戶/全市場比率，但即時欄位對應及目前key要求尚未完整驗證；本輪只做兩個獨立方向，因此未跑績效。屬未完成，不能說已測無效。', '',
              '## 資料來源、品質與可執行邊界', '',
              f'下載共{len(source)}個ZIP及各自checksum，累計{total_bytes:,} bytes（{total_bytes / 1e6:.2f} MB），低於50MB上限；先試6個跨年樣本共84,727 bytes，再補齊。未使用API金鑰、不重抓既有K線、不下載逐筆成交或委託簿。', '',
              '[官方公開封存說明](https://github.com/binance/binance-public-data)與[官方市場資料API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)支持資料類型、近期API限制及時間欄位；實際跨年覆蓋、數量、checksum與缺口由本機原檔稽核。逐檔URL/HTTP状态/時間/Last-Modified/SHA256在[下載manifest](research_results/20260910_new_information/download_manifest.json)。', '']
    source_rows = []
    for family in results:
        a = results[family]['data_audit']
        source_rows.append([family, a['raw_rows'], a['raw_start'], a['raw_end'], a['missing_raw'],
                            a.get('invalid_oi_rows', 0), a['invalid_feature_hours'],
                            f"{a['invalid_post_warmup'] / a['post_warmup_hours'] * 100:.4f}%"])
    lines += table(['來源', '原始列數', '最早台北時間', '最晚台北時間', '缺時間點', 'OI非正/非有限', '無效特徵小時', '暖機後無效率'], source_rows)
    lines += ['', '- OI為5m ETHUSDT USD-M持倉數及USDT價值；溢價為無單位率（1bp=0.0001），premium的volume欄不作成交量使用。來源完整范围比回測略長，只取決策已知部分；前綴測試實際截去未來原檔資料。',
              '- OI有8個時間點缺漏，另有非正值；83個日檔未按時間排序。只排序及精確時間對齊，沒有向前填值、改價或製造樣本；排序後無重複/衝突。原始值及缺口完整保留。',
              '- 溢價月檔缺少UTC 2026-06-29整天24根，因此24h歷史窗口合計48個小時不可用。缺口以quality gate處理，未假造或從其他日期補值。',
              '- metrics封存的create_time按OI期間終點解讀，另以q+5min作保守close上界、q+10min作假設可用時間，皆早於D。premium留一整小時。**這是可審查的延遲假設，不是歷史實際接收證據。**',
              '- 部分舊日期檔的Last-Modified在2026年；官方也允許修訂封存。這不能證明數值一定改過，也不能證明沒改過。當年不能靠隔天/月後才發布的archive即時交易；若未來要升級，需核實即時來源並保存received_ts及前瞻資料。',
              '- 來源分類：既有價格/funding/mark/現貨/5m為本機已有；本輪OI/premium是已核實免費長歷史、含明列缺口；openInterestHist仍只是近期API。逐筆book/options長歷史的免費性與50MB內可行性未完成核實，本輪未下載、未宣稱不可得。', '',
              '## 品質與單因子對照', '']
    controls = ['base', 'oi_quality', 'premium_quality', 'premium_level', 'premium_change', 'premium_both']
    lines += table(['規則', '交易數', '淨收益$', '淨勝率', '相對原策略$'], [
        [NAMES[n], lookup[n, 0, False]['full']['n'], money(lookup[n, 0, False]['full']['net_pnl']),
         f"{lookup[n, 0, False]['full']['wr']:.2f}%", money(lookup[n, 0, False]['full']['net_pnl'] - base['net_pnl'])] for n in controls])
    pq_delta = results['premium']['quality_control']['net_delta']
    p_delta = diagnostics['premium_both']['delta']
    lines += ['', f'OI品質對照與基準逐筆相同。溢價品質對照本身少賺${abs(pq_delta):,.2f}；交互規則相對品質對照仍少賺${abs(p_delta - pq_delta):,.2f}，不能將退化歸因於這一天缺資料。交互規則雖比單因子少砍交易、少虧增量，但仍未改善原策略；另做以下同拒絕數對照，不宣稱交互alpha。', '',
              '## 完整交易變化與收益分解', '',
              '配對鍵為方向+實際進場時間，全部重跑占倉、冷卻、日/月/連敗熔斷及月上限；表中移除原單數可能大於直接被拒的原事件，差異來自完整狀態的後續變動。', '']
    rows = []
    for n in primary:
        x = diagnostics[n]
        p, dec = x['pair'], x['decomposition']
        rows.append([NAMES[n], p['removed'], p['new'], p['common'], p['changed_common'],
                     money(dec['avoided_losing_net']), money(dec['missed_winning_net']), money(dec['new_net']),
                     money(dec['common_delta']), money(x['delta'])])
    lines += table(['規則', '移除原單', '新增單', '共同單', '共同變動', '避開原虧損+', '錯失原獲利−', '新增淨收益', '共同差異', '總增量$'], rows)
    lines += ['', '每列精確核對：避開原虧損＋錯失原獲利（負值）＋新增淨收益＋共同差異＝總增量，容差$1e−7。原始trade/equity/funding/events/paired CSV都保留於本機data資料夾。', '',
              '## 獨立樣本與事件漏斗', '']
    rows = []
    for n in primary:
        x = diagnostics[n]['sample']
        r = lookup[n, 0, False]
        rows.append([NAMES[n], r['eligible_gates'], r['rejected_gates'], x['direct_original_events'],
                     x['clusters24h'], x['late_clusters24h'], f"{x['annual_clusters']:.1f}",
                     f"{x['years_for_30_new_clusters_at_historical_rate']:.2f}"])
    lines += table(['規則', '到新gate次數', '拒絕次數', '直接原事件', '24h群數', '後期群數', '年均群數', '累積新30群年數估計'], rows)
    lines += ['', '門檻為全期至少30群、後期至少15群；跨L/S且相距≤24h歸同一群，新增替代單不算獨立證據。這種群分法只是保守近似，不保證完全獨立。年數為歷史頻率外推，不是承諾；即使樣本足夠，也不抵消已觀察到的收益退化。', '',
              'gate次數是原引擎其他條件和狀態都通過後的檢查，狀態變動會改變各候選漏斗；不是把全期兩倍bar數当作有效交易事件。', '',
              '## 同拒絕數隨機對照與不確定性', '',
              '每個主候選100次完整引擎重放，共600次。按方向×季度、原交易/其他技術合格事件兩層，精確匹配新規則的拒絕數；保留相同品質gate。這是事後診斷負對照，不是可實盤使用的規則。', '']
    rows = []
    for n in primary:
        x = diagnostics[n]
        rnd = x['random_control']
        ci = x['uncertainty7']['ci95']
        q = rnd['delta_quantiles_025_50_975']
        rows.append([NAMES[n], f'[{money(q[0])}, {money(q[2])}]', money(q[1]),
                     f"{rnd['p_random_at_least_actual']:.3f}", f"{x['holm_random_p_6_main']:.3f}", f'[{money(ci[0])}, {money(ci[1])}]',
                     f"{x['holm_p_6_main']:.3f}"])
    lines += table(['規則', '隨機增量2.5～97.5%$', '隨機中位$', '隨機≥候選比例p', '隨機比較Holm p', '7日區塊增量95%區間$', '正增量Holm p'], rows)
    lines += ['', 'Holm以「正向增量」單尾檢驗校正六個預登記主候選；另報30日區塊/2,000次敏感度在JSON。它只校正本輪，不能消除歷年重用同一行情的選擇偏誤。隨機p是診斷比較比例，不是這個策略有效的保證。', '',
              '## 分期與額外滑價', '',
              '下表每格為相對相同滑價基準的淨收益差額；早期/後期/最近以mark淨值時間差額衡量，closed_net與交易數另外在JSON按出場時刻分期。最近期間含於後期，不與前後期相加。', '']
    rows = []
    for n in primary:
        for slip in [0, 2, 5]:
            r, b = lookup[n, slip, False], lookup['base', slip, False]
            rows.append([NAMES[n], slip] + [money(r[p]['net_pnl'] - b[p]['net_pnl']) for p in ['full', 'early', 'late', 'recent']] +
                         [f"{r['full']['wr'] - b['full']['wr']:+.2f}"])
    lines += table(['規則', '額外bp/成交', '全期增量$', '2026前增量$', '2026起增量$', '2026/6起增量$', '全期勝率差pp'], rows)
    lines += ['', '## 其他績效及曝險（固定200U、額外0bp）', '']
    rows = []
    for n in ['base'] + primary:
        m = lookup[n, 0, False]['full']
        rows.append([NAMES[n], money(m['pf']), money(m['expectancy']), money(m['avg_win']), money(m['avg_loss']),
                     money(m['tail5_mean']), money(m['mh_net']), f"{m['mh_pct']:.2f}%", m['position_hours'],
                     f"{m['exposure_pct']:.2f}%"])
    lines += table(['規則', 'PF', '每筆期望$', '平均贏$', '平均虧$', '最差5%均值$', 'MH淨額$', 'MH占比', '部位小時', '有倉時間占比'], rows)
    lines += ['', '部位小時是L/S持有時數總和；有倉時間去除雙向重疊，以全部17,519小時為分母。最大同時持倉未超過原L/S各一筆。L/S各自收益、風險、交易數完整列於JSON，以下附方向收益。', '']
    lines += table(['規則', 'L筆数', 'L淨收益$', 'S筆數', 'S淨收益$'], [
        [NAMES[n], lookup[n, 0, False]['side']['L']['n'], money(lookup[n, 0, False]['side']['L']['net_pnl']),
         lookup[n, 0, False]['side']['S']['n'], money(lookup[n, 0, False]['side']['S']['net_pnl'])] for n in ['base'] + primary])
    lines += ['', '## 歷史保證金排程（獨立第二口徑）', '',
              '依既有排程200U→300U@2026-07-03→500U@2026-08-01；此表不用來宣稱固定部位改善。', '']
    rows = []
    for n in ['base'] + primary:
        for slip in [0, 2, 5]:
            m, b = lookup[n, slip, True]['full'], lookup['base', slip, True]['full']
            rows.append([NAMES[n], slip, money(m['net_pnl']), money(m['net_pnl'] - b['net_pnl']), f"{m['wr']:.2f}%", money(m['mdd'])])
    lines += table(['規則', '額外bp/成交', '排程淨收益$', '同排程增量$', '淨勝率', 'mark MDD$'], rows)
    lines += ['', '## 驗證、停止與未完成項目', '',
              '- 7個人工可算的小型fixture測例通過：時間隔離、缺值不填造、相等與單側邊界、premium中位排除当前值、群聚、bootstrap零差。合成fixture只驗計算，不是歷史收益證據。',
              '- 每個家族6種成本/保證金no-op，原引擎所有交易欄位及既有9/8逐筆pnl/funding/net一致；两家族合計12組檢查。',
              '- OI 5規則、premium 7規則在10,000與16,000根截斷點重建原始來源特徵，過去特徵、gate及已平倉交易完全一致，共24組規則×截斷檢查。',
              '- 獨立Decimal逐一重算兩方向各17,519個小時的特徵，包含嚴格正/負/零門檻。72份保存情境檢查gate/成交守恆、現金/淨值/funding、部位、持倉bar及同側冷卻；兩家族重複的6份baseline扣除後為66個唯一情境。',
              '- 所有完整情境期末平倉；未靜默捨棄未平倉資產。每一項收益分解與淨值末值核對，正式程式和凍結價格/成本輸入SHA256維持。',
              '- 初次執行修正兩個研究輸出問題：pandas只讀numpy視圖加copy=True、numpy int轉原生int供JSON序列化。沒有改候選公式、门檻、資料版本或反向挑選；初始registration與最終實作hash均保留。',
              '- 六候選基本經濟門檻均失敗，按預登記停止，**不執行**鄰域、10bp、額外資料延遲、訓練式連續WF及真正未見資料驗證。這些不是PASS，也不是聲稱已被WF否定；它們不會把已少賺的主假說改成可採用。最佳月份/配對成分剔除已作描述性輸出在JSON，沒有依結果刪除最差月份。', '',
              '## 管理決策與交付', '',
              '**現行策略不值得因本輪結果變更。** OI過濾避開了一些虧損，也錯失更多獲利；溢價交互同樣無法彌補被刪除的原收益。沒有達到「同部位净收益+5%、净勝率+1pp、風險不惡化」的候選。', '',
              '最值得保留的是已核實、約9MB的免費存量/溢價資料與嚴格對齊流程，並非某個本輪交易規則。未研究的持倉分布差異仍列未完成；若未來開新研究，需先解決即時欄位及接收/修訂證據，另定假說，不因本輪失敗自動反轉OI或掃新閾值。舊文件「全域最佳/所有alpha耗盡」不能當市場定理。', '',
              '- [預登記與去重矩陣](new_information_plan_20260910.md)',
              '- [精簡結果/重現README](research_results/20260910_new_information/README.md)',
              '- [66個唯一情境CSV](research_results/20260910_new_information/summary.csv)',
              '- [完整指標與判定JSON](research_results/20260910_new_information/results.json)',
              '- 本機完整逐筆、淨值、funding、gate、配對：`data/new_information_20260910/`。', '']
    report_text = '\n'.join(lines)
    for before, after in [('决策', '決策'), ('状态', '狀態'), ('范围', '範圍'), ('当作', '當作'),
                          ('筆数', '筆數'), ('当前', '當前'), ('两家族', '兩家族'), ('门檻', '門檻'),
                          ('净收益', '淨收益'), ('净勝率', '淨勝率')]:
        report_text = report_text.replace(before, after)
    REPORT.write_text(report_text, encoding='utf-8')
    readme = '''# 2026-09-10 新資訊研究重現

結論：NO PROMOTION；六個主候選全部REJECTED。完整說明見[研究報告](../../new_information_results_20260910.md)，固定定義見[預登記](../../new_information_plan_20260910.md)。

## 執行

在專案根目錄，以既有.venv執行：

```powershell
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_new_information_research.py -v
.\\.venv\\Scripts\\python.exe backtest/research/fetch_new_information_20260910.py probe
.\\.venv\\Scripts\\python.exe backtest/research/fetch_new_information_20260910.py metrics
.\\.venv\\Scripts\\python.exe backtest/research/fetch_new_information_20260910.py premium
.\\.venv\\Scripts\\python.exe backtest/research/new_information_20260910.py oi
.\\.venv\\Scripts\\python.exe backtest/research/new_information_20260910.py premium
.\\.venv\\Scripts\\python.exe backtest/research/audit_new_information_20260910.py oi
.\\.venv\\Scripts\\python.exe backtest/research/audit_new_information_20260910.py premium
.\\.venv\\Scripts\\python.exe backtest/research/write_new_information_report_20260910.py
```

下載遵守原始使用者授权与平台權限；不繞過代理、不重試ProxyError，不同環境需正常网络權限。共764個小ZIP及checksum，9.05MB；hard ceiling50MB。已有快取驗SHA256後跳過。來源可能事後修訂，新下載版本不能直接當成本輪原樣重現；保留原raw檔與manifest。

必要本機輸入：

- data/maxhold_review_20260908/candles.csv
- data/public_cost_history_20260908/{mark_1h_full.csv,funding_full.csv,manifest.json}
- data/optimization_execution_20260908/base_{flat,hist}_{0,2,5}_trades.csv（逐筆parity參考）
- data/new_information_20260910/raw/、download_manifest.json（本輪公開來源）

單靠fresh clone不保證完整重現，缺少凍結來源時會拒絕執行；不以合成資料取代。7個純fixture測試不需歷史下載。資料輸出保留所有來源窗口，feature函数只按決策時間讀取。

## 檔案

results.json包含兩家族完整前/後/近期、L/S、成本、風險、品質對照、配對、7/30日bootstrap與六主候選Holm校正。summary.csv有66個唯一完整情境；兩家族合計保存72份，含重複baseline。

每主候選另有matched_random.csv（100次完整狀態負對照），original_affected.csv（直接原事件與24h群），monthly_delta.csv。更大的每情境trade/equity/funding/events及paired全保留data/new_information_20260910。download_manifest和source audit列checksum、原始網址、欄位、日期、缺口與延遲限制。

history_index.json是V9～V37與最近結果的101份文件索引，不是宣稱全數重跑。初始registration不回寫；最終執行與交付檔hash見artifact_manifest.json。

未執行項目：因基本經濟條件失敗而停止10bp/鄰域/進一步延遲/WF/真未見資料驗證。不改策略、不部署、不push。
'''
    readme = readme.replace('授权与', '授權與').replace('网络', '網路')
    (OUT / 'README.md').write_text(readme, encoding='utf-8')
    # Reopen exported tables/JSON and verify row counts and reported PnL from saved ledgers.
    reopened = pd.read_csv(OUT / 'summary.csv')
    assert len(reopened) == 66
    for row in reopened.itertuples():
        stem = f'{row.name}_{"hist" if row.historical else "flat"}_{row.slip}'
        trade = pd.read_csv(DATA / f'{stem}_trades.csv')
        assert abs(trade.net.sum() - row.net_pnl) < 1e-7
    reread = json.loads((OUT / 'results.json').read_text())
    assert len(reread['diagnostics']) == 6 and reread['status'] == 'NO_PROMOTION'
    protected = results['premium']['hashes']
    artifacts = [REPORT, ROOT / 'doc/new_information_plan_20260910.md'] + [p for p in OUT.iterdir() if p.name != 'artifact_manifest.json']
    artifacts += [ROOT / 'backtest/research' / f for f in ['fetch_new_information_20260910.py', 'index_new_information_20260910.py',
                  'new_information_20260910.py', 'audit_new_information_20260910.py', 'write_new_information_report_20260910.py']]
    artifacts += [ROOT / 'tests/test_new_information_research.py', ROOT / 'tests/fixtures/new_information_small.json']
    manifest = {'protected_inputs_verified': protected, 'export_reloaded_rows': len(reopened),
                'artifacts': {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}}
    (OUT / 'artifact_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(REPORT), 'unique_scenarios': len(reopened), 'main_candidates': 6,
                      'random_scenarios': 600, 'protected_hashes': 'PASS', 'reload': 'PASS'}))


if __name__ == '__main__':
    main()
