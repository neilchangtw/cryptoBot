"""V71 第一部分：V70 首筆重播、啟動日敏感度、移除首筆／前 3 段完整重播、固定資金基數對照。
登記：doc/research_results/v71/v71_part1_plan.md。用法：python backtest/research/v71_part1.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC, v70_run as RN
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v71'); os.makedirs(OUT, exist_ok=True)
SL = dict(mode='rebal', g=0.25, band=0.20, lev=5.0)

def wait_flip(sig, start):
    s = sig[sig.index >= start]; f = s.index[(s != s.iloc[0]).values]; v = sig.copy(); v[v.index < (f[0] if len(f) else s.index[-1] + pd.Timedelta(days=1))] = 0.0; return v

def quick(R, cap=E.CAP):
    e = R['acc'].equity; pk = np.maximum(e.cummax(), cap); t0 = e.index[0]; blocks = []
    for k in range(3):
        a = t0 + pd.DateOffset(months=12 * k); b = t0 + pd.DateOffset(months=12 * (k + 1)); seg = e[(e.index >= a) & (e.index < b)]; prev = cap if k == 0 else e[e.index < a].iloc[-1]; blocks.append(round(seg.iloc[-1] - prev))
    return dict(淨利=round(e.iloc[-1] - cap), 第1年=blocks[0], 第2年=blocks[1], 第3年=blocks[2], 最低權益=round(e.min()), 最大百分比回撤=round(((pk - e) / pk).max() * 100, 1))

if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; sig = RC.trend_sig(X['dc']); rows = []
    for st in pd.date_range('2021-01-01', '2023-10-01', freq='MS'):
        en = st + pd.DateOffset(months=36) - pd.Timedelta(hours=1); kw = dict(base=X['q20'], base_mode=('risk', 0.03, 5.0), cross=True, start=st, end=en)
        a = quick(E.replay(df, fund, sleeve=dict(sig=sig, **SL), **kw)); b = quick(E.replay(df, fund, sleeve=dict(sig=wait_flip(sig, st), **SL), **kw)); c = quick(E.replay(df, fund, **kw))
        rows.append(dict(啟動日=str(st.date()), 啟動時訊號=int(sig[st]), **{f'a_{k}': v for k, v in a.items()}, **{f'b_{k}': v for k, v in b.items()}, **{f'對照_{k}': v for k, v in c.items()}))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'start_date_sensitivity.csv'), index=False); print(T[['啟動日', '啟動時訊號', 'a_淨利', 'a_第1年', 'a_第2年', 'a_第3年', 'a_最大百分比回撤', 'b_淨利', 'b_第1年', 'b_第2年', 'b_第3年', '對照_淨利', '對照_第1年', '對照_第2年', '對照_第3年']].to_string(index=False))
    for p in ('a', 'b', '對照'):
        y1 = (T[f'{p}_第1年'] > 0).mean(); al = ((T[f'{p}_第1年'] > 0) & (T[f'{p}_第2年'] > 0) & (T[f'{p}_第3年'] > 0)).mean(); print(p, '第1年為正比例', round(y1 * 100, 1), '三段全正比例', round(al * 100, 1), '36個月淨利中位數', T[f'{p}_淨利'].median(), '最低', T[f'{p}_淨利'].min(), '最低權益最小', T[f'{p}_最低權益'].min(), '最大回撤最大', T[f'{p}_最大百分比回撤'].max())
    # 70 個月完整重播的變體
    m = pd.read_csv(os.path.join(RC.OUT, 'v70_main_trades.csv'), parse_dates=['t_in', 't_out']); top = m[m.leg == 'trend'].sort_values('net', ascending=False).head(3); v3 = sig.copy()
    for r in top.itertuples(): v3[(v3.index >= r.t_in.floor('D')) & (v3.index < r.t_out.floor('D'))] = 0.0
    out = []
    for nm, kw in (('V70 主要（重跑）', dict(sleeve=dict(sig=sig, **SL))), ('移除首筆後完整重播（等第一次翻向）', dict(sleeve=dict(sig=wait_flip(sig, E.W0), **SL))), ('移除趨勢腿前 3 段後完整重播', dict(sleeve=dict(sig=v3, **SL))),
                   ('固定資金基數：V70', dict(sleeve=dict(sig=sig, **SL), fixed_basis=True)), ('固定資金基數：Q20W30＋固定風險', dict(fixed_basis=True)), ('固定資金基數：只有趨勢腿', dict(sleeve=dict(sig=sig, **SL), fixed_basis=True, nobase=True))):
        nb = kw.pop('nobase', False); R = E.replay(df, fund, cross=True, **({} if nb else dict(base=X['q20'], base_mode=('risk', 0.03, 5.0))), **kw); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(nm, R, df); G = RN.gates(d); t = R['tr']; s = t[t.leg == 'trend']
        d.update(G全過=all(G.values()), 未過項='、'.join(k for k, v in G.items() if not v), 趨勢腿筆數=len(s), 趨勢腿淨=round(s.net.sum()) if len(s) else 0, 現行腿淨=round(t[t.leg == 'base'].net.sum())); out.append(d)
        print(json.dumps({k: d.get(k) for k in ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '勝率', '最大百分比回撤', '最低權益', '最差連敗段', '最長連敗', 'G全過', '未過項', '趨勢腿筆數', '趨勢腿淨', '現行腿淨')}, ensure_ascii=False), flush=True)
        R['tr'].to_csv(os.path.join(OUT, f'part1_{len(out)}_trades.csv'), index=False)
    pd.DataFrame(out).to_csv(os.path.join(OUT, 'part1_variants.csv'), index=False); print('前 3 段', top[['side', 't_in', 't_out', 'net']].round(1).to_dict('records'))
