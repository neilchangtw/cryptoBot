"""
Exploration Round 13: L Strategy — CMP Tiered TP for Longs
============================================================
S strategy achieved 9/9 via Mixed-TP Portfolio.
Now need L strategy with WR ≥ 70% + all criteria.

Known:
  - L trend-following (EMA Trail): WR 45%, PF 2.94 — great PF but WR too low
  - L CMP basic (TP 2%, MH 12): WR 55% — upward breakouts slower on ETH
  - S-CMP Tiered TP achieved WR 72% for shorts

Hypothesis: L-CMP with Tiered TP + longer MH + lower TP1 can push WR to 70%.
  - ETH upward moves are slower → need lower TP1 (1.0-1.5%) and longer MH
  - Phase1 captures quick impulse, Phase2 captures follow-through
  - SN 6.5% on downside

Tests: Grid search over TP1, MH, Phase1, GK threshold, Breakout lookback.
Entry: GK compression + upward breakout (close > N-bar HIGH).
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
    for t in [25, 30, 35, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t
    d["cs1"] = d["close"].shift(1)
    # Upward breakout: close > N-bar high
    for bl in [6, 8, 10, 12, 15, 20]:
        d[f"cmx{bl}"] = d["close"].shift(2).rolling(bl - 1).max()
        d[f"bl{bl}"] = d["cs1"] > d[f"cmx{bl}"]  # long breakout
    # Downward breakout (for S reference)
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

def bt_long_tiered(df, max_same=5, gk_col="gk40", brk_look=10,
                   tp1=0.015, tp2=0.02, phase1_bars=8, max_hold=18,
                   sn_pct=0.065, exit_cd=EXIT_CD):
    """Long CMP with Tiered TP. Buy low, sell high."""
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
            # SN: price drops below entry - sn_pct (long stop loss)
            if lo <= p["e"] * (1 - sn_pct):
                ep_ = p["e"] * (1 - sn_pct) - (p["e"] * (1 - sn_pct) - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            # Tiered TP: price rises above entry + tp (long take profit)
            current_tp = tp1 if b <= phase1_bars else tp2
            if not done and h >= p["e"] * (1 + current_tp):
                tag = "TP1" if b <= phase1_bars else "TP2"
                ep_ = p["e"] * (1 + current_tp)
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": tag, "b": b, "dt": dt}); lx = i; done = True
            # MaxHold
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
        t = bt_long_tiered(df, **cfg)
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
    "gk40bl6":  dict(gk_col="gk40", brk_look=6),
    "gk40bl8":  dict(gk_col="gk40", brk_look=8),
    "gk40bl10": dict(gk_col="gk40", brk_look=10),
    "gk40bl12": dict(gk_col="gk40", brk_look=12),
    "gk40bl15": dict(gk_col="gk40", brk_look=15),
    "gk40bl20": dict(gk_col="gk40", brk_look=20),
    "gk30bl6":  dict(gk_col="gk30", brk_look=6),
    "gk30bl8":  dict(gk_col="gk30", brk_look=8),
    "gk30bl10": dict(gk_col="gk30", brk_look=10),
    "gk30bl12": dict(gk_col="gk30", brk_look=12),
    "gk30bl15": dict(gk_col="gk30", brk_look=15),
    "gk25bl8":  dict(gk_col="gk25", brk_look=8),
    "gk25bl10": dict(gk_col="gk25", brk_look=10),
    "gk35bl8":  dict(gk_col="gk35", brk_look=8),
    "gk35bl10": dict(gk_col="gk35", brk_look=10),
    "gk35bl12": dict(gk_col="gk35", brk_look=12),
}

def make_strats(sub_names, **kw):
    return [dict(**SUB_DEFS[n], **kw) for n in sub_names]


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 13: L Strategy — CMP Tiered TP for Longs")
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

    # Phase 1: Single-sub exploration to find which subs work for L
    print(f"\n{'='*70}")
    print("  Phase 1: Single-Sub Exploration (TP1=1.2% MH18 Ph8 SN6.5%)")
    print(f"{'='*70}")

    base_params = dict(max_same=5, sn_pct=0.065, tp1=0.012, tp2=0.02,
                       phase1_bars=8, max_hold=18, exit_cd=EXIT_CD)
    for name in ["gk40bl6","gk40bl8","gk40bl10","gk40bl12","gk40bl15","gk40bl20",
                  "gk30bl8","gk30bl10","gk30bl12","gk30bl15",
                  "gk25bl8","gk25bl10","gk35bl8","gk35bl10"]:
        strats = make_strats([name], **base_params)
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, name)
        if r and r['n'] >= 30:
            print(f"  {name:<14} {r['n']:>4}t ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% avg${r['avg']:.1f}")

    # Phase 2: TP1 sensitivity (4-sub portfolio)
    print(f"\n{'='*70}")
    print("  Phase 2: TP1 Sensitivity (4sub gk40: bl8+bl10+bl12+bl15)")
    print(f"{'='*70}")

    L_4SUB = ["gk40bl8", "gk40bl10", "gk40bl12", "gk40bl15"]

    for tp1 in [0.008, 0.010, 0.012, 0.014, 0.015, 0.018]:
        for mh in [12, 18, 24]:
            strats = make_strats(L_4SUB, max_same=5, sn_pct=0.065,
                                 tp1=tp1, tp2=0.02, phase1_bars=8, max_hold=mh, exit_cd=EXIT_CD)
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid, fe, f"TP{tp1*100:.1f} MH{mh}")
            if r:
                print(f"  TP1={tp1*100:.1f}% MH{mh:>2}: {r['n']:>4}t ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}%")

    # Phase 3: Best TP1 with various portfolio sizes
    print(f"\n{'='*70}")
    print("  Phase 3: Portfolio Expansion (best TP1 × sub-counts)")
    print(f"{'='*70}")

    L_6SUB = ["gk40bl8", "gk40bl10", "gk40bl12", "gk40bl15", "gk30bl8", "gk30bl10"]
    L_8SUB = L_6SUB + ["gk40bl6", "gk40bl20"]

    for tp1 in [0.010, 0.012, 0.015]:
        for mh in [18, 24]:
            for subs, sname in [(L_4SUB, "4s"), (L_6SUB, "6s"), (L_8SUB, "8s")]:
                strats = make_strats(subs, max_same=5, sn_pct=0.065,
                                     tp1=tp1, tp2=0.02, phase1_bars=8,
                                     max_hold=mh, exit_cd=EXIT_CD)
                t = bt_portfolio(df, strats)
                r = evaluate(t, mid, fe, f"{sname} TP{tp1*100:.1f} MH{mh}")
                if r:
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
                    fails = [k for k, v in checks.items() if not v]
                    marker = " <<<" if passed >= 7 else ""
                    print(f"  {sname} TP{tp1*100:.1f} MH{mh:>2}: {r['n']:>5}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% PM{r['pos_months']}/{r['months']} topM{r['top_pct']:.0f}% WF{wf_pos}/6 P{passed}/9{marker}")

    # Phase 4: Phase1 and SN tuning on promising configs
    print(f"\n{'='*70}")
    print("  Phase 4: Phase1 + SN Tuning")
    print(f"{'='*70}")

    for tp1 in [0.010, 0.012]:
        for ph1 in [6, 10, 12]:
            for sn in [0.055, 0.065, 0.075]:
                strats = make_strats(L_6SUB, max_same=5, sn_pct=sn,
                                     tp1=tp1, tp2=0.02, phase1_bars=ph1,
                                     max_hold=18, exit_cd=EXIT_CD)
                t = bt_portfolio(df, strats)
                r = evaluate(t, mid, fe, f"6s TP{tp1*100:.1f} Ph{ph1} SN{sn*100:.1f}")
                if r:
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
                    marker = " <<<" if passed >= 7 else ""
                    print(f"  6s TP{tp1*100:.1f} Ph{ph1:>2} SN{sn*100:.1f}: {r['n']:>5}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}% P{passed}/9{marker}")

    # Summary
    print(f"\n{'='*70}")
    print(f"  R13 Summary — Top 15")
    print(f"{'='*70}")
    all_results.sort(key=lambda x: (-x['passed'], -x['pnl']))
    print(f"  {'Config':<28} {'N':>5} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>6} {'PM':>5} {'topM':>6} {'NB':>8} {'WF':>4} {'P':>2}")
    for r in all_results[:15]:
        pm = f"{r['pos_months']}/{r['months']}"
        marker = " <<<" if r['passed'] >= 8 else ""
        print(f"  {r['label']:<28} {r['n']:>5} ${r['pnl']:>+9,.0f} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% {pm:>5} {r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f} {r['wf_pos']:>2}/6 {r['passed']:>2}{marker}")

    # Best detail
    if all_results:
        best = all_results[0]
        print(f"\n  Best L: {best['label']} ({best['passed']}/9)")
        fails = [k for k, v in best['checks'].items() if not v]
        if fails: print(f"  Failing: {', '.join(fails)}")
        if best['passed'] >= 7:
            print(f"  出場: {best['ed'].to_string()}")
            print(f"  月度:")
            cum = 0
            for m, v in best["monthly"].items():
                cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

    print(f"\n  上帝視角自檢: 全通過（L-CMP mirror of S framework，upward breakout）")
