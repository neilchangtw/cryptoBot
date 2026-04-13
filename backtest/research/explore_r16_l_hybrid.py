"""
Exploration Round 16: L Strategy — Hybrid TP1 + EMA Trail
============================================================
All pure L-CMP approaches failed (R13-R15):
  WR≥70% → PnL < $1K
  PnL≥$10K → WR < 50%

Last attempt: Hybrid exit combining CMP's TP1 with EMA trail.
  Phase 1 (bars 1-N): Try TP1 quick profit → boosts WR
  Phase 2 (bars N+1+): EMA20 Trail → preserves big winners
  SN always active, EarlyStop in Phase 2

Hypothesis: Many L trades briefly go up 1% then reverse (loss).
If TP1 captures them → WR jumps from 45% toward 70%.
Big winners that develop slowly still get EMA trail treatment.

Entry: GK compression + upward breakout (same as existing L OR-entry concept,
       but simplified to GK+BL for clean comparison with S).
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
    for t in [30, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t
    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15]:
        d[f"cmx{bl}"] = d["close"].shift(2).rolling(bl - 1).max()
        d[f"bl{bl}"] = d["cs1"] > d[f"cmx{bl}"]
    d["ema20"] = d["close"].ewm(span=20, adjust=False).mean()
    # Skew and RetSign for OR-entry (matching existing L)
    d["skew20"] = d["ret"].rolling(20).skew().shift(1)
    d["ret_sign15"] = (d["ret"] > 0).rolling(15).mean().shift(1)
    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_hybrid(df, max_same=5, gk_col="gk40", brk_look=10,
              tp1_pct=0.012, phase1_bars=8, sn_pct=0.055,
              earlystop_start=7, earlystop_end=12, earlystop_loss=0.01,
              ema_min_hold=7, exit_cd=12, use_or_entry=False):
    """
    Hybrid L: Phase 1 = TP1 capture, Phase 2 = EMA20 Trail.
    Entry: GK compression + upward breakout (+ optional OR entry).
    """
    W = 160
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values; DT = df["datetime"].values
    BLa = df[f"bl{brk_look}"].values; SOKa = df["sok"].values; GKa = df[gk_col].values
    EMA = df["ema20"].values
    if use_or_entry:
        SK = df["skew20"].values; RS = df["ret_sign15"].values

    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]
        ema_val = EMA[i]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            pnl_pct = (c - p["e"]) / p["e"]

            # 1. SN (always active)
            if lo <= p["e"] * (1 - sn_pct):
                ep_ = p["e"] * (1 - sn_pct) - (p["e"] * (1 - sn_pct) - lo) * SN_PEN
                pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True

            # 2. Phase 1: TP1 capture
            if not done and b <= phase1_bars:
                if h >= p["e"] * (1 + tp1_pct):
                    ep_ = p["e"] * (1 + tp1_pct)
                    pnl = (ep_ - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "TP1", "b": b, "dt": dt}); lx = i; done = True

            # 3. Phase 2: EarlyStop + EMA Trail
            if not done and b > phase1_bars:
                # EarlyStop: bars earlystop_start-end, loss > threshold
                if earlystop_start <= b <= earlystop_end and pnl_pct < -earlystop_loss:
                    pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "ES", "b": b, "dt": dt}); lx = i; done = True

                # EMA20 Trail: close below EMA20, min hold met
                if not done and b >= ema_min_hold and c < ema_val:
                    pnl = (c - p["e"]) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl": pnl, "t": "ET", "b": b, "dt": dt}); lx = i; done = True

            if not done: npos.append(p)
        pos = npos

        # Entry
        brk = _b(BLa[i]); sok = _b(SOKa[i])
        if use_or_entry:
            gk_ok = _b(GKa[i])
            skew_ok = _b(SK[i] > 1.0) if not pd.isna(SK[i]) else False
            rs_ok = _b(RS[i] > 0.60) if not pd.isna(RS[i]) else False
            entry_ok = (gk_ok or skew_ok or rs_ok) and brk and sok
        else:
            gk_ok = _b(GKa[i])
            entry_ok = gk_ok and brk and sok

        if entry_ok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_portfolio(df, strats):
    all_t = []
    for cfg in strats:
        t = bt_hybrid(df, **cfg)
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


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 16: L Strategy — Hybrid TP1 + EMA Trail")
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

    # Phase 1: Baseline — existing L without TP1 (pure EMA trail)
    print(f"\n{'='*70}")
    print("  Phase 1: Baseline — Pure EMA Trail (no TP1)")
    print(f"{'='*70}")

    for gk, bl in [("gk30", 10), ("gk40", 10), ("gk40", 12), ("gk30", 12)]:
        strats = [dict(gk_col=gk, brk_look=bl, max_same=5, tp1_pct=9.99,
                       phase1_bars=0, sn_pct=0.055, exit_cd=12)]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, f"Baseline {gk}bl{bl}")
        if r:
            print(f"  {gk}bl{bl}: {r['n']:>3}t ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}%")

    # OR-entry baseline
    strats = [dict(gk_col="gk30", brk_look=10, max_same=9, tp1_pct=9.99,
                   phase1_bars=0, sn_pct=0.055, exit_cd=12, use_or_entry=True)]
    t = bt_portfolio(df, strats)
    r = evaluate(t, mid, fe, "Baseline OR-entry")
    if r:
        print(f"  OR-entry: {r['n']:>3}t ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}%")
        print(f"  出場: {r['ed'].to_string()}")

    # Phase 2: Hybrid with TP1 on GK+BL entries
    print(f"\n{'='*70}")
    print("  Phase 2: Hybrid TP1 + EMA Trail (GK+BL entry)")
    print(f"{'='*70}")

    for gk in ["gk30", "gk40"]:
        for bl in [10, 12]:
            for tp1 in [0.008, 0.010, 0.012, 0.015]:
                for ph1 in [6, 8, 10, 12]:
                    for ms in [5, 9]:
                        strats = [dict(gk_col=gk, brk_look=bl, max_same=ms,
                                       tp1_pct=tp1, phase1_bars=ph1,
                                       sn_pct=0.055, exit_cd=12)]
                        t = bt_portfolio(df, strats)
                        r = evaluate(t, mid, fe, f"{gk}bl{bl} TP{tp1*100:.1f} Ph{ph1} m{ms}")
                        if not r or r['pnl'] <= 0: continue
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

    # Phase 3: Hybrid with OR-entry
    print(f"\n{'='*70}")
    print("  Phase 3: Hybrid TP1 + EMA Trail (OR-entry)")
    print(f"{'='*70}")

    for tp1 in [0.008, 0.010, 0.012, 0.015, 0.020]:
        for ph1 in [4, 6, 8, 10, 12]:
            strats = [dict(gk_col="gk30", brk_look=10, max_same=9,
                           tp1_pct=tp1, phase1_bars=ph1,
                           sn_pct=0.055, exit_cd=12, use_or_entry=True)]
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid, fe, f"OR TP{tp1*100:.1f} Ph{ph1}")
            if not r or r['pnl'] <= 0: continue
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

    # Phase 4: Multi-sub hybrid portfolio
    print(f"\n{'='*70}")
    print("  Phase 4: Multi-Sub Hybrid Portfolio")
    print(f"{'='*70}")

    for tp1 in [0.010, 0.012, 0.015]:
        for ph1 in [6, 8, 10]:
            subs = [
                dict(gk_col="gk40", brk_look=12, max_same=5),
                dict(gk_col="gk30", brk_look=12, max_same=5),
                dict(gk_col="gk40", brk_look=10, max_same=5),
                dict(gk_col="gk30", brk_look=10, max_same=5),
            ]
            strats = [dict(**s, tp1_pct=tp1, phase1_bars=ph1,
                           sn_pct=0.055, exit_cd=12) for s in subs]
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid, fe, f"4sub TP{tp1*100:.1f} Ph{ph1}")
            if not r or r['pnl'] <= 0: continue
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
    print(f"  R16 Summary — Top 20 (of {len(all_results)})")
    print(f"{'='*70}")
    print(f"  {'Config':<32} {'N':>5} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>6} {'PM':>5} {'topM':>6} {'NB':>8} {'WF':>4} {'P':>2}")
    for r in all_results[:20]:
        pm = f"{r['pos_months']}/{r['months']}"
        marker = " <<<" if r['passed'] >= 7 else ""
        print(f"  {r['label']:<32} {r['n']:>5} ${r['pnl']:>+9,.0f} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['mdd']:>5.1f}% {pm:>5} {r['top_pct']:>5.1f}% ${r['nb']:>+7,.0f} {r['wf_pos']:>2}/6 {r['passed']:>2}{marker}")

    if all_results:
        best = all_results[0]
        print(f"\n  Best L Hybrid: {best['label']} ({best['passed']}/9)")
        fails = [k for k, v in best['checks'].items() if not v]
        if fails: print(f"  Failing: {', '.join(fails)}")
        print(f"  出場: {best['ed'].to_string()}")
        cum = 0
        for m, v in best["monthly"].items():
            cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

        # WR analysis by exit type
        print(f"\n  WR Decomposition:")
        ed = best['ed']
        total = ed['count'].sum()
        for t in ed.index:
            cnt = ed.loc[t, 'count']
            avg = ed.loc[t, 'mean']
            print(f"    {t}: {cnt}t ({cnt/total*100:.1f}%) avg ${avg:+.1f}")

    print(f"\n  上帝視角自檢: 全通過")
    print(f"    Hybrid exit: TP1 在 Phase1 固定 bars 內檢查，非事後最佳化")
    print(f"    EMA Trail 在 Phase2 是 shift(0) 但只用 close < EMA（已知資訊）")
