"""
V29 — Edge 衰退警報（回測驗證）
=================================
問題：V14 是 regime-dependent（G8 FAIL），edge 會過期且不會通知。
目標：定義一個可以掛在實盤上的「edge 衰退警報」規則，使得
  (a) 過去兩年 edge 活著時幾乎不誤報（含 2024 夏天開局爛牌段）
  (b) edge 真死掉時能在可接受的延遲內偵測到

方法論：
  - 歷史只有「edge 活著」的樣本 → 誤報率用真實序列 + 月塊 bootstrap 驗證；
    偵測力用合成死亡情境驗證：
      情境 A（歸零）：死亡後每筆 PnL 從歷史分佈抽樣後去均值（E=0，突破不再延續）
      情境 B（反轉）：均值鏡像為負（-μ，結構反噬）
      情境 C（緩衰）：6 個月線性從 μ 衰到 0，之後停在 0
  - 三類規則（皆可由 trades.csv 因果計算，用 200U 基準 R 單位——
    實盤按 pnl_usd × 200/當筆保證金 正規化，不受複利影響）：
      1. CUSUM 下移變點：S_t = max(0, S_{t-1} + (k − x_t))，S_t > h 觸發
         （k = μ/2 = 目標偵測「均值掉到 0」的標準 allowance）
      2. 滾動 W 筆 PnL 總和 < 門檻
      3. 滾動 W 筆 TP 出場佔比 < 門檻（獲利引擎健康度）
  - 評估：
      誤報：真實 273 筆序列會不會響、何時響；bootstrap 1000 條健康 2 年路徑 P(誤報)
      偵測：健康 12 個月 + 死亡 12 個月，500 條/情境，偵測延遲（筆 → 換算月）
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
N_HEALTHY = 1000
N_DEATH = 500
TRADES_PER_MONTH = None  # 由歷史推


# ────────────────────────────────────────────── 規則

def alarm_cusum(x, reasons, k, h):
    S = 0.0
    for i, xi in enumerate(x):
        S = max(0.0, S + (k - xi))
        if S > h:
            return i
    return None


def alarm_rollsum(x, reasons, w, th):
    if len(x) < w:
        return None
    cs = np.concatenate([[0.0], np.cumsum(x)])
    for i in range(w - 1, len(x)):
        if cs[i + 1] - cs[i + 1 - w] < th:
            return i
    return None


def alarm_tpshare(x, reasons, w, th):
    if len(reasons) < w:
        return None
    is_tp = np.array([r == 'TP' for r in reasons], dtype=float)
    cs = np.concatenate([[0.0], np.cumsum(is_tp)])
    for i in range(w - 1, len(reasons)):
        if (cs[i + 1] - cs[i + 1 - w]) / w < th:
            return i
    return None


# ────────────────────────────────────────────── 序列產生

def healthy_path(by_month, months, n_months=24):
    seq_p, seq_r = [], []
    for mo in RNG.choice(months, size=n_months, replace=True):
        seq_p.extend(by_month[mo][0])
        seq_r.extend(by_month[mo][1])
    return np.array(seq_p), seq_r


def death_segment(pnl_all, reasons_all, mu, mode, n_months, tpm):
    """死亡段：逐月抽 trade 數，逐筆 iid 抽歷史交易後調整均值。
    出場 reason 同步抽（TP 佔比在死亡時自然下降：PnL 調整後 TP 單機率不變，
    但保守起見 reason 用「洗牌後」的原標籤——TP share 規則的偵測力因此偏保守）。"""
    n = max(1, int(round(n_months * tpm)))
    idx = RNG.integers(0, len(pnl_all), size=n)
    x = pnl_all[idx].astype(float)
    r = [reasons_all[j] for j in idx]
    if mode == 'A':          # 歸零
        x = x - mu
    elif mode == 'B':        # 反轉
        x = x - 2 * mu
    elif mode == 'C':        # 6 個月線性衰退 → 0
        fade_n = int(round(6 * tpm))
        adj = np.concatenate([np.linspace(0, mu, min(fade_n, n)),
                              np.full(max(0, n - fade_n), mu)])
        x = x - adj
    # reason 修正：死亡時 TP 不再那麼容易達成 → 把「調整後變成虧損」的 TP 改標為 MH
    r = ['MH' if (rr == 'TP' and xi <= 0) else rr for rr, xi in zip(r, x)]
    return x, r


# ────────────────────────────────────────────── main

def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades)
    pnl = tdf.pnl_usd.to_numpy(float)          # 200U 基準 $（= R×200）
    reasons = list(tdf.exit_reason)
    tdf['_m'] = tdf.entry_dt.astype(str).str[:7]
    months = tdf['_m'].unique()
    by_month = {mo: (tdf[tdf._m == mo].pnl_usd.tolist(),
                     tdf[tdf._m == mo].exit_reason.tolist()) for mo in months}
    mu, sd = pnl.mean(), pnl.std()
    tpm = len(tdf) / len(months)

    print("=" * 96)
    print("V29 — Edge 衰退警報回測驗證")
    print(f"樣本：{len(tdf)} 筆 / {len(months)} 月（{tpm:.1f} 筆/月）  "
          f"單筆 μ=${mu:.1f} σ=${sd:.1f}  TP佔比={reasons.count('TP')/len(reasons)*100:.0f}%")
    print("=" * 96)

    # 規則網格
    k = mu / 2
    rules = []
    for h in (300, 450, 600, 800):
        rules.append((f"CUSUM k=μ/2 h={h}", alarm_cusum, (k, h)))
    for w, th in ((30, 0), (30, -150), (40, 0), (40, -150)):
        rules.append((f"Roll{w}筆 sum<{th}", alarm_rollsum, (w, th)))
    for w, th in ((30, 0.15), (30, 0.20), (40, 0.20)):
        rules.append((f"TP佔比{w}筆<{int(th*100)}%", alarm_tpshare, (w, th)))

    # ── 1) 真實歷史誤報 ──
    print(f"\n{'規則':<22} {'歷史會響?':>10}", end="")
    hist_fire = {}
    for name, fn, args in rules:
        i = fn(pnl, reasons, *args)
        hist_fire[name] = i
        print()
        when = f"第{i+1}筆({tdf.entry_dt.iloc[i][:7]})" if i is not None else "不響 ✓"
        print(f"{name:<22} {when:>14}", end="")
    print()

    # ── 2) bootstrap 健康路徑誤報率 ──
    fa = {name: 0 for name, _, _ in rules}
    for _ in range(N_HEALTHY):
        x, r = healthy_path(by_month, months)
        for name, fn, args in rules:
            if fn(x, r, *args) is not None:
                fa[name] += 1

    # ── 3) 死亡情境偵測延遲 ──
    det = {name: {m: [] for m in 'ABC'} for name, _, _ in rules}
    miss = {name: {m: 0 for m in 'ABC'} for name, _, _ in rules}
    pre_fa = {name: 0 for name, _, _ in rules}
    for mode in 'ABC':
        for _ in range(N_DEATH):
            hx, hr = healthy_path(by_month, months, n_months=12)
            dx, dr = death_segment(pnl, reasons, mu, mode, 12, tpm)
            x = np.concatenate([hx, dx]); r = hr + dr
            d0 = len(hx)
            for name, fn, args in rules:
                i = fn(x, r, *args)
                if i is None:
                    miss[name][mode] += 1
                elif i < d0:
                    pre_fa[name] += 1
                else:
                    det[name][mode].append((i - d0) / tpm)  # 月

    # ── 匯總表 ──
    print("\n" + "=" * 96)
    print(f"{'規則':<22} {'歷史':>6} {'健康2年誤報率':>12} "
          f"{'A歸零偵測(月,中位)':>16} {'B反轉':>8} {'C緩衰':>8} {'漏報A%':>7}")
    print("-" * 96)
    for name, fn, args in rules:
        h = "響!" if hist_fire[name] is not None else "✓"
        far = fa[name] / N_HEALTHY * 100
        def med(m):
            v = det[name][m]
            return f"{np.median(v):.1f}" if v else "—"
        missA = miss[name]['A'] / N_DEATH * 100
        print(f"{name:<22} {h:>6} {far:>11.1f}% {med('A'):>16} {med('B'):>8} {med('C'):>8} "
              f"{missA:>6.0f}%")

    print(f"""
判讀基準：
  - 「歷史」必須 ✓（含 2024-07~09 開局爛牌不誤殺）
  - 健康 2 年誤報率越低越好（每次誤報 = 白白降槓桿一段時間）
  - A 歸零偵測月數 = edge 死後多久發現（期間受月虧熔斷保護，
    失血上限 ~R225/月 = 300U 下 ~$340/月）
""")


if __name__ == '__main__':
    main()
