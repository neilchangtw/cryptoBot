"""V44 第 11 輪：持倉中 ETH 相對 BTC 明顯轉弱就提早出場（RS 出場）。

預登記：doc/v44_maxhold_reframe_plan.md 第 14 節。進場、TP、SafeNet、MFE、延長全部不變，只在 MH 到期前加一條：
  L：持倉第 m 根起、MH 到期前、浮盈 < 0，且（ETH 自進場報酬 − BTC 自進場棒收盤報酬）≤ −x → 收盤市價出場
  S：對稱（ETH 相對 BTC 轉強 x 以上且浮虧）
  ETH 跟著 BTC 一起跌不算（相對強弱 ≈ 0），只抓 ETH 自己轉弱。
候選：中心 X0.5（x=0.5%、m=2）；鄰域 X0.3、X0.8、X0.5M3（m=3）。
期間 P1 = 進場 < 2024-07-01（主要判定），P2 = 之後。資料：data/v44/BTC_1h_long.csv（第 10 輪已下載）。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_r11_rsexit.py
"""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r5_volsize import OUT_DIR

ROOT = Path(__file__).resolve().parents[2]
BTC_CSV = ROOT / "data" / "v44" / "BTC_1h_long.csv"
P_END = pd.Timestamp("2024-07-01")
CANDS = {"X0.5": (0.005, 2), "X0.3": (0.003, 2), "X0.8": (0.008, 2), "X0.5M3": (0.005, 3)}
CENTER = "X0.5"
NEIGHBORS = ("X0.3", "X0.8", "X0.5M3")
EARLY = ["MH", "RS"]


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    reps = [
        ("""                    mh = L_CMH_MH if lp_reduced else l_mh_eff
                    if not lp_ext:
""", """                    mh = L_CMH_MH if lp_reduced else l_mh_eff
                    rs_hit = ('RS_X' in ind and not lp_ext and bh >= ind['RS_MIN'] and bh < mh and cpnl < 0
                              and cpnl - (ind['btc'][i] / ind['btc'][lp_bar] - 1) <= -ind['RS_X'])
                    if rs_hit:
                        ex_price = l_mkt
                        ex_reason = 'RS'
                    elif not lp_ext:
"""),
        ("""                if not sp_ext:
                    if bh >= s_mh_eff:
""", """                rs_hit = ('RS_X' in ind and not sp_ext and bh >= ind['RS_MIN'] and bh < s_mh_eff and cpnl < 0
                          and cpnl + (ind['btc'][i] / ind['btc'][sp_bar] - 1) <= -ind['RS_X'])
                if rs_hit:
                    ex_price = s_mkt
                    ex_reason = 'RS'
                elif not sp_ext:
                    if bh >= s_mh_eff:
"""),
    ]
    for old, new in reps:
        assert src.count(old) == 1, old[:60]
        src = src.replace(old, new)
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def btc_close(dt: pd.Series) -> np.ndarray:
    b = pd.read_csv(BTC_CSV)
    b["datetime"] = pd.to_datetime(b["datetime"])
    s = b.drop_duplicates("datetime").set_index("datetime")["close"].astype(float)
    return pd.to_datetime(dt).map(s).to_numpy(float)


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
    e = g.exit_reason.isin(EARLY)
    return dict(n=len(g), wr=(p > 0).mean() * 100 if len(p) else np.nan, pnl=p.sum(),
                en=int(e.sum()), early=g.pnl_usd[e].sum(), rsn=int((g.exit_reason == "RS").sum()),
                rs=g.pnl_usd[g.exit_reason == "RS"].sum(), tpn=int((g.exit_reason == "TP").sum()), mdd=mdd)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = v40.load_long()
    sim = build_sim(engine)
    bc = btc_close(dt)
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；BTC 缺值 {int(np.isnan(bc[sb:]).sum())} 根（交易期內）；"
          f"P1 = 進場 < {P_END:%Y-%m-%d}，P2 = 之後；MH桶 = MH + RS")

    res = {}
    for slip in (0, 2, 5):
        print(f"\n[滑價 {slip}bp]  P1 2021-03～2024-06 | P2 2024-07 之後")
        for name in ["基準"] + list(CANDS):
            extra = {} if name == "基準" else {"btc": bc, "RS_X": CANDS[name][0], "RS_MIN": CANDS[name][1]}
            f = run(sim, ind, dt, extra, slip)
            a, b = part(f[f.entry_dt < P_END]), part(f[f.entry_dt >= P_END])
            res[(slip, name)] = (a, b, f)
            print(f"  {name:6s} {a['n']:3d}筆 WR {a['wr']:5.1f}% ${a['pnl']:7,.0f} MH桶 {a['en']:3d}筆 ${a['early']:7,.0f}（RS {a['rsn']:3d}筆 ${a['rs']:6,.0f}）"
                  f"TP {a['tpn']:3d} MDD ${a['mdd']:5,.0f} | {b['n']:3d}筆 WR {b['wr']:5.1f}% ${b['pnl']:7,.0f} MH桶 {b['en']:3d}筆 ${b['early']:7,.0f}"
                  f"（RS {b['rsn']:3d}筆 ${b['rs']:6,.0f}）TP {b['tpn']:3d} MDD ${b['mdd']:5,.0f}")
            if slip == 0 and name != "基準":
                f.to_csv(OUT_DIR / f"r11_{name}_trades.csv", index=False)

    print("\n[RS 出場的單若照原規則會怎樣（0bp，中心 X0.5，依進場時間配對基準）]")
    base, cen = res[(0, "基準")][2], res[(0, CENTER)][2]
    rs = cen[cen.exit_reason == "RS"][["side", "entry_dt", "pnl_usd"]]
    m = rs.merge(base[["side", "entry_dt", "exit_reason", "pnl_usd"]], on=["side", "entry_dt"], suffixes=("_rs", "_base"))
    if len(m):
        print(f"  配對 {len(m)}/{len(rs)} 筆；RS 實得 ${m.pnl_usd_rs.sum():,.0f}，原規則 ${m.pnl_usd_base.sum():,.0f}")
        print("  原規則出場分布：" + "、".join(f"{k} {v}筆 ${m.pnl_usd_base[m.exit_reason == k].sum():,.0f}"
                                         for k, v in m.exit_reason.value_counts().items()))

    print("\n[逐年 PnL，0bp]")
    yrs = sorted(base.entry_dt.dt.year.unique())
    print("  " + " ".join(f"{y:>7d}" for y in yrs) + "  名稱")
    for name in ["基準"] + list(CANDS):
        f = res[(0, name)][2]
        yy = f.groupby(f.entry_dt.dt.year).pnl_usd.sum()
        print("  " + " ".join(f"{yy.get(y, 0):7,.0f}" for y in yrs) + f"  {name}")

    print("\n[預登記門檻：中心 X0.5]")
    ok = {}
    for slip in (0, 2, 5):
        ok[f"P1 {slip}bp PnL勝"] = res[(slip, CENTER)][0]["pnl"] > res[(slip, "基準")][0]["pnl"]
    b, c = res[(0, "基準")][0], res[(0, CENTER)][0]
    ok["P1 MH桶少虧"] = c["early"] > b["early"]
    ok["P1 WR不掉逾1pp"] = c["wr"] >= b["wr"] - 1.0
    ok["P1 MDD不增逾10%"] = c["mdd"] <= b["mdd"] * 1.1
    for slip in (0, 2):
        ok[f"P2 {slip}bp PnL勝"] = res[(slip, CENTER)][1]["pnl"] > res[(slip, "基準")][1]["pnl"]
    nb = sum(res[(0, n)][0]["pnl"] > res[(0, "基準")][0]["pnl"] for n in NEIGHBORS)
    ok["鄰域P1至少2/3勝"] = nb >= 2
    verdict = "PASS → SHADOW 候選（不改實盤）" if all(ok.values()) else "REJECTED"
    print("  " + " ".join(f"{k}{'✓' if v else '✗'}" for k, v in ok.items()) + f" → {verdict}")
    print(f"  鄰域 P1 0bp 勝基準 {nb}/3")
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
