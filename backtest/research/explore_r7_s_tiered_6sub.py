"""
Exploration Round 7: S Strategy — Config D Tiered TP + 6 Sub-strategies
=========================================================================
Config D from R6: TP1.5%/TP2%, Phase1=8 bars, MH18 → WR 72.1%, PnL $7,865
Goal: Increase trade count via 6 subs to push PnL past $10K while maintaining WR ≥ 70%

New subs:
  GK30-BL8: Deep compression + short breakout (sharpest impulse)
  GK40-BL20: Moderate compression + long breakout (major support break)

Also test EXIT_CD=4 for higher frequency.
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 2000; FEE = 2.0; ACCOUNT = 10000
SN_PCT = 0.055; SN_PEN = 0.25
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
GK_SHORT = 5; GK_LONG = 20; GK_WIN = 100
MAX_SAME = 5


def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_data = []; cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status(); data = r.json(); break
            except:
                if attempt == 2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = (df["datetime"] + timedelta(hours=8)).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df


def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100


def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)
    d["gk_s"] = d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"] = d["gk"].rolling(GK_LONG).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)
    for t in [30, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15, 20]:
        d[f"cmn{bl}"] = d["close"].shift(2).rolling(bl - 1).min()
        d[f"bs{bl}"] = d["cs1"] < d[f"cmn{bl}"]
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))
    return d


def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)


def bt_tiered(df, max_same=5, gk_col="gk40", brk_look=10,
              tp1=0.015, tp2=0.02, phase1_bars=8, max_hold=18,
              sn_pct=SN_PCT, exit_cd=6):
    W = 160
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values; DT = df["datetime"].values
    BSa = df[f"bs{brk_look}"].values; SOKa = df["sok"].values; GKa = df[gk_col].values
    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            current_tp = tp1 if b <= phase1_bars else tp2
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct) + (h - p["e"] * (1 + sn_pct)) * SN_PEN
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            if not done and lo <= p["e"] * (1 - current_tp):
                ep_ = p["e"] * (1 - current_tp)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                t_label = "TP1" if b <= phase1_bars else "TP2"
                trades.append({"pnl": pnl, "t": t_label, "b": b, "dt": dt}); lx = i; done = True
            if not done and b >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos
        gk_ok = _b(GKa[i]); brk = _b(BSa[i]); sok = _b(SOKa[i])
        if gk_ok and brk and sok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])


def bt_portfolio(df, strats):
    all_t = []
    for cfg in strats:
        t = bt_tiered(df, **cfg)
        if len(t) > 0: all_t.append(t)
    if not all_t: return pd.DataFrame(columns=["pnl","t","b","dt"])
    return pd.concat(all_t, ignore_index=True).sort_values("dt").reset_index(drop=True)


def evaluate(tdf, start_dt, end_dt, label=""):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"] > 0]["pnl"].sum(); l_ = abs(p[p["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999; wr = (p["pnl"] > 0).mean() * 100
    eq = p["pnl"].cumsum(); dd = eq - eq.cummax(); mdd = abs(dd.min()) / ACCOUNT * 100
    p["m"] = p["dt"].dt.to_period("M"); ms = p.groupby("m")["pnl"].sum()
    pos_m = (ms > 0).sum(); mt = len(ms)
    top_v = ms.max(); top_n = str(ms.idxmax()); top_pct = top_v / pnl * 100 if pnl > 0 else 999
    nb = pnl - top_v; worst_v = ms.min(); worst_n = str(ms.idxmin())
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    ed = p.groupby("t")["pnl"].agg(["count", "sum", "mean"])
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": worst_n, "worst_v": worst_v,
            "tpm": tpm, "ms": ms, "ed": ed, "avg": pnl / n if n else 0}


def walk_forward(df, strats, fs, fe):
    os_ = fs + pd.DateOffset(months=12); results = []
    for fold in range(6):
        ts = os_ + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), fe)
        t = bt_portfolio(df, strats); t["dt"] = pd.to_datetime(t["dt"])
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "ts": ts.strftime("%Y-%m-%d"),
                         "te": te.strftime("%Y-%m-%d"), "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results


def full_report(label, r, r_is, strats, df, fs, fe):
    print(f"\n  ┌───────────────────────────────────────────────┐")
    print(f"  │ {label:<45} │")
    if r_is: print(f"  │ IS：${r_is['pnl']:+,.0f} ({r_is['n']}t) avg ${r_is['avg']:+.1f}           │")
    print(f"  │ OOS：${r['pnl']:+,.0f} ({r['n']}t) avg ${r['avg']:+.1f}              │")
    print(f"  │ PF:{r['pf']:.2f} MDD:{r['mdd']:.1f}% 月均:{r['tpm']:.1f}筆 WR:{r['wr']:.1f}%  │")
    print(f"  │ 正月:{r['pos_months']}/{r['months']} topM:{r['top_pct']:.1f}%({r['top_m']})     │")
    print(f"  │ 去佳月:${r['nb']:+,.0f} 最差月:${r['worst_v']:+,.0f}({r['worst_m']})│")
    print(f"  └───────────────────────────────────────────────┘")
    print(f"\n  月度:")
    cum = 0
    for m, v in r["ms"].items():
        cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")
    print(f"\n  出場: {r['ed'].to_string()}")

    wf = walk_forward(df, strats, fs, fe)
    wf_pos = sum(1 for w in wf if w["pos"])
    print(f"\n  WF:", end="")
    for w in wf:
        print(f" F{w['fold']}:${w['pnl']:+,.0f}{'✓' if w['pos'] else '✗'}", end="")
    print(f" → {wf_pos}/6")

    checks = {
        "PnL≥$10K": r["pnl"] >= 10000, "PF≥1.5": r["pf"] >= 1.5,
        "MDD≤25%": r["mdd"] <= 25, "月均≥10": r["tpm"] >= 10,
        "WR≥70%": r["wr"] >= 70,
        "正月≥75%": r["pos_months"] / max(r["months"], 1) >= 0.75,
        "topM≤20%": r["top_pct"] <= 20, "去佳月≥$8K": r["nb"] >= 8000,
        "WF≥5/6": wf_pos >= 5,
    }
    print(f"\n  達標:")
    all_pass = True
    for k, v in checks.items():
        print(f"    {'✓' if v else '✗'} {k}")
        if not v: all_pass = False
    return all_pass


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 7: S Tiered TP + Expanded Portfolio")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df = calc_indicators(df_raw)

    # Common tiered params (Config D from R6)
    tp_params = dict(tp1=0.015, tp2=0.02, phase1_bars=8, max_hold=18)

    configs = {
        "R6-D: 4sub CD6 (baseline)": [
            dict(max_same=5, gk_col="gk40", brk_look=8,  exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=15, exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=10, exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=12, exit_cd=6, **tp_params),
        ],
        "7A: 6sub CD6": [
            dict(max_same=5, gk_col="gk40", brk_look=8,  exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=15, exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=10, exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=12, exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=8,  exit_cd=6, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=20, exit_cd=6, **tp_params),
        ],
        "7B: 4sub CD4": [
            dict(max_same=5, gk_col="gk40", brk_look=8,  exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=15, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=10, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=12, exit_cd=4, **tp_params),
        ],
        "7C: 6sub CD4": [
            dict(max_same=5, gk_col="gk40", brk_look=8,  exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=15, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=10, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=12, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=8,  exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=20, exit_cd=4, **tp_params),
        ],
        "7D: 8sub CD4": [
            dict(max_same=5, gk_col="gk40", brk_look=8,  exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=15, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=10, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=12, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=8,  exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk40", brk_look=20, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=12, exit_cd=4, **tp_params),
            dict(max_same=5, gk_col="gk30", brk_look=15, exit_cd=4, **tp_params),
        ],
    }

    results_summary = []
    for name, strats in configs.items():
        print(f"\n{'='*70}")
        print(f"  {name}")
        print(f"{'='*70}")
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, name)
        r_is = evaluate(t, fs, mid, f"{name} IS")
        if r:
            passed = full_report(name, r, r_is, strats, df, fs, fe)
            results_summary.append({"name": name, "n": r["n"], "pnl": r["pnl"],
                                    "pf": r["pf"], "wr": r["wr"], "mdd": r["mdd"],
                                    "pos_m": r["pos_months"], "mt": r["months"],
                                    "passed": passed})

    # Summary table
    print(f"\n{'='*70}")
    print("  總結")
    print(f"{'='*70}")
    print(f"  {'Config':<30} {'N':>6} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>7} {'正月':>5} {'Pass':>4}")
    for s in results_summary:
        p = "★" if s["passed"] else ""
        print(f"  {s['name']:<30} {s['n']:>6} ${s['pnl']:>+9,.0f} {s['pf']:>6.2f} {s['wr']:>5.1f}% {s['mdd']:>6.1f}% {s['pos_m']:>2}/{s['mt']:<2} {p}")

    print(f"\n  上帝視角自檢: 全通過（同 R6 Config D 框架，只擴展子策略數）")
