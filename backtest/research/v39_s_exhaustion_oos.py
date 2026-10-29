"""V39 樣本外驗證：S 突破棒收盤前賣壓過重 → 較易虧損（V38 接近過關的假說）。

預登記：doc/v39_s_exhaustion_oos_plan.md（2026-10-29，看 2021～2024 逐筆資料前鎖定）。
只在 VPS 執行（不得在公司網路／設備下載行情）；每日 aggTrades 檔取完訊號小時即刪。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v39_s_exhaustion_oos.py klines    # 抓 2021 起 1h K 線
    nohup .venv/bin/python backtest/research/v39_s_exhaustion_oos.py fetch > ~/v39_fetch.log 2>&1 &
    .venv/bin/python backtest/research/v39_s_exhaustion_oos.py analyze
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base
import v38_signal_aggtrades as v38

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
LONG_CSV = ROOT / "data" / "v39" / "ETH_1h_long.csv"
OUT_DIR = ROOT / "doc" / "research_results" / "v39_s_exhaustion_oos"
EVENTS = OUT_DIR / "events.csv"
FEATURES = OUT_DIR / "features.csv"

OOS_START = "2021-03-01"
OOS_END = "2024-11-01"          # V38 母體自 2024-11-01 起，本輪不重疊
EXTRA_COST = 5.0
PRIMARY = "taker_last15"
SECONDARY = ["delta_last15", "post_cross_taker"]
CUT_QUANTILE = 2 / 3            # V38 期間 S 成交單的上三分位切點


def load_long():
    candles = pd.read_csv(LONG_CSV)
    engine = base.load_engine()
    ind = engine.compute_indicators(candles)
    return candles, engine, ind


# ---------------------------------------------------------------- 事件與標籤
def standalone_s(engine, ind, i: int) -> dict:
    """自 i 收盤進場的單筆 S（與現行引擎同出場：SafeNet/TP/MH(regime)/ext BE），不受占倉／冷卻影響。"""
    h, l, c = ind["h"], ind["l"], ind["c"]
    ep = c[i]
    regime = engine._classify_regime(ind["slope"][i]) if ind.get("slope") is not None else "NA"
    mh = engine._S_MH_BR.get(regime, engine.S_MH)
    ext, ext_bars = False, 0
    for j in range(i + 1, min(i + 60, len(c))):
        bh = j - i
        if h[j] >= ep * (1 + engine.S_SN):
            sn = ep * (1 + engine.S_SN)
            ex, why = sn + (h[j] - sn) * engine.S_SN_SLIP, "SN"
        elif l[j] <= ep * (1 - engine.S_TP):
            ex, why = c[j], "TP"
        elif not ext:
            if bh < mh:
                continue
            if (ep - c[j]) / ep > 0:
                ext = True
                continue
            ex, why = c[j], "MH"
        else:
            ext_bars += 1
            if h[j] >= ep:
                ex, why = c[j], "BE"
            elif ext_bars >= engine.S_EXT:
                ex, why = c[j], "MHx"
            else:
                continue
        pnl = (ep - ex) / ep * engine.NOTIONAL - engine.FEE - EXTRA_COST
        return {"alone_pnl": round(pnl, 2), "alone_exit": why, "regime": regime}
    return {"alone_pnl": math.nan, "alone_exit": "NA", "regime": regime}


def build_events(candles, engine, ind) -> pd.DataFrame:
    dt = pd.to_datetime(candles["datetime"])
    rblk = ind.get("regime_block_s")
    mask = (np.nan_to_num(ind["pctile_S"], nan=999) < engine.S_GK_TH) & np.asarray(ind["brk_dn"], bool)
    mask &= ~np.isin(ind["hours"], list(engine.S_BLK_H)) & ~np.isin(ind["dows"], list(engine.S_BLK_D))
    if rblk is not None:
        mask &= ~np.asarray(rblk, bool)
    mask &= (dt >= OOS_START).to_numpy() & (dt < OOS_END).to_numpy()
    mask[: engine.WARMUP] = False
    close = candles["close"].astype(float)
    low15 = close.shift(1).rolling(15).min().to_numpy()
    rows = []
    for i in np.flatnonzero(mask):
        rows.append({"entry_bar": int(i), "entry_dt": dt.iloc[i], "side": "S",
                     "breakout_level": low15[i], "signal_close": close.iloc[i],
                     **standalone_s(engine, ind, int(i))})
    ev = pd.DataFrame(rows)
    ev["open_utc_ms"] = ((ev["entry_dt"] - pd.Timedelta(hours=8) - pd.Timestamp("1970-01-01"))
                         // pd.Timedelta(milliseconds=1)).astype("int64")
    ev["key"] = "S_" + ev["entry_dt"].dt.strftime("%Y%m%d%H")
    # 實際引擎交易（含占倉、冷卻、熔斷與替代交易）標記
    start_bar = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(OOS_START))))
    tr = pd.DataFrame(engine.simulate_v14_detailed(ind, dt.to_numpy(), start_bar=start_bar,
                                                   realistic=True, extra_cost=EXTRA_COST))
    tr = tr[(tr.side == "S") & (pd.to_datetime(tr.entry_dt) < OOS_END)]
    ev = ev.merge(tr[["entry_bar", "pnl_usd", "exit_reason"]].rename(
        columns={"pnl_usd": "engine_pnl", "exit_reason": "engine_exit"}), on="entry_bar", how="left")
    return ev


# ---------------------------------------------------------------- 指令
def cmd_klines(_args) -> None:
    import fetch_backtest_data as fbd
    LONG_CSV.parent.mkdir(parents=True, exist_ok=True)
    fbd.fetch_history("ETHUSDT", "1h", 2100).to_csv(LONG_CSV, index=False)
    print(f"wrote {LONG_CSV}")


def cmd_fetch(args) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind = load_long()
    ev = build_events(candles, engine, ind)
    ev.to_csv(EVENTS, index=False)
    done = set(pd.read_csv(FEATURES)["key"]) if FEATURES.exists() else set()
    todo = ev[~ev["key"].isin(done)].copy()
    todo["utc_day"] = pd.to_datetime(todo["open_utc_ms"], unit="ms").dt.strftime("%Y-%m-%d")
    days = sorted(todo["utc_day"].unique())
    print(f"S 候選事件 {len(ev)}（引擎實際成交 {int(ev.engine_pnl.notna().sum())}），"
          f"待抓 {len(todo)} 筆／{len(days)} 天", flush=True)
    header = ["key", "n_agg", "volume"] + v38.FEATURE_COLS
    new_file = not FEATURES.exists()
    tmp = Path(args.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    with FEATURES.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        if new_file:
            writer.writeheader()
        for n, day in enumerate(days, 1):
            started = time.time()
            rows = todo[todo["utc_day"] == day]
            zip_path = tmp / f"ETHUSDT-aggTrades-{day}.zip"
            if not v38.download(v38.DAILY_URL.format(day=day), zip_path):
                print(f"[{n}/{len(days)}] {day} 無每日檔，略過", flush=True)
                continue
            size_mb = zip_path.stat().st_size / 1e6
            try:
                hours = v38.day_hours(zip_path, [int(x) for x in rows["open_utc_ms"]])
            finally:
                zip_path.unlink(missing_ok=True)
            for _, row in rows.iterrows():
                writer.writerow({k: v38.features(hours[int(row["open_utc_ms"])], row).get(k, "")
                                 for k in header})
            fh.flush()
            print(f"[{n}/{len(days)}] {day} {size_mb:.0f}MB，{len(rows)} 個事件，"
                  f"{time.time() - started:.0f}s", flush=True)
            time.sleep(args.pause)


def perm_p_low(x: np.ndarray, y: np.ndarray, n: int, rng) -> tuple[float, float]:
    """單尾：AUC 顯著低於 0.5（特徵越高越容易虧）。"""
    auc = v38.rank_auc(x, y)
    null = np.array([v38.rank_auc(x, rng.permutation(y)) for _ in range(n)])
    return auc, float((1 + (null <= auc).sum()) / (1 + n))


def cmd_analyze(args) -> None:
    ev = pd.read_csv(EVENTS).merge(pd.read_csv(FEATURES), on="key", how="inner")
    ev = ev[ev["alone_pnl"].notna()]
    rng = np.random.default_rng(39)
    print(f"有特徵的 S 候選事件 {len(ev)}；其中引擎實際成交 {int(ev.engine_pnl.notna().sum())}")
    print("\n[第一關] 候選事件、單筆標籤（淨損益 > 0）")
    y = (ev["alone_pnl"] > 0).to_numpy()
    results = {}
    for col in [PRIMARY] + SECONDARY:
        auc, p = perm_p_low(ev[col].to_numpy(float), y, args.perm, rng)
        results[col] = (auc, p)
        print(f"  {col:18s} AUC {auc:.3f}  單尾 p {p:.4f}")
    auc, p = results[PRIMARY]
    pass1 = auc <= 0.45 and p < 0.01
    print(f"  主假說 {PRIMARY}：AUC ≤ 0.45 且 p < 0.01 → {'PASS' if pass1 else 'FAIL'}")

    cut = None
    v38_feat, v38_tr = v38.FEATURES, v38.TRADES
    if v38_feat.exists() and v38_tr.exists():
        ref = pd.read_csv(v38_tr).merge(pd.read_csv(v38_feat), on="key")
        cut = float(ref.loc[ref.side == "S", PRIMARY].quantile(CUT_QUANTILE))
    print(f"\n[第二關] 規則：S 當 {PRIMARY} > {cut}（V38 期間 S 上三分位）不做")
    if cut is None:
        print("  找不到 V38 features.csv，無法計算切點")
        return
    traded = ev[ev.engine_pnl.notna()]
    skip = traded[PRIMARY] > cut
    print(f"  引擎實際 S 單 {len(traded)} 筆：被擋 {int(skip.sum())} 筆 勝率 "
          f"{(traded.engine_pnl[skip] > 0).mean() * 100:.1f}%、合計 ${traded.engine_pnl[skip].sum():.0f}；"
          f"保留 {int((~skip).sum())} 筆 勝率 {(traded.engine_pnl[~skip] > 0).mean() * 100:.1f}%")
    # 完整重播（含替代交易）：所有 S 候選都有特徵，替代單也能判斷
    candles, engine, ind = load_long()
    sim = build_block_sim(engine)
    block = np.zeros(len(candles), bool)
    block[ev.loc[ev[PRIMARY] > cut, "entry_bar"].to_numpy(int)] = True
    dt = pd.to_datetime(candles["datetime"])
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(OOS_START))))
    def run(extra):
        f = pd.DataFrame(sim({**ind, **extra}, dt.to_numpy(), start_bar=sb, realistic=True,
                             extra_cost=EXTRA_COST))
        return f[pd.to_datetime(f.entry_dt) < OOS_END]
    b0, b1 = run({}), run({"s_block": block})
    for name, f in (("現行", b0), ("加規則", b1)):
        p_ = f.pnl_usd
        print(f"  {name}：{len(f)} 筆 勝率 {(p_ > 0).mean() * 100:.1f}% PnL ${p_.sum():.0f}")
    d_wr = (b1.pnl_usd > 0).mean() * 100 - (b0.pnl_usd > 0).mean() * 100
    pass2 = d_wr >= 2 and b1.pnl_usd.sum() >= b0.pnl_usd.sum()
    print(f"  第二關：勝率 +{d_wr:.1f}pp（需 ≥ 2）且 PnL 不低於現行 → {'PASS' if pass2 else 'FAIL'}")
    print(f"\n結論：{'SHADOW 候選' if pass1 and pass2 else 'REJECTED'}")


def build_block_sim(engine):
    import inspect
    src = inspect.getsource(engine.simulate_v14_detailed)
    old = "pS[i] < S_GK_TH and brk_dn[i]):"
    assert src.count(old) == 1
    src = src.replace(old, "pS[i] < S_GK_TH and brk_dn[i] and not ('s_block' in ind and ind['s_block'][i])):")
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("klines")
    f = sub.add_parser("fetch")
    f.add_argument("--tmp-dir", default="/tmp/v39_aggtrades")
    f.add_argument("--pause", type=float, default=2.0)
    a = sub.add_parser("analyze")
    a.add_argument("--perm", type=int, default=5000)
    args = ap.parse_args()
    {"klines": cmd_klines, "fetch": cmd_fetch, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    main()
