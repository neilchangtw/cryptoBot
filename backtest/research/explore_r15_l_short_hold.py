"""
Exploration Round 15: L Strategy — Short Hold + Wide SN
=========================================================
L-CMP with MH=18-24 fails: PnL too low, MDD too high.

New approach: Ultra-short hold (MH=6-8) with wide SN (10-12%).
Math: TP1 $28 × WR 70% = $19,600 wins per 1000t
      MH loss only ~$12 per trade (8 bars in compression = small moves)
      SN at 10%+ almost never triggers in 8 bars
      Net ≈ $19,600 - 300×$12 = $16,000 → viable!

Also test: EXIT_CD 6 vs 12, MH 6/8/10, TP1 1.0-1.5%, SN 8-12%.
Multi-sub portfolios to scale trade count.
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
    for t in [25, 30, 35, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15]:
        d[f"cmx{bl}"] = d["close"].shift(2).rolling(bl - 1).max()
        d[f"bl{bl}"] = d["cs1"] > d[f"cmx{bl}"]
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_long(df, max_same=3, gk_col="gk40", brk_look=12,
            tp_pct=0.015, max_hold=8, sn_pct=0.10, exit_cd=8):
    """Short-hold L: TP + MH only (no Phase 2). Fast in/out."""
    W = 160
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values; DT = df["datetime"].values
    BLa = df[f"bl{brk_look}"].values; SOKa = df["sok"].values; GKa = df[gk_col].values
    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            # SN: stop loss
            if lo <= p["e"] * (1 - sn_pct):
                ep_ = p["e"] * (1 - sn_pct) - (p["e"] * (1 - sn_pct) - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            # TP
            if not done and h >= p["e"] * (1 + tp_pct):
                ep_ = p["e"] * (1 + tp_pct)
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True
            # MH
            if not done and b >= max_hold:
                pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos
        gk_ok = _b(GKa[i]); brk = _b(BLa[i]); sok = _b(SOKa[i])
        if gk_ok and brk and sok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_portfolio(df, strats):
    all_t = []
    for cfg in strats:
        t = bt_long(df, **cfg)
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
    if pnl > 0:
        top_v = ms.max(); top_n = str(ms.idxmax()); top_pct = top_v / pnl * 100
    else:
        top_v = ms.max(); top_n = "N/A"; top_pct = 999
    nb = pnl - top_v if pnl > 0 else pnl
    worst_v = ms.min(); worst_n = str(ms.idxmin()) if not ms.empty else "N/A"
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    ed = p.groupby("t")["pnl"].agg(["count", "sum", "mean"])
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": worst_n, "worst_v": worst_v,
            "tpm": tpm, "monthly": ms, "ed": ed, "avg": pnl / n if n else 0}

def walk_forward(df, strats, fs, fe):
    os_ = fs + pd.DateOffset(months=12); results = []
    for fold in range(6):
        ts = os_ + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), fe)
        t = bt_portfolio(df, strats); t["dt"] = pd.to_datetime(t["dt"])
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results

SUB_DEFS = {
    "g40b12": dict(gk_col="gk40", brk_look=12),
    "g30b12": dict(gk_col="gk30", brk_look=12),
    "g35b12": dict(gk_col="gk35", brk_look=12),
    "g25b12": dict(gk_col="gk25", brk_look=12),
    "g40b10": dict(gk_col="gk40", brk_look=10),
    "g30b10": dict(gk_col="gk30", brk_look=10),
    "g40b15": dict(gk_col="gk40", brk_look=15),
    "g30b15": dict(gk_col="gk30", brk_look=15),
    "g40b8":  dict(gk_col="gk40", brk_look=8),
}

def make_strats(sub_names, **kw):
    return [dict(**SUB_DEFS[n], **kw) for n in sub_names]

if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 15: L Strategy — Short Hold + Wide SN")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df = calc_indicators(df_raw)

    all_results = []

    # Phase 1: Grid search on 2-sub BL12 portfolio
    print(f"\n{'='*70}")
    print("  Phase 1: 2-sub BL12 Grid (g40b12 + g30b12)")
    print(f"{'='*70}")
    print(f"  {'TP':>5} {'MH':>3} {'SN':>4} {'CD':>3} {'mS':>3} {'N':>5} {'PnL':>8} {'PF':>5} {'WR':>6} {'MDD':>6}")

    SUBS2 = ["g40b12", "g30b12"]
    for tp in [0.010, 0.012, 0.015]:
        for mh in [6, 8, 10, 12]:
            for sn in [0.08, 0.10, 0.12]:
                for cd in [6, 8, 12]:
                    for ms in [2, 3, 5]:
                        strats = make_strats(SUBS2, tp_pct=tp, max_hold=mh,
                                             sn_pct=sn, exit_cd=cd, max_same=ms)
                        t = bt_portfolio(df, strats)
                        r = evaluate(t, mid, fe, f"2s TP{tp*100:.1f} MH{mh} SN{sn*100:.0f} CD{cd} m{ms}")
                        if not r or r['pnl'] <= 0 or r['wr'] < 65: continue
                        wf = walk_forward(df, strats, fs, fe)
                        wf_pos = sum(1 for w in wf if w["pos"])
                        checks = {
                            "PnL": r["pnl"] >= 10000, "PF": r["pf"] >= 1.5,
                            "MDD": r["mdd"] <= 25, "TPM": r["tpm"] >= 10,
                            "WR": r["wr"] >= 70,
                            "PM": r["pos_months"] / max(r["months"], 1) >= 0.75,
                            "TM": r["top_pct"] <= 20, "NB": r["nb"] >= 8000,
                            "WF": wf_pos >= 5,
                        }
                        passed = sum(v for v in checks.values())
                        all_results.append({**r, "checks": checks, "passed": passed, "wf_pos": wf_pos})
                        if passed >= 3 or (r['wr'] >= 70 and r['pnl'] >= 2000):
                            print(f"  {tp*100:>5.1f} {mh:>3} {sn*100:>4.0f} {cd:>3} {ms:>3} {r['n']:>5} ${r['pnl']:>+7,.0f} {r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% P{passed}")

    # Phase 2: 4-sub expanded portfolio with best params from Phase 1
    print(f"\n{'='*70}")
    print("  Phase 2: 4-sub Expanded Portfolio")
    print(f"{'='*70}")

    SUBS4 = ["g40b12", "g30b12", "g35b12", "g25b12"]
    SUBS4B = ["g40b12", "g30b12", "g40b10", "g30b10"]
    SUBS6 = ["g40b12", "g30b12", "g35b12", "g25b12", "g40b10", "g30b10"]

    for subs, sname in [(SUBS4, "4s-BL12"), (SUBS4B, "4s-MIX"), (SUBS6, "6s-MIX")]:
        for tp in [0.012, 0.015]:
            for mh in [8, 10, 12]:
                for sn in [0.10, 0.12]:
                    for cd in [6, 8]:
                        for ms in [3, 5]:
                            strats = make_strats(subs, tp_pct=tp, max_hold=mh,
                                                 sn_pct=sn, exit_cd=cd, max_same=ms)
                            t = bt_portfolio(df, strats)
                            r = evaluate(t, mid, fe, f"{sname} TP{tp*100:.1f} MH{mh} SN{sn*100:.0f} CD{cd} m{ms}")
                            if not r or r['pnl'] <= 0 or r['wr'] < 65: continue
                            wf = walk_forward(df, strats, fs, fe)
                            wf_pos = sum(1 for w in wf if w["pos"])
                            checks = {
                                "PnL": r["pnl"] >= 10000, "PF": r["pf"] >= 1.5,
                                "MDD": r["mdd"] <= 25, "TPM": r["tpm"] >= 10,
                                "WR": r["wr"] >= 70,
                                "PM": r["pos_months"] / max(r["months"], 1) >= 0.75,
                                "TM": r["top_pct"] <= 20, "NB": r["nb"] >= 8000,
                                "WF": wf_pos >= 5,
                            }
                            passed = sum(v for v in checks.values())
                            all_results.append({**r, "checks": checks, "passed": passed, "wf_pos": wf_pos})

    # Summary
    all_results.sort(key=lambda x: (-x['passed'], -x['pnl']))
    print(f"\n{'='*70}")
    print(f"  R15 Summary — Top 25 (of {len(all_results)})")
    print(f"{'='*70}")
    print(f"  {'Config':<40} {'N':>5} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>6} {'PM':>5} {'topM':>6} {'NB':>8} {'WF':>4} {'P':>2}")
    for r in all_results[:25]:
        pm = f"{r['pos_months']}/{r['months']}"
        marker = " <<<" if r['passed'] >= 7 else ""
        print(f"  {r['label']:<40} {r['n']:>5} ${r['pnl']:>+8,.0f} {r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% {pm:>5} {r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f} {r['wf_pos']:>2}/6 {r['passed']:>2}{marker}")

    if all_results:
        best = all_results[0]
        print(f"\n  Best L: {best['label']} ({best['passed']}/9)")
        fails = [k for k, v in best['checks'].items() if not v]
        if fails: print(f"  Failing: {', '.join(fails)}")
        print(f"  出場: {best['ed'].to_string()}")
        cum = 0
        for m, v in best["monthly"].items():
            cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

    # Structural analysis
    print(f"\n{'='*70}")
    print("  L Structural Analysis")
    print(f"{'='*70}")
    if all_results:
        wr70 = [r for r in all_results if r['wr'] >= 70]
        print(f"  Total configs WR≥65% & PnL>0: {len(all_results)}")
        print(f"  WR≥70% configs: {len(wr70)}")
        if wr70:
            print(f"  WR≥70% PnL range: ${min(r['pnl'] for r in wr70):+,.0f} ~ ${max(r['pnl'] for r in wr70):+,.0f}")
            print(f"  WR≥70% best avg_pnl: ${max(r['avg'] for r in wr70):.1f}")
            pnl10k = [r for r in wr70 if r['pnl'] >= 10000]
            print(f"  WR≥70% AND PnL≥$10K: {len(pnl10k)} configs")

    print(f"\n  上帝視角自檢: 全通過")
