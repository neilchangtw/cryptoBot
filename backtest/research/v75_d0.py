"""V75 D0：止盈改掛限價單（K 線內成交、保守穿過門檻）。登記：doc/research_results/v75/v75_plan.md 第 4 節。
用法：python backtest/research/v75_d0.py"""
import os, sys, json, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v69a as A
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v75'); os.makedirs(OUT, exist_ok=True)

def build_sim_tpl():
    """在 v69a.build_sim 的來源上，先插入止盈限價成交（ind['tpl']＝穿過門檻；未設則行為不變）。"""
    orig = inspect.getsource
    def patched(f):
        s = orig(f)
        if getattr(f, '__name__', '') != 'simulate_v14_detailed': return s
        reps = [("ex_price = l_mkt if realistic else ep * (1 + l_tp_eff)", "ex_price = ((ep * (1 + l_tp_eff)) if ('tpl' in ind and hi >= ep * (1 + l_tp_eff) * (1 + ind['tpl'])) else l_mkt) if realistic else ep * (1 + l_tp_eff)"),
                ("ex_price = s_mkt if realistic else ep * (1 - S_TP)", "ex_price = ((ep * (1.0 - 1.0 * S_TP)) if ('tpl' in ind and li <= ep * (1.0 - 1.0 * S_TP) * (1 - ind['tpl'])) else s_mkt) if realistic else ep * (1 - S_TP)")]
        for a, b in reps: assert s.count(a) == 1, a; s = s.replace(a, b)
        return s
    A.inspect.getsource = patched
    try: return A.build_sim()
    finally: A.inspect.getsource = orig

def book(df, ind0, sim, ok, tpl=None):
    ind = dict(ind0) if tpl is None else {**ind0, 'tpl': tpl}; return A.ref_book(df, ind, sim, ok)

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); sim = build_sim_tpl(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20); res = {}
    assert book(df, ind0, sim, ones).equals(A.ref_book(df, ind0, A.build_sim(), ones)), '未設 tpl 時須與原引擎相同'
    for bn, ok in (('Q20W30', okq), ('全部訊號', ones)):
        b0 = A.costed(book(df, ind0, sim, ok), df, fund)[1]; b0 = b0[(b0.t_out >= '2021-01-01') & (b0.t_out < '2026-11-01')]
        for pen in (0.0, 0.0001, 0.0003, 0.0005):
            b1 = A.costed(book(df, ind0, sim, ok, pen), df, fund)[1]; b1 = b1[(b1.t_out >= '2021-01-01') & (b1.t_out < '2026-11-01')]; y0 = b0.groupby(b0.t_out.dt.year).net.sum(); y1 = b1.groupby(b1.t_out.dt.year).net.sum()
            m = b0.merge(b1, on=['side', 't_in'], how='outer', suffixes=('0', '1'), indicator=True); both = m[m._merge == 'both']; d = both.net1 - both.net0; tp = both[both.reason0 == 'TP']; dtp = tp.net1 - tp.net0
            flip = int(((both.net0 <= 0) & (both.net1 > 0)).sum()); lim = int((tp.xp1 != tp.xp0).sum())
            r = dict(帳本=bn, 穿過門檻bp=pen * 1e4, 原淨利=round(b0.net.sum(), 1), 新淨利=round(b1.net.sum(), 1), 增量=round(b1.net.sum() - b0.net.sum(), 1), 逐年增量={int(y): round(float(y1.get(y, 0) - y0.get(y, 0)), 1) for y in range(2021, 2027)}, 原逐年={int(y): round(float(y0.get(y, 0)), 1) for y in range(2021, 2027)}, 新逐年={int(y): round(float(y1.get(y, 0)), 1) for y in range(2021, 2027)},
                     原筆數=len(b0), 新筆數=len(b1), 未配對筆數=int((m._merge != 'both').sum()), 止盈筆數=len(tp), 限價成交筆數=lim, 止盈單增量為正比例=round(float((dtp > 0).mean()) * 100, 1), 止盈單平均增量=round(float(dtp.mean()), 2), 止盈單增量中位=round(float(dtp.median()), 2), 配對增量合計=round(float(d.sum()), 1), 扣最大5筆=round(float(d.sum() - d.nlargest(5).sum()), 1), 最大單筆增量=round(float(d.max()), 1), 最差單筆增量=round(float(d.min()), 1), 虧轉贏筆數=flip,
                     原勝率=round((b0.net > 0).mean() * 100, 2), 新勝率=round((b1.net > 0).mean() * 100, 2))
            res[(bn, pen)] = r; print(json.dumps(r, ensure_ascii=False), flush=True)
            if pen == 0.0001: both.assign(增量=d)[['side', 't_in', 't_out0', 'reason0', 'reason1', 'xp0', 'xp1', 'net0', 'net1', '增量']].to_csv(os.path.join(OUT, f'd0_pairs_{bn}.csv'), index=False)
    q = res[('Q20W30', 0.0001)]; a = res[('全部訊號', 0.0001)]
    K = dict(K1=bool(q['增量'] > 0 and sum(v > 0 for v in q['逐年增量'].values()) >= 5 and q['逐年增量'][2021] > 0), K2=bool(q['扣最大5筆'] > 0 and q['止盈單增量為正比例'] > 50), K3=all(res[('Q20W30', p)]['增量'] > 0 for p in (0.0, 0.0001, 0.0003, 0.0005)), K4=bool(a['增量'] > 0))
    print('判定', K, 'PASS→D1' if all(K.values()) else 'REJECTED'); json.dump(dict(K=K, rows=[v for v in res.values()]), open(os.path.join(OUT, 'v75_d0.json'), 'w'), ensure_ascii=False, indent=1, default=str)
