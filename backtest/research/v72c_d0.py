"""V72-C 診斷：突破前 6 小時未平倉量變化在過去 720 根的百分位，≤1/3 為平倉推動。登記：doc/research_results/v72/v72c_plan.md。
用法：python backtest/research/v72c_d0.py"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v72b_d0 as B
if __name__ == '__main__':
    m = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_metrics_1h_2021_2026.csv'), parse_dates=['t']); oi = pd.Series(m.sum_open_interest.values, index=m.t - pd.Timedelta(hours=7)).sort_index()   # 標籤（UTC+8 開盤）→ 該根收盤 UTC
    oi = oi[~oi.index.duplicated()].asfreq('h'); ch = (oi / oi.shift(6) - 1).shift(1); x = ch.values; pct = np.full(len(x), np.nan)   # shift(1)：只用到進場那根的前一根
    for i in range(720, len(x)):
        h = x[i - 720:i]; h = h[~np.isnan(h)]
        if len(h) >= 360 and not np.isnan(x[i]): pct[i] = (h < x[i]).mean()
    pct = pd.Series(pct, index=oi.index); print('OI 缺值比例', round(oi.isna().mean() * 100, 2), '百分位可用起點', pct.dropna().index[0]); rows = []
    for name, f in (('Q20W30', 'd0_Q20W30_trades.csv'), ('現行原配置', 'd0_基準_trades.csv')):
        t = pd.read_csv(os.path.join(B.V69, f), parse_dates=['t_in', 't_out']); t = t[t.t_in >= '2022-01-01'].reset_index(drop=True); rows.append(B.run(name, t, pct, labels=('平倉推動', '中性', '新倉推動'), tag='c'))
    pd.DataFrame(rows).to_csv(os.path.join(B.OUT, 'v72c_d0_summary.csv'), index=False)
