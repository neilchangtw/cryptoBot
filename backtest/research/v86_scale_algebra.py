"""V86 固定路徑代數快篩：交易集合、時點、價格不變，名目與所有成本按倍率 k 等比例變動。
不是帳戶回測，不重跑引擎、不搜尋倍率。只算預登記的兩個倍率（k*＝追平現行、k_min＝最低權益剛好 1,000U）。
用法：python backtest/research/v86_scale_algebra.py"""
import os, json
import numpy as np, pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
V81 = os.path.join(ROOT, 'doc', 'research_results', 'v81'); OUT = os.path.join(ROOT, 'doc', 'research_results', 'v86'); os.makedirs(OUT, exist_ok=True)
S0 = 1500.0
summ = pd.read_csv(os.path.join(V81, 'acct_summary.csv'))
def row(sc, cfg): return summ[(summ.情境 == sc) & (summ.配置 == cfg) & (summ.口徑 == '參考路徑＋拒單')].iloc[0]
R = {'核對原檔': {}, '倍率表': []}
for sc in ('D0', 'D1'):
    v = row(sc, 'V77 配置（Q20W30＋停損價差預算3%）'); c = row(sc, '現行原配置')
    R['核對原檔'][sc] = dict(V77淨利=float(v.淨利), V77最低權益=float(v.最低權益), V77_2021=float(v.y2021), 現行淨利=float(c.淨利), 現行2021=float(c.y2021), 現行2023=float(c.y2023))
    eq = pd.read_csv(os.path.join(V81, f'acct_v77_{sc}_equity_hourly.csv.gz'), parse_dates=['datetime'])
    ks = {'k*＝追平同情境現行淨利': float(c.淨利) / float(v.淨利), 'k_min＝最低權益剛好1,000U': 500.0 / (S0 - float(v.最低權益)), '對照 k=1（原值）': 1.0}
    for nm, k in ks.items():
        e = S0 + k * (eq.equity.values - S0); ei = S0 + k * (eq.equity_intrahour.values - S0)
        peak = np.maximum.accumulate(e); ddp = ((peak - e) / peak).max() * 100; dda = (peak - e).max()
        R['倍率表'].append({'情境': sc, '倍率': nm, 'k': round(k, 3), '淨利': round(k * float(v.淨利), 0), '2021': round(k * float(v.y2021), 0),
                         **{str(y): round(k * float(v[f'y{y}']), 0) for y in range(2022, 2027)},
                         '最低權益_逐時': round(float(e.min()), 0), '最低權益_小時內保守': round(float(ei.min()), 0), '最大百分比回撤_對運行高點': round(float(ddp), 1),
                         '全期逐時市價最大回撤': round(float(dda), 0), '五年已實現MDD': round(k * float(v.五年MDD), 0), '最差滾動12月': round(k * float(v.最差滾動12月), 0),
                         '最差連敗段': round(k * float(v.最差連敗段_五年口徑), 0), '勝率': float(v.勝率), '最高保證金占用': round(k * float(v.最高保證金占用), 0),
                         '最低可用資金_原路徑': float(v.最低可用資金), '停損價差預算pct': round(3.0 * k, 2),
                         '與現行淨利差': round(k * float(v.淨利) - float(c.淨利), 0)})
tb = pd.DataFrame(R['倍率表']); tb.to_csv(os.path.join(OUT, 'scale_algebra.csv'), index=False)
gates = lambda r: {'淨利≥現行': r['與現行淨利差'] >= 0, '各完整年度為正': all(r[str(y)] > 0 for y in ['2021', '2022', '2023', '2024', '2025']), '2026 YTD 為正': r['2026'] > 0,
                   '勝率>50%': r['勝率'] > 50, '最低權益≥1,000U': r['最低權益_逐時'] >= 1000, '百分比回撤≤40%': r['最大百分比回撤_對運行高點'] <= 40,
                   '五年MDD≤1,400': r['五年已實現MDD'] <= 1400, '最差滾動12月≥−900': r['最差滾動12月'] >= -900, '最差連敗段≥−500': r['最差連敗段'] >= -500}
R['門檻對照_僅快篩'] = [{'情境': r['情境'], '倍率': r['倍率'], **{k: bool(v) for k, v in gates(r).items()}} for r in R['倍率表']]
R['判讀'] = ('k>0 時逐年符號不變；V77 的 2021 在 D0、D1 都是負數，所以任何正倍率都過不了「每個完整年度為正」。現行原配置 2021、2023 為負，同理。'
           '固定放大不是已達標候選。此表是固定路徑快篩：放大後保證金占用可能超過可用資金而跳單、固定美元熔斷會提早觸發、強平與穿價不等比例，都會改變路徑，未涵蓋。')
json.dump(R, open(os.path.join(OUT, 'scale_algebra.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(json.dumps(R['核對原檔'], ensure_ascii=False)); print(tb.to_string())
print(pd.DataFrame(R['門檻對照_僅快篩']).to_string())
