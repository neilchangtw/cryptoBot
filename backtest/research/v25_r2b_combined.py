"""
V25 R2B — 組合配置測試

R2 發現僅 S_MH_UP=8 通過嚴格 Pareto（IS & OOS 皆改善）。
另有兩組 OOS 贏家 IS 略虧：L_TP_DOWN=0.045 / L_TP_DOWN=0.040

此腳本測試：
  V25-A: S_MH_UP=8（唯一 Pareto 嚴格通過）
  V25-B: S_MH_UP=8 + L_TP_DOWN=0.045（加 OOS 最強 L_TP 擴大）
  V25-C: S_MH_UP=8 + L_TP_DOWN=0.040（保守版）
  V25-D: S_MH_UP=8 + L_TP_DOWN=0.040 + L_MH_MILD_UP=7（加 R2 中 L_MH MILD_UP=7 微幅改善）

基底對比：V14+R baseline
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v25_engine import run_v25, stats_from, load_data, build_slope, build_r_gate

def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    N = len(o); IS_END = N // 2
    slope_use = build_slope(c)
    block_L, block_S = build_r_gate(slope_use)

    configs = [
        ('V14+R baseline', None, None, None, None),
        ('V25-A: S_MH_UP=8', None, None, None, {'UP':8}),
        ('V25-B: S_MH_UP=8 + L_TP_DOWN=0.045', {'DOWN':0.045}, None, None, {'UP':8}),
        ('V25-C: S_MH_UP=8 + L_TP_DOWN=0.040', {'DOWN':0.040}, None, None, {'UP':8}),
        ('V25-D: S_MH_UP=8 + L_TP_DOWN=0.040 + L_MH_MILD_UP=7',
         {'DOWN':0.040}, {'MILD_UP':7}, None, {'UP':8}),
        ('V25-E: S_MH_UP=8 + L_TP_DOWN=0.050 (aggressive)', {'DOWN':0.050}, None, None, {'UP':8}),
    ]

    print("=" * 130)
    print("V25 R2B — 組合配置測試")
    print("=" * 130)
    hdr = (f"{'Config':<55} | {'n':>4} {'PnL':>6} {'WR%':>5} {'PF':>5} {'MDD':>5} {'Shrp':>5} | "
           f"{'IS_pnl':>7} {'IS_WR':>6} | {'OOS_pnl':>8} {'OOS_WR':>7} | {'d_PnL':>6} {'d_WR':>6}")
    print(hdr); print("-" * len(hdr))

    base_stats = None
    results = []
    for label, L_TP, L_MH, S_TP, S_MH in configs:
        trs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, N,
                      block_bars_L=block_L, block_bars_S=block_S,
                      L_TP_BY_RG=L_TP, L_MH_BY_RG=L_MH,
                      S_TP_BY_RG=S_TP, S_MH_BY_RG=S_MH)
        st = stats_from(trs)
        st_is = stats_from([t for t in trs if t['entry_bar'] < IS_END])
        st_oos = stats_from([t for t in trs if t['entry_bar'] >= IS_END])
        if base_stats is None:
            base_stats = (st, st_is, st_oos)
            d_pnl, d_wr = 0, 0
        else:
            d_pnl = st['pnl'] - base_stats[0]['pnl']
            d_wr = st['wr'] - base_stats[0]['wr']
        print(f"{label:<55} | {st['n']:>4} {st['pnl']:>6.0f} {st['wr']:>5.1f} {st['pf']:>5.2f} "
              f"{st['mdd']:>5.0f} {st['sharpe']:>5.2f} | "
              f"{st_is['pnl']:>7.0f} {st_is['wr']:>6.1f} | "
              f"{st_oos['pnl']:>8.0f} {st_oos['wr']:>7.1f} | "
              f"{d_pnl:>+6.0f} {d_wr:>+6.1f}")
        results.append((label, st, st_is, st_oos, trs))

    # Monthly profit curve for best candidate
    print("\n--- 月度 PnL 比較（V14+R baseline vs V25 candidates）---")
    by_mo = {}
    for label, st, si, so, trs in results:
        mo = {}
        for t in trs:
            mo[t['entry_mk']] = mo.get(t['entry_mk'], 0) + t['pnl']
        by_mo[label] = mo
    all_mos = sorted(set().union(*[m.keys() for m in by_mo.values()]))
    hdr = f"{'Month':<8} " + " | ".join(f"{lbl[:20]:>20}" for lbl, *_ in results)
    print(hdr)
    for mo in all_mos:
        row = f"{mo:<8} " + " | ".join(f"{by_mo[lbl].get(mo, 0):>20.0f}" for lbl, *_ in results)
        print(row)
    # Worst month / positive month count
    print()
    for lbl, st, si, so, trs in results:
        mos = by_mo[lbl]
        vals = list(mos.values())
        pos = sum(1 for v in vals if v > 0)
        worst = min(vals) if vals else 0
        print(f"{lbl:<55} 正月 {pos}/{len(vals)}, 最差月 ${worst:.0f}")

if __name__ == "__main__":
    main()
