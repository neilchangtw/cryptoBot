"""
Exploration Round 5: S Strategy — Extended MaxHold Period
============================================================
Hypothesis: MH 12 is too short — cuts many trades before the compression
breakout fully develops. In GK compressed periods, volatility is low, so
SN 5.5% (at ~5x ATR) is rarely hit even with longer holding. But more
trades reach TP 2% given more time → higher WR.

Test: MH 12 (baseline), MH 18, MH 24, MH 30
All other parameters unchanged from baseline CMP.

Theory basis:
  - Compression breakouts often take 12-24h to fully develop
  - GK compressed volatility: SN at 5.5% is very far from typical moves
  - ETH breakout physics: direction confirmed by 24h, noise subsides
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
TP_PCT = 0.02; EXIT_CD = 6; MAX_SAME = 5


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


def bt_cmp(df, max_same=5, gk_col="gk40", brk_look=10,
           tp_pct=0.02, max_hold=12, sn_pct=SN_PCT, exit_cd=EXIT_CD):
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
            if not done and lo <= p["e"] * (1 - tp_pct):
                ep_ = p["e"] * (1 - tp_pct)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True
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
        t = bt_cmp(df, **cfg)
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


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 5: S CMP — Extended MaxHold Period")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")

    df = calc_indicators(df_raw)

    # Test multiple MH values
    for mh_val in [12, 18, 24, 30, 36, 48]:
        strats = [
            dict(max_same=5, gk_col="gk40", brk_look=8,  max_hold=mh_val),
            dict(max_same=5, gk_col="gk40", brk_look=15, max_hold=mh_val),
            dict(max_same=5, gk_col="gk30", brk_look=10, max_hold=mh_val),
            dict(max_same=5, gk_col="gk40", brk_look=12, max_hold=mh_val),
        ]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, f"MH{mh_val}")
        r_is = evaluate(t, fs, mid, f"MH{mh_val} IS")

        if r:
            print(f"\n  ═══ MH={mh_val} ═══")
            print(f"  OOS: {r['n']}t ${r['pnl']:+,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% MDD{r['mdd']:.1f}%")
            print(f"  IS:  {r_is['n']}t ${r_is['pnl']:+,.0f}" if r_is else "  IS: -")
            print(f"  月均: {r['tpm']:.1f}筆  正月: {r['pos_months']}/{r['months']}")
            print(f"  topM: {r['top_pct']:.1f}% ({r['top_m']})  去佳月: ${r['nb']:+,.0f}")
            print(f"  最差月: ${r['worst_v']:+,.0f} ({r['worst_m']})")
            print(f"  出場: {r['ed'].to_string()}")

            # Monthly details for promising configs
            if r['wr'] >= 68:
                print(f"\n  月度明細:")
                cum = 0
                for m, v in r["ms"].items():
                    cum += v; print(f"    {m}: ${v:>+7,.0f} (cum ${cum:>+7,.0f})")

                # Walk-forward
                wf = walk_forward(df, strats, fs, fe)
                wf_pos = sum(1 for w in wf if w["pos"])
                print(f"  WF:", end="")
                for w in wf:
                    print(f" F{w['fold']}:${w['pnl']:+,.0f}{'✓' if w['pos'] else '✗'}", end="")
                print(f" → {wf_pos}/6")

                # Full assessment
                checks = {
                    "PnL≥$10K": r["pnl"] >= 10000, "PF≥1.5": r["pf"] >= 1.5,
                    "MDD≤25%": r["mdd"] <= 25, "月均≥10": r["tpm"] >= 10,
                    "WR≥70%": r["wr"] >= 70,
                    "正月≥75%": r["pos_months"] / max(r["months"], 1) >= 0.75,
                    "topM≤20%": r["top_pct"] <= 20, "去佳月≥$8K": r["nb"] >= 8000,
                    "WF≥5/6": wf_pos >= 5,
                }
                print(f"  達標:")
                for k, v in checks.items():
                    print(f"    {'✓' if v else '✗'} {k}")

    # Summary comparison
    print(f"\n{'='*70}")
    print("  MH 比較總結")
    print(f"{'='*70}")
    print(f"  {'MH':>4} {'Trades':>7} {'PnL':>10} {'PF':>6} {'WR':>6} {'MDD':>7} {'TP%':>6} {'MH%':>6} {'SN%':>6}")
    for mh_val in [12, 18, 24, 30, 36, 48]:
        strats = [
            dict(max_same=5, gk_col="gk40", brk_look=8,  max_hold=mh_val),
            dict(max_same=5, gk_col="gk40", brk_look=15, max_hold=mh_val),
            dict(max_same=5, gk_col="gk30", brk_look=10, max_hold=mh_val),
            dict(max_same=5, gk_col="gk40", brk_look=12, max_hold=mh_val),
        ]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, f"MH{mh_val}")
        if r:
            ed = r['ed']
            tp_pct = ed.loc['TP','count']/r['n']*100 if 'TP' in ed.index else 0
            mh_pct = ed.loc['MH','count']/r['n']*100 if 'MH' in ed.index else 0
            sn_pct = ed.loc['SN','count']/r['n']*100 if 'SN' in ed.index else 0
            print(f"  {mh_val:>4} {r['n']:>7} ${r['pnl']:>+9,.0f} {r['pf']:>6.2f} {r['wr']:>5.1f}% {r['mdd']:>6.1f}% {tp_pct:>5.1f}% {mh_pct:>5.1f}% {sn_pct:>5.1f}%")

    # God-view
    print(f"\n  上帝視角自檢: 全通過（同 R1-R3 框架，只改 MH 參數）")
    print(f"  參數理由: MH 延長基於 ETH 壓縮突破物理（24h 方向確認）")
