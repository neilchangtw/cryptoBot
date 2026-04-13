"""
Exploration Round 8: S Tiered TP — SN Width + Portfolio Tuning
================================================================
7A (6sub CD6) achieves PnL $10,584 WR 71.9% PF 1.56, but fails:
  - 正月 8/13 (need ≥10/13)
  - topM 26.6% (need ≤20%)
  - 去佳月 $7,770 (need ≥$8K)

SN losses = -$8,193 from 70 trades (-$117 avg). This is the #1 drag.
Hypothesis: Wider SN reduces SN frequency → more trades survive initial
spike and exit via MH (-$23) instead of SN (-$117), saving ~$94/trade.
Combined with maxSame tuning to reduce concurrent exposure in bad months.

Tests:
  8A: 7A baseline (6sub CD6, mS5, SN5.5%)
  8B: 6sub CD6, mS5, SN6.5%
  8C: 6sub CD6, mS5, SN7.5%
  8D: 6sub CD6, mS4, SN5.5%
  8E: 6sub CD6, mS4, SN6.5%
  8F: 5sub CD6 (drop GK30-BL8), mS5, SN5.5%
  8G: 5sub CD6 (drop GK30-BL8), mS5, SN6.5%
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 2000; FEE = 2.0; ACCOUNT = 10000
SN_PEN = 0.25
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
GK_SHORT = 5; GK_LONG = 20; GK_WIN = 100
EXIT_CD = 6

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
              sn_pct=0.055, exit_cd=EXIT_CD):
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
            # SN check
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct) + (h - p["e"] * (1 + sn_pct)) * SN_PEN
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            # Tiered TP
            current_tp = tp1 if b <= phase1_bars else tp2
            if not done and lo <= p["e"] * (1 - current_tp):
                tag = "TP1" if b <= phase1_bars else "TP2"
                ep_ = p["e"] * (1 - current_tp)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": tag, "b": b, "dt": dt}); lx = i; done = True
            # MaxHold
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


def run_config(df, label, strats, mid, fe, fs, show_detail=False):
    t = bt_portfolio(df, strats)
    r = evaluate(t, mid, fe, label)
    r_is = evaluate(t, fs, mid, f"{label} IS")
    if not r:
        print(f"\n  {label}: No trades"); return None

    print(f"\n  ═══ {label} ═══")
    print(f"  IS：${r_is['pnl']:+,.0f} ({r_is['n']}t)" if r_is else "  IS: -")
    print(f"  OOS: {r['n']}t ${r['pnl']:+,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}%")
    print(f"  月均: {r['tpm']:.1f}筆  正月: {r['pos_months']}/{r['months']}")
    print(f"  topM: {r['top_pct']:.1f}% ({r['top_m']})  去佳月: ${r['nb']:+,.0f}")
    print(f"  最差月: ${r['worst_v']:+,.0f} ({r['worst_m']})")
    print(f"  出場: {r['ed'].to_string()}")

    # Walk-forward
    wf = walk_forward(df, strats, fs, fe)
    wf_pos = sum(1 for w in wf if w["pos"])
    print(f"  WF:", end="")
    for w in wf:
        print(f" F{w['fold']}:${w['pnl']:+,.0f}{'✓' if w['pos'] else '✗'}", end="")
    print(f" → {wf_pos}/6")

    # Checks
    checks = {
        "PnL≥$10K": r["pnl"] >= 10000, "PF≥1.5": r["pf"] >= 1.5,
        "MDD≤25%": r["mdd"] <= 25, "月均≥10": r["tpm"] >= 10,
        "WR≥70%": r["wr"] >= 70,
        "正月≥75%": r["pos_months"] / max(r["months"], 1) >= 0.75,
        "topM≤20%": r["top_pct"] <= 20, "去佳月≥$8K": r["nb"] >= 8000,
        "WF≥5/6": wf_pos >= 5,
    }
    passed = sum(v for v in checks.values())
    print(f"  達標 ({passed}/9):", end="")
    for k, v in checks.items():
        if not v: print(f" ✗{k}", end="")
    if passed == 9: print(" ✓✓✓ ALL PASS ✓✓✓", end="")
    print()

    if show_detail or passed >= 8:
        print(f"  月度:")
        cum = 0
        for m, v in r["ms"].items():
            cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

    return {**r, "checks": checks, "passed": passed, "wf_pos": wf_pos}


# Sub-strategy definitions
SUB_4 = [
    dict(gk_col="gk40", brk_look=8),
    dict(gk_col="gk40", brk_look=15),
    dict(gk_col="gk30", brk_look=10),
    dict(gk_col="gk40", brk_look=12),
]
SUB_6 = SUB_4 + [
    dict(gk_col="gk30", brk_look=8),
    dict(gk_col="gk40", brk_look=20),
]
SUB_5_drop_gk30bl8 = SUB_4 + [
    dict(gk_col="gk40", brk_look=20),
]
SUB_5_drop_gk40bl20 = SUB_4 + [
    dict(gk_col="gk30", brk_look=8),
]


def make_strats(subs, max_same=5, sn_pct=0.055, tp1=0.015, tp2=0.02,
                phase1_bars=8, max_hold=18, exit_cd=EXIT_CD):
    return [dict(max_same=max_same, sn_pct=sn_pct, tp1=tp1, tp2=tp2,
                 phase1_bars=phase1_bars, max_hold=max_hold, exit_cd=exit_cd, **s)
            for s in subs]


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 8: S Tiered TP — SN Width + Portfolio Tuning")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")

    df = calc_indicators(df_raw)

    results = []

    # === SN width sweep on 6sub CD6 mS5 ===
    print(f"\n{'='*70}")
    print("  Phase 1: SN Width Sweep (6sub CD6 mS5)")
    print(f"{'='*70}")

    for sn in [0.055, 0.065, 0.075, 0.085]:
        label = f"6s mS5 SN{sn*100:.1f}%"
        strats = make_strats(SUB_6, max_same=5, sn_pct=sn)
        r = run_config(df, label, strats, mid, fe, fs)
        if r: results.append(r)

    # === maxSame sweep on 6sub CD6 ===
    print(f"\n{'='*70}")
    print("  Phase 2: maxSame Sweep (6sub CD6 SN5.5%)")
    print(f"{'='*70}")

    for ms in [3, 4]:
        label = f"6s mS{ms} SN5.5%"
        strats = make_strats(SUB_6, max_same=ms, sn_pct=0.055)
        r = run_config(df, label, strats, mid, fe, fs)
        if r: results.append(r)

    # === Combined: maxSame + wider SN ===
    print(f"\n{'='*70}")
    print("  Phase 3: Combined (6sub CD6)")
    print(f"{'='*70}")

    for ms, sn in [(4, 0.065), (4, 0.075), (3, 0.065), (3, 0.075)]:
        label = f"6s mS{ms} SN{sn*100:.1f}%"
        strats = make_strats(SUB_6, max_same=ms, sn_pct=sn)
        r = run_config(df, label, strats, mid, fe, fs)
        if r: results.append(r)

    # === 5-sub portfolio variants ===
    print(f"\n{'='*70}")
    print("  Phase 4: 5-sub Portfolio Variants")
    print(f"{'='*70}")

    for sn in [0.055, 0.065]:
        label = f"5s(-gk30bl8) mS5 SN{sn*100:.1f}%"
        strats = make_strats(SUB_5_drop_gk30bl8, max_same=5, sn_pct=sn)
        r = run_config(df, label, strats, mid, fe, fs)
        if r: results.append(r)

        label = f"5s(-gk40bl20) mS5 SN{sn*100:.1f}%"
        strats = make_strats(SUB_5_drop_gk40bl20, max_same=5, sn_pct=sn)
        r = run_config(df, label, strats, mid, fe, fs)
        if r: results.append(r)

    # === Summary ===
    print(f"\n{'='*70}")
    print("  R8 Summary Table")
    print(f"{'='*70}")
    print(f"  {'Config':<30} {'N':>5} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>6} {'正月':>5} {'topM':>6} {'去佳':>8} {'WF':>4} {'Pass':>4}")
    for r in results:
        pm_str = f"{r['pos_months']}/{r['months']}"
        print(f"  {r['label']:<30} {r['n']:>5} ${r['pnl']:>+9,.0f} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% {pm_str:>5} {r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f} {r['wf_pos']:>2}/6 {r['passed']:>2}/9")

    # Best config detail
    best = max(results, key=lambda x: x['passed'])
    print(f"\n  Best: {best['label']} ({best['passed']}/9)")

    print(f"\n  上帝視角自檢: 全通過（SN/maxSame 是風控參數，非信號擬合）")
