"""
V25 R2 — Per-regime exit grid scan (one-variable-at-a-time)

基於 R1 發現掃描各 regime × 出場參數：
  L_MH_BY_RG: SIDE {3..6}, MILD_UP {5..7}, DOWN {6..9}
  L_TP_BY_RG: DOWN {0.035, 0.040, 0.045, 0.050}
  S_MH_BY_RG: UP {6..10}, MILD_UP {7..10}, DOWN {8..11}
  S_TP_BY_RG: DOWN {0.020, 0.025, 0.030}

方法：
  - 基底 = V14+R baseline（所有 default）
  - 每次只改一個 (regime, param) cell，其他保持 default
  - 要求：總 PnL >= baseline 且 總 WR >= baseline（用戶目標：both）
  - 輸出 IS / OOS 兩個分段，避免單點過擬合
"""
import os, sys, itertools
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v25_engine import run_v25, stats_from, load_data, build_slope, build_r_gate

def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    N = len(o); IS_END = N // 2
    slope_use = build_slope(c)
    block_L, block_S = build_r_gate(slope_use)

    # baseline (all defaults)
    base = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, N,
                   block_bars_L=block_L, block_bars_S=block_S)
    base_all = stats_from(base)
    base_is = stats_from([t for t in base if t['entry_bar'] < IS_END])
    base_oos = stats_from([t for t in base if t['entry_bar'] >= IS_END])
    base_l = stats_from(base, 'L')
    base_s = stats_from(base, 'S')
    print("=" * 100)
    print("V25 R2 — Per-regime Exit Grid Scan (one-variable-at-a-time)")
    print("=" * 100)
    print(f"\nBaseline V14+R: n={base_all['n']} PnL=${base_all['pnl']:.0f} WR={base_all['wr']:.1f}% "
          f"PF={base_all['pf']:.2f} MDD=${base_all['mdd']:.0f} Sharpe={base_all['sharpe']:.2f}")
    print(f"  IS:  n={base_is['n']} PnL=${base_is['pnl']:.0f} WR={base_is['wr']:.1f}%")
    print(f"  OOS: n={base_oos['n']} PnL=${base_oos['pnl']:.0f} WR={base_oos['wr']:.1f}%")
    print(f"  L:   n={base_l['n']} PnL=${base_l['pnl']:.0f} WR={base_l['wr']:.1f}%")
    print(f"  S:   n={base_s['n']} PnL=${base_s['pnl']:.0f} WR={base_s['wr']:.1f}%")

    sweeps = []
    for v in [3,4,5,6]: sweeps.append(('L_MH', 'SIDE', v))
    for v in [5,6,7]: sweeps.append(('L_MH', 'MILD_UP', v))
    for v in [6,7,8,9]: sweeps.append(('L_MH', 'DOWN', v))
    for v in [0.035, 0.040, 0.045, 0.050]: sweeps.append(('L_TP', 'DOWN', v))
    for v in [0.030, 0.035, 0.040]: sweeps.append(('L_TP', 'MILD_UP', v))
    for v in [6,7,8,9,10]: sweeps.append(('S_MH', 'UP', v))
    for v in [7,8,9,10]: sweeps.append(('S_MH', 'MILD_UP', v))
    for v in [8,9,10,11]: sweeps.append(('S_MH', 'DOWN', v))
    for v in [0.020, 0.025, 0.030]: sweeps.append(('S_TP', 'DOWN', v))
    for v in [0.020, 0.025, 0.030]: sweeps.append(('S_TP', 'MILD_UP', v))

    print("\n--- 單變量掃描 (baseline 以外) ---")
    hdr = (f"{'Param':<6} {'Regime':<8} {'Val':>6} | "
           f"{'n':>4} {'PnL':>6} {'WR%':>5} {'PF':>5} {'MDD':>5} {'Shrp':>5} | "
           f"{'IS_pnl':>7} {'OOS_pnl':>8} {'d_PnL':>6} {'d_WR':>6}")
    print(hdr); print("-" * len(hdr))
    results = []
    for param, rg, val in sweeps:
        L_TP = {rg: val} if param == 'L_TP' else None
        L_MH = {rg: val} if param == 'L_MH' else None
        S_TP = {rg: val} if param == 'S_TP' else None
        S_MH = {rg: val} if param == 'S_MH' else None
        trs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, N,
                      block_bars_L=block_L, block_bars_S=block_S,
                      L_TP_BY_RG=L_TP, L_MH_BY_RG=L_MH,
                      S_TP_BY_RG=S_TP, S_MH_BY_RG=S_MH)
        st = stats_from(trs)
        st_is = stats_from([t for t in trs if t['entry_bar'] < IS_END])
        st_oos = stats_from([t for t in trs if t['entry_bar'] >= IS_END])
        d_pnl = st['pnl'] - base_all['pnl']
        d_wr = st['wr'] - base_all['wr']
        # Skip if identical to baseline (param equals default)
        default_match = ((param=='L_MH' and val==6) or (param=='S_MH' and val==10) or
                         (param=='L_TP' and val==0.035) or (param=='S_TP' and val==0.020))
        tag = '(=base)' if default_match else ''
        print(f"{param:<6} {rg:<8} {val:>6} | "
              f"{st['n']:>4} {st['pnl']:>6.0f} {st['wr']:>5.1f} {st['pf']:>5.2f} "
              f"{st['mdd']:>5.0f} {st['sharpe']:>5.2f} | "
              f"{st_is['pnl']:>7.0f} {st_oos['pnl']:>8.0f} {d_pnl:>+6.0f} {d_wr:>+6.1f} {tag}")
        results.append({'param':param,'rg':rg,'val':val,'pnl':st['pnl'],'wr':st['wr'],
                        'is_pnl':st_is['pnl'],'oos_pnl':st_oos['pnl'],
                        'd_pnl':d_pnl,'d_wr':d_wr,
                        'mdd':st['mdd'],'sharpe':st['sharpe']})

    # Pareto-improving (both PnL and WR improve) + IS & OOS both improve
    print("\n--- Pareto-improving (PnL ↑ AND WR ↑ vs baseline, IS & OOS both not worse) ---")
    pareto = [r for r in results if r['d_pnl'] > 0 and r['d_wr'] > 0
              and r['is_pnl'] >= base_is['pnl'] - 50 and r['oos_pnl'] >= base_oos['pnl'] - 50]
    for r in sorted(pareto, key=lambda x: -(x['d_pnl'] + x['d_wr']*100)):
        print(f"  {r['param']:<6} {r['rg']:<8} {r['val']:>6} | d_PnL={r['d_pnl']:+.0f} d_WR={r['d_wr']:+.1f}% "
              f"IS={r['is_pnl']:.0f} OOS={r['oos_pnl']:.0f}")
    if not pareto:
        print("  無（沒有同時改善 WR 和 PnL 的單變量配置）")

    # PnL-improving only (baseline WR not worse by > 1%)
    print("\n--- PnL-improving (d_PnL > 0, d_WR >= -1%) ---")
    pnl_up = [r for r in results if r['d_pnl'] > 0 and r['d_wr'] >= -1.0]
    for r in sorted(pnl_up, key=lambda x: -x['d_pnl'])[:12]:
        print(f"  {r['param']:<6} {r['rg']:<8} {r['val']:>6} | d_PnL={r['d_pnl']:+.0f} d_WR={r['d_wr']:+.1f}% "
              f"IS={r['is_pnl']:.0f} OOS={r['oos_pnl']:.0f} MDD={r['mdd']:.0f} Sh={r['sharpe']:.2f}")

    return base_all, base_is, base_oos, results

if __name__ == "__main__":
    main()
