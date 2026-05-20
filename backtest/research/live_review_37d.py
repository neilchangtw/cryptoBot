"""
模擬盤 37 天複盤分析（2026-04-14 ~ 2026-05-20）

4 個面向：
  1. 績效 vs 同期回測（V14+R+V25-D 完整邏輯）
  2. 10 筆逐筆解剖
  3. Regime / 訊號分佈
  4. 風險 / 執行品質

直接 import strategy.py 確保指標與線上一致。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import strategy as S  # noqa: E402

DATA = ROOT / "data"
START = "2026-04-14 00:00:00"  # bot startup window (UTC+8)
END = "2026-05-20 23:59:59"


# ─────────────────────────────────────────────────────────────────
# 1. Load
# ─────────────────────────────────────────────────────────────────
raw = pd.read_csv(DATA / "ETHUSDT_1h_latest730d.csv", parse_dates=["datetime"])
ind = S.compute_indicators(raw).reset_index(drop=True)

live_trades = pd.read_csv(DATA / "trades.csv")
live_bars = pd.read_csv(DATA / "bar_snapshots.csv", parse_dates=["bar_time_utc8"])
live_lc = pd.read_csv(DATA / "position_lifecycle.csv", parse_dates=["bar_time_utc8"])
live_daily = pd.read_csv(DATA / "daily_summary.csv")


# ─────────────────────────────────────────────────────────────────
# 2. V14+R+V25-D simulator（與 strategy.py 對齊）
# ─────────────────────────────────────────────────────────────────
def simulate(ind: pd.DataFrame, start_dt: str, end_dt: str):
    o = ind["open"].values
    h = ind["high"].values
    l = ind["low"].values
    c = ind["close"].values
    pL = ind["gk_pctile"].values
    pS = ind["gk_pctile_s"].values
    brkL = ind["breakout_long"].values
    brkS = ind["breakout_short"].values
    ok_l = ind["session_ok_l"].values
    ok_s = ind["session_ok_s"].values
    blk_l = ind["regime_block_l"].values
    blk_s = ind["regime_block_s"].values
    slope = ind["sma_slope"].values
    dt = ind["datetime"]

    start_bar = int((dt >= start_dt).idxmax())
    end_bar = int((dt > end_dt).idxmax()) if (dt > end_dt).any() else len(ind)

    months = (dt.dt.year * 100 + dt.dt.month).values
    days = (dt.dt.year * 10000 + dt.dt.month * 100 + dt.dt.day).values

    L_TP_BY = S.L_TP_BY_REGIME
    L_MH_BY = S.L_MH_BY_REGIME
    S_MH_BY = S.S_MH_BY_REGIME

    def rg(i):
        return S.classify_regime(slope[i])

    def tp_l(r):
        return L_TP_BY.get(r, S.L_TP_PCT)

    def mh_l(r):
        return L_MH_BY.get(r, S.L_MAX_HOLD)

    def mh_s(r):
        return S_MH_BY.get(r, S.S_MAX_HOLD)

    trades = []

    lp = dict(active=False)
    sp = dict(active=False)
    l_last = -999
    s_last = -999
    cur_m = -1
    l_me = s_me = 0
    l_mp = s_mp = 0.0
    cur_d = -1
    d_pnl = 0.0
    consec = 0
    consec_end = -999

    for i in range(start_bar, end_bar):
        oi, hi, li, ci = o[i], h[i], l[i], c[i]
        mk, dk = months[i], days[i]
        if mk != cur_m:
            cur_m = mk
            l_me = s_me = 0
            l_mp = s_mp = 0.0
        if dk != cur_d:
            cur_d = dk
            d_pnl = 0.0

        # ── L exit ──
        if lp["active"]:
            lp["held"] += 1
            ep = lp["entry"]
            bh = lp["held"]
            bmfe = (hi - ep) / ep
            bmae = (li - ep) / ep
            if bmfe > lp["mfe"]:
                lp["mfe"] = bmfe
            if bmae < lp["mae"]:
                lp["mae"] = bmae
            ex_p = 0.0
            ex_r = ""
            sn_lv = ep * (1 - S.L_SAFENET_PCT)
            if li <= sn_lv:
                # 25% slip 穿透模型
                ex_p = sn_lv - (sn_lv - li) * 0.25
                ex_r = "SafeNet"
            elif hi >= ep * (1 + lp["tp"]):
                ex_p = ep * (1 + lp["tp"])
                ex_r = "TP"
            else:
                cpnl = (ci - ep) / ep
                if lp["mfe"] >= S.L_MFE_ACT and (lp["mfe"] - cpnl) >= S.L_MFE_TRAIL_DD and bh >= S.L_MFE_MIN_BAR:
                    ex_p = ci
                    ex_r = "MFE-trail"
                else:
                    if bh == S.L_COND_CHECK_BAR and cpnl <= S.L_COND_EXIT_THRESH:
                        lp["reduced"] = True
                    mh_eff = S.L_COND_REDUCED_MH if lp["reduced"] else lp["mh"]
                    if not lp["ext"]:
                        if bh >= mh_eff:
                            if cpnl > 0:
                                lp["ext"] = True
                                lp["ext_bars"] = 0
                            else:
                                ex_p = ci
                                ex_r = "MaxHold"
                    else:
                        lp["ext_bars"] += 1
                        if li <= ep:
                            ex_p = ep
                            ex_r = "BE"
                        elif lp["ext_bars"] >= S.L_EXT_BARS:
                            ex_p = ci
                            ex_r = "MH-ext"
            if ex_p > 0:
                pnl_pct = (ex_p - ep) / ep
                pnl = pnl_pct * S.NOTIONAL - S.FEE
                trades.append(dict(
                    side="L",
                    entry_bar=lp["bar"],
                    exit_bar=i,
                    entry_dt=str(dt.iloc[lp["bar"]]),
                    exit_dt=str(dt.iloc[i]),
                    entry_price=round(ep, 2),
                    exit_price=round(ex_p, 2),
                    pnl=round(pnl, 2),
                    reason=ex_r,
                    bars_held=bh,
                    mfe_pct=round(lp["mfe"] * 100, 3),
                    mae_pct=round(lp["mae"] * 100, 3),
                    regime=lp["rg"],
                    gk_pctile=round(lp["gk"], 2),
                ))
                lp = dict(active=False)
                l_last = i
                l_mp += pnl
                d_pnl += pnl
                if pnl < 0:
                    consec += 1
                else:
                    consec = 0
                if consec >= S.CONSEC_LOSS_PAUSE:
                    consec_end = i + S.CONSEC_LOSS_COOLDOWN

        # ── S exit ──
        if sp["active"]:
            sp["held"] += 1
            ep = sp["entry"]
            bh = sp["held"]
            bmfe = (ep - li) / ep
            bmae = (ep - hi) / ep
            if bmfe > sp["mfe"]:
                sp["mfe"] = bmfe
            if bmae < sp["mae"]:
                sp["mae"] = bmae
            ex_p = 0.0
            ex_r = ""
            sn_lv = ep * (1 + S.S_SAFENET_PCT)
            if hi >= sn_lv:
                ex_p = sn_lv + (hi - sn_lv) * 0.25
                ex_r = "SafeNet"
            elif li <= ep * (1 - S.S_TP_PCT):
                ex_p = ep * (1 - S.S_TP_PCT)
                ex_r = "TP"
            else:
                cpnl = (ep - ci) / ep
                if not sp["ext"]:
                    if bh >= sp["mh"]:
                        if cpnl > 0:
                            sp["ext"] = True
                            sp["ext_bars"] = 0
                        else:
                            ex_p = ci
                            ex_r = "MaxHold"
                else:
                    sp["ext_bars"] += 1
                    if hi >= ep:
                        ex_p = ep
                        ex_r = "BE"
                    elif sp["ext_bars"] >= S.S_EXT_BARS:
                        ex_p = ci
                        ex_r = "MH-ext"
            if ex_p > 0:
                pnl_pct = (ep - ex_p) / ep
                pnl = pnl_pct * S.NOTIONAL - S.FEE
                trades.append(dict(
                    side="S",
                    entry_bar=sp["bar"],
                    exit_bar=i,
                    entry_dt=str(dt.iloc[sp["bar"]]),
                    exit_dt=str(dt.iloc[i]),
                    entry_price=round(ep, 2),
                    exit_price=round(ex_p, 2),
                    pnl=round(pnl, 2),
                    reason=ex_r,
                    bars_held=bh,
                    mfe_pct=round(sp["mfe"] * 100, 3),
                    mae_pct=round(sp["mae"] * 100, 3),
                    regime=sp["rg"],
                    gk_pctile=round(sp["gk"], 2),
                ))
                sp = dict(active=False)
                s_last = i
                s_mp += pnl
                d_pnl += pnl
                if pnl < 0:
                    consec += 1
                else:
                    consec = 0
                if consec >= S.CONSEC_LOSS_PAUSE:
                    consec_end = i + S.CONSEC_LOSS_COOLDOWN

        # ── entry checks ──
        l_cb = d_pnl <= S.DAILY_LOSS_LIMIT or l_mp <= S.L_MONTHLY_LOSS_CAP or i < consec_end
        s_cb = d_pnl <= S.DAILY_LOSS_LIMIT or s_mp <= S.S_MONTHLY_LOSS_CAP or i < consec_end

        # L entry
        if (not lp["active"] and not l_cb
                and (i - l_last) >= S.L_EXIT_CD
                and l_me < S.L_MONTHLY_ENTRY_CAP
                and not np.isnan(pL[i]) and pL[i] < S.L_GK_THRESH
                and brkL[i] and ok_l[i] and not blk_l[i]):
            r = rg(i)
            lp = dict(active=True, entry=ci, bar=i, held=0, mfe=0.0, mae=0.0,
                      reduced=False, ext=False, ext_bars=0, gk=pL[i], rg=r,
                      tp=tp_l(r), mh=mh_l(r))
            l_me += 1

        # S entry
        if (not sp["active"] and not s_cb
                and (i - s_last) >= S.S_EXIT_CD
                and s_me < S.S_MONTHLY_ENTRY_CAP
                and not np.isnan(pS[i]) and pS[i] < S.S_GK_THRESH
                and brkS[i] and ok_s[i] and not blk_s[i]):
            r = rg(i)
            sp = dict(active=True, entry=ci, bar=i, held=0, mfe=0.0, mae=0.0,
                      ext=False, ext_bars=0, gk=pS[i], rg=r,
                      tp=S.S_TP_PCT, mh=mh_s(r))
            s_me += 1

    return pd.DataFrame(trades)


bt = simulate(ind, START, END)

# ─────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────
sep = "=" * 72


def section(title):
    print(f"\n{sep}\n{title}\n{sep}")


# ============================ SECTION 1 ============================
section("§1. 績效 vs 同期回測（V14+R+V25-D）")

lt = live_trades.copy()
lt["entry_dt"] = pd.to_datetime(lt["entry_time_utc8"])
lt["pnl"] = lt["net_pnl_usd"]
lt = lt.sort_values("entry_dt").reset_index(drop=True)

bt = bt.sort_values("entry_dt").reset_index(drop=True)

print(f"{'':12} {'Live':>14} {'Backtest':>14}  {'Diff':>10}")
print(f"{'trades':12} {len(lt):>14} {len(bt):>14}  {len(bt) - len(lt):>+10}")
print(f"{'PnL':12} {lt['pnl'].sum():>14.2f} {bt['pnl'].sum():>14.2f}  {bt['pnl'].sum() - lt['pnl'].sum():>+10.2f}")
print(f"{'WR%':12} {(lt['pnl'] > 0).mean() * 100:>14.1f} {(bt['pnl'] > 0).mean() * 100:>14.1f}")
print(f"{'avg PnL':12} {lt['pnl'].mean():>14.2f} {bt['pnl'].mean():>14.2f}")

print("\n逐筆對齊（按進場時間配對）：")
print(f"{'#':>3} {'side':>5} {'live_entry':>20} {'bt_entry':>20} {'live_pnl':>10} {'bt_pnl':>10} {'live_exit':>10} {'bt_exit':>10}")
# 雙向遍歷
i = j = 0
while i < len(lt) or j < len(bt):
    L = lt.iloc[i] if i < len(lt) else None
    B = bt.iloc[j] if j < len(bt) else None
    if L is not None and B is not None:
        # 容差 = 同一根 bar（1h）
        if abs((L["entry_dt"] - pd.Timestamp(B["entry_dt"])).total_seconds()) <= 3600:
            print(f"{i+1:>3} {L['sub_strategy']:>5} {str(L['entry_dt']):>20} {B['entry_dt']:>20} {L['pnl']:>10.2f} {B['pnl']:>10.2f} {L['exit_type']:>10} {B['reason']:>10}")
            i += 1
            j += 1
        elif L["entry_dt"] < pd.Timestamp(B["entry_dt"]):
            print(f"{i+1:>3} {L['sub_strategy']:>5} {str(L['entry_dt']):>20} {'(missing)':>20} {L['pnl']:>10.2f} {'-':>10} {L['exit_type']:>10} {'-':>10}")
            i += 1
        else:
            print(f"{'+':>3} {B['side']:>5} {'(missing)':>20} {B['entry_dt']:>20} {'-':>10} {B['pnl']:>10.2f} {'-':>10} {B['reason']:>10}")
            j += 1
    elif L is not None:
        print(f"{i+1:>3} {L['sub_strategy']:>5} {str(L['entry_dt']):>20} {'(missing)':>20} {L['pnl']:>10.2f} {'-':>10} {L['exit_type']:>10} {'-':>10}")
        i += 1
    else:
        print(f"{'+':>3} {B['side']:>5} {'(missing)':>20} {B['entry_dt']:>20} {'-':>10} {B['pnl']:>10.2f} {'-':>10} {B['reason']:>10}")
        j += 1


# ============================ SECTION 2 ============================
section("§2. 10 筆逐筆解剖")

for _, t in lt.iterrows():
    sub = live_lc[live_lc["trade_id"] == t["trade_id"]]
    print(f"\n#{int(t['trade_number'])} [{t['sub_strategy']}] {t['entry_time_utc8']} → {t['exit_time_utc8']}  ({t['exit_type']})")
    print(f"  進場條件：GK_pctile={t['gk_pctile_at_entry']:.1f}  breakout_strength={t['breakout_strength_pct']:+.2f}%  regime={t['entry_regime']}")
    print(f"  進場價/出場價：{t['entry_price']:.2f} → {t['exit_price']:.2f}  ({t['hold_hours']}h, {t['hold_bars']} bars)")
    print(f"  MAE/MFE：{t['max_adverse_excursion_pct']:+.2f}% (${t['max_adverse_excursion_usd']:+.2f}) / {t['max_favorable_excursion_pct']:+.2f}% (${t['max_favorable_excursion_usd']:+.2f})")
    if t['sub_strategy'] == 'L':
        sn_dist = 100 * (-0.035 - t['max_adverse_excursion_pct'] / 100) * 100
        tp_dist = 100 * (0.035 - t['max_favorable_excursion_pct'] / 100) * 100
    else:
        sn_dist = 100 * (0.04 - (-t['max_adverse_excursion_pct'] / 100)) * 100
        tp_dist = 100 * (0.02 - t['max_favorable_excursion_pct'] / 100) * 100
    print(f"  距 SafeNet：{abs(sn_dist):.1f} bp   距 TP：{abs(tp_dist):.1f} bp")
    print(f"  PnL：${t['net_pnl_usd']:+.2f} ({t['net_pnl_pct']:+.2f}%)  commission ${t['commission_usd']:.2f}")


# ============================ SECTION 3 ============================
section("§3. Regime / 訊號分佈")

bm = live_bars[(live_bars["bar_time_utc8"] >= START) & (live_bars["bar_time_utc8"] <= END)].copy()
total = len(bm)
print(f"分析窗口：{total} bars ({total / 24:.1f} 天)")


def rg_class(s):
    if pd.isna(s):
        return "NA"
    if s > 0.045:
        return "UP"
    if abs(s) < 0.010:
        return "SIDE"
    if s < -0.010:
        return "DOWN"
    return "MILD_UP"


bm["regime"] = bm["sma_slope"].apply(rg_class)
rg_dist = bm["regime"].value_counts()
print("\nRegime 分佈：")
for r, n in rg_dist.items():
    print(f"  {r:>10}: {n:>4} bars ({n / total * 100:5.1f}%)")

# breakout 出現率
print("\nBreakout 出現率（按 regime）：")
print(f"{'regime':>10} {'bars':>6} {'brk_L':>6} {'brk_S':>6} {'brk_L%':>8} {'brk_S%':>8}")
for r in ["UP", "MILD_UP", "SIDE", "DOWN"]:
    sub = bm[bm["regime"] == r]
    if len(sub) == 0:
        continue
    bL = int(sub["breakout_long"].fillna(False).astype(bool).sum())
    bS = int(sub["breakout_short"].fillna(False).astype(bool).sum())
    print(f"{r:>10} {len(sub):>6} {bL:>6} {bS:>6} {bL / len(sub) * 100:>7.2f}% {bS / len(sub) * 100:>7.2f}%")

# 信號漏接：理論能 fire vs 實際 fire
# 理論 fire = GK_pctile<25 + breakout_L + session_ok_l + !regime_block_l
bm["gk_pctile"] = pd.to_numeric(bm["gk_pctile"], errors="coerce")
bm["gk_pctile_s"] = pd.to_numeric(bm["gk_pctile_s"], errors="coerce")

candL_raw = (bm["gk_pctile"] < 25) & bm["breakout_long"].fillna(False) & bm["session_ok_l"].fillna(False)
candS_raw = (bm["gk_pctile_s"] < 35) & bm["breakout_short"].fillna(False) & bm["session_ok_s"].fillna(False)
candL_post = candL_raw & ~bm["regime_block_l"].fillna(False)
candS_post = candS_raw & ~bm["regime_block_s"].fillna(False)
fireL_actual = bm["long_signal"].astype(str).ne("HOLD")
fireS_actual = bm["short_signals"].astype(str).ne("HOLD")

print(f"\nL 候選 bar：raw(GK+brk+session)={candL_raw.sum()} → 過 regime gate {candL_post.sum()} → 實際進場 {fireL_actual.sum()}")
print(f"  R gate 擋下：{candL_raw.sum() - candL_post.sum()}  其他（CD/cap/maxTotal）擋下：{candL_post.sum() - fireL_actual.sum()}")
print(f"S 候選 bar：raw(GK+brk+session)={candS_raw.sum()} → 過 regime gate {candS_post.sum()} → 實際進場 {fireS_actual.sum()}")
print(f"  R gate 擋下：{candS_raw.sum() - candS_post.sum()}  其他（CD/cap/maxTotal）擋下：{candS_post.sum() - fireS_actual.sum()}")

# 進場時的 regime 分佈
print("\n實際進場 regime 分佈：")
print(lt.groupby(["sub_strategy", "entry_regime"]).size().unstack(fill_value=0))


# ============================ SECTION 4 ============================
section("§4. 風險 / 執行品質")

# Slippage = entry_price - entry_signal_bar_close
lt["slip_abs"] = lt["entry_price"] - lt["entry_signal_bar_close"]
lt["slip_pct"] = lt["slip_abs"] / lt["entry_signal_bar_close"] * 100
# L 進場滑價負方向（買到更便宜）=有利，反之；S 反之
lt["slip_unfavorable_pct"] = np.where(
    lt["sub_strategy"] == "L", lt["slip_pct"], -lt["slip_pct"]
)
print(f"進場滑價：avg unfavorable {lt['slip_unfavorable_pct'].mean() * 100:.2f} bp  max {lt['slip_unfavorable_pct'].max() * 100:.2f} bp  min {lt['slip_unfavorable_pct'].min() * 100:.2f} bp")

print(f"Commission 總和：${lt['commission_usd'].sum():.2f}  / 平均 ${lt['commission_usd'].mean():.2f} / 筆")

# SafeNet 距離
lt["sn_buffer_pct"] = np.where(
    lt["sub_strategy"] == "L",
    3.5 - (-lt["max_adverse_excursion_pct"]),  # L: 3.5% - |MAE%|
    4.0 - (-lt["max_adverse_excursion_pct"]),
)
print(f"\n最深 MAE 距 SafeNet：min {lt['sn_buffer_pct'].min():.2f} %pt  median {lt['sn_buffer_pct'].median():.2f} %pt")
print("最接近 SafeNet 的 trade：")
worst = lt.nsmallest(3, "sn_buffer_pct")[["trade_number", "sub_strategy", "max_adverse_excursion_pct", "sn_buffer_pct", "exit_type"]]
print(worst.to_string(index=False))

# Hold bars 分佈
print("\nHold bars 分佈（按 exit_type）：")
print(lt.groupby("exit_type")["hold_bars"].agg(["count", "mean", "min", "max"]).to_string())

# 30 天 max DD
eq = lt.sort_values("entry_dt").reset_index(drop=True)
eq["cum_pnl"] = eq["pnl"].cumsum()
eq["peak"] = eq["cum_pnl"].cummax()
eq["dd"] = eq["cum_pnl"] - eq["peak"]
print(f"\n累計 PnL：${eq['cum_pnl'].iloc[-1]:.2f}  Max DD：${-eq['dd'].min():.2f}")

# 月度
lt["month"] = lt["entry_dt"].dt.to_period("M")
print("\n月度績效：")
print(lt.groupby(["month", "sub_strategy"])["pnl"].agg(["count", "sum"]).to_string())
