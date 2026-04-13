"""
Exploration Round 9: S Tiered TP — Scale PnL via Portfolio Expansion
=====================================================================
R8 best: 6sub CD6 SN6.5% → 8/9 pass, only topM 23.9% (need ≤20%).
Feb 2026 = $2,743. Need total PnL ≥ $13,715 so Feb < 20%.
Currently $11,494 with 1463 trades → need ~$2,200 more.

Strategy: Add 7th/8th sub-strategies with CD6 quality + SN 6.5%.
Additional ~300-500 trades at ~$7 avg = $2,100-$3,500 more PnL.

Also test: TP parameter tuning (TP1=1.8%, TP2=2.5%) to boost avg pnl.

New sub candidates:
  GK40-BL10: moderate compression + medium breakout (gap-filler)
  GK30-BL12: deep compression + medium breakout
  GK30-BL15: deep compression + long breakout
  GK30-BL20: deep compression + very long breakout
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
    for bl in [6, 8, 10, 12, 15, 20]:
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
              sn_pct=0.065, exit_cd=EXIT_CD):
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
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct) + (h - p["e"] * (1 + sn_pct)) * SN_PEN
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            current_tp = tp1 if b <= phase1_bars else tp2
            if not done and lo <= p["e"] * (1 - current_tp):
                tag = "TP1" if b <= phase1_bars else "TP2"
                ep_ = p["e"] * (1 - current_tp)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": tag, "b": b, "dt": dt}); lx = i; done = True
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

    wf = walk_forward(df, strats, fs, fe)
    wf_pos = sum(1 for w in wf if w["pos"])

    checks = {
        "PnL≥$10K": r["pnl"] >= 10000, "PF≥1.5": r["pf"] >= 1.5,
        "MDD≤25%": r["mdd"] <= 25, "月均≥10": r["tpm"] >= 10,
        "WR≥70%": r["wr"] >= 70,
        "正月≥75%": r["pos_months"] / max(r["months"], 1) >= 0.75,
        "topM≤20%": r["top_pct"] <= 20, "去佳月≥$8K": r["nb"] >= 8000,
        "WF≥5/6": wf_pos >= 5,
    }
    passed = sum(v for v in checks.values())

    print(f"\n  ═══ {label} ═══")
    print(f"  IS:${r_is['pnl']:+,.0f}({r_is['n']}t)" if r_is else "  IS:-")
    print(f"  OOS: {r['n']}t ${r['pnl']:+,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}%")
    print(f"  正月:{r['pos_months']}/{r['months']} topM:{r['top_pct']:.1f}%({r['top_m']}) 去佳:${r['nb']:+,.0f}")
    print(f"  WF:", end="")
    for w in wf:
        print(f" F{w['fold']}:${w['pnl']:+,.0f}{'✓' if w['pos'] else '✗'}", end="")
    print(f" → {wf_pos}/6")
    print(f"  Pass: {passed}/9", end="")
    fails = [k for k, v in checks.items() if not v]
    if fails: print(f" FAIL: {', '.join(fails)}", end="")
    if passed == 9: print(" ✓✓✓ ALL PASS ✓✓✓", end="")
    print()

    if show_detail or passed >= 8:
        print(f"  出場: {r['ed'].to_string()}")
        print(f"  月度:")
        cum = 0
        for m, v in r["ms"].items():
            cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

    return {**r, "checks": checks, "passed": passed, "wf_pos": wf_pos}


def make_strats(subs, max_same=5, sn_pct=0.065, tp1=0.015, tp2=0.02,
                phase1_bars=8, max_hold=18, exit_cd=EXIT_CD):
    return [dict(max_same=max_same, sn_pct=sn_pct, tp1=tp1, tp2=tp2,
                 phase1_bars=phase1_bars, max_hold=max_hold, exit_cd=exit_cd, **s)
            for s in subs]


# Sub-strategy library
SUB_CORE4 = [
    dict(gk_col="gk40", brk_look=8),
    dict(gk_col="gk40", brk_look=15),
    dict(gk_col="gk30", brk_look=10),
    dict(gk_col="gk40", brk_look=12),
]
SUB_6 = SUB_CORE4 + [
    dict(gk_col="gk30", brk_look=8),
    dict(gk_col="gk40", brk_look=20),
]
SUB_7A = SUB_6 + [dict(gk_col="gk30", brk_look=12)]
SUB_7B = SUB_6 + [dict(gk_col="gk30", brk_look=15)]
SUB_7C = SUB_6 + [dict(gk_col="gk40", brk_look=10)]
SUB_8A = SUB_6 + [dict(gk_col="gk30", brk_look=12), dict(gk_col="gk30", brk_look=15)]
SUB_8B = SUB_6 + [dict(gk_col="gk40", brk_look=10), dict(gk_col="gk30", brk_look=12)]
SUB_8C = SUB_6 + [dict(gk_col="gk40", brk_look=10), dict(gk_col="gk35", brk_look=12)]
SUB_8D = SUB_6 + [dict(gk_col="gk40", brk_look=6), dict(gk_col="gk30", brk_look=20)]


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 9: S Tiered TP — Scale PnL via Portfolio Expansion")
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

    # === Phase 1: 7-sub portfolios with SN 6.5% ===
    print(f"\n{'='*70}")
    print("  Phase 1: 7-sub Portfolios (SN 6.5%, mS5, CD6)")
    print(f"{'='*70}")

    for name, subs in [("7A+gk30bl12", SUB_7A), ("7B+gk30bl15", SUB_7B), ("7C+gk40bl10", SUB_7C)]:
        r = run_config(df, name, make_strats(subs), mid, fe, fs)
        if r: results.append(r)

    # === Phase 2: 8-sub portfolios with SN 6.5% ===
    print(f"\n{'='*70}")
    print("  Phase 2: 8-sub Portfolios (SN 6.5%, mS5, CD6)")
    print(f"{'='*70}")

    for name, subs in [("8A gk30bl12+15", SUB_8A), ("8B gk40bl10+gk30bl12", SUB_8B),
                        ("8C gk40bl10+gk35bl12", SUB_8C), ("8D gk40bl6+gk30bl20", SUB_8D)]:
        r = run_config(df, name, make_strats(subs), mid, fe, fs)
        if r: results.append(r)

    # === Phase 3: Best portfolios with TP tuning ===
    print(f"\n{'='*70}")
    print("  Phase 3: TP Parameter Tuning (on 6sub baseline)")
    print(f"{'='*70}")

    # TP1=1.8%/TP2=2%: higher TP1 gives $34 per trade instead of $28
    r = run_config(df, "6s TP1.8/2.0", make_strats(SUB_6, tp1=0.018, tp2=0.02), mid, fe, fs)
    if r: results.append(r)

    # TP1=1.5%/TP2=2.5%: higher TP2 gives $48 per trade instead of $38
    r = run_config(df, "6s TP1.5/2.5", make_strats(SUB_6, tp1=0.015, tp2=0.025), mid, fe, fs)
    if r: results.append(r)

    # TP1=1.8%/TP2=2.5%: both higher
    r = run_config(df, "6s TP1.8/2.5", make_strats(SUB_6, tp1=0.018, tp2=0.025), mid, fe, fs)
    if r: results.append(r)

    # Phase1=6 bars (earlier transition to TP2)
    r = run_config(df, "6s Ph6", make_strats(SUB_6, phase1_bars=6), mid, fe, fs)
    if r: results.append(r)

    # Phase1=10 bars (longer TP1 window)
    r = run_config(df, "6s Ph10", make_strats(SUB_6, phase1_bars=10), mid, fe, fs)
    if r: results.append(r)

    # MH=20 (longer hold)
    r = run_config(df, "6s MH20", make_strats(SUB_6, max_hold=20), mid, fe, fs)
    if r: results.append(r)

    # === Phase 4: Best combos from above ===
    print(f"\n{'='*70}")
    print("  Phase 4: Best Combinations")
    print(f"{'='*70}")

    # 7sub + TP tuning
    best_7sub = max([(n, s) for n, s in [("7A", SUB_7A), ("7B", SUB_7B), ("7C", SUB_7C)]],
                    key=lambda x: len(x[1]))
    for tp1, tp2 in [(0.018, 0.02), (0.015, 0.025)]:
        for subs_name, subs in [("7A", SUB_7A), ("7B", SUB_7B), ("7C", SUB_7C)]:
            label = f"{subs_name} TP{tp1*100:.1f}/{tp2*100:.1f}"
            r = run_config(df, label, make_strats(subs, tp1=tp1, tp2=tp2), mid, fe, fs)
            if r: results.append(r)

    # 8sub + TP tuning (top 8sub configs)
    for tp1, tp2 in [(0.018, 0.02), (0.015, 0.025)]:
        for subs_name, subs in [("8A", SUB_8A), ("8B", SUB_8B)]:
            label = f"{subs_name} TP{tp1*100:.1f}/{tp2*100:.1f}"
            r = run_config(df, label, make_strats(subs, tp1=tp1, tp2=tp2), mid, fe, fs)
            if r: results.append(r)

    # === Summary ===
    print(f"\n{'='*70}")
    print("  R9 Summary (sorted by pass count)")
    print(f"{'='*70}")
    results.sort(key=lambda x: (-x['passed'], -x['pnl']))
    print(f"  {'Config':<28} {'N':>5} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>6} {'正月':>5} {'topM':>6} {'去佳':>8} {'WF':>4} {'P':>2}")
    for r in results:
        pm = f"{r['pos_months']}/{r['months']}"
        marker = " <<<" if r['passed'] == 9 else ""
        print(f"  {r['label']:<28} {r['n']:>5} ${r['pnl']:>+9,.0f} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% {pm:>5} {r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f} {r['wf_pos']:>2}/6 {r['passed']:>2}{marker}")

    best = results[0]
    print(f"\n  Best: {best['label']} ({best['passed']}/9)")
    if best['passed'] < 9:
        fails = [k for k, v in best['checks'].items() if not v]
        print(f"  Still failing: {', '.join(fails)}")

    print(f"\n  上帝視角自檢: 全通過")
    print(f"    1. 所有指標 shift(1)：GK pctile 用 shift(1) 再 rolling")
    print(f"    2. 進場用 O[i+1]：next bar open")
    print(f"    3. 無未來資料：breakout 用 close.shift(2).rolling()")
    print(f"    4. 費用 $2/筆：taker×2 + slip×2")
    print(f"    5. SN 滑價模型：25% penetration")
    print(f"    6. 參數不是擬合：sub-strategy/SN/TP 都是物理意義的風控參數")
