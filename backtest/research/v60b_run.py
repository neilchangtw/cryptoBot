"""V60-B：選擇權偏斜模組與現行策略共用額度（現行策略優先）的單一帳戶重播。預登記：doc/research_results/v60/v60b_plan.md
用法：python backtest/research/v60b_run.py [perm次數，預設200] [額外滑價bp，預設0]"""
import os, sys, json, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57_metrics as M, v57b_lowvol as V, v60_d0 as S
CUT = '2026-11-01'; STOP = 0.04; GRID = list(itertools.product(((7, 45), (20, 70)), (0.2, 0.3), (1, 3))); DEFAULT = ((7, 45), 0.2, 1)

def module_trades(sig, df, H, base, slip_bp=0.0):
    """sig：日訊號（index=決策日 UTC 00:00；+1 多、-1 空）。base：現行策略逐筆（side,t_in,t_out,ep）。現行策略優先：
    同向被佔用不進；持倉中現行策略同向進場則以其進場價讓出（YIELD）。"""
    ct = (df.datetime - pd.Timedelta(hours=7)).values; pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values; cl = df.close.values; n = len(cl); sl = slip_bp / 1e4
    occ = {s: np.zeros(n, bool) for s in 'LS'}; ent = {s: {} for s in 'LS'}
    for r in base.itertuples():
        a = pos.get(np.datetime64(r.t_in)); b = pos.get(np.datetime64(r.t_out))
        if a is None or b is None: continue
        occ[r.side][a:b + 1] = True; ent[r.side][a] = r.ep
    out = []; P = {'L': None, 'S': None}; stats = dict(blocked=0, yielded=0)
    def close(side, i, xp, reason):
        p = P[side]; xp = xp * (1 - sl) if side == 'L' else xp * (1 + sl)
        out.append(dict(side=side, t_in=pd.Timestamp(ct[p['i0']]), t_out=pd.Timestamp(ct[i]), ep=p['ep'], xp=xp, reason=reason)); P[side] = None
    def advance(side, upto):
        p = P[side]
        for j in range(p['chk'] + 1, upto + 1):
            if j in ent[side]: close(side, j, ent[side][j], 'YIELD'); stats['yielded'] += 1; return
            if side == 'L' and lo[j] <= p['ep'] * (1 - STOP):
                st = p['ep'] * (1 - STOP); close(side, j, st - 0.25 * (st - lo[j]), 'STOP'); return
            if side == 'S' and hi[j] >= p['ep'] * (1 + STOP):
                st = p['ep'] * (1 + STOP); close(side, j, st + 0.25 * (hi[j] - st), 'STOP'); return
        p['chk'] = upto
    for t, s in sig.items():
        k = np.datetime64(t + pd.Timedelta(hours=2))
        if k not in pos: continue
        i = pos[k]
        for side, want in (('L', 1), ('S', -1)):
            had = P[side] is not None
            if had: advance(side, i)
            closed_now = had and P[side] is None
            if P[side] is not None:
                if s == want: P[side]['x'] = i + 24 * H
                elif i >= P[side]['x']: close(side, i, cl[i], 'TIME')
            elif s == want and not closed_now:
                if occ[side][i]: stats['blocked'] += 1
                else:
                    ep = cl[i] * (1 + sl) if side == 'L' else cl[i] * (1 - sl); P[side] = dict(i0=i, ep=ep, x=i + 24 * H, chk=i)
    for side in 'LS':
        if P[side] is not None:
            advance(side, n - 1)
            if P[side] is not None: close(side, n - 1, cl[-1], 'END')
    return pd.DataFrame(out, columns=['side', 't_in', 't_out', 'ep', 'xp', 'reason']), stats

def quick_net(tr):
    return (np.where(tr.side == 'L', 1, -1) * 4000.0 / tr.ep * (tr.xp - tr.ep) - 9.0) if len(tr) else np.array([])

