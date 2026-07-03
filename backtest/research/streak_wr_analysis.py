"""
連敗條件勝率分析 — 連虧 N 筆後進場的下一筆，勝率/期望值如何？
================================================================
方法：
  - 用 V27 引擎（= 線上 V14+R+V25-D，realistic 成交）跑全期 2 年
  - 對每筆交易，因果地重建「進場當下的連敗數」：
    取所有 exit_bar <= 該筆 entry_bar 的交易，依 (exit_bar, side=L 先) 排序
    （同 bar 時引擎先處理 L 出場再 S 出場，再處理進場），從最近一筆往回數連續虧損
    —— 與引擎 consec 熔斷計數器同義，進場當下完全可知，無前瞻
  - 分組（0 / 1 / 2 / 3 / 4+）看下一筆 WR、平均 PnL，附 Wilson 95% CI
  - Permutation 檢定：把交易結果順序洗牌 2000 次，看觀察到的組間 WR 偏離
    是否超出「結果與順序無關」的 null 分佈（連敗後 WR 高/低可能純粹是運氣）
  - 加映：連勝 N 筆後的下一筆（對照）

注意：引擎本身有「連虧 4 筆 → 24 bar 冷卻」熔斷，4+ 組的進場已被此規則篩過。
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
N_PERM = 2000


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return center - half, center + half


def trailing_streak(outcomes):
    """outcomes: list of bool(win) 依時序。回傳最後的連敗數（>0 敗 / 以 -連勝數表連勝）。"""
    if not outcomes:
        return 0
    n = 0
    last = outcomes[-1]
    for w in reversed(outcomes):
        if w == last:
            n += 1
        else:
            break
    return -n if last else n  # 敗=正數、勝=負數


def build_streaks(tdf):
    """每筆交易進場當下的 streak（+N=連虧 N、-N=連勝 N、0=前面沒有交易）。"""
    # 出場事件時序：同 bar 引擎先 L 後 S
    ev = tdf.sort_values(['exit_bar', 'side'], ascending=[True, True]).reset_index()
    ev_bars = ev['exit_bar'].to_numpy()
    ev_win = (ev['pnl_usd'].to_numpy() > 0)

    streaks = []
    for _, t in tdf.iterrows():
        # 同 bar 出場先於進場處理 → exit_bar <= entry_bar 都可知
        mask = ev_bars <= t.entry_bar
        streaks.append(trailing_streak(list(ev_win[mask])))
    return np.array(streaks)


def group_label(s):
    if s <= 0:
        return None  # 這裡只看連敗；0/連勝另表
    return min(s, 4)


def print_table(title, tdf, streaks, groups):
    print(f"\n### {title}")
    print(f"  {'連敗':<6} {'n':>4} {'下一筆WR':>9} {'Wilson95%CI':>16} {'平均PnL':>9} {'合計PnL':>10}")
    overall_wr = (tdf.pnl_usd > 0).mean()
    for g, label in groups:
        m = np.isin(streaks, g)
        sub = tdf[m]
        n = len(sub)
        if n == 0:
            print(f"  {label:<6} {0:>4}")
            continue
        k = int((sub.pnl_usd > 0).sum())
        lo, hi = wilson_ci(k, n)
        print(f"  {label:<6} {n:>4} {k / n * 100:>8.1f}% {lo * 100:>7.1f}~{hi * 100:<5.1f}% "
              f"{sub.pnl_usd.mean():>+9.1f} {sub.pnl_usd.sum():>+10.0f}")
    print(f"  （全體 WR = {overall_wr * 100:.1f}%）")


def perm_test(tdf, streaks):
    """null：結果與順序無關。統計量 = 各連敗組 WR 與全體 WR 的最大絕對偏離（n>=10 的組）。"""
    wins = (tdf.pnl_usd > 0).to_numpy()
    overall = wins.mean()

    def stat(streak_arr, win_arr):
        dev = 0.0
        for g in (1, 2, 3):
            m = streak_arr == g
            if m.sum() >= 10:
                dev = max(dev, abs(win_arr[m].mean() - overall))
        m = streak_arr >= 4
        if m.sum() >= 10:
            dev = max(dev, abs(win_arr[m].mean() - overall))
        return dev

    obs = stat(streaks, wins)

    # 洗牌：打亂「結果序列」重算 streak（交易時點/結構不變，只斷開結果與順序的關聯）
    null = []
    order = np.argsort(tdf.entry_bar.to_numpy())
    for _ in range(N_PERM):
        wp = RNG.permutation(wins)
        # 簡化 null：以交易序（entry_bar 排序）的前一段尾端連敗計 streak
        st = np.zeros(len(wp), dtype=int)
        run = 0
        for idx, j in enumerate(order):
            st[j] = run
            run = run + 1 if not wp[j] else 0
        null.append(stat(st, wp))
    null = np.array(null)
    p = float((null >= obs).mean())
    return obs, null, p


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades)

    print("=" * 76)
    print("連敗後下一筆勝率分析（V14+R+V25-D realistic，2 年全期）")
    print(f"{len(tdf)} 筆，全體 WR {(tdf.pnl_usd > 0).mean() * 100:.1f}%，"
          f"總 PnL ${tdf.pnl_usd.sum():+,.0f}")
    print("=" * 76)

    streaks = build_streaks(tdf)

    groups = [([1], '1'), ([2], '2'), ([3], '3'), ([4, 5, 6, 7, 8, 9, 10], '4+')]
    print_table("全部交易（L+S 合併連敗計數，= 引擎熔斷同款）", tdf, streaks, groups)

    # 對照：連勝後 / 無前史
    groups_w = [([0], '無前史'), ([-1], '連勝1'), ([-2], '連勝2'),
                ([-3, -4, -5, -6, -7, -8, -9, -10], '連勝3+')]
    print(f"\n### 對照：連勝後的下一筆")
    print(f"  {'狀態':<8} {'n':>4} {'下一筆WR':>9} {'平均PnL':>9}")
    for g, label in groups_w:
        m = np.isin(streaks, g)
        sub = tdf[m]
        if len(sub):
            print(f"  {label:<8} {len(sub):>4} {(sub.pnl_usd > 0).mean() * 100:>8.1f}% "
                  f"{sub.pnl_usd.mean():>+9.1f}")

    # 分側（各自只看自己側的交易，但 streak 仍用全體 = 進場當下實際可知的狀態）
    for side in ('L', 'S'):
        m_side = (tdf.side == side).to_numpy()
        print_table(f"{side} 側進場（streak 為全體連敗數）",
                    tdf[m_side], streaks[m_side], groups)

    # Permutation 檢定
    obs, null, p = perm_test(tdf, streaks)
    print(f"\n### Permutation 檢定（順序與結果無關的 null，{N_PERM} 次）")
    print(f"  觀察最大 WR 偏離 = {obs * 100:.1f}pp")
    print(f"  null：mean {null.mean() * 100:.1f}pp / P95 {np.percentile(null, 95) * 100:.1f}pp")
    print(f"  p-value = {p:.3f}  → {'顯著' if p < 0.05 else '不顯著（連敗後 WR 差異在運氣範圍內）'}")


if __name__ == '__main__':
    main()
