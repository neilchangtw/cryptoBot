"""
V28 — 複利化（equity-proportional sizing）研究
=================================================
問題：名目倉位從固定 $4,000 改為「權益 × 4」（維持現行曝險比：$4K/$1K=4x，
每側各一倉、兩倉同開時總曝險 8x 不變），複利會不會比較好？

方法：
  R1 歷史精確重放：引擎（V14+R+V25-D realistic）交易序列不動，
     以事件序（同 bar 先出後進，= 引擎處理順序）重放權益：
       - 固定注碼：notional = $4,000（驗證 = 引擎 baseline）
       - 複利：進場時 notional = m × 當下權益（m = 1~8，現行=4）
       - 月初再平衡變體：notional 每月初鎖定 m × 權益（實務上更好操作）
     指標：終值、CAGR、MDD%（權益峰谷）、最壞 30 天%、最大單筆虧損%
  R2 Kelly 檢查：對交易報酬分佈求 E[log(1+m·r)] 最大的 m*（r = pnl% - fee%）
     —— 複利下 m > m* 就是在傷幾何成長
  R3 Block bootstrap（月為塊，重排 1000 條路徑）：
     終值分佈 P5/P50/P95、MDD% 分佈、P(複利終值<固定)、P(權益腰斬)
  R4 壓力：抽掉最好的 3 個月重放（edge 衰退情境下複利的表現）

假設（誠實聲明）：
  - 交易序列沿用固定注碼引擎輸出。熔斷若百分比化（日虧 -20%·E 等），
    觸發時點與固定注碼近似同步（PnL% 等比），二階誤差忽略。
  - fee 等比：0.1% of notional（= 現行 $4/$4,000）。
  - bootstrap 內用逐筆序列重放（重疊倉位以事件先後近似），固定/複利同法，公平對比。
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

RNG = np.random.default_rng(42)
E0 = 1000.0
FEE_RATE = 4.0 / 4000.0          # 0.1% of notional
BASE_MULT = 4.0                  # 現行曝險比 $4,000 / $1,000
N_BOOT = 1000


# ---------------------------------------------------------------- 重放核心

def make_events(tdf):
    """事件序：(bar, 0=exit/1=entry, side_order, trade_idx)；同 bar 先出後進（= 引擎）。"""
    ev = []
    for idx, t in tdf.iterrows():
        so = 0 if t.side == 'L' else 1
        ev.append((t.entry_bar, 1, so, idx))
        ev.append((t.exit_bar, 0, so, idx))
    ev.sort()
    return ev


def replay(tdf, events, mode='fixed', mult=BASE_MULT, monthly_rebal=False,
           notional_cap=None):
    """回傳 (exit_dt 序列, 權益序列, 每筆 pnl$)。r_i = pnl%/100（價差報酬，不含費）。

    notional_cap: 單倉名目上限（流動性/槓桿分層的務實約束），到頂後等同固定注碼。
    """
    eq = E0
    notional_of = {}
    month_notional = None
    cur_month = None
    curve_dt, curve_eq, pnls = [], [], {}
    r = tdf['pnl_pct'].to_numpy() / 100.0

    for bar, kind, so, idx in events:
        if kind == 1:  # entry
            if mode == 'fixed':
                notional_of[idx] = 4000.0
            elif monthly_rebal:
                m = str(tdf.at[idx, 'entry_dt'])[:7]
                if m != cur_month:
                    cur_month = m
                    month_notional = mult * eq
                notional_of[idx] = month_notional
            else:
                notional_of[idx] = mult * eq
            if notional_cap is not None:
                notional_of[idx] = min(notional_of[idx], notional_cap)
        else:          # exit
            N = notional_of.pop(idx)
            pnl = N * r[idx] - N * FEE_RATE
            eq += pnl
            pnls[idx] = pnl
            curve_dt.append(str(tdf.at[idx, 'exit_dt']))
            curve_eq.append(eq)
            if eq <= 0:   # 爆倉保護（理論上 SafeNet 擋住，防數值荒謬）
                eq = 1e-9
    return curve_dt, np.array(curve_eq), pnls


def metrics(curve_dt, curve_eq, years):
    term = curve_eq[-1]
    peak = np.maximum.accumulate(np.concatenate([[E0], curve_eq]))
    dd = (np.concatenate([[E0], curve_eq]) - peak) / peak
    mdd_pct = -dd.min() * 100
    cagr = ((term / E0) ** (1 / years) - 1) * 100 if term > 0 else -100
    # 最壞 30 天（日終權益）
    s = pd.Series(curve_eq, index=pd.to_datetime(curve_dt))
    daily = s.resample('D').last().ffill()
    w30 = ((daily / daily.shift(30) - 1) * 100).min() if len(daily) > 31 else np.nan
    return term, cagr, mdd_pct, w30


# ---------------------------------------------------------------- main

def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades).reset_index(drop=True)
    events = make_events(tdf)
    years = (pd.to_datetime(str(tdf.exit_dt.iloc[-1])) -
             pd.to_datetime(str(tdf.entry_dt.iloc[0]))).days / 365.25

    print("=" * 78)
    print("V28 複利化研究（V14+R+V25-D realistic，2 年，起始權益 $1,000）")
    print("=" * 78)

    # ---- R1 歷史重放 ----
    dt_f, eq_f, pnl_f = replay(tdf, events, 'fixed')
    base_engine = tdf.pnl_usd.sum()
    print(f"\n### R1 歷史精確重放")
    print(f"  固定注碼重放總 PnL ${eq_f[-1] - E0:+,.0f} vs 引擎 ${base_engine:+,.0f}"
          f"（差 ${abs(eq_f[-1] - E0 - base_engine):.1f}，捨入誤差）")

    print(f"\n  {'模式':<14} {'終值':>10} {'CAGR':>7} {'MDD%':>7} {'最壞30天':>9} {'最大單筆虧':>10}")
    rows = {}
    t, c, m, w = metrics(dt_f, eq_f, years)
    worst_f = min(pnl_f.values())
    print(f"  {'固定 $4,000':<14} {t:>10,.0f} {c:>6.1f}% {m:>6.1f}% {w:>8.1f}% "
          f"{worst_f:>+10.0f}")
    rows['fixed'] = (t, c, m, w)

    for mult in (1, 2, 3, 4, 5, 6, 8):
        dt_c, eq_c, pnl_c = replay(tdf, events, 'compound', mult=mult)
        t, c, m, w = metrics(dt_c, eq_c, years)
        worst = min(pnl_c.values())
        worst_pct = min(np.array(list(pnl_c.values())) /
                        np.array([1.0]))  # 佔進場時權益% 另算
        tag = f"複利 {mult}x" + ("（現行曝險）" if mult == 4 else "")
        print(f"  {tag:<14} {t:>10,.0f} {c:>6.1f}% {m:>6.1f}% {w:>8.1f}% {worst:>+10.0f}")
        rows[mult] = (t, c, m, w)

    dt_m, eq_m, _ = replay(tdf, events, 'compound', mult=4, monthly_rebal=True)
    t, c, m, w = metrics(dt_m, eq_m, years)
    print(f"  {'複利4x月再平衡':<14} {t:>10,.0f} {c:>6.1f}% {m:>6.1f}% {w:>8.1f}%")

    # 務實變體：複利 4x + 單倉名目上限（流動性/Binance 槓桿分層約束；到頂=固定注碼）
    print(f"\n  務實變體（複利 4x + 名目上限，月再平衡）：")
    for cap in (20_000, 40_000, 100_000, 400_000):
        dt_c, eq_c, pnl_c = replay(tdf, events, 'compound', mult=4,
                                   monthly_rebal=True, notional_cap=cap)
        t, c, m, w = metrics(dt_c, eq_c, years)
        print(f"  {'cap $' + format(cap, ','):<14} {t:>10,.0f} {c:>6.1f}% {m:>6.1f}% "
              f"{w:>8.1f}% {min(pnl_c.values()):>+10.0f}")

    # ---- R2 Kelly ----
    r = tdf.pnl_pct.to_numpy() / 100.0 - FEE_RATE
    grid = np.arange(0.5, 20.01, 0.25)
    elog = []
    for mm in grid:
        x = 1 + mm * r
        elog.append(np.mean(np.log(x)) if (x > 0).all() else -np.inf)
    elog = np.array(elog)
    m_star = grid[elog.argmax()]
    # 每筆權益報酬統計（m=4）
    re4 = 4 * r
    print(f"\n### R2 Kelly / 幾何成長檢查（r = 單筆 pnl% − fee 0.1%）")
    print(f"  單筆 r：mean {r.mean()*100:+.3f}% / std {r.std()*100:.3f}% / min {r.min()*100:+.2f}%")
    print(f"  m=4 時單筆權益報酬：mean {re4.mean()*100:+.2f}% / std {re4.std()*100:.2f}% / "
          f"min {re4.min()*100:+.1f}%")
    print(f"  E[log(1+m·r)] 最大化 m* = {m_star:.2f}"
          f"（m=4 的 E[log] = {elog[np.searchsorted(grid, 4.0)]:.5f}，"
          f"m* 的 = {elog.max():.5f}）")
    print(f"  → 現行 4x {'低於' if 4 < m_star else '高於'} Kelly 最適，"
          f"複利在幾何層面{'可行' if 4 < m_star else '有害'}"
      )

    # ---- R3 Block bootstrap（月塊重排）----
    tdf['_month'] = tdf.entry_dt.astype(str).str[:7]
    months = tdf['_month'].unique()
    by_month = {mo: tdf[tdf._month == mo] for mo in months}

    def seq_replay(sub_list, mode, mult=BASE_MULT):
        eq = E0
        curve = [eq]
        for sub in sub_list:
            for rr in sub.pnl_pct.to_numpy() / 100.0:
                N = 4000.0 if mode == 'fixed' else mult * eq
                eq += N * rr - N * FEE_RATE
                if eq <= 0:
                    eq = 1e-9
                curve.append(eq)
        curve = np.array(curve)
        peak = np.maximum.accumulate(curve)
        return curve[-1], -((curve - peak) / peak).min() * 100, curve.min()

    term_f, term_c, mdd_f, mdd_c, halve_f, halve_c, cworse = [], [], [], [], 0, 0, 0
    for _ in range(N_BOOT):
        sample = [by_month[mo] for mo in RNG.choice(months, size=len(months), replace=True)]
        tf, mf, lof = seq_replay(sample, 'fixed')
        tc, mc, loc = seq_replay(sample, 'compound')
        term_f.append(tf); term_c.append(tc)
        mdd_f.append(mf); mdd_c.append(mc)
        halve_f += (lof < E0 * 0.5); halve_c += (loc < E0 * 0.5)
        cworse += (tc < tf)
    term_f, term_c = np.array(term_f), np.array(term_c)
    mdd_f, mdd_c = np.array(mdd_f), np.array(mdd_c)

    def q(a):
        return np.percentile(a, [5, 50, 95])

    print(f"\n### R3 Block bootstrap（月塊重排 {N_BOOT} 條路徑，2 年）")
    print(f"  {'':<12} {'P5':>10} {'P50':>10} {'P95':>12}")
    p = q(term_f); print(f"  固定 終值   {p[0]:>10,.0f} {p[1]:>10,.0f} {p[2]:>12,.0f}")
    p = q(term_c); print(f"  複利4x 終值 {p[0]:>10,.0f} {p[1]:>10,.0f} {p[2]:>12,.0f}")
    p = q(mdd_f); print(f"  固定 MDD%   {p[0]:>9.1f}% {p[1]:>9.1f}% {p[2]:>11.1f}%")
    p = q(mdd_c); print(f"  複利4x MDD% {p[0]:>9.1f}% {p[1]:>9.1f}% {p[2]:>11.1f}%")
    print(f"  P(複利終值 < 固定終值) = {cworse / N_BOOT * 100:.1f}%")
    print(f"  P(權益曾腰斬 <$500)：固定 {halve_f / N_BOOT * 100:.1f}% / "
          f"複利4x {halve_c / N_BOOT * 100:.1f}%")

    # ---- R4 壓力：抽掉最好的 3 個月 ----
    msum = tdf.groupby('_month').pnl_usd.sum().sort_values(ascending=False)
    best3 = set(msum.index[:3])
    keep = [by_month[mo] for mo in months if mo not in best3]
    tf, mf, _ = seq_replay(keep, 'fixed')
    tc, mc, _ = seq_replay(keep, 'compound')
    print(f"\n### R4 壓力：抽掉最好的 3 個月（{', '.join(sorted(best3))}）")
    print(f"  固定：終值 {tf:,.0f}（MDD {mf:.1f}%） / 複利4x：終值 {tc:,.0f}（MDD {mc:.1f}%）")

    # 半 Kelly 對照（若 m* < 8）
    tf2, mf2, _ = seq_replay(keep, 'compound', mult=m_star / 2)
    print(f"  複利 m*/2={m_star/2:.1f}x：終值 {tf2:,.0f}（MDD {mf2:.1f}%）")


if __name__ == '__main__':
    main()
