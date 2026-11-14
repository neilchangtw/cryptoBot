"""V74：多週期趨勢多單腿＋Q20W30 固定風險現行腿，1,500U、全倉 5 倍。登記：doc/research_results/v74/v74_plan.md。
用法：python backtest/research/v74_run.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v69a as A, v69b as B, v69_baseline as S69, v70_account as E, v70_recon as RC, v70_run as RN, v71_part1 as P1, v71_run as R71
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v74'); os.makedirs(OUT, exist_ok=True)
NS = (50, 100, 150, 200, 250); BASE_KW = dict(base_mode=('risk', 0.03, 5.0)); LATEST = '2026-11-13 23:00'
KEYS = ('版本', '淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '勝率', '筆數', '最大百分比回撤', '最低權益', 'MDD金額', '五年MDD', '最差滾動12月', '最差連敗段_五年口徑', '最長連敗', '強平觸及_全倉帳戶', '跳過', 'G全過', '未過項')

def setup(end):
    df = C.load_1h(end=end); fund = C.load_funding(); sim = A.build_sim(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20)
    px, dc = B.daily_close(df); st = S69.states(df); st['m'] = pd.Series(A.V66.vol_mult(df, 99.0), index=st.index)
    return dict(df=df, fund=fund, px=px, dc=dc, st=st, base=A.ref_book(df, ind0, sim, ones), q20=A.ref_book(df, ind0, sim, okq))

def ens_sig(dc, ns=NS, ls=False):
    """合議比例：收盤高於 N 日均線的個數 ÷ 個數（只做多）；ls=True 時為方向平均（-1~1）。任一均線暖機不足則無訊號。"""
    m = pd.concat([(dc > dc.rolling(n, min_periods=n).mean()).astype(float).where(dc.rolling(n, min_periods=n).mean().notna()) for n in ns], axis=1)
    return ((2 * m - 1) if ls else m).mean(axis=1, skipna=False)

def sl(sig, g=0.25, band=0.20): return dict(sig=sig, mode='rebal', g=g, band=band, lev=5.0)

def yearly(e, cap=E.CAP):
    ye = e.groupby(e.index.year).last(); y = ye.diff(); y.iloc[0] = ye.iloc[0] - cap; return {int(k): round(float(v), 1) for k, v in y.items()}

def weak(e):
    d = e.diff().fillna(e.iloc[0] - E.CAP); return dict(弱_2021=round(d['2021'].sum(), 1), 弱_2023=round(d['2023'].sum(), 1), 弱_2026下半=round(d['2026-07':'2026-10'].sum(), 1))

def brief(nm, R):
    e = R['acc'].equity; t = R['tr']; pk = np.maximum(e.cummax(), E.CAP); mx, c4 = R71.sstats(t) if len(t) else (0, 0)
    return dict(版本=nm, 截止=str(e.index[-1]), 淨利=round(e.iloc[-1] - E.CAP, 1), 逐年=yearly(e), 最低權益=round(e.min(), 1), 小時內保守最低=round(R['acc'].equity_intrahour.min(), 1), 最大百分比回撤=round(((pk - e) / pk).max() * 100, 1), MDD金額=round((pk - e).max(), 1),
                筆數=len(t), 勝率=round((t.net > 0).mean() * 100, 2) if len(t) else None, 最長連敗=mx, 強平=R['liq_acct'] if R['cross'] else int(t.liq.sum()), 跳過=len(R['skips']))

if __name__ == '__main__':
    X = setup('2026-11-01'); df, fund, dc = X['df'], X['fund'], X['dc']; sig = ens_sig(dc); rows = []; wr = []; keep = {}
    print('合議比例分布（2021-01~2026-10 日數）', sig['2021':'2026-10'].value_counts().sort_index().to_dict(), flush=True)
    def run(name, key, base='q20', sleeve=None, cross=True, mode=None, fixed=False, save=True):
        kw = dict(cross=cross, fixed_basis=fixed)
        if base: kw.update(base=X[base], base_mode=mode or ('risk', 0.03, 5.0))
        if sleeve: kw.update(sleeve=sleeve)
        R = E.replay(df, fund, **kw); R['tr'] = RC.tag(R['tr'], X['st']); d = E.summary(name, R, df); G = RN.gates(d); d['G全過'] = all(G.values()); d['未過項'] = '、'.join(k for k, v in G.items() if not v); d.update(weak(R['acc'].equity))
        t = R['tr']; s = t[t.leg == 'trend']; b = t[t.leg == 'base']; mx, c4 = R71.sstats(t); d.update(趨勢腿筆數=len(s), 趨勢腿淨=round(s.net.sum()) if len(s) else 0, 趨勢腿成交次數=int(s.nfill.sum() + len(s)) if len(s) else 0, 現行腿淨=round(b.net.sum()) if len(b) else 0, 連敗4以上段數=c4, PF=round(t[t.net > 0].net.sum() / -t[t.net < 0].net.sum(), 2))
        rows.append(d); keep[key] = R
        if save: t.to_csv(os.path.join(OUT, f'{key}_trades.csv'), index=False); R['acc'].to_csv(os.path.join(OUT, f'{key}_account_hourly.csv'))
        print(json.dumps({k: d.get(k) for k in KEYS + ('趨勢腿淨', '現行腿淨', '趨勢腿筆數', '趨勢腿成交次數', 'PF')}, ensure_ascii=False), flush=True); return R, d
    # 對照
    R0, d0 = run('現行原配置', 'orig', base='base', cross=False, mode=('fixed', 4000.0, 20.0)); BASE_NET = d0['淨利']
    Rq, dq = run('Q20W30＋固定風險', 'q20risk'); R7, d7 = run('V70', 'v70', sleeve=sl(RC.trend_sig(dc, 200)))
    # 主要
    Rm, dm = run('V74 主要（合議 50~250、只做多、25%、±20%）', 'v74_main', sleeve=sl(sig)); H1 = bool(dm['G全過'] and dm['淨利'] >= BASE_NET)
    print('H1', H1, '淨利對現行差額', dm['淨利'] - BASE_NET, '逐年差額', {y: dm[f'y{y}'] - d0[f'y{y}'] for y in range(2021, 2027)}, '合併部位檢查', E.merged_check(Rm, df))
    wr += RC.wr_tables('現行原配置', R0['tr'], 54.35) + RC.wr_tables('Q20W30＋固定風險', Rq['tr'], 54.35) + RC.wr_tables('V70', R7['tr'], 54.35) + RC.wr_tables('V74 主要', Rm['tr'], 54.35)
    # H2 鄰域
    nb = []
    for nm, key, kw in (('鄰域 均線組 50~200', 'nb_a', dict(sig=ens_sig(dc, (50, 100, 150, 200)))), ('鄰域 均線組 100~300', 'nb_b', dict(sig=ens_sig(dc, (100, 150, 200, 250, 300)))), ('鄰域 比例15%', 'nb_g15', dict(sig=sig, g=.15)), ('鄰域 比例35%', 'nb_g35', dict(sig=sig, g=.35)), ('鄰域 區間10%', 'nb_b10', dict(sig=sig, band=.10)), ('鄰域 區間30%', 'nb_b30', dict(sig=sig, band=.30))):
        _, d = run(nm, key, sleeve=sl(**kw), save=False); nb.append(d)
    h2y = sum(all(x[f'y{y}'] > 0 for y in range(2021, 2027)) for x in nb); h2a = sum(bool(x['G全過'] and x['淨利'] >= BASE_NET) for x in nb); H2 = h2y >= 5 and h2a >= 4; print('H2', H2, '年度過', h2y, '/6 全過', h2a, '/6')
    run('參考 多空合議版', 'ref_ls', sleeve=sl(ens_sig(dc, ls=True)), save=False); run('參考 只有趨勢腿', 'ref_trendonly', base=None, sleeve=sl(sig), save=False)
    # H4 集中度
    s = Rm['tr'][Rm['tr'].leg == 'trend']; top = s.sort_values('net', ascending=False).head(3); v3 = sig.copy()
    for r in top.itertuples(): v3[(v3.index >= r.t_in.floor('D')) & (v3.index < r.t_out.floor('D') + pd.Timedelta(days=1))] = 0.0
    _, d4 = run('H4 移除趨勢腿前 3 段後完整重播', 'h4', sleeve=sl(v3), save=False); H4 = bool(d4['趨勢腿淨'] > 0 and all(d4[f'y{y}'] > 0 for y in range(2021, 2027)))
    print('H4', H4, '前3段', top[['t_in', 't_out', 'net']].round(1).astype(str).to_dict('records'), '趨勢腿各段', len(s), '段，合計', round(s.net.sum()), '；前3段合計', round(top.net.sum()), '；其餘', round(s.net.sum() - top.net.sum()))
    # H5 固定資金基數
    _, f0 = run('固定基數：Q20W30＋固定風險', 'fx_q20', fixed=True, save=False); _, f1 = run('固定基數：V74 主要', 'fx_v74', sleeve=sl(sig), fixed=True, save=False); _, f2 = run('固定基數：只有趨勢腿', 'fx_trend', base=None, sleeve=sl(sig), fixed=True, save=False); _, f3 = run('固定基數：現行訊號全做（不暫停）固定風險', 'fx_all', base='base', fixed=True, save=False)
    inc = {k: round(f1[k] - f0[k], 1) for k in ('弱_2021', '弱_2023', '弱_2026下半')}; H5 = bool(all(v >= 0 for v in inc.values()) and sum(inc.values()) > 0); print('H5', H5, '弱勢期增量', inc, '固定基數淨利 對照/候選/只有趨勢腿', f0['淨利'], f1['淨利'], f2['淨利'], '；完整配置', dq['淨利'], dm['淨利'])
    C6 = dict(對照最長=f0['最長連敗'], 候選最長=f1['最長連敗'], 對照每百筆4以上=round(f0['連敗4以上段數'] / f0['筆數'] * 100, 2), 候選每百筆4以上=round(f1['連敗4以上段數'] / f1['筆數'] * 100, 2), 對照最差段=f0['最差連敗段_五年口徑'], 候選最差段=f1['最差連敗段_五年口徑'], 對照勝率=f0['勝率'], 候選勝率=f1['勝率'], 弱勢期對照={k: f0[k] for k in inc}, 弱勢期候選={k: f1[k] for k in inc})
    C6['C1'] = C6['候選最長'] <= 6; C6['C2'] = C6['候選每百筆4以上'] <= 1.8; C6['C3'] = C6['候選最差段'] >= 0.8 * C6['對照最差段']; C6['C5'] = all(f1[k] >= f0[k] for k in inc); C6['C6'] = bool(f1['勝率'] > 50 and f1['勝率'] >= f0['勝率'] + 3); print('連敗 C', json.dumps(C6, ensure_ascii=False, default=str))
    # H6 成本壓力
    hc = E.HC; E.HC = hc + 0.0005
    s0 = brief('成本壓力：現行原配置', E.replay(df, fund, base=X['base'], base_mode=('fixed', 4000.0, 20.0), cross=False)); s1 = brief('成本壓力：V74 主要', E.replay(df, fund, base=X['q20'], cross=True, sleeve=sl(sig), **BASE_KW)); sq = brief('成本壓力：Q20W30＋固定風險', E.replay(df, fund, base=X['q20'], cross=True, **BASE_KW)); E.HC = hc
    for b in (s0, sq, s1): print(json.dumps(b, ensure_ascii=False, default=str))
    H6 = bool(all(s1['逐年'][y] > 0 for y in range(2021, 2027)) and s1['淨利'] >= s0['淨利']); print('H6', H6); pd.DataFrame([s0, sq, s1]).to_csv(os.path.join(OUT, 'v74_cost_stress.csv'), index=False)
    # H3 啟動日
    sd = []
    for st in pd.date_range('2021-01-01', '2023-10-01', freq='MS'):
        en = st + pd.DateOffset(months=36) - pd.Timedelta(hours=1); kw = dict(base=X['q20'], cross=True, start=st, end=en, **BASE_KW); a = P1.quick(E.replay(df, fund, sleeve=sl(sig), **kw)); c = P1.quick(E.replay(df, fund, **kw))
        sd.append(dict(啟動日=str(st.date()), 啟動時比例=float(sig[st]), **{f'a_{k}': v for k, v in a.items()}, **{f'對照_{k}': v for k, v in c.items()}))
    T = pd.DataFrame(sd); T.to_csv(os.path.join(OUT, 'start_date_sensitivity.csv'), index=False); h3 = {}
    for p in ('a', '對照'): h3[p] = dict(第1年為正=round((T[f'{p}_第1年'] > 0).mean() * 100, 1), 三段全正=round(((T[f'{p}_第1年'] > 0) & (T[f'{p}_第2年'] > 0) & (T[f'{p}_第3年'] > 0)).mean() * 100, 1), 淨利中位=float(T[f'{p}_淨利'].median()), 淨利最低=float(T[f'{p}_淨利'].min()), 最低權益最小=float(T[f'{p}_最低權益'].min()), 最大回撤最大=float(T[f'{p}_最大百分比回撤'].max()))
    H3 = bool(h3['a']['第1年為正'] >= 80 and h3['a']['三段全正'] >= 70 and h3['a']['第1年為正'] > h3['對照']['第1年為正'] and h3['a']['三段全正'] > h3['對照']['三段全正']); print('H3', H3, json.dumps(h3, ensure_ascii=False))
    # 最新截止
    X2 = setup('2026-11-14'); L = []
    for nm, kw in (('現行原配置', dict(base=X2['base'], base_mode=('fixed', 4000.0, 20.0), cross=False)), ('Q20W30＋固定風險', dict(base=X2['q20'], cross=True, **BASE_KW)), ('V70', dict(base=X2['q20'], cross=True, sleeve=sl(RC.trend_sig(X2['dc'], 200)), **BASE_KW)), ('V74 主要', dict(base=X2['q20'], cross=True, sleeve=sl(ens_sig(X2['dc'])), **BASE_KW))):
        R = E.replay(X2['df'], X2['fund'], end=LATEST, **kw); b = brief(nm, R); L.append(b); print(json.dumps(b, ensure_ascii=False, default=str), flush=True)
        if nm == 'V74 主要': R['tr'].to_csv(os.path.join(OUT, 'v74_main_latest_trades.csv'), index=False); print('未平倉（截止時）', [(p['leg'], p['side'], round(p['q'], 3)) for p in R.get('open', [])])
    HL = bool(L[3]['淨利'] >= L[0]['淨利'] and L[3]['逐年'][2026] > 0 and L[3]['最低權益'] >= 1000 and L[3]['最大百分比回撤'] <= 40); print('最新截止條件', HL)
    # 逐月比較
    mo = pd.DataFrame({k: keep[k]['acc'].equity.diff().fillna(keep[k]['acc'].equity.iloc[0] - E.CAP).groupby(lambda t: t.strftime('%Y-%m')).sum().round(1) for k in ('orig', 'q20risk', 'v70', 'v74_main')}); mo['V74減現行'] = (mo.v74_main - mo.orig).round(1)
    for k in ('orig', 'v74_main'): t = keep[k]['tr']; g = t.groupby(t.t_out.dt.strftime('%Y-%m')).net.agg(n='count', w=lambda x: (x > 0).sum()); mo[f'{k}_筆數'] = g.n; mo[f'{k}_勝'] = g.w
    mo.fillna(0).to_csv(os.path.join(OUT, 'monthly_compare.csv')); print('月為正比例 現行/V74', round((mo.orig > 0).mean() * 100, 1), round((mo.v74_main > 0).mean() * 100, 1), '最差月', mo.orig.min(), mo.v74_main.min())
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'v74_summary.csv'), index=False); pd.DataFrame(wr).to_csv(os.path.join(OUT, 'v74_winrate.csv'), index=False); pd.DataFrame(L).to_csv(os.path.join(OUT, 'v74_latest_cutoff.csv'), index=False)
    V = dict(H1=H1, H2=H2, H3=H3, H4=H4, H5=H5, H6=H6, 最新截止=HL, 連敗=C6, 現行淨利=BASE_NET, H3明細=h3, 弱勢期增量=inc); json.dump(V, open(os.path.join(OUT, 'v74_verdict.json'), 'w'), ensure_ascii=False, indent=1, default=str); print('判定', json.dumps({k: V[k] for k in ('H1', 'H2', 'H3', 'H4', 'H5', 'H6', '最新截止')}))
