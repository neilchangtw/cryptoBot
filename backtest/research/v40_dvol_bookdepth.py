"""V40 兩個從未實測的新資訊：A. Deribit ETH DVOL 突升（9/11 第三輪規則 D）；B. 幣安 bookDepth 盤口深度。

預登記：doc/v40_dvol_bookdepth_plan.md（2026-10-29，看資料前鎖定）。
只在 VPS 執行（不得在公司網路／設備下載行情）；bookDepth 每日檔取完訊號小時即刪。
需先有 V39 的 data/v39/ETH_1h_long.csv（v39_s_exhaustion_oos.py klines）。

用法（VPS，cryptobot，~/cryptoBot）：
    .venv/bin/python backtest/research/v40_dvol_bookdepth.py dvol-fetch
    .venv/bin/python backtest/research/v40_dvol_bookdepth.py dvol-analyze
    .venv/bin/python backtest/research/v40_dvol_bookdepth.py depth-probe
    nohup .venv/bin/python backtest/research/v40_dvol_bookdepth.py depth-fetch > ~/v40_depth.log 2>&1 &
    .venv/bin/python backtest/research/v40_dvol_bookdepth.py depth-analyze
"""

from __future__ import annotations

import argparse
import csv
import inspect
import io
import json
import math
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

import price_candle_trade_analysis as base
import v38_signal_aggtrades as v38

ROOT = Path(__file__).resolve().parents[2]
LONG_CSV = ROOT / "data" / "v39" / "ETH_1h_long.csv"
OUT_DIR = ROOT / "doc" / "research_results" / "v40_dvol_bookdepth"
DVOL_CSV = OUT_DIR / "eth_dvol_1h.csv"
DEPTH_EVENTS = OUT_DIR / "depth_events.csv"
DEPTH_FEATURES = OUT_DIR / "depth_features.csv"

START = "2021-03-01"
SPLIT = "2024-11-01"           # 開發 < SPLIT ≤ 保留（保留期 = 近 2 年）
EXTRA_COST = 5.0
DVOL_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
DEPTH_URL = "https://data.binance.vision/data/futures/um/daily/bookDepth/ETHUSDT/ETHUSDT-bookDepth-{day}.zip"
SHOCK_MAIN, SHOCK_NEIGHBORS = 0.10, (0.05, 0.15)
DEPTH_FEATURES_COLS = ["support_share_1", "support_share_2", "resist_change_1"]


# ---------------------------------------------------------------- 共用
def load_long():
    candles = pd.read_csv(LONG_CSV)
    engine = base.load_engine()
    ind = engine.compute_indicators(candles)
    dt = pd.to_datetime(candles["datetime"])
    return candles, engine, ind, dt


def build_sim(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    for old, key in (("pL[i] < L_GK_TH and brk_up[i]):", "l_block"),
                     ("pS[i] < S_GK_TH and brk_dn[i]):", "s_block")):
        assert src.count(old) == 1, old
        src = src.replace(old, old[:-2] + f" and not ('{key}' in ind and ind['{key}'][i])):")
    ns = dict(engine.__dict__)
    exec(src, ns)
    return ns["simulate_v14_detailed"]


def replay(sim, ind, dt, extra=None) -> pd.DataFrame:
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(START))))
    f = pd.DataFrame(sim({**ind, **(extra or {})}, dt.to_numpy(), start_bar=sb,
                         realistic=True, extra_cost=EXTRA_COST))
    f["entry_dt"] = pd.to_datetime(f["entry_dt"])
    return f


def summary(f: pd.DataFrame) -> dict:
    out = {}
    for name, part in (("dev", f[f.entry_dt < SPLIT]), ("hold", f[f.entry_dt >= SPLIT])):
        p = part.pnl_usd
        out[name] = (len(part), float((p > 0).mean() * 100) if len(part) else math.nan, float(p.sum()))
    return out


def print_gate(label: str, base_s: dict, cand_s: dict) -> bool:
    ok = True
    for per in ("dev", "hold"):
        n0, w0, p0 = base_s[per]
        n1, w1, p1 = cand_s[per]
        good = (w1 - w0 >= 2.0) and (p1 >= p0)
        ok &= good
        print(f"  {label} {per:4s}：{n0}→{n1} 筆  勝率 {w0:.1f}%→{w1:.1f}%（{w1 - w0:+.1f}pp）"
              f"  PnL ${p0:.0f}→${p1:.0f}  {'PASS' if good else 'FAIL'}")
    return ok


