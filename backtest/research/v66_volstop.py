"""V66：依事前波動同步調整安全網止損距離與名目（固定每筆風險），2,000U 有限帳戶完整重播。
登記：doc/research_results/v64/v64_plan.md 第 5 節。用法：python backtest/research/v66_volstop.py"""
import os, sys, json, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v61_finite as F
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v64'); CAP = 2000.0

def build_sim():
    src = inspect.getsource(V.B.eng.simulate_v14_detailed)
    for old, new in [("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):", "not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):"),
                     ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):", "not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):"),
                     ("sn_lv = ep * (1 - L_SN)", "sn_lv = ep * (1 - L_SN * (ind['snm'][lp_bar] if 'snm' in ind else 1.0))"),
                     ("sn_lv = ep * (1 + S_SN)", "sn_lv = ep * (1 + S_SN * (ind['snm'][sp_bar] if 'snm' in ind else 1.0))"),
                     ("    sim_start = max(start_bar, WARMUP)", "    if 'scale' in ind: scale_arr = ind['scale']\n    sim_start = max(start_bar, WARMUP)")]:
        assert src.count(old) == 1, old; src = src.replace(old, new)
    ns = dict(V.B.eng.__dict__); exec(src, ns); return ns['simulate_v14_detailed']

def vol_mult(df, cap):
    """m＝前一根 ATR14% ÷ 其過去 365 日中位數（最少 90 日），限制 [1, cap]；只用進場前已知資料。"""
    h, l, c = (df[k].to_numpy(float) for k in ('high', 'low', 'close')); pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); a = (pd.Series(tr).rolling(14, min_periods=14).mean() / c * 100).shift(1)
    med = a.rolling(365 * 24, min_periods=90 * 24).median(); return (a / med).clip(1, cap).fillna(1.0).to_numpy()

def run_base(df, ind0, sim, okL, okS):
    t = pd.DataFrame(sim({**ind0, 'okL': okL, 'okS': okS}, df['datetime'].values, realistic=True, slip_bps=SLIP, extra_cost=5.0))
    return pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7), ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, bar=t.entry_bar,
                             ntl=t.margin * 20.0, mg=200.0, mod='base', pri=np.where(t.side == 'L', 0, 1)))
SLIP = 0.0

def mdd3(eq):
    pk = np.maximum(eq.cummax(), CAP); dd = pk - eq; j = dd.idxmax(); pct = dd / pk; k = pct.idxmax()
    return dict(MDD金額=round(dd.max()), MDD谷底=str(j.date()), MDD占當時高點=round(dd.max() / pk[j] * 100, 1), MDD占初始資金=round(dd.max() / CAP * 100, 1), 最大百分比回撤=round(pct.max() * 100, 1), 最大百分比回撤谷底=str(k.date()))

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); sim = build_sim(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20); F.run_base = run_base
    one = np.ones(len(df)); rows = []; sk_all = []
    CFGS = [('原版', None, None), ('V66 上限2（主要）', 2.0, True), ('V66 上限1.5', 1.5, True), ('V66 上限3', 3.0, True), ('對照：只縮名目 上限2', 2.0, False)]
    for bname, ok in (('基準', ones), ('Q20W30', okq)):
        for cname, cap, widen in CFGS:
            m = vol_mult(df, cap) if cap else one; ind = {**ind0, 'scale': 1.0 / m, 'snm': m if widen else one} if cap else ind0
            name = f'{bname}｜{cname}'; tr, eq, cost, sk, it, halted = F.replay(name, df, ind, sim, fund, ok, None, None); mt = F.metrics(name, tr, eq, df, sk); mt.update(mdd3(eq)); mt['停止交易'] = str(halted) if halted is not None else '否'
            c = cost[cost.t_in >= F.W0]; mt.update(勝率=round((c.net > 0).mean() * 100, 2), SN筆數=int((tr.reason == 'SN').sum()), SN淨損=round(c[tr.reason.values[:len(c)] == 'SN'].net.sum()) if len(c) == len(tr) else None, 平均名目=round(tr.ntl.mean()), m大於1比例=round((tr.ntl < 3999).mean() * 100, 1))
            rows.append(mt); sk_all.append(sk); tag = name.replace('｜', '_').replace('：', '').replace(' ', ''); tr.to_csv(os.path.join(OUT, f'v66_{tag}_trades.csv'), index=False); eq.rename('equity').to_csv(os.path.join(OUT, f'v66_{tag}_equity_hourly.csv'))
            print(json.dumps({k: mt[k] for k in ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', 'MDD金額', 'MDD占初始資金', '最大百分比回撤', '最低淨值', '最差月', '筆數', '勝率', 'SN筆數', '平均名目', '觸及強平價_現行', '跳過_現行', '停止交易')}, ensure_ascii=False), flush=True)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'v66_summary.csv'), index=False); s = pd.concat([x for x in sk_all if len(x)]) if any(len(x) for x in sk_all) else pd.DataFrame(); s.to_csv(os.path.join(OUT, 'v66_skipped.csv'), index=False)
