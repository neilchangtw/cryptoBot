"""V44 MaxHold 診斷：實戰 2026-06～10 vs 2021-03 起完整回測，找出 MH 變多的原因。

純診斷，不改策略、不下載資料。回測用 V39 的 data/v39/ETH_1h_long.csv（固定 200U、realistic、每筆 +$5，
與 V41 相同口徑）；實戰讀 data_live/trades.csv。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v44_mh_diagnosis.py
輸出：螢幕摘要（請整段貼回），逐筆明細存到 doc/research_results/v44/。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "doc" / "research_results" / "v44"
LIVE_CSV = ROOT / "data_live" / "trades.csv"
FEATURES = ["atr_pct", "brk_atr", "gk_pctile", "slope_pct", "ret24_dir", "dist_sma50_dir", "rel_vol", "hour"]
FEATURE_NAMES = {
    "atr_pct": "ATR14 %", "brk_atr": "突破幅度(ATR)", "gk_pctile": "GK 分位", "slope_pct": "SMA200 斜率%",
    "ret24_dir": "前24h順向報酬%", "dist_sma50_dir": "距SMA50順向%", "rel_vol": "訊號棒量/20均量", "hour": "進場小時",
}


def entry_features(candles: pd.DataFrame, ind: dict, f: pd.DataFrame) -> pd.DataFrame:
    h, l, c = candles["high"].to_numpy(float), candles["low"].to_numpy(float), candles["close"].to_numpy(float)
    v = candles["volume"].to_numpy(float)
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    hi15 = pd.Series(pc).rolling(15, min_periods=15).max().to_numpy()
    lo15 = pd.Series(pc).rolling(15, min_periods=15).min().to_numpy()
    sma50 = pd.Series(c).rolling(50, min_periods=50).mean().to_numpy()
    vma = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
    hours = pd.to_datetime(candles["datetime"]).dt.hour.to_numpy()
    rows = []
    for t in f.itertuples():
        i = int(t.entry_bar)
        d = 1 if t.side == "L" else -1
        brk = (c[i] - hi15[i]) if d == 1 else (lo15[i] - c[i])
        rows.append({
            "atr_pct": atr[i] / c[i] * 100,
            "brk_atr": brk / atr[i],
            "slope_pct": ind["slope"][i] * 100,
            "ret24_dir": d * (c[i] / c[i - 24] - 1) * 100,
            "dist_sma50_dir": d * (c[i] / sma50[i] - 1) * 100,
            "rel_vol": v[i] / vma[i],
            "hour": hours[i],
        })
    return pd.DataFrame(rows, index=f.index)


def after_mh(candles: pd.DataFrame, f: pd.DataFrame, bars: int = 12) -> pd.Series:
    """MH 出場後 N 根內，是否碰到原本的 TP 價（L 3.5%、S 2%，從原進場價算）。"""
    h, l = candles["high"].to_numpy(float), candles["low"].to_numpy(float)
    out = pd.Series(np.nan, index=f.index)
    for t in f[f.exit_reason == "MH"].itertuples():
        j0, j1 = int(t.exit_bar) + 1, min(int(t.exit_bar) + bars, len(h) - 1) + 1
        if t.side == "L":
            out[t.Index] = float(h[j0:j1].max() >= t.entry_price * 1.035) if j1 > j0 else np.nan
        else:
            out[t.Index] = float(l[j0:j1].min() <= t.entry_price * 0.98) if j1 > j0 else np.nan
    return out


def stats(g: pd.DataFrame) -> str:
    n = len(g)
    if not n:
        return "0 筆"
    mh, tp = g[g.exit_reason == "MH"], g[g.exit_reason == "TP"]
    ratio = -mh.pnl_usd.sum() / tp.pnl_usd.sum() if tp.pnl_usd.sum() > 0 else np.nan
    return (f"{n:3d} 筆 WR {(g.pnl_usd > 0).mean() * 100:5.1f}%  PnL ${g.pnl_usd.sum():7,.0f}  "
            f"MH {len(mh) / n * 100:4.1f}% (均 ${mh.pnl_usd.mean() if len(mh) else 0:5.1f})  "
            f"TP {len(tp) / n * 100:4.1f}% (均 ${tp.pnl_usd.mean() if len(tp) else 0:5.1f})  MH虧/TP賺 {ratio:4.2f}")


def load_live(path: Path, start: str, end: str) -> pd.DataFrame | None:
    if not path.exists():
        return None
    d = pd.read_csv(path)
    d = d[d["exit_type"].notna() & (d["exit_type"].astype(str) != "")].copy()
    d["entry_dt"] = pd.to_datetime(d["entry_time_utc8"].astype(str).str.slice(0, 16), errors="coerce")
    d = d[(d.entry_dt >= start) & (d.entry_dt < end)]
    d["side"] = d["sub_strategy"].astype(str).str.upper().str[0]
    d["pnl_usd"] = pd.to_numeric(d["net_pnl_usd"], errors="coerce")
    names = {"MaxHold": "MH", "MH-ext": "MHx", "MFE-trail": "MFE", "SafeNet": "SN"}
    d["exit_reason"] = d["exit_type"].astype(str).map(lambda x: names.get(x, x))
    for col in ("max_favorable_excursion_pct", "max_adverse_excursion_pct", "hold_bars", "entry_price", "exit_price"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    return d.reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-11-01")
    ap.add_argument("--live", default=str(LIVE_CSV))
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    f = f.join(entry_features(candles, ind, f))  # gk_pctile 已在回測明細
    f["mh_later_tp"] = after_mh(candles, f)
    f["period"] = np.where(f.entry_dt >= a.start, "近期", "歷史")
    f.loc[f.entry_dt >= a.end, "period"] = "之後"
    f.to_csv(OUT_DIR / "backtest_trades_features.csv", index=False)
    hist, rec = f[f.period == "歷史"], f[f.period == "近期"]
    print(f"回測 {f.entry_dt.min():%Y-%m-%d}～{f.entry_dt.max():%Y-%m-%d}，K 線到 {dt.iloc[-1]:%Y-%m-%d %H:%M}")

    print("\n[1] 每年（回測）")
    for y, g in f.groupby(f.entry_dt.dt.year):
        print(f"  {y}  {stats(g)}")
    print(f"  近期 {a.start}～  {stats(rec)}")

    print("\n[2] 方向 × regime：歷史 vs 近期（回測）")
    for (s, r), g in f[f.period != "之後"].groupby(["side", "entry_regime"]):
        print(f"  {s} {r:8s} 歷史 {stats(g[g.period == '歷史'])}")
        print(f"  {s} {r:8s} 近期 {stats(g[g.period == '近期'])}")

    print("\n[3] MH 單的路徑：歷史 vs 近期（回測）")
    for name, g in (("歷史", hist), ("近期", rec)):
        mh = g[g.exit_reason == "MH"]
        if len(mh):
            print(f"  {name} MH {len(mh)} 筆  MFE 中位 {mh.mfe_pct.median():.2f}%  MAE 中位 {mh.mae_pct.median():.2f}%  "
                  f"MFE<0.5% 占 {(mh.mfe_pct < 0.5).mean() * 100:.0f}%  出場後 12h 內碰原 TP 價 {mh.mh_later_tp.mean() * 100:.0f}%")

    print("\n[4] 進場特徵：近期分佈是否偏移？各分位的 MH 比例（分位切點只用歷史）")
    for col in FEATURES:
        for side in ("L", "S"):
            hs, rs = hist[hist.side == side], rec[rec.side == side]
            if len(hs) < 30 or len(rs) < 3:
                continue
            if col == "hour":
                bins = [-1, 7, 15, 23]
            else:
                q = np.nanpercentile(hs[col], [33.3, 66.7])
                bins = [-np.inf, q[0], q[1], np.inf]
            hb = pd.cut(hs[col], bins, labels=["低", "中", "高"])
            rb = pd.cut(rs[col], bins, labels=["低", "中", "高"])
            parts = []
            for lab in ("低", "中", "高"):
                hh, rr = hs[hb == lab], rs[rb == lab]
                hm = (hh.exit_reason == "MH").mean() * 100 if len(hh) else np.nan
                rm = (rr.exit_reason == "MH").mean() * 100 if len(rr) else np.nan
                parts.append(f"{lab}:歷{len(hh)}筆MH{hm:3.0f}%/近{len(rr)}筆MH{rm:3.0f}%")
            print(f"  {side} {FEATURE_NAMES[col]:12s} 歷史中位 {hs[col].median():7.2f} 近期中位 {rs[col].median():7.2f} | "
                  + "  ".join(parts))

    live = load_live(Path(a.live), a.start, a.end)
    print("\n[5] 實戰 vs 回測（同期間）")
    if live is None or not len(live):
        print(f"  找不到實戰明細 {a.live}")
    else:
        print(f"  實戰 {stats(live)}")
        print(f"  回測 {stats(rec)}")
        print("  實戰出場：" + "、".join(f"{k} {v}" for k, v in live.exit_reason.value_counts().items()))
        m = pd.merge_asof(live.sort_values("entry_dt"), rec.sort_values("entry_dt")[["entry_dt", "side", "exit_reason", "pnl_usd", "margin"]],
                          on="entry_dt", by="side", tolerance=pd.Timedelta(hours=1), direction="nearest", suffixes=("", "_bt"))
        matched = m.exit_reason_bt.notna()
        same = (m.exit_reason == m.exit_reason_bt) & matched
        print(f"  配對 {matched.sum()}/{len(m)} 筆，出場原因相同 {same.sum()} 筆")
        for t in m[matched & ~same].itertuples():
            print(f"    {t.entry_dt:%m-%d %H:%M} {t.side} 實戰 {t.exit_reason} ${t.pnl_usd:.1f} / 回測 {t.exit_reason_bt} ${t.pnl_usd_bt:.1f}")
        for t in m[~matched].itertuples():
            print(f"    {t.entry_dt:%m-%d %H:%M} {t.side} 實戰 {t.exit_reason} ${t.pnl_usd:.1f} / 回測無此單")
        lmh = live[live.exit_reason == "MH"]
        if len(lmh):
            print(f"  實戰 MH {len(lmh)} 筆 MFE 中位 {lmh.max_favorable_excursion_pct.median():.2f}%  "
                  f"MAE 中位 {lmh.max_adverse_excursion_pct.median():.2f}%")
        m.to_csv(OUT_DIR / "live_vs_backtest.csv", index=False)
    print(f"\n明細：{OUT_DIR}")


if __name__ == "__main__":
    main()
