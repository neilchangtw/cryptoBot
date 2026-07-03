"""
V28 R2b — 「快到頂遇連虧」有多慘？（回吐episode 量化）
========================================================
針對使用者提問：cap 版獲利加倉，接近頂時吃連虧的實際傷害。
  - 歷史路徑（cap 1000U 保證金 / $20K 名目）上所有回吐 episode：
    峰值權益 → 谷底，回吐 $ 與「佔已累積獲利的 %」
  - 對照固定注碼在同時段的回吐
  - 設計性壓力：在頂上（cum 獲利剛好在 cap 門檻）連吃 4 筆 MH 均值虧損
    / 連吃 2 筆 SafeNet 的權益路徑（含逐筆縮倉效果）
  - 月度 PnL 表（cap 版），找出最痛的月份
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
from v28_r2_profit_ratchet import notional_fn

E0 = 1000.0
FEE_RATE = 4.0 / 4000.0


def replay_curve(tdf, events, scheme):
    eq = E0
    notional_of = {}
    rows = []
    r = tdf['pnl_pct'].to_numpy() / 100.0
    for bar, kind, so, idx in events:
        if kind == 1:
            notional_of[idx] = notional_fn(scheme, eq)
        else:
            N = notional_of.pop(idx)
            pnl = N * r[idx] - N * FEE_RATE
            eq += pnl
            rows.append((str(tdf.at[idx, 'exit_dt']), eq, pnl, N))
    return pd.DataFrame(rows, columns=['dt', 'equity', 'pnl', 'notional'])


def episodes(cv):
    """回吐 episode：新高 → 谷底（下一個新高前）。"""
    out = []
    peak, peak_i = E0, -1
    trough, trough_i = E0, -1
    for i, e in enumerate(cv['equity']):
        if e > peak:
            if peak_i >= 0 and trough < peak:
                out.append((peak_i, trough_i, peak, trough))
            peak, peak_i = e, i
            trough, trough_i = e, i
        elif e < trough:
            trough, trough_i = e, i
    if trough < peak:
        out.append((peak_i, trough_i, peak, trough))
    return out


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades).reset_index(drop=True)
    events = v28.make_events(tdf)

    cv = replay_curve(tdf, events, 'ratchet_cap20')
    cv_f = replay_curve(tdf, events, 'fixed')

    print("=" * 78)
    print("V28 R2b — cap 1000U（$20K 名目）版：回吐 episode 全記錄")
    print("=" * 78)
    eps = episodes(cv)
    eps.sort(key=lambda t: t[3] - t[2])  # 依回吐金額
    print(f"\n  前 8 大回吐（峰→谷）：")
    print(f"  {'峰值日':<12} {'谷底日':<12} {'峰值權益':>10} {'谷底':>10} {'回吐$':>9} "
          f"{'佔獲利%':>8} {'固定注碼同段':>10}")
    for pi, ti, pk, tr in eps[:8]:
        dd = tr - pk
        prof = pk - E0
        pct = dd / prof * 100 if prof > 0 else np.nan
        f_dd = cv_f['equity'].iloc[ti] - cv_f['equity'].iloc[pi]
        print(f"  {cv.dt.iloc[pi][:10]:<12} {cv.dt.iloc[ti][:10]:<12} {pk:>10,.0f} "
              f"{tr:>10,.0f} {dd:>9,.0f} {pct:>7.1f}% {f_dd:>+10.0f}")

    # 月度 PnL（cap 版）
    cv['m'] = cv.dt.str[:7]
    mo = cv.groupby('m').agg(equity=('equity', 'last'), pnl=('pnl', 'sum'))
    print(f"\n  月度 PnL（cap 1000U 版） 最差 5 個月：")
    for m, row in mo.sort_values('pnl').head(5).iterrows():
        print(f"    {m}  ${row.pnl:>+8,.0f}（月末權益 {row['equity']:>10,.0f}）")

    # 設計性壓力：站在頂上連虧
    print(f"\n### 設計性壓力：權益剛好在 cap 門檻（cum=+$800，保證金 1000U）開始連虧")
    for name, seq in [
        ("連吃 4 筆 MH 均值虧損（-1.17%/筆）", [-0.0117] * 4),
        ("連吃 4 筆 MH 較深虧損（-2.0%/筆）", [-0.020] * 4),
        ("連吃 2 筆 SafeNet（-4.2%/筆）", [-0.042] * 2),
    ]:
        eq = E0 + 800.0
        path = [eq]
        for rr in seq:
            N = notional_fn('ratchet_cap20', eq)
            eq += N * rr - N * FEE_RATE
            path.append(eq)
        total = path[-1] - path[0]
        print(f"  {name:<34} 權益 {path[0]:,.0f} → {path[-1]:,.0f}"
              f"（{total:+,.0f}，回吐獲利的 {-total / 800 * 100:.0f}%）")
        sizes = ' → '.join(f"{notional_fn('ratchet_cap20', e) / 20:,.0f}U"
                           for e in path[:-1])
        print(f"    逐筆保證金：{sizes}")

    # 對照：固定注碼同樣連虧
    eq = E0 + 800 / 5  # 固定注碼要累積同比例獲利…直接看金額
    print(f"\n  對照固定 200U：同樣 4 筆 MH 均值虧損 = 4 × -$51 ≈ -$204")


if __name__ == '__main__':
    main()
