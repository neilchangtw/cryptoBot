"""V42 新策略初步篩選：A＝現行策略原封不動套到其他幣；B＝10 幣橫截面動能（週頻）。

預登記：doc/v42_new_strategy_screen_plan.md（2026-10-29，看資料前鎖定）。
資料：先在 VPS 跑 backtest/research/v42_data.py fetch（不得在公司網路／設備下載行情）。

期間（台北日期，UTC+8，左閉右開）：
    dev      2021-01-01 ～ 2024-01-01
    val      2024-01-01 ～ 2025-01-01
    holdout  2025-01-01 ～ 2026-10-29（只准跑一次，需 --i-understand-holdout-once）

A：直接呼叫現行研究引擎 v14_export_trades.compute_indicators / simulate_v14_detailed
   （V14+R+V25-D，V25-D 查表取自 strategy.py），realistic=True、每筆 +$5、滑價 BTC 2bp／
   其他 4bp，再加實際歷史 funding。0 個自由參數。
B：每週一 00:00 UTC 依過去 L 天報酬排名，多前 3、空後 3，等權、總名目 $4,000，持有一週；
   只對變動部位收 taker 0.04%＋滑價，加實際 funding。L ∈ {14, 28, 56} 在 dev 以
   「自己＋鄰居 PnL 中位數」選定後鎖定。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v42_screen.py a --period dev
    .venv/bin/python backtest/research/v42_screen.py b --period dev
    .venv/bin/python backtest/research/v42_screen.py a --period val
    .venv/bin/python backtest/research/v42_screen.py b --period val
    .venv/bin/python backtest/research/v42_screen.py a --period holdout --i-understand-holdout-once
    .venv/bin/python backtest/research/v42_screen.py b --period holdout --i-understand-holdout-once
    .venv/bin/python backtest/research/v42_screen.py report
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))                       # 讓引擎取得 strategy.py 的 V25-D 查表
sys.path.insert(0, str(Path(__file__).resolve().parent))
import v14_export_trades as engine  # noqa: E402
from v42_data import A_SYMBOLS, B_SYMBOLS, DATA_DIR  # noqa: E402

OUT_DIR = ROOT / "doc" / "research_results" / "v42"
HOLDOUT_LOG = OUT_DIR / "holdout_log.txt"

PERIODS = {"dev": ("2021-01-01", "2024-01-01"),
           "val": ("2024-01-01", "2025-01-01"),
           "holdout": ("2025-01-01", "2026-10-29")}
BASELINE = "ETHUSDT"
EXTRA_COST = 5.0
A_TAIL = pd.Timedelta(days=3)       # 期末最後一筆交易平倉用（最長持倉 < 20 根）
B_TAIL = pd.Timedelta(days=8)
TAKER = 0.0004
NOTIONAL = 4000.0
B_LOOKBACKS = (14, 28, 56)
B_TOP = 3
N_RANDOM = 200
G8_MDD_CAP = 2281.0
YEARS = range(2021, 2027)


def slip_bps(symbol: str) -> float:
    return 2.0 if symbol == "BTCUSDT" else 4.0


# ---------------------------------------------------------------- 資料
def load_klines(symbol: str, data_dir: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv((data_dir or DATA_DIR) / f"{symbol}_1h.csv")
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)


def load_funding(symbol: str, data_dir: Path | None = None) -> pd.DataFrame:
    p = (data_dir or DATA_DIR) / f"{symbol}_funding.csv"
    if not p.exists():
        return pd.DataFrame({"funding_time_ms": pd.Series(dtype="int64"), "rate": pd.Series(dtype=float)})
    return pd.read_csv(p)[["funding_time_ms", "rate"]]


def require_data(symbols) -> None:
    missing = [s for s in symbols if not (DATA_DIR / f"{s}_1h.csv").exists()]
    if missing:
        sys.exit(f"缺 K 線：{missing}；先在 VPS 跑 backtest/research/v42_data.py fetch")


def bar_funding_cum(candles: pd.DataFrame, funding: pd.DataFrame) -> np.ndarray:
    """F[j] = Σ_{k≤j} close[k]×rate(結算時刻 = bar k 收盤)。
    持有區間 (entry_bar 收盤, exit_bar 收盤] 的 funding 單價 = F[exit] − F[entry]。"""
    close_ms = ((candles["datetime"] - pd.Timedelta(hours=7) - pd.Timestamp("1970-01-01"))
                // pd.Timedelta(milliseconds=1)).to_numpy("int64")          # UTC+8 開盤 +1h −8h
    rate = pd.Series(funding["rate"].to_numpy(float), index=funding["funding_time_ms"].to_numpy("int64"))
    rate = rate.groupby(level=0).last()
    r = rate.reindex(close_ms).fillna(0.0).to_numpy()
    return np.cumsum(candles["close"].to_numpy(float) * r)


def period_bounds(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    s, e = PERIODS[period]
    return pd.Timestamp(s), pd.Timestamp(e)


# ---------------------------------------------------------------- A
def a_trades(candles: pd.DataFrame, funding: pd.DataFrame, start, end, slip: float,
             extra_cost: float = EXTRA_COST, ind_override: dict | None = None,
             ind: dict | None = None) -> pd.DataFrame:
    """現行引擎原封不動；candles 需已截到 end + A_TAIL。回傳 entry 在 [start, end) 的交易＋funding。"""
    if ind is None:
        ind = engine.compute_indicators(candles)
    if ind_override:
        ind = {**ind, **ind_override}
    dts = candles["datetime"].to_numpy()
    sb = int(np.searchsorted(dts, np.datetime64(pd.Timestamp(start))))
    tr = pd.DataFrame(engine.simulate_v14_detailed(ind, dts, start_bar=sb, realistic=True,
                                                   slip_bps=slip, extra_cost=extra_cost))
    if tr.empty:
        return tr.assign(funding_usd=pd.Series(dtype=float), net_pnl=pd.Series(dtype=float))
    tr["entry_dt"] = pd.to_datetime(tr["entry_dt"])
    tr["exit_dt"] = pd.to_datetime(tr["exit_dt"])
    tr = tr[(tr.entry_dt >= pd.Timestamp(start)) & (tr.entry_dt < pd.Timestamp(end))].reset_index(drop=True)
    F = bar_funding_cum(candles, funding)
    # 引擎輸出的 entry_price 四捨五入到 2 位小數（DOGE 等低價幣會變 0），數量改用未捨入的進場棒收盤價
    qty = tr["margin"].to_numpy(float) * 20 / candles["close"].to_numpy(float)[tr.entry_bar.to_numpy(int)]
    sign = np.where(tr["side"] == "L", 1.0, -1.0)
    tr["funding_usd"] = np.round(-sign * qty * (F[tr.exit_bar.to_numpy(int)] - F[tr.entry_bar.to_numpy(int)]), 4)
    tr["net_pnl"] = tr["pnl_usd"] + tr["funding_usd"]
    return tr


def a_random_pnls(candles, funding, start, end, slip, n=N_RANDOM, seed=42) -> np.ndarray:
    """G5：保留現行時段／Path R／冷卻／熔斷／出場，只把「GK＋突破」訊號換成同數量的隨機小時。"""
    ind = engine.compute_indicators(candles)
    dts = candles["datetime"]
    win = np.flatnonzero(((dts >= pd.Timestamp(start)) & (dts < pd.Timestamp(end))).to_numpy())
    nl = int(((np.nan_to_num(ind["pctile_L"], nan=999) < engine.L_GK_TH) & ind["brk_up"])[win].sum())
    ns = int(((np.nan_to_num(ind["pctile_S"], nan=999) < engine.S_GK_TH) & ind["brk_dn"])[win].sum())
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        o = {}
        for side, k in (("L", nl), ("S", ns)):
            pick = np.zeros(len(candles), bool)
            pick[rng.choice(win, size=min(k, len(win)), replace=False)] = True
            o[f"pctile_{side}"] = np.where(pick, 0.0, 100.0)
            o["brk_up" if side == "L" else "brk_dn"] = pick
        out.append(a_trades(candles, funding, start, end, slip, ind=ind, ind_override=o)["net_pnl"].sum())
    return np.array(out, float)


def monthly(series_dt: pd.Series, pnl: pd.Series) -> pd.Series:
    return pnl.groupby(series_dt.dt.to_period("M").astype(str)).sum()


def best_month_stats(m: pd.Series) -> tuple[str, float, float]:
    if m.empty:
        return "", 0.0, 0.0
    k = m.idxmax()
    return k, float(m.max()), float(m.sum() - m.max())


def side_stats(tr: pd.DataFrame) -> dict:
    out = {"n": int(len(tr)), "wr": float((tr.net_pnl > 0).mean() * 100) if len(tr) else float("nan"),
           "engine_pnl": float(tr.pnl_usd.sum()) if len(tr) else 0.0,
           "funding": float(tr.funding_usd.sum()) if len(tr) else 0.0,
           "net": float(tr.net_pnl.sum()) if len(tr) else 0.0}
    for side in ("L", "S"):
        t = tr[tr.side == side] if len(tr) else tr
        out[f"net_{side}"] = float(t.net_pnl.sum()) if len(t) else 0.0
        out[f"n_{side}"] = int(len(t))
    return out


def run_a(period: str, symbols: list[str], with_random: bool) -> dict:
    start, end = period_bounds(period)
    rows, months_df, summary = [], {}, {}
    for sym in [BASELINE] + symbols:
        c = load_klines(sym)
        c = c[c.datetime < end + A_TAIL].reset_index(drop=True)
        f = load_funding(sym)
        slip = 0.0 if sym == BASELINE else slip_bps(sym)       # 基準＝現行設定（V39～V41 同）
        tr = a_trades(c, f, start, end, slip)
        tr.insert(0, "symbol", sym)
        tr.insert(1, "role", "baseline" if sym == BASELINE else "candidate")
        rows.append(tr)
        m = monthly(tr.exit_dt, tr.net_pnl) if len(tr) else pd.Series(dtype=float)
        months_df[sym] = m
        bm, bv, ex = best_month_stats(m)
        s = side_stats(tr) | {"slip_bps": slip, "best_month": bm, "best_month_pnl": bv, "net_ex_best": ex,
                              "first_bar": f"{c.datetime.min():%Y-%m-%d}"}
        if with_random and sym != BASELINE:
            null = a_random_pnls(c, f, start, end, slip)
            s |= {"random_p95": float(np.percentile(null, 95)), "random_median": float(np.median(null)),
                  "random_pctile": float((null < s["net"]).mean() * 100)}
        summary[sym] = s
        print(f"  {sym:9s} {s['n']:4d} 筆  勝率 {s['wr']:5.1f}%  淨 ${s['net']:9,.0f}（funding ${s['funding']:7,.0f}）", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.concat(rows, ignore_index=True).to_csv(OUT_DIR / f"a_{period}_trades.csv", index=False)
    pd.DataFrame(months_df).fillna(0.0).sort_index().to_csv(OUT_DIR / f"a_{period}_monthly.csv")
    res = {"period": period, "bounds": PERIODS[period], "symbols": symbols, "summary": summary,
           "generated": datetime.now().isoformat(timespec="seconds")}
    (OUT_DIR / f"a_{period}_summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


# ---------------------------------------------------------------- B
def price_panel(symbols, data_dir: Path | None = None, end=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """以 UTC「收盤時刻」為索引的收盤價（= 該時刻可成交價），及 funding 費率表（UTC 結算時刻）。"""
    px, fr = {}, {}
    for s in symbols:
        c = load_klines(s, data_dir)
        if end is not None:
            c = c[c.datetime < end]
        t = c["datetime"] - pd.Timedelta(hours=7)                 # UTC+8 開盤 → UTC 收盤
        px[s] = pd.Series(c["close"].to_numpy(float), index=t.to_numpy())
        f = load_funding(s, data_dir)
        fr[s] = pd.Series(f["rate"].to_numpy(float),
                          index=pd.to_datetime(f["funding_time_ms"], unit="ms").to_numpy()).groupby(level=0).last()
    px = pd.DataFrame(px).sort_index()
    full = pd.date_range(px.index.min(), px.index.max(), freq="h")
    raw = px.reindex(full)
    return raw, pd.DataFrame(fr).sort_index()


def rebalance_times(start, end, raw: pd.DataFrame) -> list[pd.Timestamp]:
    """週一 00:00 UTC；以台北時間（+8h）判斷屬於哪個期間；需有該時刻價格。"""
    s_utc, e_utc = pd.Timestamp(start) - pd.Timedelta(hours=8), pd.Timestamp(end) - pd.Timedelta(hours=8)
    days = pd.date_range(s_utc.normalize(), e_utc, freq="W-MON")
    return [t for t in days if s_utc <= t < e_utc and t <= raw.index.max()]


def momentum_pick(raw: pd.DataFrame, t: pd.Timestamp, lookback_days: int) -> tuple[list[str], list[str]]:
    """過去 L 天報酬（t 與 t−L 天的收盤皆需真實存在）；多前 3、空後 3；有效幣 < 6 則空手。"""
    t0 = t - pd.Timedelta(days=lookback_days)
    if t0 not in raw.index or t not in raw.index:
        return [], []
    ret = (raw.loc[t] / raw.loc[t0] - 1).dropna()
    if len(ret) < 2 * B_TOP:
        return [], []
    order = ret.sort_values(kind="mergesort")              # 平手依幣池順序
    return list(order.index[-B_TOP:][::-1]), list(order.index[:B_TOP])


def simulate_b(raw: pd.DataFrame, fr: pd.DataFrame, times: list[pd.Timestamp], picker,
               slip_map=None, end_cap: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """週頻多空組合。picker(t) → (longs, shorts)。raw 為逐小時連續索引（price_panel）。
    部位以「美元名目」記帳：每週調回目標權重，只對 |目標 − 漂移後部位| 收 taker＋滑價；期末全平。
    funding：持有 (t, t1] 內每次結算 cash = −名目(結算時) × rate（多付空收，費率為負則反向）。"""
    syms = list(raw.columns)
    slip_map = slip_map or {s: slip_bps(s) / 1e4 for s in syms}
    unit_cost = np.array([TAKER + slip_map[s] for s in syms])
    pxf = raw.ffill(limit=6)
    px = pxf.to_numpy(float)
    rate = fr.reindex(index=raw.index, columns=syms).fillna(0.0).to_numpy(float)
    row = {t: i for i, t in enumerate(raw.index)}
    last_t = raw.index.max() if end_cap is None else min(raw.index.max(), end_cap)
    col = {s: j for j, s in enumerate(syms)}
    leg = NOTIONAL / 2 / B_TOP
    hold = np.zeros(len(syms))                               # 目前（漂移後）名目，+ 多 − 空
    pos_rows, wk_rows = [], []
    for k, t in enumerate(times):
        t1 = times[k + 1] if k + 1 < len(times) else min(t + pd.Timedelta(days=7), last_t)
        a, b = row[t], row[t1]
        longs, shorts = picker(t)
        target = np.zeros(len(syms))
        target[[col[s] for s in longs]] = leg
        target[[col[s] for s in shorts]] = -leg
        p0, p1 = px[a], px[b]
        cost = float((np.abs(target - hold) * unit_cost).sum())
        rel = np.where(np.isfinite(p1 / p0), p1 / p0, 1.0)
        g = target * (rel - 1)
        mark = px[a + 1:b + 1] / p0
        mark = np.where(np.isfinite(mark), mark, 1.0)
        fs = -(target * (mark * rate[a + 1:b + 1]).sum(axis=0))
        for j in np.flatnonzero(target):
            pos_rows.append({"week_start": t, "week_end": t1, "symbol": syms[j],
                             "side": "L" if target[j] > 0 else "S", "notional": round(target[j], 2),
                             "entry_px": p0[j], "exit_px": p1[j],
                             "gross_usd": round(g[j], 4), "funding_usd": round(fs[j], 4)})
        hold = target * rel
        if k + 1 == len(times):                             # 期末全平
            cost += float((np.abs(hold) * unit_cost).sum())
            hold[:] = 0.0
        gross, fund = float(g.sum()), float(fs.sum())
        wk_rows.append({"week_start": t, "week_end": t1, "longs": " ".join(longs), "shorts": " ".join(shorts),
                        "gross_usd": gross, "funding_usd": fund, "cost_usd": cost,
                        "net_pnl": gross + fund - cost})
    return pd.DataFrame(wk_rows), pd.DataFrame(pos_rows)


def b_run_one(raw, fr, period, lookback, end_cap=None):
    start, end = period_bounds(period)
    times = rebalance_times(start, end, raw)
    return simulate_b(raw, fr, times, lambda t: momentum_pick(raw, t, lookback), end_cap=end_cap)


def b_random_pnls(raw, fr, period, n=N_RANDOM, seed=42, end_cap=None) -> np.ndarray:
    start, end = period_bounds(period)
    times = rebalance_times(start, end, raw)
    rng = np.random.default_rng(seed)

    def picker(t):
        valid = list(raw.loc[t].dropna().index) if t in raw.index else []
        if len(valid) < 2 * B_TOP:
            return [], []
        pick = list(rng.choice(valid, size=2 * B_TOP, replace=False))
        return pick[:B_TOP], pick[B_TOP:]
    return np.array([simulate_b(raw, fr, times, picker, end_cap=end_cap)[0]["net_pnl"].sum() for _ in range(n)], float)


def plateau_choice(pnl_by_l: dict[int, float]) -> tuple[int, dict[int, float]]:
    """自己＋相鄰 L 的 PnL 中位數最高者；同分取較短 L。"""
    ls = sorted(pnl_by_l)
    score = {}
    for i, L in enumerate(ls):
        vals = [pnl_by_l[x] for x in ls[max(0, i - 1): i + 2]]
        score[L] = float(np.median(vals))
    best = max(ls, key=lambda L: (score[L], -L))
    return best, score


def neighbors(L: int, ls=B_LOOKBACKS) -> list[int]:
    ls = sorted(ls)
    i = ls.index(L)
    return [ls[j] for j in (i - 1, i + 1) if 0 <= j < len(ls)]


def b_save(period, lookback, wk, pos, extra=None) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wk.to_csv(OUT_DIR / f"b_{period}_weekly.csv", index=False)
    pos.to_csv(OUT_DIR / f"b_{period}_positions.csv", index=False)
    wk_end_tpe = pd.to_datetime(wk["week_end"]) + pd.Timedelta(hours=8)
    m = monthly(wk_end_tpe, wk["net_pnl"]) if len(wk) else pd.Series(dtype=float)
    m.rename("B").to_csv(OUT_DIR / f"b_{period}_monthly.csv")
    bm, bv, ex = best_month_stats(m)
    s = {"lookback": lookback, "weeks": int(len(wk)), "gross": float(wk.gross_usd.sum()),
         "funding": float(wk.funding_usd.sum()), "cost": float(wk.cost_usd.sum()),
         "net": float(wk.net_pnl.sum()), "week_wr": float((wk.net_pnl > 0).mean() * 100) if len(wk) else float("nan"),
         "best_month": bm, "best_month_pnl": bv, "net_ex_best": ex} | (extra or {})
    res = {"period": period, "bounds": PERIODS[period], "summary": s,
           "generated": datetime.now().isoformat(timespec="seconds")}
    (OUT_DIR / f"b_{period}_summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


# ---------------------------------------------------------------- 保留期守門
def holdout_guard(cmd: str, flag: bool) -> None:
    if not flag:
        sys.exit("保留期只准跑一次：確認後加 --i-understand-holdout-once")
    if HOLDOUT_LOG.exists():
        for line in HOLDOUT_LOG.read_text(encoding="utf-8").splitlines():
            if line.split("\t")[0] == cmd:
                sys.exit(f"拒絕：{cmd} 的保留期已於 {line.split(chr(9))[1]} 跑過（{HOLDOUT_LOG}）")


def holdout_record(cmd: str, note: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with HOLDOUT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{cmd}\t{datetime.now().isoformat(timespec='seconds')}\t{note}\n")


def read_summary(kind: str, period: str) -> dict | None:
    p = OUT_DIR / f"{kind}_{period}_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def a_included(dev: dict | None, val: dict | None) -> list[str]:
    if not dev or not val:
        return []
    return [s for s in A_SYMBOLS if dev["summary"].get(s, {}).get("net", -1) > 0
            and val["summary"].get(s, {}).get("net", -1) > 0]


# ---------------------------------------------------------------- 指令
def cmd_a(args) -> None:
    require_data([BASELINE] + A_SYMBOLS)
    if args.period == "holdout":
        holdout_guard("a", args.i_understand_holdout_once)
        inc = a_included(read_summary("a", "dev"), read_summary("a", "val"))
        if not inc:
            sys.exit("沒有任何幣開發期與驗證期皆 >0（或尚未跑 dev/val）→ 不動用保留期，A 結案")
        holdout_record("a", "symbols=" + ",".join(inc))
        symbols = inc
    else:
        symbols = A_SYMBOLS
    print(f"[A] {args.period} {PERIODS[args.period][0]}～{PERIODS[args.period][1]}"
          f"（基準 {BASELINE} 現行設定；候選滑價 BTC 2bp／其他 4bp；每筆 +$5；funding 實際）")
    res = run_a(args.period, symbols, with_random=args.period == "dev")
    print_a_table(res)


def print_a_table(res: dict) -> None:
    rnd = "隨機95%" if any("random_p95" in v for v in res["summary"].values()) else ""
    print(f"\n| 幣 | 筆 | 勝率 | L淨 | S淨 | funding | 淨PnL | 去最佳月 | {rnd} |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sym, s in res["summary"].items():
        r = f"{s['random_p95']:,.0f}（{s['random_pctile']:.0f}%）" if "random_p95" in s else ""
        tag = "（基準）" if sym == BASELINE else ""
        print(f"| {sym}{tag} | {s['n']} | {s['wr']:.1f}% | {s['net_L']:,.0f} | {s['net_S']:,.0f} | "
              f"{s['funding']:,.0f} | **{s['net']:,.0f}** | {s['net_ex_best']:,.0f} | {r} |")


def cmd_b(args) -> None:
    require_data(B_SYMBOLS)
    start, end = period_bounds(args.period)
    raw, fr = price_panel(B_SYMBOLS, end=end + B_TAIL)
    end_cap = end - pd.Timedelta(hours=8) + pd.Timedelta(days=7)
    if args.period == "dev":
        pnl = {}
        for L in B_LOOKBACKS:
            wk, _ = b_run_one(raw, fr, "dev", L, end_cap)
            pnl[L] = float(wk.net_pnl.sum())
            print(f"  L={L:2d} 天：{len(wk)} 週  淨 ${pnl[L]:,.0f}", flush=True)
        L, score = plateau_choice(pnl)
        null = b_random_pnls(raw, fr, "dev", end_cap=end_cap)
        wk, pos = b_run_one(raw, fr, "dev", L, end_cap)
        sel = {"pnl_by_L": {str(k): v for k, v in pnl.items()}, "plateau_score": {str(k): v for k, v in score.items()},
               "neighbors": neighbors(L), "random_p95": float(np.percentile(null, 95)),
               "random_median": float(np.median(null)), "random_pctile": float((null < pnl[L]).mean() * 100)}
        res = b_save("dev", L, wk, pos, sel)
        (OUT_DIR / "b_selection.json").write_text(json.dumps({"lookback": L, **sel}, indent=1), encoding="utf-8")
    else:
        selp = OUT_DIR / "b_selection.json"
        if not selp.exists():
            sys.exit("先跑 b --period dev 鎖定 L")
        L = json.loads(selp.read_text(encoding="utf-8"))["lookback"]
        if args.period == "holdout":
            holdout_guard("b", args.i_understand_holdout_once)
            g = gates_b(read_summary("b", "dev"), read_summary("b", "val"), None, None)
            if not (g["G1"] == "PASS" and g["G2"] == "PASS"):
                sys.exit("B 未過 G1/G2 → 不動用保留期，B 結案")
            holdout_record("b", f"lookback={L}")
        wk, pos = b_run_one(raw, fr, args.period, L, end_cap)
        res = b_save(args.period, L, wk, pos)
    s = res["summary"]
    print(f"\n[B] {args.period} L={s['lookback']} 天：{s['weeks']} 週  週勝率 {s['week_wr']:.1f}%  "
          f"毛 ${s['gross']:,.0f}  funding ${s['funding']:,.0f}  成本 ${s['cost']:,.0f}  "
          f"**淨 ${s['net']:,.0f}**  去最佳月 ${s['net_ex_best']:,.0f}")
    if "random_p95" in s:
        print(f"    L 平台分數 {s['plateau_score']}；隨機 95% ${s['random_p95']:,.0f}（本策略在 {s['random_pctile']:.0f}%）")


# ---------------------------------------------------------------- 報告／門檻
def pf(x: bool | None) -> str:
    return "—" if x is None else ("PASS" if x else "FAIL")


def corr_with_baseline(cand: pd.Series, base: pd.Series) -> float:
    idx = pd.period_range(PERIODS["dev"][0], pd.Timestamp(PERIODS["dev"][1]) - pd.Timedelta(days=1), freq="M").astype(str)
    a, b = cand.reindex(idx).fillna(0.0), base.reindex(idx).fillna(0.0)
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def read_monthly(kind: str, period: str) -> pd.DataFrame:
    p = OUT_DIR / f"{kind}_{period}_monthly.csv"
    return pd.read_csv(p, index_col=0) if p.exists() else pd.DataFrame()


def gates_a(sym, dev, val, hold) -> dict:
    g = {}
    d = dev["summary"].get(sym) if dev else None
    v = val["summary"].get(sym) if val else None
    h = hold["summary"].get(sym) if hold else None
    g["G1"] = pf(d["net"] > 0) if d else "—"            # 0 自由參數：無鄰居
    g["G2"] = pf(v["net"] > 0) if v else "—"
    g["G3"] = pf(h["net"] > 0) if h else "—"
    md = read_monthly("a", "dev")
    if d and sym in md and BASELINE in md:
        r = corr_with_baseline(md[sym], md[BASELINE])
        g["corr"] = r
        g["G4"] = pf(bool(r < 0.3)) if np.isfinite(r) else "—"
    else:
        g["G4"] = "—"
    g["G5"] = pf(d["net"] >= d["random_p95"]) if d and "random_p95" in d else "—"
    g["G6"] = pf(h["net_ex_best"] > 0) if h else "—"
    return g


def gates_b(dev, val, hold, _unused) -> dict:
    g = {}
    d = dev["summary"] if dev else None
    if d:
        L = d["lookback"]
        nb = [d["pnl_by_L"][str(x)] for x in d["neighbors"]]
        g["G1"] = pf(d["pnl_by_L"][str(L)] > 0 and all(x > 0 for x in nb))
        g["G5"] = pf(d["net"] >= d["random_p95"])
    else:
        g["G1"] = g["G5"] = "—"
    g["G2"] = pf(val["summary"]["net"] > 0) if val else "—"
    g["G3"] = pf(hold["summary"]["net"] > 0) if hold else "—"
    g["G6"] = pf(hold["summary"]["net_ex_best"] > 0) if hold else "—"
    md, mb = read_monthly("b", "dev"), read_monthly("a", "dev")
    if not md.empty and BASELINE in mb:
        r = corr_with_baseline(md["B"], mb[BASELINE])
        g["corr"] = r
        g["G4"] = pf(bool(r < 0.3)) if np.isfinite(r) else "—"
    else:
        g["G4"] = "—"
    return g


def daily_realized(symbols_a: list[str], with_b: bool) -> pd.Series:
    """所有期間的已實現 PnL（台北日期）：A 依出場、B 依週末結算。"""
    parts = []
    for per in PERIODS:
        p = OUT_DIR / f"a_{per}_trades.csv"
        if p.exists():
            t = pd.read_csv(p, parse_dates=["exit_dt"])
            t = t[t.symbol.isin([BASELINE] + symbols_a)]
            parts.append(t.groupby(t.exit_dt.dt.normalize())["net_pnl"].sum())
        q = OUT_DIR / f"b_{per}_weekly.csv"
        if with_b and q.exists():
            w = pd.read_csv(q, parse_dates=["week_end"])
            parts.append(w.groupby((w.week_end + pd.Timedelta(hours=8)).dt.normalize())["net_pnl"].sum())
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).groupby(level=0).sum().sort_index()


def mdd(daily: pd.Series) -> float:
    eq = np.concatenate([[0.0], daily.cumsum().to_numpy()])
    return float((np.maximum.accumulate(eq) - eq).max())


def cmd_report(_args) -> None:
    S = {(k, p): read_summary(k, p) for k in ("a", "b") for p in PERIODS}
    out = {"A": {}, "B": None, "combo": None}
    print("## V42 篩選結果（G1 開發 >0／平台、G2 2024 >0、G3 保留 >0、G4 月相關 <0.3、"
          "G5 ≥隨機95%、G6 保留去最佳月 >0）\n")
    print("| 候選 | 開發淨 | 2024淨 | 保留淨 | 相關 | G1 | G2 | G3 | G4 | G5 | G6 | 納入 |")
    print("|---|---:|---:|---:|---:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
    fmt = lambda x: "" if x is None else f"{x:,.0f}"
    if S[("a", "dev")]:
        b = [S[("a", p)]["summary"].get(BASELINE, {}).get("net") if S[("a", p)] else None for p in PERIODS]
        print(f"| 現行 ETH（基準） | {fmt(b[0])} | {fmt(b[1])} | {fmt(b[2])} |  |  |  |  |  |  |  |  |")
    passed_a = []
    inc = a_included(S[("a", "dev")], S[("a", "val")])
    for sym in A_SYMBOLS:
        if not S[("a", "dev")]:
            break
        g = gates_a(sym, S[("a", "dev")], S[("a", "val")], S[("a", "holdout")])
        nets = [S[("a", p)]["summary"].get(sym, {}).get("net") if S[("a", p)] else None for p in PERIODS]
        ok = all(g[k] == "PASS" for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
        if ok:
            passed_a.append(sym)
        out["A"][sym] = g | {"nets": nets, "included": sym in inc, "all_pass": ok}
        corr = f"{g['corr']:+.2f}" if "corr" in g else ""
        print(f"| A {sym} | {fmt(nets[0])} | {fmt(nets[1])} | {fmt(nets[2])} | {corr} | "
              + " | ".join(g[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
              + f" | {'✅' if ok else ('dev+val' if sym in inc else '❌')} |")
    b_ok = False
    if S[("b", "dev")]:
        g = gates_b(S[("b", "dev")], S[("b", "val")], S[("b", "holdout")], None)
        nets = [S[("b", p)]["summary"]["net"] if S[("b", p)] else None for p in PERIODS]
        b_ok = all(g[k] == "PASS" for k in ("G1", "G2", "G3", "G4", "G5", "G6"))
        L = S[("b", "dev")]["summary"]["lookback"]
        out["B"] = g | {"nets": nets, "lookback": L, "all_pass": b_ok}
        corr = f"{g['corr']:+.2f}" if "corr" in g else ""
        print(f"| B 動能 L={L} | {fmt(nets[0])} | {fmt(nets[1])} | {fmt(nets[2])} | {corr} | "
              + " | ".join(g[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6")) + f" | {'✅' if b_ok else '❌'} |")

    have_all = all(S[("a", p)] for p in PERIODS)
    print()
    if not have_all:
        print("G7／G8：需 a 的 dev、val、holdout 都跑完才計算（組合＝現行＋通過 G1～G6 的候選）")
    else:
        for label, syms, wb in (("現行 ETH 單獨", [], False), ("組合（現行＋通過者）", passed_a, b_ok)):
            d = daily_realized(syms, wb)
            yr = d.groupby(d.index.year).sum()
            yrs = {int(y): float(yr.get(y, 0.0)) for y in YEARS}
            g7 = all(v >= 0 for v in yrs.values())
            g8 = mdd(d) <= G8_MDD_CAP
            print(f"{label}：" + "  ".join(f"{y} ${v:,.0f}" for y, v in yrs.items())
                  + f"  | 合計 ${d.sum():,.0f}  MDD ${mdd(d):,.0f}  G7 {pf(g7)}  G8 {pf(g8)}")
            if wb or syms or label.startswith("組合"):
                out["combo"] = {"members": [BASELINE] + syms + (["B"] if wb else []), "years": yrs,
                                "total": float(d.sum()), "mdd": mdd(d), "G7": pf(g7), "G8": pf(g8)}
        if not passed_a and not b_ok:
            print("沒有候選通過 G1～G6 → 組合＝現行，V42 結論 REJECTED")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    shown = OUT_DIR.relative_to(ROOT) if OUT_DIR.is_relative_to(ROOT) else OUT_DIR
    print(f"\n明細：{shown}/（gates.json、*_trades.csv、*_monthly.csv、*_weekly.csv）")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("a", "b"):
        p = sub.add_parser(name)
        p.add_argument("--period", choices=list(PERIODS), required=True)
        p.add_argument("--i-understand-holdout-once", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    {"a": cmd_a, "b": cmd_b, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
