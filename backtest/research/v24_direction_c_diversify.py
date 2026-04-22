"""
V24 Direction C — Multi-asset diversification
$700 V14+R (ETH) + $300 獨立策略（不同標的/時框）

目標（KPI）：
  - C 標的 strat 年化 >= 10% ($30/yr on $300)
  - |月度相關 r| < 0.3 vs V14+R
  - C Worst30 <= $90
  - 總組合 Sharpe 改善 (total Sharpe > V14+R standalone)
  - 8/10 gates pass

V20 規則：不能照搬 V14 參數到其他幣
因此使用全新策略骨架：Donchian breakout + SMA200-slope trend filter
參數僅：Donchian_N、slope_threshold（2 個，符合 G10）

測試：BTC/SOL/BNB/XRP/DOGE × 1h→4h/12h/1d aggregation
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, stats_from, load_data as load_eth

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')

# ============ 1. 先跑 ETH V14+R 得到月度 PnL 序列 ============
df_e, o_e, h_e, l_e, c_e, hr_e, dw_e, mk_e, dk_e = load_eth()
N_e = len(o_e); IS_END_e = N_e // 2

sma200_e = pd.Series(c_e).rolling(200).mean().values
slope_e = np.full(N_e, np.nan)
for i in range(300, N_e):
    if not np.isnan(sma200_e[i]) and sma200_e[i-100] > 0:
        slope_e[i] = (sma200_e[i] - sma200_e[i-100]) / sma200_e[i-100]
slope_e_use = np.roll(slope_e, 1); slope_e_use[0] = np.nan
block_L = (slope_e_use > 0.045) & (~np.isnan(slope_e_use))
block_S = (np.abs(slope_e_use) < 0.010) & (~np.isnan(slope_e_use))

# $700 V14+R: 按比例縮 notional to 0.7x = $2800 + $700 equivalent
# 實際：$1000 account 現在分配 $700 給 V14+R → notional_L/S = 4000*0.7 = 2800
V14R_NOT = 2800; V14R_FEE = 4.0 * 0.7
tr_eth = run_v14_overlay(o_e, h_e, l_e, c_e, hr_e, dw_e, mk_e, dk_e, 0, N_e,
                          block_bars_L=block_L, block_bars_S=block_S,
                          notional_L=V14R_NOT, notional_S=V14R_NOT,
                          fee_L=V14R_FEE, fee_S=V14R_FEE)
eth_monthly = {}
for t in tr_eth:
    eth_monthly[t['entry_mk']] = eth_monthly.get(t['entry_mk'],0) + t['pnl']
eth_pnl_total = sum(t['pnl'] for t in tr_eth)
eth_stat = stats_from(tr_eth)

print("="*110)
print(f"V24 Direction C — Multi-asset diversification")
print(f"基準：$700 V14+R (notional=$2800, fee=$2.80)")
print(f"  ETH V14+R $700 PnL ${eth_pnl_total:+.0f} / Sharpe {eth_stat['sharpe']:.2f} / MDD ${eth_stat['mdd']:.0f}")
print("="*110)

# ============ 2. 獨立策略骨架：Donchian breakout + SMA200 slope filter ============
def load_coin(sym):
    path = os.path.join(DATA_DIR, f'{sym}USDT_1h_latest730d.csv')
    if not os.path.exists(path): return None
    df = pd.read_csv(path)
    dt = pd.to_datetime(df['datetime'])
    return df, dt

def aggregate(df, dt, tf_hours):
    """1h → tf_hours. 從小時 0 開始每 tf_hours 根聚合"""
    # group by bar index // tf_hours
    df2 = df.copy()
    df2['grp'] = np.arange(len(df2)) // tf_hours
    agg = df2.groupby('grp').agg(
        datetime=('datetime','first'),
        open=('open','first'),
        high=('high','max'),
        low=('low','min'),
        close=('close','last'),
    ).reset_index(drop=True)
    # Drop last incomplete group
    if len(agg) > 0 and (len(df2) % tf_hours) != 0:
        agg = agg.iloc[:-1].reset_index(drop=True)
    return agg

def run_donchian_strat(df, notional=300, fee=0.25, don_n=20, slope_th=0.03,
                       sl_pct=0.03, tp_pct=0.06, max_hold=10):
    """
    Donchian 20 breakout long-only with SMA200 slope > slope_th
    SL = -3%, TP = +6%, MaxHold 10 bar
    $300 notional, $0.25 fee per side (比例較小，因 TF 較大)
    Long-only（避免跟 V14+R S 重複）— 其實可選兩側但為獨立性先 long
    """
    o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
    n = len(c)
    sma200 = pd.Series(c).rolling(200).mean().values
    slope = np.full(n, np.nan)
    for i in range(300, n):
        if not np.isnan(sma200[i]) and sma200[i-50] > 0:
            slope[i] = (sma200[i] - sma200[i-50]) / sma200[i-50]
    slope_use = np.roll(slope, 1); slope_use[0] = np.nan
    sc = np.roll(c, 1); sc[0] = np.nan
    hdon = pd.Series(sc).rolling(don_n).max().values
    brk_up = c > hdon
    trades = []
    active=False; ep=0; ebar=0; bh=0
    for i in range(don_n+300, n):
        if active:
            bh += 1
            ex_p=0; ex_r=''
            if l[i] <= ep*(1-sl_pct):
                ex_p = ep*(1-sl_pct); ex_r='SL'
            elif h[i] >= ep*(1+tp_pct):
                ex_p = ep*(1+tp_pct); ex_r='TP'
            elif bh >= max_hold:
                ex_p = c[i]; ex_r='MH'
            if ex_p > 0:
                pct = (ex_p-ep)/ep
                pnl = pct*notional - fee
                trades.append({'entry_bar':ebar,'exit_bar':i,'pnl':pnl,'reason':ex_r,'bars_held':bh,'entry_dt':df['datetime'].iloc[ebar]})
                active=False
        if not active:
            if brk_up[i] and not np.isnan(slope_use[i]) and slope_use[i] > slope_th:
                active=True; ep=c[i]; ebar=i; bh=0
    return trades

def trades_to_monthly(trades):
    out = {}
    for t in trades:
        dt = pd.to_datetime(t['entry_dt'])
        key = dt.year*100 + dt.month
        out[key] = out.get(key,0) + t['pnl']
    return out

def corr_with_eth(coin_monthly, eth_monthly):
    all_keys = sorted(set(list(coin_monthly.keys()) + list(eth_monthly.keys())))
    if len(all_keys) < 5: return 0
    e_arr = np.array([eth_monthly.get(k, 0) for k in all_keys])
    c_arr = np.array([coin_monthly.get(k, 0) for k in all_keys])
    if e_arr.std() == 0 or c_arr.std() == 0: return 0
    return np.corrcoef(e_arr, c_arr)[0,1]

def worst30_for_trades(trades, n_bars, window_bars=30*24, top_n=1):
    if not trades: return 0
    equity = np.zeros(n_bars)
    trs = sorted(trades, key=lambda x: x['exit_bar'])
    ptr=0; cum=0
    for i in range(n_bars):
        while ptr < len(trs) and trs[ptr]['exit_bar'] <= i:
            cum += trs[ptr]['pnl']; ptr+=1
        equity[i] = cum
    wdd = []
    for i in range(window_bars, n_bars):
        win = equity[i-window_bars:i+1]
        peak = np.maximum.accumulate(win)
        wdd.append((win-peak).min())
    return min(wdd) if wdd else 0

# ============ 3. 掃描所有組合 ============
COINS = ['BTC','SOL','BNB','XRP','DOGE','ADA','AVAX','BCH','LINK','LTC','MATIC']
TFS = [4, 12, 24]  # hours
DON_N_VALS = [20, 30]
SLOPE_VALS = [0.02, 0.03, 0.05]

print(f"\n掃描：{len(COINS)} 幣 × {len(TFS)} TF × {len(DON_N_VALS)} Don_N × {len(SLOPE_VALS)} Slope "
      f"= {len(COINS)*len(TFS)*len(DON_N_VALS)*len(SLOPE_VALS)} 配置\n")
print(f"{'Sym':<5}{'TF':>4}{'DonN':>6}{'SlTh':>7}{'Tr':>4}{'PnL $':>9}{'WR%':>6}"
      f"{'W30':>8}{'r_ETH':>8}{'Sh':>6}{'Status':>10}")
print("-"*110)

results = []
for coin in COINS:
    d = load_coin(coin)
    if d is None: continue
    df_raw, dt_raw = d
    df_raw = df_raw.copy(); df_raw['datetime'] = dt_raw
    for tf in TFS:
        agg = aggregate(df_raw, dt_raw, tf)
        n_bars_agg = len(agg)
        for dn in DON_N_VALS:
            for sl_th in SLOPE_VALS:
                trs = run_donchian_strat(agg, don_n=dn, slope_th=sl_th)
                if not trs: continue
                st = stats_from([{'side':'L','entry_bar':t['entry_bar'],'exit_bar':t['exit_bar'],
                                  'entry_mk':0,'pnl':t['pnl'],'reason':t['reason'],
                                  'bars_held':t['bars_held'],'scale':1.0} for t in trs])
                mo = trades_to_monthly(trs)
                r = corr_with_eth(mo, eth_monthly)
                # Convert agg bar count to equivalent 1h bars for Worst30
                w30 = worst30_for_trades(trs, n_bars_agg, window_bars=int(30*24/tf))
                pass_pnl = st['pnl'] >= 30
                pass_corr = abs(r) < 0.3
                pass_w30 = w30 >= -90
                ok = pass_pnl and pass_corr and pass_w30
                status = "CANDIDATE" if ok else ""
                results.append({
                    'coin':coin,'tf':tf,'don_n':dn,'slope':sl_th,'n':st['n'],
                    'pnl':st['pnl'],'wr':st['wr'],'w30':w30,'r':r,'sharpe':st['sharpe'],
                    'monthly':mo,'trades':trs,'ok':ok
                })
                if st['pnl'] > -50 or ok:  # only print interesting results
                    print(f"{coin:<5}{tf:>4}h{dn:>6}{sl_th:>7.2f}{st['n']:>4}"
                          f"{st['pnl']:>+9.0f}{st['wr']:>6.1f}{w30:>+8.0f}{r:>+8.2f}{st['sharpe']:>6.2f}"
                          f"{status:>10}")

# ============ 4. 篩選候選 ============
print("\n" + "="*110)
print("候選篩選結果")
print("="*110)
candidates = [r for r in results if r['ok']]
print(f"通過 KPI (PnL>=$30, |r|<0.3, Worst30>=-$90): {len(candidates)}/{len(results)}")

if candidates:
    candidates.sort(key=lambda r: -(r['pnl']/max(abs(r['w30']),1)))  # PnL/risk ratio
    print(f"\nTop 候選（依 PnL/W30 ratio 排序）：")
    for i, c in enumerate(candidates[:10]):
        print(f"  #{i+1} {c['coin']} {c['tf']}h DonN={c['don_n']} Sl={c['slope']:.2f} "
              f"PnL=${c['pnl']:+.0f} W30=${c['w30']:+.0f} r={c['r']:+.2f} Sh={c['sharpe']:.2f} N={c['n']}")
else:
    print("\nNo candidates passed KPI.")

# ============ 5. Portfolio 效果估算 ============
print("\n" + "="*110)
print("Portfolio 分析（$700 V14+R + $300 Best candidate）")
print("="*110)
if candidates:
    best = candidates[0]
    # Portfolio Sharpe: monthly std assumption
    all_months = sorted(set(list(eth_monthly.keys()) + list(best['monthly'].keys())))
    e_arr = np.array([eth_monthly.get(k,0) for k in all_months])
    b_arr = np.array([best['monthly'].get(k,0) for k in all_months])
    p_arr = e_arr + b_arr
    p_mean = p_arr.mean(); p_std = p_arr.std()
    p_sharpe_monthly = p_mean / p_std if p_std > 0 else 0
    p_sharpe_ann = p_sharpe_monthly * np.sqrt(12)
    e_mean = e_arr.mean(); e_std = e_arr.std()
    e_sharpe_monthly = e_mean / e_std if e_std > 0 else 0
    e_sharpe_ann = e_sharpe_monthly * np.sqrt(12)
    p_worst_mo = p_arr.min()
    e_worst_mo = e_arr.min()
    print(f"  Best C candidate: {best['coin']} {best['tf']}h DonN={best['don_n']} Sl={best['slope']:.2f}")
    print(f"  $700 V14+R only:  Monthly Sharpe={e_sharpe_monthly:.2f} / Annual {e_sharpe_ann:.2f} / Worst month ${e_worst_mo:+.0f}")
    print(f"  $700+$300 combo:  Monthly Sharpe={p_sharpe_monthly:.2f} / Annual {p_sharpe_ann:.2f} / Worst month ${p_worst_mo:+.0f}")
    print(f"  Sharpe improvement: {(p_sharpe_ann - e_sharpe_ann)/e_sharpe_ann*100:+.1f}%")
    print(f"  C total PnL: ${best['pnl']:+.0f} / V14+R $700 PnL: ${eth_pnl_total:+.0f} / Combined: ${eth_pnl_total+best['pnl']:+.0f}")
    # Verdict
    if p_sharpe_ann > e_sharpe_ann:
        print(f"\n  → 組合 Sharpe 改善 — Direction C CONDITIONAL PASS")
    else:
        print(f"\n  → 組合 Sharpe 未改善 — Direction C FAIL")
else:
    print("\n  無候選可組合 — Direction C REJECTED")
