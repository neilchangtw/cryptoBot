"""V43 現貨-永續資金費率套利（V42 候選 C）：買現貨＋同數量空永續，只收資金費。

預登記：doc/v43_funding_carry_plan.md（2026-10-29，看資料前鎖定）。
資料：先在 VPS 跑 v42_data.py fetch（永續 1h＋funding）與 v43_data.py fetch（現貨 1h）。

期間（台北日期，UTC+8，左閉右開；各段從空手開始，期末強制平倉）：
    dev      2021-01-01 ～ 2024-01-01
    val      2024-01-01 ～ 2025-01-01
    holdout  2025-01-01 ～ 2026-10-29（只准跑一次，需 --i-understand-holdout-once）

BTCUSDT、ETHUSDT 各名目 $2,000（永續保證金各 $1,000，2x），資金分母 $6,000。
每天 00:00 UTC 決策，於下一根 1h 收盤成交（現貨、永續各用自己的收盤價）。
    C0：一直持有（0 參數）。
    C1：過去 N 次結算平均費率 > 0.01% 建倉、< 0 平倉；N ∈ {9, 21, 42}，dev 以「自己＋相鄰 N
        的 PnL 中位數」最高者為平台中心（同分取較長 N），鎖定後用於 val／holdout。
損益＝資金費（持有 (建倉, 平倉] 內每次結算 +數量×永續收盤×費率）＋基差（現貨漲跌−永續漲跌）
     −成本（現貨 taker 0.10%、永續 taker 0.05%，每腿加滑價 BTC 2bp／ETH 3bp）。
永續未實現虧損 ≥ 保證金 70% → 兩腿平倉後同棒重建，照收一次完整來回成本。
日 PnL 用逐日盯市（持倉動輒數月，依平倉日歸屬會讓 G4／G7／G8 失真）；現行策略基準照 V42 依出場日。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v43_carry.py c0 --period dev
    .venv/bin/python backtest/research/v43_carry.py c1 --period dev
    .venv/bin/python backtest/research/v43_carry.py c0 --period val
    .venv/bin/python backtest/research/v43_carry.py c1 --period val
    .venv/bin/python backtest/research/v43_carry.py report
    # G1、G-APR、G2 皆過的候選才准：
    .venv/bin/python backtest/research/v43_carry.py c0 --period holdout --i-understand-holdout-once
    .venv/bin/python backtest/research/v43_carry.py c1 --period holdout --i-understand-holdout-once
    .venv/bin/python backtest/research/v43_carry.py report
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v42_data import DATA_DIR as PERP_DIR  # noqa: E402
from v43_data import DATA_DIR as SPOT_DIR, SYMBOLS  # noqa: E402

OUT_DIR = ROOT / "doc" / "research_results" / "v43"
HOLDOUT_LOG = OUT_DIR / "holdout_log.txt"

PERIODS = {"dev": ("2021-01-01", "2024-01-01"),
           "val": ("2024-01-01", "2025-01-01"),
           "holdout": ("2025-01-01", "2026-10-29")}
NOTIONAL_SYM = 2000.0
MARGIN_SYM = 1000.0
CAPITAL = 6000.0
REBAL_FRAC = 0.70
SPOT_FEE = 0.0010
PERP_FEE = 0.0005
SLIP_BPS = {"BTCUSDT": 2.0, "ETHUSDT": 3.0}
ENTER_TH = 0.0001
EXIT_TH = 0.0
C1_NS = (9, 21, 42)
APR_MIN = 0.05
BASELINE = "ETHUSDT"
G8_MDD_CAP = 2281.0
YEARS = range(2021, 2027)
TPE = pd.Timedelta(hours=8)


# ---------------------------------------------------------------- 資料
def load_close(path: Path) -> pd.Series:
    """收盤價，索引＝UTC 收盤時刻（CSV 的 datetime 是 UTC+8 開盤）。"""
    df = pd.read_csv(path, usecols=["close", "datetime"])
    t = pd.to_datetime(df["datetime"]) - TPE + pd.Timedelta(hours=1)
    return pd.Series(df["close"].to_numpy(float), index=t).groupby(level=0).last().sort_index()


def load_panel(symbol: str, perp_dir: Path | None = None, spot_dir: Path | None = None) -> pd.DataFrame:
    perp = load_close((perp_dir or PERP_DIR) / f"{symbol}_1h.csv")
    spot = load_close((spot_dir or SPOT_DIR) / f"{symbol}_spot_1h.csv")
    return pd.DataFrame({"perp": perp, "spot": spot}).dropna()


def load_funding(symbol: str, perp_dir: Path | None = None) -> pd.Series:
    df = pd.read_csv((perp_dir or PERP_DIR) / f"{symbol}_funding.csv")
    t = pd.to_datetime(df["funding_time_ms"].astype("int64"), unit="ms")
    return pd.Series(df["rate"].to_numpy(float), index=t).groupby(level=0).last().sort_index()


def require_data() -> None:
    miss = [str(p.relative_to(ROOT)) for s in SYMBOLS
            for p in (PERP_DIR / f"{s}_1h.csv", PERP_DIR / f"{s}_funding.csv", SPOT_DIR / f"{s}_spot_1h.csv")
            if not p.exists()]
    if miss:
        sys.exit(f"缺資料：{miss}；先在 VPS 跑 v42_data.py fetch 與 v43_data.py fetch")


def period_utc(period: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    s, e = PERIODS[period]
    return pd.Timestamp(s) - TPE, pd.Timestamp(e) - TPE


# ---------------------------------------------------------------- 模擬
def desired_by_bar(times: pd.DatetimeIndex, funding: pd.Series, start_utc, end_utc,
                   n: int | None) -> dict[int, bool]:
    """每天 00:00 UTC 決策 → {成交棒位置: 是否應持有}。n=None 為 C0（一直持有）。"""
    days = pd.date_range(start_utc.ceil("D"), end_utc, freq="D", inclusive="left")
    out: dict[int, bool] = {}
    fr_t = funding.index.to_numpy()
    fr_v = funding.to_numpy(float)
    for d in days:
        i = int(times.searchsorted(d + pd.Timedelta(hours=1)))
        if i >= len(times) or times[i] > end_utc:
            continue
        if n is None:
            out[i] = True
            continue
        k = int(np.searchsorted(fr_t, np.datetime64(d), side="right"))   # 結算時刻 ≤ d
        if k < n:
            continue                                                    # 暖機不足：不改變狀態
        fbar = float(fr_v[k - n:k].mean())
        if fbar > ENTER_TH:
            out[i] = True
        elif fbar < EXIT_TH:
            out[i] = False
    return out


def simulate(panel: pd.DataFrame, funding: pd.Series, start_utc, end_utc, n: int | None,
             slip_bps: float) -> tuple[pd.DataFrame, pd.Series]:
    """回傳（交易表, 每棒盯市權益 index=UTC 收盤時刻）。"""
    p_all = panel[(panel.index > start_utc) & (panel.index <= end_utc)]
    times = p_all.index
    perp = p_all["perp"].to_numpy(float)
    spot = p_all["spot"].to_numpy(float)
    want = desired_by_bar(times, funding, start_utc, end_utc, n)
    slip = slip_bps / 1e4
    fr = funding[(funding.index > start_utc) & (funding.index <= end_utc)]
    fr_t, fr_v = fr.index, fr.to_numpy(float)
    fi = 0
    trades, eq = [], np.zeros(len(times))
    realized = 0.0
    pos = None

    def leg_cost(q, s, p):
        return q * s * (SPOT_FEE + slip) + q * p * (PERP_FEE + slip)

    def open_pos(i):
        q = NOTIONAL_SYM / perp[i]
        return {"entry_i": i, "qty": q, "spot_in": spot[i], "perp_in": perp[i],
                "cost_open": leg_cost(q, spot[i], perp[i]), "funding": 0.0, "n_settle": 0}

    def close_pos(i, reason):
        nonlocal realized
        q = pos["qty"]
        basis = q * ((spot[i] - pos["spot_in"]) - (perp[i] - pos["perp_in"]))
        cost = pos["cost_open"] + leg_cost(q, spot[i], perp[i])
        net = pos["funding"] + basis - cost
        realized += net
        trades.append({"entry_dt": times[pos["entry_i"]] + TPE, "exit_dt": times[i] + TPE, "reason": reason,
                       "qty": q, "spot_in": pos["spot_in"], "perp_in": pos["perp_in"],
                       "spot_out": spot[i], "perp_out": perp[i], "n_settle": pos["n_settle"],
                       "funding_usd": pos["funding"], "basis_usd": basis, "cost_usd": cost, "net_pnl": net,
                       "hours": int((times[i] - times[pos["entry_i"]]) / pd.Timedelta(hours=1))})

    for i, t in enumerate(times):
        # 1) 結算：時刻落在 (上一棒, 本棒]，且晚於建倉棒收盤
        while fi < len(fr_t) and fr_t[fi] <= t:
            if pos is not None and fr_t[fi] > times[pos["entry_i"]]:
                pos["funding"] += pos["qty"] * perp[i] * fr_v[fi]
                pos["n_settle"] += 1
            fi += 1
        # 2) 每日決策
        just_opened = False
        if i in want:
            if pos is not None and not want[i]:
                close_pos(i, "signal")
                pos = None
            elif pos is None and want[i]:
                pos, just_opened = open_pos(i), True
        # 3) 保證金再平衡
        if pos is not None and not just_opened and pos["qty"] * (perp[i] - pos["perp_in"]) >= REBAL_FRAC * MARGIN_SYM:
            close_pos(i, "rebalance")
            pos = open_pos(i)
        # 4) 期末
        if pos is not None and i == len(times) - 1:
            close_pos(i, "period_end")
            pos = None
        unreal = 0.0
        if pos is not None:
            q = pos["qty"]
            unreal = pos["funding"] + q * ((spot[i] - pos["spot_in"]) - (perp[i] - pos["perp_in"])) - pos["cost_open"]
        eq[i] = realized + unreal
    return pd.DataFrame(trades), pd.Series(eq, index=times)


def daily_from_equity(eq: pd.Series) -> pd.Series:
    """台北日期的逐日盯市 PnL。"""
    if eq.empty:
        return pd.Series(dtype=float)
    last = eq.groupby((eq.index + TPE).normalize()).last()
    return last.diff().fillna(last.iloc[0])


def mdd(daily: pd.Series) -> float:
    eq = np.concatenate([[0.0], daily.cumsum().to_numpy()])
    return float((np.maximum.accumulate(eq) - eq).max())


def summarize(tr: pd.DataFrame, daily: pd.Series, eq_hours: int, held_hours: int, years: float) -> dict:
    m = daily.groupby(daily.index.to_period("M").astype(str)).sum() if len(daily) else pd.Series(dtype=float)
    net = float(tr.net_pnl.sum()) if len(tr) else 0.0
    best = m.idxmax() if len(m) else ""
    return {"trades": int(len(tr)),
            "rebalances": int((tr.reason == "rebalance").sum()) if len(tr) else 0,
            "funding": float(tr.funding_usd.sum()) if len(tr) else 0.0,
            "basis": float(tr.basis_usd.sum()) if len(tr) else 0.0,
            "cost": float(tr.cost_usd.sum()) if len(tr) else 0.0,
            "net": net, "apr": net / CAPITAL / years,
            "time_in_market": held_hours / eq_hours if eq_hours else 0.0,
            "mdd_mtm": mdd(daily), "best_month": best,
            "best_month_pnl": float(m.max()) if len(m) else 0.0,
            "net_ex_best": float(m.sum() - m.max()) if len(m) else 0.0,
            "month_wr": float((m > 0).mean() * 100) if len(m) else float("nan")}


def run(period: str, n: int | None, panels=None, fundings=None) -> tuple[dict, pd.DataFrame, pd.Series]:
    s_utc, e_utc = period_utc(period)
    years = (e_utc - s_utc) / pd.Timedelta(days=365.25)
    rows, dailies, per = [], [], {}
    for sym in SYMBOLS:
        pnl = panels[sym] if panels else load_panel(sym)
        fr = fundings[sym] if fundings else load_funding(sym)
        tr, eq = simulate(pnl, fr, s_utc, e_utc, n, SLIP_BPS[sym])
        held = int(tr.hours.sum()) if len(tr) else 0
        d = daily_from_equity(eq)
        per[sym] = summarize(tr, d, len(eq), held, years)
        if len(tr):
            rows.append(tr.assign(symbol=sym))
        dailies.append(d.rename(sym))
    tr_all = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["symbol", "net_pnl"])
    daily = pd.concat(dailies, axis=1).fillna(0.0).sum(axis=1)
    held_avg = np.mean([per[s]["time_in_market"] for s in SYMBOLS])
    total = summarize(tr_all, daily, 1, 0, years) | {"time_in_market": float(held_avg)}
    return {"by_symbol": per, "total": total, "N": n}, tr_all, daily


def plateau_choice(pnl_by_n: dict[int, float]) -> tuple[int, dict[int, float]]:
    """自己＋相鄰 N 的 PnL 中位數最高者；同分取較長 N。"""
    ns = sorted(pnl_by_n)
    score = {}
    for i, n in enumerate(ns):
        score[n] = float(np.median([pnl_by_n[x] for x in ns[max(0, i - 1): i + 2]]))
    return max(ns, key=lambda n: (score[n], n)), score


def neighbors(n: int, ns=C1_NS) -> list[int]:
    ns = sorted(ns)
    i = ns.index(n)
    return [ns[j] for j in (i - 1, i + 1) if 0 <= j < len(ns)]


# ---------------------------------------------------------------- 存檔／保留期
def save(kind: str, period: str, res: dict, tr: pd.DataFrame, daily: pd.Series, extra: dict | None = None) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tr.to_csv(OUT_DIR / f"{kind}_{period}_trades.csv", index=False)
    daily.rename("pnl").to_csv(OUT_DIR / f"{kind}_{period}_daily.csv", index_label="date")
    out = {"kind": kind, "period": period, "bounds": PERIODS[period], **res, **(extra or {}),
           "generated": datetime.now().isoformat(timespec="seconds")}
    (OUT_DIR / f"{kind}_{period}_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def read_summary(kind: str, period: str) -> dict | None:
    p = OUT_DIR / f"{kind}_{period}_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def read_daily(kind: str, period: str) -> pd.Series:
    p = OUT_DIR / f"{kind}_{period}_daily.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    d = pd.read_csv(p, parse_dates=["date"])
    return pd.Series(d["pnl"].to_numpy(float), index=d["date"])


def holdout_guard(kind: str, flag: bool) -> None:
    if not flag:
        sys.exit("保留期只准跑一次：確認後加 --i-understand-holdout-once")
    if HOLDOUT_LOG.exists():
        for line in HOLDOUT_LOG.read_text(encoding="utf-8").splitlines():
            if line.split("\t")[0] == kind:
                sys.exit(f"拒絕：{kind} 的保留期已於 {line.split(chr(9))[1]} 跑過（{HOLDOUT_LOG}）")
    g = pre_holdout_gates(kind)
    if not all(g.get(k) == "PASS" for k in ("G1", "G-APR", "G2")):
        sys.exit(f"拒絕：{kind} 未通過 G1／G-APR／G2（{g}），依預登記不動用保留期，直接結案")


def holdout_record(kind: str, note: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with HOLDOUT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{kind}\t{datetime.now().isoformat(timespec='seconds')}\t{note}\n")


# ---------------------------------------------------------------- 指令
def print_run(label: str, res: dict) -> None:
    for sym, s in list(res["by_symbol"].items()) + [("合計", res["total"])]:
        print(f"  {label} {sym:8s} 交易 {s['trades']:3d}（再平衡 {s['rebalances']}） 持有 {s['time_in_market']*100:5.1f}%  "
              f"資金費 ${s['funding']:8,.0f}  基差 ${s['basis']:7,.0f}  成本 ${s['cost']:6,.0f}  "
              f"淨 ${s['net']:8,.0f}  年化 {s['apr']*100:5.1f}%  盯市MDD ${s['mdd_mtm']:,.0f}", flush=True)


def cmd_c0(args) -> None:
    require_data()
    if args.period == "holdout":
        holdout_guard("c0", args.i_understand_holdout_once)
        holdout_record("c0", "always-on")
    res, tr, d = run(args.period, None)
    print_run("C0", res)
    save("c0", args.period, res, tr, d)


def cmd_c1(args) -> None:
    require_data()
    panels = {s: load_panel(s) for s in SYMBOLS}
    fundings = {s: load_funding(s) for s in SYMBOLS}
    sel_path = OUT_DIR / "c1_selection.json"
    if args.period == "dev":
        runs = {}
        for n in C1_NS:
            runs[n] = run("dev", n, panels, fundings)
            print_run(f"C1 N={n:2d}", runs[n][0])
        pnl_by_n = {n: runs[n][0]["total"]["net"] for n in C1_NS}
        best, score = plateau_choice(pnl_by_n)
        sel = {"N": best, "pnl_by_N": pnl_by_n, "score": score, "neighbors": neighbors(best),
               "generated": datetime.now().isoformat(timespec="seconds")}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        sel_path.write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"平台中心 N={best}（分數 {score}）→ 鎖定", flush=True)
        res, tr, d = runs[best]
        save("c1", "dev", res, tr, d, {"pnl_by_N": {str(k): v for k, v in pnl_by_n.items()},
                                       "neighbors": neighbors(best)})
        return
    if not sel_path.exists():
        sys.exit("先跑 c1 --period dev 鎖定 N")
    n = json.loads(sel_path.read_text(encoding="utf-8"))["N"]
    if args.period == "holdout":
        holdout_guard("c1", args.i_understand_holdout_once)
        holdout_record("c1", f"N={n}")
    res, tr, d = run(args.period, n, panels, fundings)
    print_run(f"C1 N={n}", res)
    save("c1", args.period, res, tr, d)


def baseline_daily(period: str) -> pd.Series:
    """現行 ETH 策略（同 V42 基準：realistic、+$5、滑價 0、實際 funding），依出場台北日期。"""
    p = OUT_DIR / f"baseline_{period}_trades.csv"
    if not p.exists():
        import v42_screen as v42
        s, e = v42.period_bounds(period)
        c = v42.load_klines(BASELINE)
        c = c[c.datetime < e + v42.A_TAIL].reset_index(drop=True)
        tr = v42.a_trades(c, v42.load_funding(BASELINE), s, e, 0.0)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tr.to_csv(p, index=False)
    t = pd.read_csv(p, parse_dates=["exit_dt"])
    if t.empty:
        return pd.Series(dtype=float)
    return t.groupby(t.exit_dt.dt.normalize())["net_pnl"].sum()


def monthly(d: pd.Series) -> pd.Series:
    return d.groupby(d.index.to_period("M").astype(str)).sum()


def pf(x: bool | None) -> str:
    return "—" if x is None else ("PASS" if x else "FAIL")


def pre_holdout_gates(kind: str) -> dict:
    dev, val = read_summary(kind, "dev"), read_summary(kind, "val")
    g = {"G1": "—", "G-APR": "—", "G2": "—"}
    if dev:
        ok = dev["total"]["net"] > 0
        if kind == "c1":
            ok = ok and all(dev["pnl_by_N"][str(x)] > 0 for x in dev["neighbors"])
        g["G1"] = pf(ok)
        g["G-APR"] = pf(dev["total"]["apr"] >= APR_MIN)
    if val:
        g["G2"] = pf(val["total"]["net"] > 0)
    return g


def gates(kind: str) -> dict:
    g = pre_holdout_gates(kind)
    dev, hold = read_summary(kind, "dev"), read_summary(kind, "holdout")
    g["G3"] = pf(hold["total"]["net"] > 0) if hold else "—"
    g["G6"] = pf(hold["total"]["net_ex_best"] > 0) if hold else "—"
    if dev:
        idx = pd.period_range(PERIODS["dev"][0], pd.Timestamp(PERIODS["dev"][1]) - pd.Timedelta(days=1), freq="M").astype(str)
        a = monthly(read_daily(kind, "dev")).reindex(idx).fillna(0.0)
        b = monthly(baseline_daily("dev")).reindex(idx).fillna(0.0)
        r = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")
        g["corr"] = r
        g["G4"] = pf(bool(r < 0.3)) if np.isfinite(r) else "—"
    else:
        g["G4"] = "—"
    if kind == "c1":
        c0 = read_summary("c0", "dev")
        g["G5"] = pf(dev["total"]["net"] >= c0["total"]["net"]) if dev and c0 else "—"
    return g


REQ = {"c0": ("G1", "G-APR", "G2", "G3", "G4", "G6"), "c1": ("G1", "G-APR", "G2", "G3", "G4", "G5", "G6")}


def cmd_report(_args) -> None:
    out = {}
    print("## V43 資金費率套利（G1 開發 >0／平台、G-APR 年化 ≥5%、G2 2024 >0、G3 保留 >0、"
          "G4 月相關 <0.3、G5 C1 ≥ C0、G6 保留去最佳月 >0）\n")
    print("| 候選 | 開發淨 | 年化 | 2024淨 | 保留淨 | 相關 | G1 | G-APR | G2 | G3 | G4 | G5 | G6 | 結果 |")
    print("|---|---:|---:|---:|---:|---:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|")
    fmt = lambda x: "" if x is None else f"{x:,.0f}"
    passed = []
    for kind in ("c0", "c1"):
        S = {p: read_summary(kind, p) for p in PERIODS}
        if not S["dev"]:
            continue
        g = gates(kind)
        nets = [S[p]["total"]["net"] if S[p] else None for p in PERIODS]
        ok = all(g.get(k) == "PASS" for k in REQ[kind])
        if ok:
            passed.append(kind)
        label = "C0 一直持有" if kind == "c0" else f"C1 N={S['dev']['N']}"
        corr = f"{g['corr']:+.2f}" if np.isfinite(g.get("corr", np.nan)) else ""
        print(f"| {label} | {fmt(nets[0])} | {S['dev']['total']['apr']*100:.1f}% | {fmt(nets[1])} | {fmt(nets[2])} | "
              f"{corr} | " + " | ".join(g.get(k, "—") for k in ("G1", "G-APR", "G2", "G3", "G4", "G5", "G6"))
              + f" | {'✅' if ok else '❌'} |")
        out[kind] = g | {"nets": nets, "all_pass": ok}
    sel = OUT_DIR / "c1_selection.json"
    if sel.exists():
        s = json.loads(sel.read_text(encoding="utf-8"))
        print("\nC1 開發期各 N：" + "  ".join(f"N={k} ${v:,.0f}" for k, v in s["pnl_by_N"].items())
              + f"（平台中心 N={s['N']}）")
    print()
    pick = "c1" if "c1" in passed else ("c0" if "c0" in passed else None)
    have_hold = all(read_summary(k, "holdout") for k in ([pick] if pick else []))
    if pick and have_hold:
        base = pd.concat([baseline_daily(p) for p in PERIODS])
        cand = pd.concat([read_daily(pick, p) for p in PERIODS])
        for label, d in (("現行 ETH 單獨", base), (f"組合（現行＋{pick.upper()}）", pd.concat([base, cand]))):
            d = d.groupby(level=0).sum().sort_index()
            yr = d.groupby(d.index.year).sum()
            yrs = {int(y): float(yr.get(y, 0.0)) for y in YEARS}
            g7, g8 = all(v >= 0 for v in yrs.values()), mdd(d) <= G8_MDD_CAP
            print(f"{label}：" + "  ".join(f"{y} ${v:,.0f}" for y, v in yrs.items())
                  + f"  | 合計 ${d.sum():,.0f}  MDD ${mdd(d):,.0f}  G7 {pf(g7)}  G8 {pf(g8)}")
            out["combo" if label.startswith("組合") else "baseline"] = {"years": yrs, "total": float(d.sum()),
                                                                       "mdd": mdd(d), "G7": pf(g7), "G8": pf(g8)}
        verdict = "SHADOW 候選" if out["combo"]["G7"] == "PASS" and out["combo"]["G8"] == "PASS" else "REJECTED"
        print(f"\nV43 結論：{verdict}（{pick.upper()}）")
        out["verdict"] = verdict
    elif pick:
        print(f"{pick.upper()} 已過 G1～G6 中保留期以前的門檻；跑完保留期再看 G7／G8")
    else:
        dead = [k for k in out if any(out[k].get(x) == "FAIL" for x in REQ[k])]
        ready = [k for k in out if k not in dead and all(out[k].get(x) == "PASS" for x in ("G1", "G-APR", "G2"))]
        if ready:
            print(f"{'、'.join(k.upper() for k in ready)} 可動用保留期（G1／G-APR／G2 皆過）")
        elif len(out) == 2 and len(dead) == 2:
            print("C0、C1 皆未通過 → V43 結論 REJECTED（不動用保留期）")
            out["verdict"] = "REJECTED"
        else:
            print("尚未跑完 c0／c1 的 dev、val")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    shown = OUT_DIR.relative_to(ROOT) if OUT_DIR.is_relative_to(ROOT) else OUT_DIR
    print(f"\n明細：{shown}/（gates.json、*_trades.csv、*_daily.csv、*_summary.json）")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("c0", "c1"):
        p = sub.add_parser(name)
        p.add_argument("--period", choices=list(PERIODS), required=True)
        p.add_argument("--i-understand-holdout-once", action="store_true")
    sub.add_parser("report")
    args = ap.parse_args()
    {"c0": cmd_c0, "c1": cmd_c1, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