def pick_wf(trs):
    """trs：{組: 模組逐筆(含 net)}。每 6 個月以過去已平倉淨利重選；歷史不足 12 個月用預設。回傳前推逐筆與選用表。"""
    bounds = pd.date_range('2021-01-01', '2027-01-01', freq='6MS'); first = min((t.t_in.min() for t in trs.values() if len(t)), default=pd.Timestamp('2021-01-01')); parts = []; picks = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        k = DEFAULT if (a - first).days < 365 else max(GRID, key=lambda g: trs[g][trs[g].t_out < a].net.sum())
        c = trs[k]; parts.append(c[(c.t_in >= a) & (c.t_in < b)]); picks.append((str(a.date()), k))
    wf = pd.concat(parts).sort_values('t_in'); keep = []; free = {'L': pd.Timestamp('2000-01-01'), 'S': pd.Timestamp('2000-01-01')}
    for r in wf.itertuples():
        if r.t_in >= free[r.side]: keep.append(r.Index); free[r.side] = r.t_out
    return wf.loc[keep].reset_index(drop=True), picks

def all_modules(skews, df, base, slip=0.0, shift=0):
    trs = {}; st = {}
    for dte, q, H in GRID:
        # 補成完整日曆（缺偏斜值的日子訊號為 0），否則缺值日不會檢查到期出場
        s0 = S.signal(skews[dte], q=q); full = pd.date_range(s0.index.min(), s0.index.max(), freq='D'); v = s0.reindex(full).fillna(0).astype(int)
        sig = pd.Series(np.roll(v.values, shift) if shift else v.values, index=full)
        t, s = module_trades(sig[sig.index >= '2020-12-25'], df, H, base, slip); t['net'] = quick_net(t); trs[(dte, q, H)] = t; st[(dte, q, H)] = s
    return trs, st

