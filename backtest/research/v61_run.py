"""V61：偏斜模組＋低槓桿＋獨立額度。預登記：doc/research_results/v61/v61_plan.md
用法：python backtest/research/v61_run.py [置換次數，預設200]"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57_metrics as M, v57b_lowvol as V, v60_d0 as S, v60b_run as B
CUT = '2026-11-01'; MARGIN = 300.0; CFG = {'S4': 0.04, 'S8': 0.08, 'S12': 0.12}; MMR = 0.005
LOW = ['2021-11', '2021-12', '2022-04', '2022-05', '2022-11', '2022-12'] + [f'2023-{i:02d}' for i in range(1, 11)] + ['2025-10', '2026-05', '2026-06', '2026-08', '2026-09', '2026-10']

def cal_sig(sk, shift=0):
    s0 = S.signal(sk, q=0.2); full = pd.date_range(s0.index.min(), s0.index.max(), freq='D'); v = s0.reindex(full).fillna(0).astype(int).values
    sig = pd.Series(np.roll(v, shift) if shift else v, index=full); return sig[sig.index >= '2020-12-25']

def mod(sig, df, stop, base, slip=0.0, ntl=None):
    B.STOP = stop; t, st = B.module_trades(sig, df, 1, base, slip)
    t['ntl'] = ntl if ntl else MARGIN * 0.5 / stop; t['net'] = np.where(t.side == 'L', 1, -1) * t.ntl / t.ep * (t.xp - t.ep) - 9.0 * t.ntl / 4000; return t, st

def wf(trs):
    """每 6 個月以過去已平倉模組淨利重選；不足 12 個月用 S8。"""
    out = []; picks = []
    for a in pd.date_range('2021-01-01', '2026-07-01', freq='6MS'):
        b = a + pd.DateOffset(months=6); k = 'S8'
        if a >= pd.Timestamp('2022-01-01'):
            sc = {c: t[t.t_out < a].net.sum() for c, t in trs.items()}; k = max(sc, key=sc.get)
        picks.append((str(a.date()), k)); t = trs[k]; out.append(t[(t.t_in >= a) & (t.t_in < b)].assign(cfg=k))
    return pd.concat(out).sort_values('t_in').reset_index(drop=True), picks

def liq_check(t, df):
    ct = (df.datetime - pd.Timedelta(hours=7)).values; pos = {x: i for i, x in enumerate(ct)}; hi = df.high.values; lo = df.low.values; n = 0; worst = 0
    for r in t.itertuples():
        L = r.ntl / MARGIN; d = 1 / L - MMR; i0 = pos[np.datetime64(r.t_in)]; i1 = pos[np.datetime64(r.t_out)]
        if i1 > i0:
            adv = (r.ep - lo[i0 + 1:i1 + 1].min()) / r.ep if r.side == 'L' else (hi[i0 + 1:i1 + 1].max() - r.ep) / r.ep
            n += adv >= d
        worst = min(worst, r.net)
    return int(n), round(worst / MARGIN, 3)

def margin_use(costs, df):
    idx = C.close_utc(df).index; pos = {t: i for i, t in enumerate(idx)}; m = np.zeros(len(idx)); e = np.zeros(len(idx))
    for c, mg in costs:
        for r in c.itertuples():
            a = pos[r.t_in]; b = pos[r.t_out]; m[a:b] += mg; e[a:b] += getattr(r, 'ntl', 4000.0)
    return pd.Series(m, index=idx), pd.Series(e, index=idx)

def report(name, inc, cost, Rb, mods, mg, ex, OUT, tag):
    R, T = M.evaluate(inc, cost, name); J, G = V.judge(R, Rb); J.to_csv(os.path.join(OUT, f'v61_{tag}_judgement.csv'), index=False)
    for k in ('monthly', 'year_side', 'r12', 'streaks'): T[k].to_csv(os.path.join(OUT, f'v61_{tag}_{k}.csv'))
    inc = inc['2021-01-01':'2026-10-31']; mg = mg['2021-01-01':'2026-10-31']; ex = ex['2021-01-01':'2026-10-31']; eq = inc.cumsum(); need = (mg - eq).max(); mon = inc.groupby(inc.index.to_period('M')).sum()
    d = dict(name=name, total=round(R['total']), yearly={int(k): round(v) for k, v in R['yearly'].items()}, wr=round(R['wr'], 4), trades=R['trades'], mdd_5y=round(R['mdd_5y']), mdd_all=round((eq.cummax() - eq).max()), r12_worst=round(R['r12_worst']),
             worst_month=(str(mon.idxmin()), round(mon.min())), streak_worst=round(R['streak_worst_loss']), stab_pass=f"{int(J.passed.astype(str).eq('True').sum())}/{len(J)}",
             max_margin=round(mg.max()), max_notional=round(ex.max()), avg_notional=round(ex.mean()), min_capital=round(need))
    print(json.dumps(d, ensure_ascii=False)); return d, R

if __name__ == '__main__':
    NP = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v61'); df = C.load_1h(end=CUT); fund = C.load_funding(); sim = V.build_sim(); ones = np.ones(len(df), bool)
    inc0, c0 = V.run_masked(df, ones, ones, sim); okq = ~(V.vol_rank(df) < 20); incq, cq = V.run_masked(df, okq, okq, sim)
    R0, _ = M.evaluate(inc0, c0, '基準'); RQ, _ = M.evaluate(incq, cq, 'Q20W30'); empty = c0.iloc[0:0]
    sk = S.daily_skew((7, 45)); sig = cal_sig(sk); summ = {}
    # 2.5 診斷拆解（4,000U 名目）
    rows = []
    for slot, base in (('獨立', empty), ('讓位', c0)):
        for st in (9.9, 0.04, 0.08, 0.12):
            t, s = mod(sig, df, st, base, ntl=4000.0); y = t.groupby(t.t_out.dt.year).net.sum().round().to_dict()
            rows.append(dict(額度=slot, 止損='無' if st > 1 else f'{st:.0%}', 筆數=len(t), 淨=round(t.net.sum()), 多=round(t.net[t.side == 'L'].sum()), 空=round(t.net[t.side == 'S'].sum()), 止損筆=int((t.reason == 'STOP').sum()), 讓位筆=s['yielded'], **{f'y{k}': v for k, v in y.items()}))
    dec = pd.DataFrame(rows); dec.to_csv(os.path.join(OUT, 'v61_decomposition.csv'), index=False); print('=== 診斷拆解（4,000U 名目，不含 funding）===\n' + dec.to_string(index=False))
    # 對照
    for nm, inc, c in (('基準', inc0, c0), ('Q20W30', incq, cq)):
        mg, ex = margin_use([(c, 200.0)], df); summ[nm], _ = report(nm, inc, c, R0, None, mg, ex, OUT, nm)
    # 2.2 配置 × 滑價
    for slip in (0.0, 2.0, 5.0):
        trs = {k: mod(sig, df, s, empty, slip)[0] for k, s in CFG.items()}; w, picks = wf(trs); trs['WF'] = w
        if slip == 0: print('前推選用', picks)
        for k, t in trs.items():
            im, cm = C.mtm_equity(t, df, fund); cm['reason'] = t.reason.values; cm['ntl'] = t.ntl.values; mi = im.groupby(im.index.to_period('M')).sum(); yi = im.groupby(im.index.year).sum().round().to_dict()
            lq = liq_check(t, df); mgm, exm = margin_use([(cm, MARGIN)], df)
            md = dict(cfg=k, slip=slip, trades=len(t), wr=round((cm.net > 0).mean(), 4), net=round(im.sum()), yearly=yi, low=round(mi[mi.index.astype(str).isin(LOW)].sum()), p2123=round(im[:'2023'].sum()), p2426=round(im['2024':].sum()),
                      funding=round(cm.funding.sum()), cost=round((cm.fee + cm.extra).sum()), stops=int((t.reason == 'STOP').sum()), liq_touch=lq[0], worst_trade_vs_margin=lq[1], mdd=round((im.cumsum().cummax() - im.cumsum()).max()), avg_notional=round(exm.mean()))
            print('模組', json.dumps(md, ensure_ascii=False)); summ[f'mod_{k}_slip{int(slip)}'] = md
            if slip == 0: cm.to_csv(os.path.join(OUT, f'v61_module_{k}_trades.csv'), index=False); im.to_csv(os.path.join(OUT, f'v61_module_{k}_hourly.csv'))
            for cn, (ib, cb, Rb) in {'A': (inc0, c0, R0), 'B': (incq, cq, RQ)}.items():
                if slip > 0 and k != 'WF': continue
                allc = pd.concat([cb, cm], ignore_index=True); mg, ex = margin_use([(cb, 200.0), (cm, MARGIN)], df)
                summ[f'{cn}_{k}_slip{int(slip)}'], _ = report(f'{cn}＋模組 {k} 滑價{int(slip)}bp', ib.add(im, fill_value=0), allc, Rb, None, mg, ex, OUT, f'{cn}_{k}_slip{int(slip)}')
            if slip == 0 and k == 'WF':
                mgB, exB = margin_use([(cq, 200.0), (cm, MARGIN)], df); _, exq = margin_use([(cq, 200.0)], df); W = slice('2021-01-01', '2026-10-31'); f = exB[W].mean() / exq[W].mean(); sc = (incq * f)[W]; eq = sc.cumsum()
                summ['ctrl_scaled_Q20W30'] = dict(factor=round(f, 3), total=round(sc.sum()), yearly=sc.groupby(sc.index.year).sum().round().to_dict(), mdd=round((eq.cummax() - eq).max())); print('等曝險放大 Q20W30', summ['ctrl_scaled_Q20W30'])
    # 2.6(a) 置換
    if NP:
        rng = np.random.default_rng(61); real = {k: summ[f'mod_{k}_slip0']['net'] for k in ('S4', 'S8', 'S12', 'WF')}; pm = {k: [] for k in real}; n = len(pd.date_range(sk.index.min(), sk.index.max(), freq='D'))
        for _ in range(NP):
            sh = int(rng.integers(30, n - 30)); sg = cal_sig(sk, sh); trs = {k: mod(sg, df, s, empty)[0] for k, s in CFG.items()}; trs['WF'] = wf(trs)[0]
            for k, t in trs.items(): pm[k].append(t.net.sum())
        realq = {}; trs = {k: mod(sig, df, s, empty)[0] for k, s in CFG.items()}; trs['WF'] = wf(trs)[0]
        summ['perm'] = {k: dict(real_quick=round(trs[k].net.sum()), p95=round(np.quantile(pm[k], .95)), median=round(np.median(pm[k])), p=round((np.array(pm[k]) >= trs[k].net.sum()).mean(), 3)) for k in pm}; print('置換（不含 funding）', summ['perm'])
    json.dump(summ, open(os.path.join(OUT, 'v61_summary.json'), 'w'), ensure_ascii=False, indent=1, default=str)
