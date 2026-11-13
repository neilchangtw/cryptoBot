"""V57-A D0：基準 690 筆依「資金流方向」（V55 美元溢價日訊號）分類。預登記：doc/research_results/v57/v57_plan.md 第 6 節
用法：python backtest/research/v57a_d0.py"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v55_d0 as D

def hourly_state(sig, idx):
    """sig：日訊號（index=UTC 00:00）。回傳對齊 idx（訊號 K 線收盤時間 UTC）的狀態：收盤時間在 D 01:00~D+1 01:00 之間用 D 日訊號。"""
    day = (idx - pd.Timedelta(hours=1)).floor('D'); return sig.reindex(day).fillna(0).astype(int).values

if __name__ == '__main__':
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v57')
    c = pd.read_csv(os.path.join(OUT, 'baseline_trades.csv'), parse_dates=['t_in', 't_out']); c = c[(c.t_out >= '2021-01-01') & (c.t_out < '2026-11-01')].reset_index(drop=True)
    sig = D.signal(D.daily_premium())
    # 引擎於訊號 K 線收盤後的下一根開盤進場：t_in（成交時間）即訊號 K 線收盤時間
    def classify(s):
        st = hourly_state(s, pd.DatetimeIndex(c.t_in)); d = np.where(c.side == 'L', 1, -1)
        return np.where(st == 0, 'neutral', np.where(st == d, 'aligned', 'opposed'))
    c['flow'] = classify(sig)
    print(c.groupby('flow').net.agg(['size', 'sum', 'mean']).round(1).to_string()); print(c.groupby(['flow', 'side']).net.agg(['size', 'sum', 'mean']).round(1).to_string())
    rows = []
    for y, g in c.groupby(c.t_out.dt.year):
        o = g[g.flow == 'opposed'].net; r = g[g.flow != 'opposed'].net
        rows.append(dict(year=y, opp_n=len(o), opp_sum=round(o.sum()), opp_mean=round(o.mean(), 1) if len(o) else np.nan, rest_mean=round(r.mean(), 1), ok=bool(len(o) >= 5 and o.mean() < r.mean())))
    yt = pd.DataFrame(rows); print(yt.to_string(index=False)); k1 = int(yt.ok.sum())
    opp = c[c.flow == 'opposed'].net.sum(); rng = np.random.default_rng(571); perm = []
    for _ in range(2000):
        k = rng.integers(30, len(sig) - 30); s2 = pd.Series(np.roll(sig.values, k), index=sig.index); perm.append(c.net[classify(s2) == 'opposed'].sum())
    p5 = np.quantile(perm, .05); p = float((np.array(perm) <= opp).mean())
    res = dict(opp_n=int((c.flow == 'opposed').sum()), opp_sum=round(opp, 1), K1_years=k1, K1=k1 >= 4, perm_p5=round(p5), perm_median=round(np.median(perm)), K2_p=p, K2=bool(opp < 0 and opp <= p5), share_of_4474=round(-opp / 4474, 3))
    print(res); yt.to_csv(os.path.join(OUT, 'v57a_d0_yearly.csv'), index=False); pd.DataFrame([res]).to_csv(os.path.join(OUT, 'v57a_d0_summary.csv'), index=False)
    print('判定', 'PASS→D1' if res['K1'] and res['K2'] else 'REJECTED（D0）')