def candidate_mask(engine, ind, dt, side: str) -> np.ndarray:
    if side == "L":
        m = (np.nan_to_num(ind["pctile_L"], nan=999) < engine.L_GK_TH) & np.asarray(ind["brk_up"], bool)
        m &= ~np.isin(ind["hours"], list(engine.L_BLK_H)) & ~np.isin(ind["dows"], list(engine.L_BLK_D))
        rb = ind.get("regime_block_l")
    else:
        m = (np.nan_to_num(ind["pctile_S"], nan=999) < engine.S_GK_TH) & np.asarray(ind["brk_dn"], bool)
        m &= ~np.isin(ind["hours"], list(engine.S_BLK_H)) & ~np.isin(ind["dows"], list(engine.S_BLK_D))
        rb = ind.get("regime_block_s")
    if rb is not None:
        m &= ~np.asarray(rb, bool)
    m &= (dt >= START).to_numpy()
    m[: engine.WARMUP] = False
    return m


def open_utc_ms(dt: pd.Series) -> np.ndarray:
    return ((dt - pd.Timedelta(hours=8) - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)).to_numpy("int64")


# ---------------------------------------------------------------- A. DVOL
def cmd_dvol_fetch(_args) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    start = int(pd.Timestamp("2021-01-01").value // 1_000_000)
    end = int(time.time() * 1000)
    rows = []
    for _ in range(200):
        q = urllib.parse.urlencode({"currency": "ETH", "start_timestamp": start,
                                    "end_timestamp": end, "resolution": "3600"})
        with urllib.request.urlopen(f"{DVOL_URL}?{q}", timeout=30) as resp:
            res = json.loads(resp.read())["result"]
        rows += res["data"]
        print(f"  {len(rows)} 根，至 {pd.to_datetime(res['data'][-1][0] if res['data'] else end, unit='ms')}", flush=True)
        if not res.get("continuation") or not res["data"]:
            break
        end = int(res["continuation"])
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).drop_duplicates("ts").sort_values("ts")
    df.to_csv(DVOL_CSV, index=False)
    print(f"wrote {DVOL_CSV}：{len(df)} 根，{pd.to_datetime(df.ts.min(), unit='ms')} ～ {pd.to_datetime(df.ts.max(), unit='ms')}")


def dvol_shock(dvol: pd.DataFrame, dt: pd.Series) -> np.ndarray:
    """決策 D = 訊號棒開盤+1h；q = D−2h 為 DVOL 小時棒「開始」時間（保守，見 9/11 規則）。
    shock = close(q)/close(q−24h) − 1；q−24h～q 的 25 根須齊全，否則 NaN（禁止進場）。"""
    s = pd.Series(dvol["close"].to_numpy(float), index=dvol["ts"].to_numpy("int64"))
    s = s[(s > 0) & np.isfinite(s)]
    q = open_utc_ms(dt) - 3_600_000       # D = 開盤+1h；q = D−2h，視為 DVOL 棒開始，最晚於 q+1h = 訊號棒開盤可得
    full = s.reindex(np.arange(s.index.min(), s.index.max() + 1, 3_600_000))
    ok25 = full.notna().rolling(25).sum() == 25
    v_now = full.reindex(q).to_numpy()
    v_prev = full.reindex(q - 24 * 3_600_000).to_numpy()
    good = ok25.reindex(q).eq(True).to_numpy(bool)
    out = v_now / v_prev - 1
    out[~good] = np.nan
    return out


def cmd_dvol_analyze(_args) -> None:
    candles, engine, ind, dt = load_long()
    dvol = pd.read_csv(DVOL_CSV)
    shock = dvol_shock(dvol, dt)
    sim = build_sim(engine)
    b = replay(sim, ind, dt)
    base_s = summary(b)
    cov = np.isfinite(shock[b.entry_bar.to_numpy(int)])
    print(f"DVOL {len(dvol)} 根；基準 {len(b)} 筆，有效 shock {cov.sum()}／{len(b)}（{cov.mean() * 100:.1f}%）")
    tr = b.assign(shock=shock[b.entry_bar.to_numpy(int)])
    for side in ("L", "S"):
        for per, part in (("dev", tr[tr.entry_dt < SPLIT]), ("hold", tr[tr.entry_dt >= SPLIT])):
            t = part[part.side == side].dropna(subset=["shock"])
            auc = v38.rank_auc(t.shock.to_numpy(), (t.pnl_usd > 0).to_numpy())
            hi = t[t.shock > SHOCK_MAIN]
            print(f"  診斷 {side} {per:4s}：AUC {auc:.3f}；shock>10% {len(hi)} 筆 勝率 "
                  f"{(hi.pnl_usd > 0).mean() * 100 if len(hi) else float('nan'):.1f}%")
    print("\n[主候選 D] shock > 10% 拒絕 L/S；缺值也拒絕（預登記）")
    def blocks(th):
        blk = ~(np.nan_to_num(shock, nan=np.inf) <= th)
        return {"l_block": blk, "s_block": blk}
    ok = print_gate("D(10%)", base_s, summary(replay(sim, ind, dt, blocks(SHOCK_MAIN))))
    if ok:
        for th in SHOCK_NEIGHBORS:
            print_gate(f"D({th * 100:.0f}%)", base_s, summary(replay(sim, ind, dt, blocks(th))))
    print(f"\n結論 A（DVOL）：{'SHADOW 候選（需鄰域同向）' if ok else 'REJECTED'}")


