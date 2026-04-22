"""
V22 共用模組 — 資料 + 模擬器 + 統計
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])
df = df.sort_values("datetime").reset_index(drop=True)

H = df["high"].values.astype(float)
L = df["low"].values.astype(float)
C = df["close"].values.astype(float)
O = df["open"].values.astype(float)
DT = df["datetime"].values
TOTAL = len(df)
IS_END = TOTAL // 2

FEE = 4.0
NOTIONAL = 4000.0

def simulate(signal_arr, side, start, end, tp_pct=0.035, sl_pct=0.035,
             maxhold=12, cooldown=6):
    trades = []
    in_pos = False
    last_exit = -999
    entry_price = 0.0
    entry_bar = -1
    for i in range(start, min(end, TOTAL - 1)):
        if in_pos:
            held = i - entry_bar
            ep = entry_price
            if side == "L":
                sl_level = ep * (1 - sl_pct)
                tp_level = ep * (1 + tp_pct)
                if L[i] <= sl_level:
                    ex_p = sl_level - (sl_level - L[i]) * 0.25
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ex_p-ep)/ep - FEE, "reason": "SL",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif H[i] >= tp_level:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(tp_level-ep)/ep - FEE, "reason": "TP",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif held >= maxhold:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(C[i]-ep)/ep - FEE, "reason": "MH",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
            else:
                sl_level = ep * (1 + sl_pct)
                tp_level = ep * (1 - tp_pct)
                if H[i] >= sl_level:
                    ex_p = sl_level + (H[i] - sl_level) * 0.25
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-ex_p)/ep - FEE, "reason": "SL",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif L[i] <= tp_level:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-tp_level)/ep - FEE, "reason": "TP",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
                elif held >= maxhold:
                    trades.append({"entry_bar": entry_bar, "exit_bar": i,
                                   "pnl": NOTIONAL*(ep-C[i])/ep - FEE, "reason": "MH",
                                   "entry_month": pd.Timestamp(DT[entry_bar]).strftime("%Y-%m")})
                    in_pos = False; last_exit = i
        if in_pos: continue
        if i - last_exit < cooldown: continue
        if signal_arr[i] and i + 1 < TOTAL:
            entry_price = O[i+1]; entry_bar = i; in_pos = True
    return trades

def stats(trades):
    if not trades: return dict(n=0, pnl=0, wr=0, pf=0, mdd=0, worst=0, pos=0, total=0)
    pnls = np.array([t["pnl"] for t in trades])
    total = pnls.sum()
    wr = (pnls>0).mean()*100
    gw = pnls[pnls>0].sum(); gl = abs(pnls[pnls<0].sum())
    pf = gw/gl if gl>0 else np.inf
    eq = np.cumsum(pnls); peak = np.maximum.accumulate(eq)
    mdd = float((peak-eq).max()) if len(eq) else 0
    monthly = {}
    for t in trades:
        monthly[t["entry_month"]] = monthly.get(t["entry_month"], 0) + t["pnl"]
    worst = min(monthly.values()) if monthly else 0
    pos = sum(1 for v in monthly.values() if v>0)
    return dict(n=len(pnls), pnl=float(total), wr=float(wr), pf=float(pf),
                mdd=mdd, worst=float(worst), pos=pos, total=len(monthly))

def grid_evaluate(signals_dict, tp_grid, mh_grid, sl_pct=0.035, cd=6, min_n=10):
    """For each (signal_name, side) + tp × mh, compute IS stats, return sorted candidates."""
    best = []
    for sig_name, (sig_arr, side) in signals_dict.items():
        for tp in tp_grid:
            for mh in mh_grid:
                t_is = simulate(sig_arr, side, 0, IS_END, tp, sl_pct, mh, cd)
                s = stats(t_is)
                if s["n"] >= min_n and s["pnl"] > 0:
                    best.append({"sig": sig_name, "side": side, "tp": tp, "mh": mh,
                                 "is": s})
    return sorted(best, key=lambda x: -x["is"]["pnl"])

def confirm_oos(candidates, signals_dict, sl_pct=0.035, cd=6, top_n=10):
    print(f"{'Signal':<28} {'Side':<4} {'TP':>6} {'MH':>4} | {'IS $':>7} {'IS n':>4} {'IS WR':>5} | {'OOS $':>7} {'OOS n':>5} {'OOS WR':>6} {'OOS MDD':>7}")
    print("-"*125)
    results = []
    for r in candidates[:top_n]:
        sig_arr, side = signals_dict[r["sig"]]
        t_oos = simulate(sig_arr, side, IS_END, TOTAL, r["tp"], sl_pct, r["mh"], cd)
        s_oos = stats(t_oos)
        tag = ""
        if s_oos["pnl"] > 0:
            tag = " OK"
        print(f"{r['sig']:<28} {r['side']:<4} {r['tp']:>6.3f} {r['mh']:>4} | "
              f"${r['is']['pnl']:>6.0f} {r['is']['n']:>4} {r['is']['wr']:>4.1f}% | "
              f"${s_oos['pnl']:>6.0f} {s_oos['n']:>5} {s_oos['wr']:>5.1f}% ${s_oos['mdd']:>6.0f}{tag}")
        results.append((r, s_oos))
    return results
