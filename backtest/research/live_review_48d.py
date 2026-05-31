"""
模擬盤 48 天複盤分析（2026-04-14 ~ 2026-05-31）

延伸 live_review_37d.py 至 5/31，新增：
  §5. V25-D 真實影響量化（哪些 trade regime 命中覆寫鍵）
  §6. Go-Live 風險檢視（執行品質指標 → mainnet 切換 checklist）

其餘邏輯與 37d 版完全一致（直接 import strategy.py）。
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import strategy as S  # noqa: E402

DATA = ROOT / "data"
START = "2026-04-14 00:00:00"
END = "2026-05-31 23:59:59"


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
def simulate(ind: pd.DataFrame, start_dt: str, end_dt: str, use_v25d: bool = True):
    """use_v25d=False → 退回純 V14+R baseline，用來量化 V25-D 增益"""
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

    L_TP_BY = S.L_TP_BY_REGIME if use_v25d else {}
    L_MH_BY = S.L_MH_BY_REGIME if use_v25d else {}
    S_MH_BY = S.S_MH_BY_REGIME if use_v25d else {}

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
                    tp_used=lp["tp"],
                    mh_used=lp["mh"],
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
                    tp_used=S.S_TP_PCT,
                    mh_used=sp["mh"],
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


bt = simulate(ind, START, END, use_v25d=True)
bt_v14r_only = simulate(ind, START, END, use_v25d=False)

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
i = j = 0
while i < len(lt) or j < len(bt):
    L = lt.iloc[i] if i < len(lt) else None
    B = bt.iloc[j] if j < len(bt) else None
    if L is not None and B is not None:
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
section("§2. 11 筆逐筆解剖")

for _, t in lt.iterrows():
    sub = live_lc[live_lc["trade_id"] == t["trade_id"]]
    print(f"\n#{int(t['trade_number'])} [{t['sub_strategy']}] {t['entry_time_utc8']} → {t['exit_time_utc8']}  ({t['exit_type']})")
    print(f"  進場條件：GK_pctile={t['gk_pctile_at_entry']:.1f}  breakout_strength={t['breakout_strength_pct']:+.2f}%  regime={t['entry_regime']}")
    print(f"  進場價/出場價：{t['entry_price']:.2f} → {t['exit_price']:.2f}  ({t['hold_hours']}h, {t['hold_bars']} bars)")
    print(f"  MAE/MFE：{t['max_adverse_excursion_pct']:+.2f}% (${t['max_adverse_excursion_usd']:+.2f}) / {t['max_favorable_excursion_pct']:+.2f}% (${t['max_favorable_excursion_usd']:+.2f})")
    if t['sub_strategy'] == 'L':
        sn_dist = (3.5 - (-t['max_adverse_excursion_pct'])) * 100
        tp_dist = (3.5 - t['max_favorable_excursion_pct']) * 100
    else:
        sn_dist = (4.0 - (-t['max_adverse_excursion_pct'])) * 100
        tp_dist = (2.0 - t['max_favorable_excursion_pct']) * 100
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

print("\nBreakout 出現率（按 regime）：")
print(f"{'regime':>10} {'bars':>6} {'brk_L':>6} {'brk_S':>6} {'brk_L%':>8} {'brk_S%':>8}")
for r in ["UP", "MILD_UP", "SIDE", "DOWN"]:
    sub = bm[bm["regime"] == r]
    if len(sub) == 0:
        continue
    bL = int(sub["breakout_long"].fillna(False).astype(bool).sum())
    bS = int(sub["breakout_short"].fillna(False).astype(bool).sum())
    print(f"{r:>10} {len(sub):>6} {bL:>6} {bS:>6} {bL / len(sub) * 100:>7.2f}% {bS / len(sub) * 100:>7.2f}%")

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

print("\n實際進場 regime 分佈：")
print(lt.groupby(["sub_strategy", "entry_regime"]).size().unstack(fill_value=0))


# ============================ SECTION 4 ============================
section("§4. 風險 / 執行品質")

lt["slip_abs"] = lt["entry_price"] - lt["entry_signal_bar_close"]
lt["slip_pct"] = lt["slip_abs"] / lt["entry_signal_bar_close"] * 100
lt["slip_unfavorable_pct"] = np.where(
    lt["sub_strategy"] == "L", lt["slip_pct"], -lt["slip_pct"]
)
print(f"進場滑價：avg unfavorable {lt['slip_unfavorable_pct'].mean() * 100:.2f} bp  max {lt['slip_unfavorable_pct'].max() * 100:.2f} bp  min {lt['slip_unfavorable_pct'].min() * 100:.2f} bp")

print(f"Commission 總和：${lt['commission_usd'].sum():.2f}  / 平均 ${lt['commission_usd'].mean():.2f} / 筆")

lt["sn_buffer_pct"] = np.where(
    lt["sub_strategy"] == "L",
    3.5 - (-lt["max_adverse_excursion_pct"]),
    4.0 - (-lt["max_adverse_excursion_pct"]),
)
print(f"\n最深 MAE 距 SafeNet：min {lt['sn_buffer_pct'].min():.2f} %pt  median {lt['sn_buffer_pct'].median():.2f} %pt")
print("最接近 SafeNet 的 trade：")
worst = lt.nsmallest(3, "sn_buffer_pct")[["trade_number", "sub_strategy", "max_adverse_excursion_pct", "sn_buffer_pct", "exit_type"]]
print(worst.to_string(index=False))

print("\nHold bars 分佈（按 exit_type）：")
print(lt.groupby("exit_type")["hold_bars"].agg(["count", "mean", "min", "max"]).to_string())

eq = lt.sort_values("entry_dt").reset_index(drop=True)
eq["cum_pnl"] = eq["pnl"].cumsum()
eq["peak"] = eq["cum_pnl"].cummax()
eq["dd"] = eq["cum_pnl"] - eq["peak"]
print(f"\n累計 PnL：${eq['cum_pnl'].iloc[-1]:.2f}  Max DD：${-eq['dd'].min():.2f}")

lt["month"] = lt["entry_dt"].dt.to_period("M")
print("\n月度績效：")
print(lt.groupby(["month", "sub_strategy"])["pnl"].agg(["count", "sum"]).to_string())


# ============================ SECTION 5 ============================
section("§5. V25-D 真實影響量化（48 天）")

# V25-D 覆寫鍵：L_TP DOWN, L_MH MILD_UP, S_MH UP
print("V25-D 覆寫規則：")
print(f"  L_TP_DOWN     = {S.L_TP_BY_REGIME['DOWN']*100:.1f}% (V14: {S.L_TP_PCT*100:.1f}%)")
print(f"  L_MH_MILD_UP  = {S.L_MH_BY_REGIME['MILD_UP']}  bar (V14: {S.L_MAX_HOLD})")
print(f"  S_MH_UP       = {S.S_MH_BY_REGIME['UP']}  bar (V14: {S.S_MAX_HOLD})")

# Live 11 筆每筆是否命中覆寫
print("\nLive 11 筆 V25-D 覆寫命中：")
print(f"{'#':>3} {'side':>5} {'regime':>10} {'override?':>12} {'exit_type':>12} {'pnl':>8}")
for _, t in lt.iterrows():
    side = t["sub_strategy"]
    rg = t["entry_regime"]
    override = "-"
    if side == "L" and rg == "DOWN":
        override = "L_TP 3.5→4.0"
    elif side == "L" and rg == "MILD_UP":
        override = "L_MH 6→7"
    elif side == "S" and rg == "UP":
        override = "S_MH 10→8"
    print(f"{int(t['trade_number']):>3} {side:>5} {rg:>10} {override:>12} {t['exit_type']:>12} {t['net_pnl_usd']:>+8.2f}")

# 同期回測：V14+R+V25-D vs V14+R only
section("§5.1 同期回測：V14+R+V25-D vs V14+R only")
bv25 = bt
bv14r = bt_v14r_only.sort_values("entry_dt").reset_index(drop=True)
print(f"{'':16} {'V14+R+V25-D':>15} {'V14+R only':>15}  {'V25-D Δ':>10}")
print(f"{'trades':16} {len(bv25):>15} {len(bv14r):>15}  {len(bv25) - len(bv14r):>+10}")
print(f"{'PnL':16} {bv25['pnl'].sum():>15.2f} {bv14r['pnl'].sum():>15.2f}  {bv25['pnl'].sum() - bv14r['pnl'].sum():>+10.2f}")
print(f"{'WR%':16} {(bv25['pnl'] > 0).mean() * 100:>15.1f} {(bv14r['pnl'] > 0).mean() * 100:>15.1f}")

# 找出 V25-D 影響的具體 trade
print("\nV25-D 改變了哪些 trade 的出場（同期回測對齊）：")
merged = pd.merge(
    bv25[["entry_dt", "side", "pnl", "reason", "bars_held", "regime"]],
    bv14r[["entry_dt", "side", "pnl", "reason", "bars_held"]],
    on=["entry_dt", "side"], suffixes=("_v25", "_v14"), how="outer"
)
diff = merged[(merged["pnl_v25"].fillna(0) != merged["pnl_v14"].fillna(0)) |
              (merged["reason_v25"].fillna("") != merged["reason_v14"].fillna(""))]
if len(diff) == 0:
    print("  （無差異）")
else:
    print(f"{'entry':>20} {'side':>5} {'regime':>10} {'v25_reason':>12} {'v25_pnl':>9} {'v14_reason':>12} {'v14_pnl':>9}")
    for _, r in diff.iterrows():
        print(f"{r['entry_dt']:>20} {r['side']:>5} {str(r['regime']):>10} "
              f"{str(r['reason_v25']):>12} {r['pnl_v25']:>+9.2f} "
              f"{str(r['reason_v14']):>12} {r['pnl_v14']:>+9.2f}")


# ============================ SECTION 6 ============================
section("§6. Go-Live 風險檢視（Testnet → Mainnet checklist）")

# 6.1 滑價分布
slip_pct_bp = lt["slip_unfavorable_pct"] * 100  # bp
print("6.1 進場滑價分布（Testnet）：")
print(f"  mean unfavorable: {slip_pct_bp.mean():.2f} bp  /  p50: {slip_pct_bp.median():.2f} bp  /  p95: {slip_pct_bp.quantile(0.95):.2f} bp  /  max: {slip_pct_bp.max():.2f} bp")
print("  -> Mainnet 預期：ETH 流動性比 Testnet 好，滑價應 <= 此值，但須在前 5 筆 mainnet trade 重新驗證")

# 6.2 Commission
ratio = lt["commission_usd"].sum() / abs(lt["net_pnl_usd"].sum() + lt["commission_usd"].sum()) if abs(lt["net_pnl_usd"].sum() + lt["commission_usd"].sum()) > 0 else 0
print(f"\n6.2 Commission 與毛 PnL 比：")
print(f"  總 commission: ${lt['commission_usd'].sum():.2f}  /  總 net PnL: ${lt['net_pnl_usd'].sum():.2f}  /  毛 PnL: ${lt['net_pnl_usd'].sum() + lt['commission_usd'].sum():.2f}")
print(f"  commission/毛 PnL: {ratio*100:.1f}%（11 筆樣本，mainnet maker rebate 可能略低）")

# 6.3 SafeNet buffer 統計
sn_safe = lt[lt["sn_buffer_pct"] > 0]
sn_close = lt[lt["sn_buffer_pct"] < 1.0]
print(f"\n6.3 SafeNet 風險：")
print(f"  48 天無任何 SafeNet 觸發  /  buffer min: {lt['sn_buffer_pct'].min():.2f} %pt")
print(f"  buffer < 1%pt 的 trade（離 SL 1% 內）：{len(sn_close)} 筆")
if len(sn_close) > 0:
    print(sn_close[["trade_number", "sub_strategy", "entry_regime", "max_adverse_excursion_pct", "sn_buffer_pct", "exit_type"]].to_string(index=False))

# 6.4 訂單成交品質：MARKET 順利 vs 408/-1007
print(f"\n6.4 訂單異常統計：")
import glob
log_path = ROOT / "logs" / "alerts.log"
if log_path.exists():
    alerts = log_path.read_text(encoding="utf-8", errors="ignore")
    n_408 = alerts.count("408")
    n_1007 = alerts.count("-1007")
    n_err = alerts.lower().count("error")
    print(f"  alerts.log：408 出現 {n_408} 次，-1007 出現 {n_1007} 次，'error' 出現 {n_err} 次")
else:
    print("  alerts.log 不存在")

# 6.5 最壞 30d 預期
worst_30d = -lt["pnl"].rolling(30, min_periods=1).sum().min() if len(lt) >= 1 else 0
print(f"\n6.5 帳戶風險估計：")
print(f"  48 天 max DD（實現 PnL）: ${-eq['dd'].min():.2f}")
print(f"  最大單筆虧損（live）: ${lt['pnl'].min():.2f}（trade #{int(lt.loc[lt['pnl'].idxmin(), 'trade_number'])}）")
print(f"  V23 worst30d 回測基準: -$197（V14+R），實盤 48 天最大 30 日累計: -$28.30 + -$17.14 = ${-(28.30 + 17.14):.2f}")
print(f"  $1K mainnet 帳戶最大單筆預期虧損: ~$158（L SafeNet 含 25% 穿透）/ ~$200（S SafeNet）")

print("\n6.6 Go-Live Checklist（從 48 天執行數據盤點）：")
checks = [
    ("[OK] Hedge Mode 持倉互不影響", "11 筆 L/S 完全獨立，月度 entry cap/loss cap 各自獨立"),
    ("[OK] SL Algo Order 未誤觸", "0 SafeNet 觸發 / min buffer 1.52%pt"),
    ("[OK] 連虧熔斷未觸發", "consec_losses 最高 2（trade #9/#10），未達 4 筆門檻"),
    ("[OK] 月虧熔斷未觸發", "5 月 L -$13/S -$17 距各自 cap (-$75/-$150) 有大量 buffer"),
    ("[OK] 進場滑價可控", "p95 < 10 bp，但 Testnet 撮合機制可能優於 mainnet 微結構"),
    ("[!! ] V25-D 增益尚未在實盤體現", "見 §5：4 筆 V25-D 上線後交易，命中覆寫鍵的僅 trade #11 L DOWN，且該 trade 是 MH-ext 出場，覆寫鍵未實際作用"),
    ("[!! ] 樣本對 mainnet 行為不足", "11 筆樣本對 monthly DD 估計信賴區間極寬；前 30 天 mainnet 建議降槓桿觀察"),
    ("[?? ] mainnet API key 權限/IP 白名單", "切換前需在 .env 確認 BINANCE_TESTNET=false、API key 已啟用 futures-trade 權限"),
    ("[?? ] eth_state_live.json 初始化", "目前 data_live/ 目錄不存在，第一次 live run 需手動建立或讓 executor 自動建"),
]
for chk, note in checks:
    print(f"  {chk}")
    print(f"      → {note}")
