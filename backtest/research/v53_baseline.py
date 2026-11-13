"""V53 基準重跑：V14+R+V25-D（run_backtest 同引擎，realistic、每筆 +$5、固定 200U），加上歷史 funding 與 UTC 年度市價權益。
用法：python backtest/research/v53_baseline.py"""
import os, sys, importlib.util
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import v53_common as C
spec = importlib.util.spec_from_file_location('eng', os.path.join(HERE, 'v14_export_trades.py')); eng = importlib.util.module_from_spec(spec); spec.loader.exec_module(eng)

def baseline_trades(df, slip=0.0, extra=5.0):
    ind = eng.compute_indicators(df)
    t = pd.DataFrame(eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True, slip_bps=slip, extra_cost=extra))
    out = pd.DataFrame(dict(side=t.side, t_in=pd.to_datetime(t.entry_dt) - pd.Timedelta(hours=7), t_out=pd.to_datetime(t.exit_dt) - pd.Timedelta(hours=7),
                            ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, eng_pnl=t.pnl_usd))
    return out
if __name__ == '__main__':
    df = C.load_1h(); OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v53')
    print('資料', df.datetime.min(), '~', df.datetime.max(), '根數', len(df), '重複', int(df.datetime.duplicated().sum()), '缺漏', int((df.datetime.diff().dropna() != pd.Timedelta(hours=1)).sum()))
    tr = baseline_trades(df); inc, cost = C.mtm_equity(tr, df)
    cost['reason'] = tr.reason.values; cost['eng_pnl'] = tr.eng_pnl.values
    print('引擎淨利(不含funding) 對帳差', round((cost.gross - cost.fee - cost.extra - cost.eng_pnl).abs().max(), 2))
    yt, mt = C.tables(inc, cost, last_full_month='2026-10')
    print(yt.to_string(index=False)); print('2021-01~2026-10 合計', round(mt.sum()), '最差月', round(mt.min()), '負月', int((mt < 0).sum()), '/', len(mt))
    yt.to_csv(os.path.join(OUT, 'baseline_yearly.csv'), index=False); mt.round(2).to_csv(os.path.join(OUT, 'baseline_monthly.csv')); cost.round(4).to_csv(os.path.join(OUT, 'baseline_trades.csv'), index=False)
