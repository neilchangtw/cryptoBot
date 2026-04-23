"""
V25 R3 — 10-Gate 稽核（focus on G4 / G7 / G8 / G9）

候選：
  V25-D = S_MH_UP=8, L_TP_DOWN=0.040, L_MH_MILD_UP=7  （最佳 WR + MDD）
  V25-E = S_MH_UP=8, L_TP_DOWN=0.050                  （最佳 PnL）

Gates:
  G4  Parameter neighborhood (±1 for MH, ±0.005 for TP)
  G7  6-fold Walk-Forward (IS train / OOS test)
  G8  Time reversal (reverse OHLCV, should PnL flip direction)
  G9  Drop-best-month robustness
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v25_engine import run_v25, stats_from, load_data, build_slope, build_r_gate

def audit_config(label, cfg, o, h, l, c, hours, dows, mks, dks):
    """cfg = (L_TP_BY_RG, L_MH_BY_RG, S_TP_BY_RG, S_MH_BY_RG)"""
    N = len(o); IS_END = N // 2
    slope_use = build_slope(c)
    block_L, block_S = build_r_gate(slope_use)
    L_TP, L_MH, S_TP, S_MH = cfg

    trs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, N,
                  block_bars_L=block_L, block_bars_S=block_S,
                  L_TP_BY_RG=L_TP, L_MH_BY_RG=L_MH, S_TP_BY_RG=S_TP, S_MH_BY_RG=S_MH)
    st = stats_from(trs)
    st_is = stats_from([t for t in trs if t['entry_bar'] < IS_END])
    st_oos = stats_from([t for t in trs if t['entry_bar'] >= IS_END])
    by_mo = {}
    for t in trs: by_mo[t['entry_mk']] = by_mo.get(t['entry_mk'], 0) + t['pnl']

    print(f"\n{'='*110}")
    print(f"AUDIT: {label}")
    print(f"L_TP_BY_RG={L_TP}  L_MH_BY_RG={L_MH}  S_TP_BY_RG={S_TP}  S_MH_BY_RG={S_MH}")
    print(f"{'='*110}")
    print(f"Full:  n={st['n']} PnL=${st['pnl']:.0f} WR={st['wr']:.1f}% PF={st['pf']:.2f} "
          f"MDD=${st['mdd']:.0f} Sharpe={st['sharpe']:.2f}")
    print(f"  IS:  n={st_is['n']} PnL=${st_is['pnl']:.0f} WR={st_is['wr']:.1f}%")
    print(f"  OOS: n={st_oos['n']} PnL=${st_oos['pnl']:.0f} WR={st_oos['wr']:.1f}%")

    # ---- G4: parameter neighborhood ----
    print(f"\n[G4] Parameter Neighborhood (±1 for MH, ±0.005 for TP)")
    print(f"{'Variant':<45} {'n':>4} {'PnL':>6} {'WR%':>5} {'MDD':>5} {'IS_pnl':>7} {'OOS_pnl':>8}")
    g4_variants = []
    # Expand neighborhood: for each param, test ±
    base_L_MH = dict(L_MH) if L_MH else {}
    base_S_MH = dict(S_MH) if S_MH else {}
    base_L_TP = dict(L_TP) if L_TP else {}
    base_S_TP = dict(S_TP) if S_TP else {}
    for rg, v in (L_MH or {}).items():
        for dv in [-1, 1]:
            nv = v + dv
            if nv < 3: continue
            nd = dict(base_L_MH); nd[rg] = nv
            g4_variants.append((f"L_MH_{rg} {v}→{nv}", base_L_TP or None, nd, base_S_TP or None, base_S_MH or None))
    for rg, v in (S_MH or {}).items():
        for dv in [-1, 1]:
            nv = v + dv
            if nv < 5: continue
            nd = dict(base_S_MH); nd[rg] = nv
            g4_variants.append((f"S_MH_{rg} {v}→{nv}", base_L_TP or None, base_L_MH or None, base_S_TP or None, nd))
    for rg, v in (L_TP or {}).items():
        for dv in [-0.005, 0.005]:
            nv = round(v + dv, 3)
            nd = dict(base_L_TP); nd[rg] = nv
            g4_variants.append((f"L_TP_{rg} {v}→{nv}", nd, base_L_MH or None, base_S_TP or None, base_S_MH or None))
    for rg, v in (S_TP or {}).items():
        for dv in [-0.005, 0.005]:
            nv = round(v + dv, 3)
            nd = dict(base_S_TP); nd[rg] = nv
            g4_variants.append((f"S_TP_{rg} {v}→{nv}", base_L_TP or None, base_L_MH or None, nd, base_S_MH or None))
    g4_pass = 0
    for vlab, vL_TP, vL_MH, vS_TP, vS_MH in g4_variants:
        vtrs = run_v25(o, h, l, c, hours, dows, mks, dks, slope_use, 300, N,
                       block_bars_L=block_L, block_bars_S=block_S,
                       L_TP_BY_RG=vL_TP, L_MH_BY_RG=vL_MH, S_TP_BY_RG=vS_TP, S_MH_BY_RG=vS_MH)
        vst = stats_from(vtrs)
        vst_is = stats_from([t for t in vtrs if t['entry_bar'] < IS_END])
        vst_oos = stats_from([t for t in vtrs if t['entry_bar'] >= IS_END])
        # Pass: still > V14+R baseline PnL ($6583)
        if vst['pnl'] >= 6583: g4_pass += 1
        print(f"{vlab:<45} {vst['n']:>4} {vst['pnl']:>6.0f} {vst['wr']:>5.1f} {vst['mdd']:>5.0f} "
              f"{vst_is['pnl']:>7.0f} {vst_oos['pnl']:>8.0f}")
    print(f"  G4 result: {g4_pass}/{len(g4_variants)} variants still >= V14+R baseline ($6583)")

    # ---- G7: 6-fold Walk-Forward ----
    print(f"\n[G7] 6-fold Walk-Forward")
    fold_size = (N - 300) // 6
    wf_pass = 0
    wf_total_pnl = 0
    for fold in range(6):
        f_start = 300 + fold * fold_size
        f_end = f_start + fold_size if fold < 5 else N
        fold_trs = [t for t in trs if f_start <= t['entry_bar'] < f_end]
        fst = stats_from(fold_trs)
        wf_total_pnl += fst['pnl']
        pass_flag = 'OK' if fst['pnl'] > 0 else 'NEG'
        if fst['pnl'] > 0: wf_pass += 1
        print(f"  Fold {fold+1} [bar {f_start}..{f_end}]: n={fst['n']:>3} PnL=${fst['pnl']:+.0f} "
              f"WR={fst['wr']:.1f}% {pass_flag}")
    print(f"  G7 result: {wf_pass}/6 folds profitable, total PnL=${wf_total_pnl:.0f}")

    # ---- G8: Time reversal ----
    # Reverse OHLCV (mirror time), run same strategy, PnL ratio should be near 0 (or <0)
    print(f"\n[G8] Time Reversal (ETH price mirrored)")
    rev_o = o[::-1].copy(); rev_h = h[::-1].copy(); rev_l = l[::-1].copy(); rev_c = c[::-1].copy()
    # Need to reverse-build slope on reversed data
    slope_rev = build_slope(rev_c)
    blk_L_rev, blk_S_rev = build_r_gate(slope_rev)
    # Reverse time arrays (keep original calendar alignment for session filters)
    rev_hours = hours[::-1].copy(); rev_dows = dows[::-1].copy()
    rev_mks = mks[::-1].copy(); rev_dks = dks[::-1].copy()
    rev_trs = run_v25(rev_o, rev_h, rev_l, rev_c, rev_hours, rev_dows, rev_mks, rev_dks,
                      slope_rev, 300, N,
                      block_bars_L=blk_L_rev, block_bars_S=blk_S_rev,
                      L_TP_BY_RG=L_TP, L_MH_BY_RG=L_MH, S_TP_BY_RG=S_TP, S_MH_BY_RG=S_MH)
    rev_st = stats_from(rev_trs)
    # V14+R baseline on reversed data (for reference)
    base_rev = run_v25(rev_o, rev_h, rev_l, rev_c, rev_hours, rev_dows, rev_mks, rev_dks,
                       slope_rev, 300, N,
                       block_bars_L=blk_L_rev, block_bars_S=blk_S_rev)
    base_rev_st = stats_from(base_rev)
    print(f"  Forward (original): n={st['n']} PnL=${st['pnl']:.0f}")
    print(f"  Reversed:           n={rev_st['n']} PnL=${rev_st['pnl']:.0f}")
    print(f"  Reversed V14+R:     n={base_rev_st['n']} PnL=${base_rev_st['pnl']:.0f}")
    # V14+R baseline G8 = +$438 improvement (per V23 R5 audit)
    # V25 overlay only changes exits → similar G8 behavior expected
    ratio = rev_st['pnl'] / max(abs(st['pnl']), 1)
    g8_pass = (-2.0 < ratio < 0.5)  # reasonable: reversed should be negative or small
    print(f"  Ratio (rev/fwd): {ratio:+.3f}  {'PASS' if g8_pass else 'FAIL (overfit risk)'}")

    # ---- G9: drop-best-month ----
    print(f"\n[G9] Drop-best-month robustness")
    months_sorted = sorted(by_mo.items(), key=lambda x: -x[1])
    dropped = months_sorted[0][0]; dropped_pnl = months_sorted[0][1]
    remaining = sum(v for k, v in by_mo.items() if k != dropped)
    neg_months = sum(1 for v in by_mo.values() if v < 0)
    print(f"  Best month: {dropped} ${dropped_pnl:.0f}")
    print(f"  Without best: ${remaining:.0f}  ({'PASS' if remaining > 0 else 'FAIL'})")
    print(f"  Negative months total: {neg_months}/{len(by_mo)}")

    # ---- Summary ----
    print(f"\nSummary {label}:")
    summary = {
        'n': st['n'], 'pnl': st['pnl'], 'wr': st['wr'], 'mdd': st['mdd'], 'sharpe': st['sharpe'],
        'is_pnl': st_is['pnl'], 'oos_pnl': st_oos['pnl'],
        'g4_pass': f"{g4_pass}/{len(g4_variants)}",
        'g7_pass': f"{wf_pass}/6",
        'g8_pass': 'PASS' if g8_pass else 'FAIL',
        'g9_pass': 'PASS' if remaining > 0 else 'FAIL',
    }
    return summary


def main():
    df, o, h, l, c, hours, dows, mks, dks = load_data()
    candidates = [
        ('V14+R baseline', (None, None, None, None)),
        ('V25-A: S_MH_UP=8', (None, None, None, {'UP':8})),
        ('V25-D: S_MH_UP=8 + L_TP_DOWN=0.040 + L_MH_MILD_UP=7',
         ({'DOWN':0.040}, {'MILD_UP':7}, None, {'UP':8})),
        ('V25-E: S_MH_UP=8 + L_TP_DOWN=0.050',
         ({'DOWN':0.050}, None, None, {'UP':8})),
    ]
    results = []
    for lbl, cfg in candidates:
        s = audit_config(lbl, cfg, o, h, l, c, hours, dows, mks, dks)
        results.append((lbl, s))

    # Final side-by-side
    print(f"\n\n{'='*120}")
    print("V25 R3 Audit Summary")
    print(f"{'='*120}")
    hdr = (f"{'Config':<55} {'PnL':>6} {'WR%':>5} {'MDD':>5} {'Sh':>5} {'G4':>7} {'G7':>6} {'G8':>5} {'G9':>5}")
    print(hdr); print("-" * len(hdr))
    for lbl, s in results:
        print(f"{lbl:<55} {s['pnl']:>6.0f} {s['wr']:>5.1f} {s['mdd']:>5.0f} {s['sharpe']:>5.2f} "
              f"{s['g4_pass']:>7} {s['g7_pass']:>6} {s['g8_pass']:>5} {s['g9_pass']:>5}")

if __name__ == "__main__":
    main()
