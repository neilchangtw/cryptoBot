"""
V24 Direction C Round 2 — IS/OOS 驗證 Top Candidates
Round 1 找到 37 個候選，但需要 IS/OOS split 驗證穩健性

測試：Top 候選逐個拆 50:50 IS/OOS，看 OOS 是否仍正向
並與 V14+R $700 組合 Sharpe 看真實改善
"""
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v24_engine import run_v14_overlay, stats_from, load_data as load_eth

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data')

# ============ 重跑 ETH V14+R $700 ============
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

V14R_NOT = 2800; V14R_FEE = 4.0 * 0.7
tr_eth_is = run_v14_overlay(o_e, h_e, l_e, c_e, hr_e, dw_e, mk_e, dk_e, 0, IS_END_e,
                             block_bars_L=block_L, block_bars_S=block_S,
                             notional_L=V14R_NOT, notional_S=V14R_NOT,
                             fee_L=V14R_FEE, fee_S=V14R_FEE)
tr_eth_oos = run_v14_overlay(o_e, h_e, l_e, c_e, hr_e, dw_e, mk_e, dk_e, IS_END_e, N_e,
                              block_bars_L=block_L, block_bars_S=block_S,
                              notional_L=V14R_NOT, notional_S=V14R_NOT,
                              fee_L=V14R_FEE, fee_S=V14R_FEE)

def tr_to_monthly(trades, key_fn=lambda t: t['entry_mk']):
    out = {}
    for t in trades:
        k = key_fn(t)
        out[k] = out.get(k,0) + t['pnl']
    return out

eth_is_m = tr_to_monthly(tr_eth_is)
eth_oos_m = tr_to_monthly(tr_eth_oos)
eth_is_pnl = sum(t['pnl'] for t in tr_eth_is)
eth_oos_pnl = sum(t['pnl'] for t in tr_eth_oos)

# ============ Helper ============
def load_coin(sym):
    path = os.path.join(DATA_DIR, f'{sym}USDT_1h_latest730d.csv')
    if not os.path.exists(path): return None
    df = pd.read_csv(path); dt = pd.to_datetime(df['datetime'])
    return df, dt

def aggregate(df, dt, tf_hours):
    df2 = df.copy(); df2['grp'] = np.arange(len(df2)) // tf_hours
    agg = df2.groupby('grp').agg(
        datetime=('datetime','first'),open=('open','first'),high=('high','max'),
        low=('low','min'),close=('close','last')
    ).reset_index(drop=True)
    if len(agg) > 0 and (len(df2) % tf_hours) != 0:
        agg = agg.iloc[:-1].reset_index(drop=True)
    return agg

def run_donchian_strat(df, start, end, notional=300, fee=0.25,
                       don_n=20, slope_th=0.03, sl_pct=0.03, tp_pct=0.06, max_hold=10):
    o = df['open'].values; h = df['high'].values; l = df['low'].values; c = df['close'].values
    dt = df['datetime'].values
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
    start = max(start, don_n+300)
    end = min(end, n)
    for i in range(start, end):
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
                trades.append({'entry_bar':ebar,'exit_bar':i,'pnl':pnl,'reason':ex_r,
                               'bars_held':bh,'entry_dt':dt[ebar]})
                active=False
        if not active:
            if brk_up[i] and not np.isnan(slope_use[i]) and slope_use[i] > slope_th:
                active=True; ep=c[i]; ebar=i; bh=0
    return trades

def to_monthly(trades):
    out = {}
    for t in trades:
        d = pd.to_datetime(t['entry_dt'])
        k = d.year*100 + d.month
        out[k] = out.get(k,0) + t['pnl']
    return out

# ============ Top 候選測試（Round 1 結果 + 幾個更 robust 的）============
CANDIDATES = [
    ('BTC', 24, 20, 0.05),  # #1 R1, but N=8
    ('BTC', 24, 30, 0.02),
    ('BTC',  4, 30, 0.02),  # more trades
    ('DOGE', 4, 30, 0.02),  # N=52
    ('DOGE', 4, 20, 0.02),  # N=58
    ('XRP',  4, 30, 0.05),  # N=37
    ('XRP',  4, 20, 0.05),  # N=47
    ('BTC', 12, 30, 0.03),
    ('SOL',  4, 30, 0.02),
    ('BNB', 12, 20, 0.02),
]

print("="*120)
print("V24 Direction C Round 2 — IS/OOS Validation + Portfolio Sharpe")
print("="*120)
print(f"$700 V14+R 基準：IS ${eth_is_pnl:+.0f} / OOS ${eth_oos_pnl:+.0f} / ALL ${eth_is_pnl+eth_oos_pnl:+.0f}")
print("="*120)
print(f"\n{'Sym':<5}{'TF':>4}{'DonN':>5}{'SlTh':>6}{'Nis':>4}{'Nos':>4}"
      f"{'IS $':>8}{'OOS $':>8}{'SameSign':>10}{'r_IS':>7}{'r_OOS':>7}"
      f"{'ComSh':>7}{'Verdict':>15}")
print("-"*120)

