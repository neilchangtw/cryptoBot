"""V44 第 9 輪：突破後不追市價，改掛限價等回踩進場。

預登記：doc/v44_maxhold_reframe_plan.md 第 12 節。第 1～8 輪顯示改 MH 出場規則全族無效，MH 虧損是進場價的代價；
本輪只改進場方式，訊號、TP／SafeNet／MH／延長全部不變（TP／SafeNet 以成交價為基準，MH 從成交棒起算）。

規則（L；S 對稱）：
  訊號棒 i（原本的進場條件：GK、突破、時段、R gate）收盤後掛買單 P = close_i - k × ATR14_i，有效 N 根。
  第 j 根（i < j ≤ i+N）low ≤ P × (1 - 5bp) 才算成交（穿過才算，不用碰價），成交價 min(P, open_j)，
  費率仍用 taker、不加進場滑價。逾時未成交就放棄。掛單中又出現新訊號 → 以新訊號重掛。
  成交當根之後才開始判斷出場（1h 無法分辨同根先後）；成交當根若 low 已破 SafeNet 價，另外計數揭露。
  冷卻、月上限、熔斷在成交棒判斷。

候選：中心 K0.5N3（k=0.5、N=3）；鄰域 K0.25N3、K0.75N3、K0.5N1、K0.5N6。
期間 P1 = 進場 < 2024-07-01，P2 = 之後。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r9_limitpb.py
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

P_END = pd.Timestamp("2024-07-01")
PEN = 0.0005                     # 穿過限價 5bp 才算成交
CANDS = {"K0.5N3": (0.5, 3), "K0.25N3": (0.25, 3), "K0.75N3": (0.75, 3), "K0.5N1": (0.5, 1), "K0.5N6": (0.5, 6)}
CENTER = "K0.5N3"


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        ("""            hr not in L_BLK_H and dw not in L_BLK_D and
            not (rblk_l is not None and bool(rblk_l[i])) and
            not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):""",
         """            ((hr not in L_BLK_H and dw not in L_BLK_D and
            not (rblk_l is not None and bool(rblk_l[i])) and
            not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]) if 'fillL' not in ind else ind['fillL'][i])):"""),
        ("lp_entry = ci * (1 + slip) if realistic else ci  # 市價買進付高",
         "lp_entry = (ci * (1 + slip) if realistic else ci) if 'fillL' not in ind else ind['fpL'][i]"),
        ("""            hr not in S_BLK_H and dw not in S_BLK_D and
            not (rblk_s is not None and bool(rblk_s[i])) and
            not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):""",
         """            ((hr not in S_BLK_H and dw not in S_BLK_D and
            not (rblk_s is not None and bool(rblk_s[i])) and
            not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]) if 'fillS' not in ind else ind['fillS'][i])):"""),
        ("sp_entry = ci * (1 - slip) if realistic else ci  # 市價賣出收低",
         "sp_entry = (ci * (1 - slip) if realistic else ci) if 'fillS' not in ind else ind['fpS'][i]"),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old[:60]
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def raw_signals(engine, ind):
    hr, dw = ind["hours"], ind["dows"]
    n = len(ind["c"])
    rl, rs = ind.get("regime_block_l"), ind.get("regime_block_s")
    pL, pS = ind["pctile_L"], ind["pctile_S"]
    sl = np.zeros(n, bool); ss = np.zeros(n, bool)
    for i in range(n):
        sl[i] = (hr[i] not in engine.L_BLK_H and dw[i] not in engine.L_BLK_D and not (rl is not None and bool(rl[i]))
                 and not np.isnan(pL[i]) and pL[i] < engine.L_GK_TH and bool(ind["brk_up"][i]))
        ss[i] = (hr[i] not in engine.S_BLK_H and dw[i] not in engine.S_BLK_D and not (rs is not None and bool(rs[i]))
                 and not np.isnan(pS[i]) and pS[i] < engine.S_GK_TH and bool(ind["brk_dn"][i]))
    return sl, ss


def atr14(ind):
    h, l, c = (np.asarray(ind[k], float) for k in ("h", "l", "c"))
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()


def fills(ind, sig, atr, k, nb, side, sn):
    o, h, l, c = (np.asarray(ind[x], float) for x in ("o", "h", "l", "c"))
    n = len(c)
    flag = np.zeros(n, bool); price = np.full(n, np.nan)
    sigs = fill_n = sn_same = 0
    pend = None                                   # (限價, 最後有效棒)
    for j in range(n):
        if pend is not None:
            p, exp = pend
            if j > exp:
                pend = None
            elif side == "L" and l[j] <= p * (1 - PEN):
                flag[j], price[j] = True, min(p, o[j]); pend = None; fill_n += 1
                sn_same += l[j] <= price[j] * (1 - sn)
            elif side == "S" and h[j] >= p * (1 + PEN):
                flag[j], price[j] = True, max(p, o[j]); pend = None; fill_n += 1
                sn_same += h[j] >= price[j] * (1 + sn)
        if sig[j] and not np.isnan(atr[j]):
            sigs += 1
            pend = (c[j] - k * atr[j] if side == "L" else c[j] + k * atr[j], j + nb)
    return flag, price, dict(sigs=sigs, fills=fill_n, sn_same=sn_same)


def run(sim, ind, dt, extra, slip):
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    f = pd.DataFrame(sim({**ind, **extra}, dt.to_numpy(), start_bar=sb,
                         realistic=True, slip_bps=slip, extra_cost=v40.EXTRA_COST))
    f["entry_dt"] = pd.to_datetime(f["entry_dt"])
    return f


def part(g: pd.DataFrame) -> dict:
    p = g.pnl_usd.to_numpy()
    eq = np.cumsum(p)
    mdd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) if len(p) else 0.0
    mh = g.exit_reason == "MH"
    tp = g.exit_reason == "TP"
    return dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(), mhn=int(mh.sum()),
                mh=g.pnl_usd[mh].sum(), tpn=int(tp.sum()), tp=g.pnl_usd[tp].sum(), mdd=mdd)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    sl, ss = raw_signals(engine, ind)
    atr = atr14(ind)
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後")

    extras = {}
    print("\n[掛單成交統計（全期，未扣持倉中／冷卻）]")
    for name, (k, nb) in CANDS.items():
        fl, pl, stl = fills(ind, sl, atr, k, nb, "L", engine.L_SN)
        fs, ps, sts = fills(ind, ss, atr, k, nb, "S", engine.S_SN)
        extras[name] = {"fillL": fl, "fpL": pl, "fillS": fs, "fpS": ps}
        print(f"  {name:8s} L 訊號 {stl['sigs']} 成交 {stl['fills']}（{stl['fills'] / max(stl['sigs'], 1) * 100:.0f}%）成交棒破SafeNet {stl['sn_same']} | "
              f"S 訊號 {sts['sigs']} 成交 {sts['fills']}（{sts['fills'] / max(sts['sigs'], 1) * 100:.0f}%）成交棒破SafeNet {sts['sn_same']}")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(CANDS):
            f = run(sim, ind, dt, {} if name == "基準" else extras[name], slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:8s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH {a['mhn']:3d}筆 ${a['mh']:7,.0f} TP {a['tpn']:3d}筆 MDD ${a['mdd']:5,.0f}"
                  f" | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH {b['mhn']:3d}筆 ${b['mh']:7,.0f} TP {b['tpn']:3d}筆 MDD ${b['mdd']:5,.0f}")
            if slip == 0:
                f.to_csv(OUT_DIR / f"r9_{name}_trades.csv", index=False)

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(res[(0, "基準")][2].entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 K0.5N3，P1 與 P2 都要成立]")
    ok = {}
    for pi, pn in ((0, "P1"), (1, "P2")):
        for slip in (0, 2, 5):
            ok[f"{pn}{slip}bp PnL勝"] = res[(slip, CENTER)][pi]["pnl"] > res[(slip, "基準")][pi]["pnl"]
        b, c = res[(0, "基準")][pi], res[(0, CENTER)][pi]
        ok[f"{pn}MH少虧"] = c["mh"] > b["mh"]
        ok[f"{pn}MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    nb_win = sum(res[(0, n)][pi]["pnl"] > res[(0, "基準")][pi]["pnl"] for n in CANDS if n != CENTER for pi in (0, 1))
    ok["鄰域8格至少6格勝"] = nb_win >= 6
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域勝基準 {nb_win}/8 格")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
