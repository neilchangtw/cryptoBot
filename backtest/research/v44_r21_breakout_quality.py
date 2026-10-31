"""V44 第 21 輪：突破本身的品質（壓縮持續、4h 壓縮、突破棒強度）。

預登記：doc/v44_maxhold_reframe_plan.md 第 24 節。步驟 A 五分位分組（不判）；只有兩期同向單調且最差分位都虧的特徵才進步驟 B。
期間 P1 = 進場 < 2024-07-01，P2 = 之後。固定 200U、realistic、每筆 +$5。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r21_breakout_quality.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR
from v44_r15_lowvolpause import build_sim, part, run

P_END = pd.Timestamp("2024-07-01")
FEATS = {"F1 壓縮持續": "f1", "F2 4h壓縮": "f2", "F3a CLV": "f3a", "F3b 實體比": "f3b", "F3c 量比": "f3c"}
MIN_RHO = 0.6
MAX_B = 3


def streak_before(pct, th):
    out = np.zeros(len(pct), int)
    run_len = 0
    for i, p in enumerate(pct):
        out[i] = run_len
        run_len = run_len + 1 if (not np.isnan(p) and p < th) else 0
    return out


def atr4h_rank(candles, dt):
    """已收盤 4h K 的 ATR14% 在過去 180 根 4h 的百分位，對齊到 1h 棒（4h 收盤當根即可用）。"""
    t = pd.to_datetime(dt)
    g = candles.assign(t=t.values, b=t.dt.floor("4h").values)
    k = g.groupby("b").agg(h=("high", "max"), l=("low", "min"), c=("close", "last"), n=("close", "size"), end=("t", "max"))
    k = k[k.n == 4]
    pc = k.c.shift(1)
    tr = pd.concat([k.h - k.l, (k.h - pc).abs(), (k.l - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean() / k.c * 100
    rank = atr.rolling(180, min_periods=180).rank(pct=True) * 100
    return pd.Series(rank.to_numpy(), index=k.end).reindex(t.values, method="ffill").to_numpy()


def features(candles, ind, dt, engine):
    o, h, l, c, v = (candles[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    rng = np.where(h > l, h - l, np.nan)
    med = pd.Series(v).shift(1).rolling(24).median().to_numpy()
    f2 = atr4h_rank(candles, dt)
    return {
        "f1": {"L": streak_before(ind["pctile_L"], engine.L_GK_TH).astype(float), "S": streak_before(ind["pctile_S"], engine.S_GK_TH).astype(float)},
        "f2": {"L": f2, "S": f2},
        "f3a": {"L": (c - l) / rng, "S": (h - c) / rng},
        "f3b": {"L": np.abs(c - o) / rng, "S": np.abs(c - o) / rng},
        "f3c": {"L": v / med, "S": v / med},
    }


def rho(means):
    s = pd.Series(means).rank()
    return s.corr(pd.Series(range(len(means)), dtype=float))


def buckets(g, col, cuts):
    q = np.digitize(g[col], cuts)
    return [g[q == k] for k in range(5)]


def stats(x):
    p = x.pnl_usd
    if not len(x):
        return "  0筆"
    return f"{len(x):3d}筆 WR {(p > 0).mean() * 100:3.0f}% ${p.sum():6,.0f} 均${p.mean():6.1f} MH {(x.exit_reason == 'MH').mean() * 100:3.0f}%"


def line(pnl_stats):
    s = pnl_stats
    return f"{s['n']:3d}筆 WR {s['wr']:5.1f}% ${s['pnl']:7,.0f} MDD ${s['mdd']:5,.0f}"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    base = {slip: run(sim, ind, dt, {}, slip) for slip in (0, 2, 5)}
    f = base[0].copy()
    feats = features(candles, ind, dt, engine)
    i = f.entry_bar.astype(int).to_numpy()
    for col, arr in feats.items():
        f[col] = np.where(f.side == "L", arr["L"][i], arr["S"][i])
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；基準 {len(f)} 筆；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    eligible = []
    print("\n[步驟 A：五分位分組（切點用全期基準交易，0bp，不判）]")
    for name, col in FEATS.items():
        d = f.dropna(subset=[col])
        cuts = np.unique(np.quantile(d[col], [0.2, 0.4, 0.6, 0.8]))
        print(f"\n  {name}（{len(d)} 筆有值）切點 " + " ".join(f"{x:.2f}" for x in cuts))
        for lab, g in (("合併", d), ("L", d[d.side == "L"]), ("S", d[d.side == "S"])):
            a, b = g[g.entry_dt < P_END], g[g.entry_dt >= P_END]
            qa, qb = buckets(a, col, cuts), buckets(b, col, cuts)
            ma = [x.pnl_usd.mean() if len(x) else np.nan for x in qa]
            mb = [x.pnl_usd.mean() if len(x) else np.nan for x in qb]
            ra, rb = rho(ma), rho(mb)
            if lab == "合併":
                for k in range(len(qa)):
                    print(f"    Q{k + 1} P1 {stats(qa[k])} | P2 {stats(qb[k])}")
            print(f"    {lab} 等級相關 P1 {ra:+.2f}、P2 {rb:+.2f}")
            if lab == "合併" and np.sign(ra) == np.sign(rb) and min(abs(ra), abs(rb)) >= MIN_RHO:
                worst = 0 if ra > 0 else len(qa) - 1            # ρ>0：越大越好，最差在 Q1
                if qa[worst].pnl_usd.sum() < 0 and qb[worst].pnl_usd.sum() < 0:
                    eligible.append((min(abs(ra), abs(rb)), name, col, ra))

    eligible = sorted(eligible, reverse=True)[:MAX_B]
    print("\n[步驟 B 資格] " + ("、".join(f"{n}（ρ {r:+.2f}）" for _, n, _, r in eligible) if eligible else "沒有特徵合格，不做 B"))
    for _, name, col, r in eligible:
        p1 = f[f.entry_dt < P_END][col].dropna()
        cands = {}
        for pct in (20, 10, 30):
            th = np.percentile(p1, pct if r > 0 else 100 - pct)
            okL = ~(feats[col]["L"] < th) if r > 0 else ~(feats[col]["L"] > th)
            okS = ~(feats[col]["S"] < th) if r > 0 else ~(feats[col]["S"] > th)
            cands[pct] = (th, {"okL": okL, "okS": okS})
        res = {}
        print(f"\n  {name}：門檻 P1 " + " ".join(f"第{p}百分位={v[0]:.3f}" for p, v in cands.items()))
        for slip in (0, 2, 5):
            b = base[slip]
            res[(slip, "基準")] = (part(b[b.entry_dt < P_END]), part(b[b.entry_dt >= P_END]))
            print(f"   [{slip}bp] 基準 {line(res[(slip, '基準')][0])} | {line(res[(slip, '基準')][1])}")
            for pct, (_, extra) in cands.items():
                x = run(sim, ind, dt, extra, slip)
                res[(slip, pct)] = (part(x[x.entry_dt < P_END]), part(x[x.entry_dt >= P_END]))
                print(f"   [{slip}bp] P{pct} {line(res[(slip, pct)][0])} | {line(res[(slip, pct)][1])}")
                if slip == 0:
                    x.to_csv(OUT_DIR / f"r21_{col}_P{pct}_trades.csv", index=False)
        extra = cands[20][1]
        ok_entry = np.where(f.side == "L", extra["okL"][i], extra["okS"][i])
        blocked = (f.pnl_usd[~ok_entry & (f.entry_dt < P_END)].sum(), f.pnl_usd[~ok_entry & (f.entry_dt >= P_END)].sum())
        ok = {f"P1 {s}bp勝": res[(s, 20)][0]["pnl"] > res[(s, "基準")][0]["pnl"] for s in (0, 2, 5)}
        ok["P1 MDD"] = res[(0, 20)][0]["mdd"] <= res[(0, "基準")][0]["mdd"] * 1.1
        ok.update({f"P2 {s}bp勝": res[(s, 20)][1]["pnl"] > res[(s, "基準")][1]["pnl"] for s in (0, 2)})
        ok["P2 MDD"] = res[(0, 20)][1]["mdd"] <= res[(0, "基準")][1]["mdd"] * 1.1
        nb = sum(res[(0, p)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for p in (10, 30))
        ok["鄰域≥1/2"] = nb >= 1
        ok["被擋P1<0"], ok["被擋P2<0"] = blocked[0] < 0, blocked[1] < 0
        verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
        print("   " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}（被擋 P1 ${blocked[0]:,.0f}、P2 ${blocked[1]:,.0f}）")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