validated = []
for coin, tf, dn, sl in CANDIDATES:
    d = load_coin(coin)
    if d is None: continue
    df_raw, dt_raw = d
    df_raw = df_raw.copy(); df_raw['datetime'] = dt_raw
    agg = aggregate(df_raw, dt_raw, tf)
    n_agg = len(agg)
    mid_agg = n_agg // 2

    tr_is = run_donchian_strat(agg, 0, mid_agg, don_n=dn, slope_th=sl)
    tr_oos = run_donchian_strat(agg, mid_agg, n_agg, don_n=dn, slope_th=sl)

    st_is = stats_from([{'side':'L','entry_bar':t['entry_bar'],'exit_bar':t['exit_bar'],
                         'entry_mk':0,'pnl':t['pnl'],'reason':t['reason'],
                         'bars_held':t['bars_held'],'scale':1.0} for t in tr_is])
    st_oos = stats_from([{'side':'L','entry_bar':t['entry_bar'],'exit_bar':t['exit_bar'],
                          'entry_mk':0,'pnl':t['pnl'],'reason':t['reason'],
                          'bars_held':t['bars_held'],'scale':1.0} for t in tr_oos])

    # Monthly
    c_is_m = to_monthly(tr_is)
    c_oos_m = to_monthly(tr_oos)

    # Correlation
    def corr_ab(a_dict, b_dict):
        ks = sorted(set(list(a_dict.keys()) + list(b_dict.keys())))
        if len(ks) < 4: return 0
        a_arr = np.array([a_dict.get(k,0) for k in ks])
        b_arr = np.array([b_dict.get(k,0) for k in ks])
        if a_arr.std()==0 or b_arr.std()==0: return 0
        return np.corrcoef(a_arr, b_arr)[0,1]

    r_is = corr_ab(c_is_m, eth_is_m)
    r_oos = corr_ab(c_oos_m, eth_oos_m)

    # Portfolio Sharpe (OOS only — 真實預測力)
    all_oos_keys = sorted(set(list(c_oos_m.keys()) + list(eth_oos_m.keys())))
    if len(all_oos_keys) > 3:
        comb_arr = np.array([c_oos_m.get(k,0) + eth_oos_m.get(k,0) for k in all_oos_keys])
        eth_arr = np.array([eth_oos_m.get(k,0) for k in all_oos_keys])
        com_m = comb_arr.mean(); com_s = comb_arr.std()
        eth_m = eth_arr.mean(); eth_s = eth_arr.std()
        com_sharpe = (com_m/com_s * np.sqrt(12)) if com_s > 0 else 0
        eth_sharpe = (eth_m/eth_s * np.sqrt(12)) if eth_s > 0 else 0
        sharpe_impr = com_sharpe - eth_sharpe
    else:
        com_sharpe = 0; sharpe_impr = 0

    same_sign = (st_is['pnl'] > 0 and st_oos['pnl'] > 0)
    pass_corr = (abs(r_is) < 0.3) and (abs(r_oos) < 0.3)
    pass_sharpe = sharpe_impr > 0

    if same_sign and pass_corr and pass_sharpe:
        verdict = "VALIDATED"
        validated.append({'coin':coin,'tf':tf,'dn':dn,'sl':sl,
                          'is_pnl':st_is['pnl'],'oos_pnl':st_oos['pnl'],
                          'r_is':r_is,'r_oos':r_oos,'com_sharpe':com_sharpe,
                          'impr':sharpe_impr})
    elif not same_sign:
        verdict = "IS/OOS FAIL"
    elif not pass_corr:
        verdict = "|r|>=0.3"
    else:
        verdict = "No Sharpe impr"

    print(f"{coin:<5}{tf:>4}h{dn:>5}{sl:>6.2f}{st_is['n']:>4}{st_oos['n']:>4}"
          f"{st_is['pnl']:>+8.0f}{st_oos['pnl']:>+8.0f}"
          f"{'Y' if same_sign else 'N':>10}{r_is:>+7.2f}{r_oos:>+7.2f}"
          f"{com_sharpe:>7.2f}{verdict:>15}")

print("\n" + "="*120)
print("驗證結果")
print("="*120)
if validated:
    validated.sort(key=lambda x: -x['impr'])
    print(f"通過 IS/OOS + 相關性 + Sharpe 改善: {len(validated)} 個")
    for v in validated:
        print(f"  {v['coin']} {v['tf']}h DonN={v['dn']} Sl={v['sl']}: "
              f"IS ${v['is_pnl']:+.0f} OOS ${v['oos_pnl']:+.0f} "
              f"r_IS {v['r_is']:+.2f} r_OOS {v['r_oos']:+.2f} "
              f"Combo Sharpe {v['com_sharpe']:.2f} (impr {v['impr']:+.2f})")
    best = validated[0]
    print(f"\n推薦：{best['coin']} {best['tf']}h DonN={best['dn']} Slope={best['sl']}")
    print(f"Direction C CONDITIONAL PASS：組合 Sharpe 改善 {best['impr']:+.2f}")
else:
    print("所有候選在 IS/OOS 驗證中失敗")
    print("Direction C REJECTED：無獨立策略可在 IS/OOS 同號 + 低相關 + Sharpe 改善")