# ---------------------------------------------------------------- B. bookDepth
def read_depth(zip_path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf, zf.open(zf.namelist()[0]) as fh:
        df = pd.read_csv(fh)
    cols = {c.lower(): c for c in df.columns}
    ts_col = cols.get("timestamp") or df.columns[0]
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    return pd.DataFrame({"T": ((ts - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)),
                         "pct": df[cols.get("percentage", df.columns[1])].astype(float),
                         "notional": df[cols.get("notional", df.columns[-1])].astype(float)}).dropna()


def depth_features(d: pd.DataFrame, side: str, t0: int) -> dict:
    """訊號棒 [t0, t0+1h)。percentage 為累積深度（±1%＝距中價 1% 內）。順向：L 支撐＝買單（負％）、阻力＝賣單（正％）。"""
    w = d[(d["T"] >= t0) & (d["T"] < t0 + 3_600_000)]
    if w.empty:
        return {}
    sgn = 1 if side == "L" else -1
    def at(frame, pct):
        return frame[np.isclose(frame["pct"], pct)].groupby("T")["notional"].last()
    last_t = w["T"].max()
    snap = w[w["T"] == last_t]
    out = {}
    for k in (1, 2):
        sup = snap.loc[np.isclose(snap.pct, -sgn * k), "notional"].sum()
        res = snap.loc[np.isclose(snap.pct, sgn * k), "notional"].sum()
        out[f"support_share_{k}"] = float(sup / (sup + res)) if sup + res > 0 else math.nan
    r = at(w, sgn * 1)
    first, last = r[r.index < t0 + 600_000], r[r.index >= t0 + 3_000_000]
    out["resist_change_1"] = float(last.mean() / first.mean()) if len(first) and len(last) and first.mean() > 0 else math.nan
    out["snap_age_s"] = float((t0 + 3_600_000 - last_t) / 1000)
    return out


def cmd_depth_probe(_args) -> None:
    for day in ("2023-01-15", "2023-06-15", "2024-01-15", "2025-06-15"):
        tmp = Path("/tmp/v40_probe.zip")
        if not v38.download(DEPTH_URL.format(day=day), tmp):
            print(f"{day}：無檔案")
            continue
        with zipfile.ZipFile(tmp) as zf, zf.open(zf.namelist()[0]) as fh:
            head = [next(fh).decode().strip() for _ in range(4)]
        print(f"{day}：{tmp.stat().st_size / 1e6:.1f}MB；{head}")
        tmp.unlink(missing_ok=True)


def cmd_depth_fetch(args) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles, engine, ind, dt = load_long()
    ms = open_utc_ms(dt)
    rows = []
    for side in ("L", "S"):
        for i in np.flatnonzero(candidate_mask(engine, ind, dt, side)):
            rows.append({"key": f"{side}_{dt.iloc[i]:%Y%m%d%H}", "side": side, "entry_bar": int(i),
                         "entry_dt": dt.iloc[i], "open_utc_ms": int(ms[i])})
    ev = pd.DataFrame(rows)
    ev = ev[ev.entry_dt >= args.since]
    ev.to_csv(DEPTH_EVENTS, index=False)
    done = set(pd.read_csv(DEPTH_FEATURES)["key"]) if DEPTH_FEATURES.exists() else set()
    todo = ev[~ev.key.isin(done)].copy()
    todo["day"] = pd.to_datetime(todo.open_utc_ms, unit="ms").dt.strftime("%Y-%m-%d")
    days = sorted(todo.day.unique())
    print(f"L/S 候選事件 {len(ev)}（{args.since} 起），待抓 {len(todo)}／{len(days)} 天", flush=True)
    header = ["key"] + DEPTH_FEATURES_COLS + ["snap_age_s"]
    new = not DEPTH_FEATURES.exists()
    tmp = Path(args.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    with DEPTH_FEATURES.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        if new:
            w.writeheader()
        for n, day in enumerate(days, 1):
            started = time.time()
            zp = tmp / f"bookDepth-{day}.zip"
            if not v38.download(DEPTH_URL.format(day=day), zp):
                print(f"[{n}/{len(days)}] {day} 無每日檔，略過", flush=True)
                continue
            try:
                d = read_depth(zp)
            finally:
                zp.unlink(missing_ok=True)
            part = todo[todo.day == day]
            for _, r in part.iterrows():
                w.writerow({"key": r.key, **{k: v for k, v in depth_features(d, r.side, int(r.open_utc_ms)).items() if k in header}})
            fh.flush()
            print(f"[{n}/{len(days)}] {day} {len(part)} 個事件，{time.time() - started:.0f}s", flush=True)
            time.sleep(args.pause)


def cmd_depth_analyze(args) -> None:
    candles, engine, ind, dt = load_long()
    ev = pd.read_csv(DEPTH_EVENTS).merge(pd.read_csv(DEPTH_FEATURES), on="key")
    ev["entry_dt"] = pd.to_datetime(ev["entry_dt"])
    first = ev.entry_dt.min()
    sim = build_sim(engine)
    b = replay(sim, ind, dt)
    b = b[b.entry_dt >= first]
    tr = b.merge(ev[["entry_bar", "side"] + DEPTH_FEATURES_COLS], on=["entry_bar", "side"], how="left")
    split = pd.Timestamp(args.split)
    print(f"bookDepth 事件 {len(ev)}（{first:%Y-%m-%d} 起）；基準成交 {len(tr)} 筆，有特徵 "
          f"{tr[DEPTH_FEATURES_COLS[0]].notna().sum()}；開發 < {split:%Y-%m-%d} ≤ 保留")
    rng = np.random.default_rng(40)
    alpha = 0.05 / (len(DEPTH_FEATURES_COLS) * 2)
    passed = []
    for side in ("L", "S"):
        for col in DEPTH_FEATURES_COLS:
            d = tr[(tr.side == side) & (tr.entry_dt < split)].dropna(subset=[col])
            h = tr[(tr.side == side) & (tr.entry_dt >= split)].dropna(subset=[col])
            x, y = d[col].to_numpy(float), (d.pnl_usd > 0).to_numpy()
            auc = v38.rank_auc(x, y)
            null = np.array([abs(v38.rank_auc(x, rng.permutation(y)) - 0.5) for _ in range(args.perm)])
            p = float((1 + (null >= abs(auc - 0.5)).sum()) / (1 + args.perm))
            auc_h = v38.rank_auc(h[col].to_numpy(float), (h.pnl_usd > 0).to_numpy())
            ok = np.isfinite(auc) and np.isfinite(auc_h) and p < alpha and (auc - 0.5) * (auc_h - 0.5) > 0 and abs(auc_h - 0.5) >= 0.05
            print(f"  {side} {col:16s} 開發 n={len(d)} AUC {auc:.3f} p {p:.4f} | 保留 n={len(h)} AUC {auc_h:.3f} {'PASS' if ok else ''}")
            if ok:
                passed.append((side, col, auc, d))
    print(f"第一關門檻：開發 p < {alpha:.4f}（Bonferroni）且保留同向 |AUC−0.5| ≥ 0.05")
    if not passed:
        print("\n結論 B（bookDepth）：REJECTED")
        return
    base_s = summary_split(b, split)
    final = False
    for side, col, auc, d in passed:
        worst_low = auc > 0.5                      # AUC>0.5：數值越低越容易虧 → 擋低三分之一
        cut = d[col].quantile(1 / 3 if worst_low else 2 / 3)
        sel = ev[(ev.side == side)]
        bad = sel[(sel[col] < cut) if worst_low else (sel[col] > cut)]
        blk = np.zeros(len(dt), bool)
        blk[bad.entry_bar.to_numpy(int)] = True
        f = replay(sim, ind, dt, {f"{side.lower()}_block": blk})
        f = f[f.entry_dt >= first]
        print(f"[第二關] {side} {col} {'<' if worst_low else '>'} {cut:.4f} 不做")
        final |= print_gate(f"{side}-{col}", base_s, summary_split(f, split))
    print(f"\n結論 B（bookDepth）：{'SHADOW 候選' if final else 'REJECTED'}")


def summary_split(f: pd.DataFrame, split: pd.Timestamp) -> dict:
    out = {}
    for name, part in (("dev", f[f.entry_dt < split]), ("hold", f[f.entry_dt >= split])):
        p = part.pnl_usd
        out[name] = (len(part), float((p > 0).mean() * 100) if len(part) else math.nan, float(p.sum()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dvol-fetch")
    sub.add_parser("dvol-analyze")
    sub.add_parser("depth-probe")
    f = sub.add_parser("depth-fetch")
    f.add_argument("--since", default="2023-01-01")
    f.add_argument("--tmp-dir", default="/tmp/v40_depth")
    f.add_argument("--pause", type=float, default=1.0)
    a = sub.add_parser("depth-analyze")
    a.add_argument("--split", default="2025-06-01")
    a.add_argument("--perm", type=int, default=2000)
    args = ap.parse_args()
    {"dvol-fetch": cmd_dvol_fetch, "dvol-analyze": cmd_dvol_analyze, "depth-probe": cmd_depth_probe,
     "depth-fetch": cmd_depth_fetch, "depth-analyze": cmd_depth_analyze}[args.cmd](args)


if __name__ == "__main__":
    main()
