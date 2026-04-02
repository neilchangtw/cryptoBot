"""
快速統計：不重跑回測，只統計出場類型次數和損益
直接 import safenet_backtest 的函數
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接跑一次（只跑 6 組，很快）
exec(open(os.path.join(os.path.dirname(__file__), "btc_safenet_backtest.py")).read().split("# ============================================================\n# 對比測試")[0])

# 上面會載入 df, entries, run_old, run_new, stats

print(f"\n{'='*120}")
print("出場類型統計（次數 + 損益拆解）")
print(f"{'='*120}")

for ename, efn in entries.items():
    print(f"\n--- {ename} ---")

    for label, fn, ex in [
        ("OLD adaptive", lambda e=efn, x="adaptive": run_old(df, e, exit_mode=x), "adaptive"),
        ("OLD ema9", lambda e=efn, x="ema9": run_old(df, e, exit_mode=x), "ema9"),
        ("NEW adaptive", lambda e=efn, x="adaptive": run_new(df, e, exit_mode=x), "adaptive"),
        ("NEW ema9", lambda e=efn, x="ema9": run_new(df, e, exit_mode=x), "ema9"),
    ]:
        tdf = fn()
        if len(tdf) == 0: continue

        total_n = len(tdf)
        total_pnl = tdf["pnl"].sum()

        print(f"\n  {label}:")
        print(f"    {'類型':<12s} {'次數':>6s} {'佔比':>7s} {'損益':>12s} {'平均':>10s}")
        print(f"    {'-'*50}")

        for t in ["SL", "SafeNet", "TP1", "Trail"]:
            sub = tdf[tdf["type"] == t]
            if len(sub) == 0: continue
            pct = len(sub) / total_n * 100
            pnl = sub["pnl"].sum()
            avg = sub["pnl"].mean()
            print(f"    {t:<12s} {len(sub):>6d} {pct:>6.1f}% ${pnl:>+10,.2f} ${avg:>+8.2f}")

        print(f"    {'TOTAL':<12s} {total_n:>6d}        ${total_pnl:>+10,.2f}")

print("\nDone.")