if __name__ == '__main__':
    NP = int(sys.argv[1]) if len(sys.argv) > 1 else 200; slip = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0; tag = '' if slip == 0 else f'_slip{int(slip)}'
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v60'); df = C.load_1h(end=CUT); fund = C.load_funding(); sim = V.build_sim(); ones = np.ones(len(df), bool)
    inc0, c0 = V.run_masked(df, ones, ones, sim); okq = ~(V.vol_rank(df) < 20); incq, cq = V.run_masked(df, okq, okq, sim)
    R0, T0 = M.evaluate(inc0, c0, '基準'); RQ, TQ = M.evaluate(incq, cq, 'Q20W30'); print('基準', R0['total'], R0['trades'], '；Q20W30', RQ['total'], RQ['trades'])
    skews = {dte: S.daily_skew(dte) for dte in ((7, 45), (20, 70))}
    low = ['2021-11', '2021-12', '2022-04', '2022-05', '2022-11', '2022-12'] + [f'2023-{i:02d}' for i in range(1, 11)] + ['2025-10', '2026-05', '2026-06', '2026-08', '2026-09', '2026-10']
    summary = {}
    for name, (incb, cb, Rb) in {'A_基準＋模組': (inc0, c0, R0), 'B_Q20W30＋模組': (incq, cq, RQ)}.items():
        trs, st = all_modules(skews, df, cb, slip); rows = []
        for g, t in trs.items():
            x = t[(t.t_out >= '2021-01-01') & (t.t_out < CUT)]; y = x.groupby(x.t_out.dt.year).net.sum()
            rows.append(dict(dte=f'{g[0][0]}~{g[0][1]}', q=g[1], H=g[2], trades=len(x), wr=round((x.net > 0).mean(), 3), stop=round((x.reason == 'STOP').mean(), 2), yielded=st[g]['yielded'], blocked=st[g]['blocked'],
                             **{f'y{k}': round(y.get(k, 0)) for k in range(2021, 2027)}, p2123=round(y.reindex([2021, 2022, 2023]).sum()), p2426=round(y.reindex([2024, 2025, 2026]).sum()), L=round(x[x.side == 'L'].net.sum()), S=round(x[x.side == 'S'].net.sum()), total=round(x.net.sum())))
        G = pd.DataFrame(rows); print(f'\n=== {name}：模組 8 組（共用額度後、不含 funding）==='); print(G.to_string(index=False)); G.to_csv(os.path.join(OUT, f'v60b_{name[0]}_grid{tag}.csv'), index=False)
        wf, picks = pick_wf(trs); print('前推選用', picks)
        for label, mod in (('前推', wf), ('預設組', trs[DEFAULT])):
            incm, cm = C.mtm_equity(mod[['side', 't_in', 't_out', 'ep', 'xp']], df, fund); cm['reason'] = mod.reason.values; cm['src'] = 'skew'; cb2 = cb.copy(); cb2['src'] = 'base'
            inc = incb.add(incm, fill_value=0.0); cost = pd.concat([cb2, cm], ignore_index=True); R, T = M.evaluate(inc, cost, f'{name}（{label}）'); J, gain = V.judge(R, Rb)
            mm = incm.groupby(incm.index.to_period('M')).sum(); mm = mm[(mm.index >= '2021-01') & (mm.index <= '2026-10')]; ym = mm.groupby(mm.index.year).sum().round(0).to_dict(); lowinc = mm[mm.index.astype(str).isin(low)].sum()
            x = cm[(cm.t_out >= '2021-01-01') & (cm.t_out < CUT)]
            print(f'\n--- {name}（{label}）---'); print(J.to_string(index=False)); print('年度', R['yearly']); print('模組增量：年度', ym, '合計', round(mm.sum()), '低波動月', round(lowinc), '筆數', len(x), '勝率', round((x.net > 0).mean(), 3), 'funding', round(x.funding.sum()), '出場', x.reason.value_counts().to_dict())
            print(T['year'].to_string()); print(T['side'].to_string())
            summary[f'{name}|{label}'] = dict(metrics=R, gain=gain, module_yearly=ym, module_total=round(mm.sum()), module_lowvol=round(lowinc), module_trades=len(x), picks=picks if label == '前推' else None, passed_all=bool(J.passed.all()))
            k = f'{name[0]}_{"wf" if label == "前推" else "default"}{tag}'; J.to_csv(os.path.join(OUT, f'v60b_{k}_judgement.csv'), index=False); T['monthly'].round(2).to_csv(os.path.join(OUT, f'v60b_{k}_monthly.csv')); T['r12'].round(2).to_csv(os.path.join(OUT, f'v60b_{k}_rolling12.csv')); T['year_side'].to_csv(os.path.join(OUT, f'v60b_{k}_year_side.csv')); cost.to_csv(os.path.join(OUT, f'v60b_{k}_trades.csv'), index=False); T['streaks'].to_csv(os.path.join(OUT, f'v60b_{k}_streaks.csv'), index=False)
        if name[0] == 'A' and NP > 0:
            real = wf[(wf.t_out >= '2021-01-01') & (wf.t_out < CUT)].net.sum(); rng = np.random.default_rng(60); perm = []
            for _ in range(NP):
                tp, _s = all_modules(skews, df, cb, slip, shift=int(rng.integers(30, 1500))); w, _p = pick_wf(tp); perm.append(w[(w.t_out >= '2021-01-01') & (w.t_out < CUT)].net.sum())
            p = float((np.array(perm) >= real).mean()); print(f'\n置換對照（{NP} 次，重跑 8 組＋前推＋共用額度）：真實模組增量 {round(real)}，對照中位 {round(np.median(perm))}，第 95 百分位 {round(np.quantile(perm, .95))}，p={p}')
            summary['perm'] = dict(real=round(real), median=round(float(np.median(perm))), p95=round(float(np.quantile(perm, .95))), p=p, n=NP)
    json.dump(summary, open(os.path.join(OUT, f'v60b_summary{tag}.json'), 'w'), ensure_ascii=False, indent=1, default=str)
