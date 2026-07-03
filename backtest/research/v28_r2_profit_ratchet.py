"""
V28 R2 — 「獲利加倉」方案回測（使用者提案）
==============================================
規則（使用者定義）：
  - 每次開倉保證金 = 200U + 累計獲利（cum PnL），20 倍槓桿 → 名目 = 20 × 保證金
  - 累計獲利為負 → 維持 200U 地板（虧損不縮倉，獲利才加倉）
  - 例：第一筆賺 50U → 下一筆 250U × 20x = $5,000 名目

數學性質（先講清楚）：
  名目 = $4,000 + 20 × max(0, cumPnL)；權益 = $1,000 + cumPnL
  → 曝險比 = 名目/權益 從 4x 隨獲利爬向 20x（獲利部分全額 20x 再投入）
  → 單筆最壞（SafeNet -4.2%）佔權益比例從 -16.8% 爬向 -84%
  對照組：固定 $4,000（現行）、V28 複利 4x（曝險比恆 4x）、
          折衷「獲利的 50% 加倉」（名目 = 4000 + 10×max(0,cum)）

輸出：終值/MDD/最壞30天/最大單筆（$ 與佔權益%）、月度權益進程表、
      月塊 bootstrap 1000 路徑、抽掉最好 3 個月壓力。
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import v27_engine as ve
import v28_compound_sizing as v28

RNG = np.random.default_rng(42)
E0 = 1000.0
FEE_RATE = 4.0 / 4000.0
LEV = 20.0


def notional_fn(scheme, eq):
    """scheme → 進場名目。eq = 當下權益（= 1000 + cumPnL）。"""
    cum = eq - E0
    if scheme == 'fixed':
        return 4000.0
    if scheme == 'ratchet':          # 使用者方案：獲利全額 20x 加倉
        return LEV * (200.0 + max(0.0, cum))
    if scheme == 'ratchet50':        # 折衷：獲利的 50% 加倉
        return LEV * (200.0 + 0.5 * max(0.0, cum))
    if scheme == 'compound4x':       # V28：曝險比恆 4x
        return 4.0 * eq
    if scheme.startswith('ratchet_cap'):   # 使用者方案 + 單倉名目上限
        cap = float(scheme.split('cap')[1]) * 1000.0
        return min(LEV * (200.0 + max(0.0, cum)), cap)
    raise ValueError(scheme)


def replay(tdf, events, scheme):
    eq = E0
    notional_of = {}
    curve_dt, curve_eq = [], []
    worst_pnl, worst_pct = 0.0, 0.0
    r = tdf['pnl_pct'].to_numpy() / 100.0
    for bar, kind, so, idx in events:
        if kind == 1:
            notional_of[idx] = notional_fn(scheme, eq)
        else:
            N = notional_of.pop(idx)
            eq_before = eq
            pnl = N * r[idx] - N * FEE_RATE
            eq += pnl
            if pnl < worst_pnl:
                worst_pnl, worst_pct = pnl, pnl / eq_before * 100
            curve_dt.append(str(tdf.at[idx, 'exit_dt']))
            curve_eq.append(eq)
            if eq <= 0:
                eq = 1e-9
    return curve_dt, np.array(curve_eq), worst_pnl, worst_pct


def seq_replay(sub_list, scheme):
    """bootstrap 用逐筆重放。回傳 (終值, MDD%, 最低權益)。"""
    eq = E0
    curve = [eq]
    for sub in sub_list:
        for rr in sub.pnl_pct.to_numpy() / 100.0:
            N = notional_fn(scheme, eq)
            eq += N * rr - N * FEE_RATE
            if eq <= 0:
                eq = 1e-9
            curve.append(eq)
    curve = np.array(curve)
    peak = np.maximum.accumulate(curve)
    return curve[-1], -((curve - peak) / peak).min() * 100, curve.min()


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades).reset_index(drop=True)
    events = v28.make_events(tdf)
    years = (pd.to_datetime(str(tdf.exit_dt.iloc[-1])) -
             pd.to_datetime(str(tdf.entry_dt.iloc[0]))).days / 365.25

    print("=" * 80)
    print("V28 R2 — 獲利加倉方案（200U+累計獲利 ×20x，虧損退回 200U 地板）")
    print("=" * 80)

    schemes = [('fixed', '固定 200U×20x（現行）'),
               ('ratchet', '獲利全額加倉（你的方案）'),
               ('ratchet_cap12', '你的方案 cap $12K 名目'),
               ('ratchet_cap20', '你的方案 cap $20K 名目'),
               ('ratchet_cap40', '你的方案 cap $40K 名目'),
               ('ratchet50', '獲利 50% 加倉（折衷）'),
               ('compound4x', 'V28 複利 4x（對照）')]

    print(f"\n  {'方案':<22} {'終值':>12} {'總PnL':>12} {'MDD%':>7} {'最壞30天':>9} "
          f"{'最大單筆虧':>10} {'佔當時權益':>9}")
    curves = {}
    for key, label in schemes:
        cdt, ceq, wp, wpct = replay(tdf, events, key)
        t, c, m, w = v28.metrics(cdt, ceq, years)
        curves[key] = (cdt, ceq)
        print(f"  {label:<22} {t:>12,.0f} {t - E0:>+12,.0f} {m:>6.1f}% {w:>8.1f}% "
              f"{wp:>+10.0f} {wpct:>8.1f}%")

    # 月度權益進程（你的方案 vs 固定）
    print(f"\n### 月度權益進程（你的方案）")
    cdt, ceq = curves['ratchet']
    s = pd.Series(ceq, index=pd.to_datetime(cdt))
    monthly = s.resample('ME').last().ffill()
    cdt_f, ceq_f = curves['fixed']
    sf = pd.Series(ceq_f, index=pd.to_datetime(cdt_f)).resample('ME').last().ffill()
    prev = E0
    print(f"  {'月份':<9} {'月末權益':>12} {'當月PnL':>12} {'下月保證金':>10} {'固定注碼月末':>12}")
    for dt_i, v in monthly.items():
        marg = 200 + max(0.0, v - E0)
        fx = sf.get(dt_i, np.nan)
        print(f"  {dt_i.strftime('%Y-%m'):<9} {v:>12,.0f} {v - prev:>+12,.0f} "
              f"{marg:>10,.0f} {fx:>12,.0f}")
        prev = v

    # Bootstrap（月塊重排 1000 路徑）
    tdf['_month'] = tdf.entry_dt.astype(str).str[:7]
    months = tdf['_month'].unique()
    by_month = {mo: tdf[tdf._month == mo] for mo in months}
    res = {k: {'term': [], 'mdd': [], 'halve': 0, 'ruin': 0} for k, _ in schemes}
    for _ in range(1000):
        sample = [by_month[mo] for mo in RNG.choice(months, size=len(months), replace=True)]
        for key, _ in schemes:
            t, m, lo = seq_replay(sample, key)
            res[key]['term'].append(t)
            res[key]['mdd'].append(m)
            res[key]['halve'] += (lo < E0 * 0.5)
            res[key]['ruin'] += (lo < 200.0)

    print(f"\n### 月塊 bootstrap（1000 條重排路徑，2 年）")
    print(f"  {'方案':<22} {'終值P5':>10} {'終值P50':>12} {'終值P95':>14} "
          f"{'MDD%P50':>8} {'MDD%P95':>8} {'腰斬%':>6} {'跌破200U%':>9}")
    for key, label in schemes:
        tq = np.percentile(res[key]['term'], [5, 50, 95])
        mq = np.percentile(res[key]['mdd'], [50, 95])
        print(f"  {label:<22} {tq[0]:>10,.0f} {tq[1]:>12,.0f} {tq[2]:>14,.0f} "
              f"{mq[0]:>7.1f}% {mq[1]:>7.1f}% {res[key]['halve'] / 10:>5.1f}% "
              f"{res[key]['ruin'] / 10:>8.1f}%")

    # 壓力：抽掉最好的 3 個月
    msum = tdf.groupby('_month').pnl_usd.sum().sort_values(ascending=False)
    best3 = set(msum.index[:3])
    keep = [by_month[mo] for mo in months if mo not in best3]
    print(f"\n### 壓力：抽掉最好的 3 個月（{', '.join(sorted(best3))}）")
    for key, label in schemes:
        t, m, lo = seq_replay(keep, key)
        print(f"  {label:<22} 終值 {t:>12,.0f}（MDD {m:.1f}%）")


if __name__ == '__main__':
    main()
