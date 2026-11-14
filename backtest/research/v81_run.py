"""V81：延遲 0／1／2／5／15 分鐘 × 四相位 × 三種重播（4,000U 固定名目）。替換引擎與獨立事件帳本逐筆核對後才輸出。
用法：python backtest/research/v81_run.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v81_ledger as Z, v80_lib as L, v79_lib as L9, v53_common as C
OUT = U.OUT; NM = dict(L='多方單獨', S='空方單獨', LS='L＋S'); pd.set_option('display.width', 330); pd.set_option('display.max_columns', 80); pd.set_option('display.max_rows', 400)

def boot(x, block=4, n=5000, seed=81):
    x = np.asarray(x, float); Ln = len(x); rng = np.random.default_rng(seed); k = int(np.ceil(Ln / block)); st = rng.integers(0, Ln - block + 1, (n, k)); idx = (st[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :Ln]
    bm = x[idx].mean(axis=1); m = x.mean(); return dict(週數=Ln, 合計=round(float(x.sum())), 尾端比例_未置中=round(float((bm <= 0).mean()), 4), p_置中虛無=round(float(((bm - m) >= m).mean()), 4), 合計90區間=[round(float(np.quantile(bm, .05) * Ln)), round(float(np.quantile(bm, .95) * Ln))])

def same(a, b):
    a = a[a.exit_reason != 'OPEN']; b = b[(b.exit_reason != 'OPEN') & (b.t_sig >= U.W0) & (b.t_out <= U.W1) & (b.t_in <= U.W1)].sort_values(['t_in', 'side']).reset_index(drop=True)
    if len(a) != len(b): return False, len(a), len(b), np.nan
    ok = (a.side.values == b.side.values).all() and (a.t_sig.values == b.t_sig.values).all() and (a.t_in.values == b.t_in.values).all() and (a.t_out.values == b.t_out.values).all() and (a.exit_reason.values == b.exit_reason.values).all()
    dp = max(np.abs(a.entry_price.values - np.round(b.entry_price.values, 2)).max(), np.abs(a.exit_price.values - np.round(b.exit_price.values, 2)).max()) if len(a) else 0.0
    return bool(ok and dp < 0.006), len(a), len(b), float(dp)

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); M = U.m1(); A = {}; IND = {ph: L9.indicators(P[ph]) for ph in U.PH}; B = {}; rows = []; xc = []; eqs = {}
    for k in U.DELAYS:
        for ph in U.PH:
            for side in ('L', 'S', 'LS'):
                t = U.book(P[ph], IND[ph], k, side, fund=fund); z, ev, cand, _ = Z.run(P[ph], IND[ph], M, k, sides=tuple(side)); ok, na, nb, dp = same(t, z); xc.append(dict(情境=f'D{k}', 相位=ph, 重播=NM[side], 替換引擎筆=na, 事件帳本筆=nb, 逐筆相同=ok, 最大價差=dp))
                t['ph'] = ph; t['mode'] = side; B[(k, ph, side)] = t; rows.append(dict(情境=f'D{k}', 相位=f':{ph:02d}', 重播=NM[side], 兩套實作逐筆相同=ok, **L.metrics(t, d15, ct15))); eqs[f'D{k}_{ph:02d}_{side}'] = L.mtm_hourly(t, d15, ct15).round(2)
                if ph == 0 and side == 'LS':
                    pd.DataFrame(ev, columns=['時刻UTC', '邊', '事件', '說明', '價格']).to_csv(os.path.join(OUT, f'events_ph00_D{k}.csv.gz'), index=False); cand.to_csv(os.path.join(OUT, f'candidates_ph00_D{k}.csv.gz'), index=False)
                    z[(z.t_sig >= U.W0)].to_csv(os.path.join(OUT, f'ledger_trades_ph00_D{k}.csv'), index=False)
    # 對照：D0′（k=0 延後入帳）、D15 即時入帳（V80 語意）
    ctl = []
    for ph in U.PH:
        for side in ('L', 'S', 'LS'):
            for nm, k, df_ in (("D0′（k=0、延後入帳）", 0, True), ('D15（V80 語意：成交損益立即入帳）', 15, False)):
                t = U.book(P[ph], IND[ph], k, side, fund=fund, defer=df_); z, *_ = Z.run(P[ph], IND[ph], M, k, sides=tuple(side), defer=df_); ok, na, nb, dp = same(t, z); xc.append(dict(情境=nm, 相位=ph, 重播=NM[side], 替換引擎筆=na, 事件帳本筆=nb, 逐筆相同=ok, 最大價差=dp))
                main = B[(k, ph, side)]; c = L.closed(t); ctl.append(dict(對照=nm, 相位=f':{ph:02d}', 重播=NM[side], 筆數=len(c), 淨利=round(c.net.sum()), 主語意筆數=len(L.closed(main)), 主語意淨利=round(L.closed(main).net.sum()), 與主語意逐筆相同=bool(len(c) == len(L.closed(main)) and np.allclose(c.net.values, L.closed(main).net.values))))
            f2 = L.book(d15, ct15, P[ph], K[ph], IND[ph], 'F2', side, fund=fund); c = L.closed(f2); d = L.closed(B[(15, ph, side)]); j = c.merge(d, on=['t_sig', 'side'], how='outer', suffixes=('', '_m'), indicator=True)
            ctl.append(dict(對照='V80 F2（15 分 K 版）', 相位=f':{ph:02d}', 重播=NM[side], 筆數=len(c), 淨利=round(c.net.sum()), 主語意筆數=len(d), 主語意淨利=round(d.net.sum()), 與主語意逐筆相同=bool((j._merge == 'both').all() and np.allclose(j.net, j.net_m))))
    XC = pd.DataFrame(xc); XC.to_csv(os.path.join(OUT, 'impl_crosscheck.csv'), index=False); A['兩套實作'] = dict(組數=len(XC), 逐筆相同組數=int(XC.逐筆相同.sum()), 最大價差=float(XC.最大價差.max())); print(A['兩套實作'])
    CT = pd.DataFrame(ctl); CT.to_csv(os.path.join(OUT, 'controls.csv'), index=False); print(CT.to_string(index=False))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'delay_phase_side_table.csv'), index=False); pd.concat(B.values()).to_csv(os.path.join(OUT, 'delay_trades.csv.gz'), index=False); pd.DataFrame(eqs).to_csv(os.path.join(OUT, 'delay_equity_hourly.csv.gz'))
    cols = ['情境', '相位', '重播', '筆數', '勝', '敗', '平', '勝率', '淨利', '平均贏', '平均輸', '每筆淨利', 'MH筆', 'MH淨', '止損筆', '止損淨', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '研究期內', '研究期外', '最長連敗', '最差連敗段', '最差滾動12月_五年口徑', '回撤_五年已實現口徑', '回撤_全期逐時市價權益', '期末未平倉筆']; print(T[cols].to_string(index=False))
    mo = pd.DataFrame({f'D{k}_{ph:02d}_{s}': L.closed(t).groupby(L.closed(t).t_out.dt.strftime('%Y-%m')).net.sum().round(1) for (k, ph, s), t in B.items()}).reindex([str(p) for p in pd.period_range('2021-01', '2026-10', freq='M')]); mo.to_csv(os.path.join(OUT, 'delay_monthly.csv'))
    A['零交易月_相位0_LS'] = {f'D{k}': int(mo[f'D{k}_00_LS'].isna().sum()) for k in U.DELAYS}
    yw = []
    for (k, ph, s), t in B.items():
        c = L.closed(t)
        for y, g in c.groupby('year'): yw.append(dict(情境=f'D{k}', 相位=f':{ph:02d}', 重播=NM[s], 年=int(y), 筆數=len(g), 勝=int((g.net > 0).sum()), 敗=int((g.net < 0).sum()), 平=int((g.net == 0).sum()), 勝率=round((g.net > 0).mean() * 100, 2), 淨利=round(g.net.sum())))
    pd.DataFrame(yw).to_csv(os.path.join(OUT, 'delay_yearly_wl.csv'), index=False)
    # ---- 進場價位移與貢獻拆解（對 D0）
    ep = []; dec = []
    for ph in U.PH:
        for side in ('L', 'S', 'LS'):
            a = L.closed(B[(0, ph, side)])
            for k in U.DELAYS[1:]:
                b = L.closed(B[(k, ph, side)]); j = a.merge(b, on=['t_sig', 'side'], suffixes=('', '_m')); sg = np.where(j.side == 'L', 1.0, -1.0); sa = np.where(a.side == 'L', 1.0, -1.0)
                pk = U.px_at(a.t_sig, k); swap_all = float((a.ntl * sa * (a.exit_price / pk - a.exit_price / a.entry_price)).sum()); ent = float((j.ntl * sg * (j.exit_price / j.entry_price_m - j.exit_price / j.entry_price)).sum()); tot = float(b.net.sum() - a.net.sum()); md = float((j.net_m - j.net).sum())
                only_a = a[~a.set_index(['t_sig', 'side']).index.isin(j.set_index(['t_sig', 'side']).index)]; only_b = b[~b.set_index(['t_sig', 'side']).index.isin(j.set_index(['t_sig', 'side']).index)]
                dec.append(dict(相位=f':{ph:02d}', 重播=NM[side], 情境=f'D{k}', D0淨利=round(a.net.sum()), 情境淨利=round(b.net.sum()), 總差=round(tot), 同訊號筆=len(j), 同訊號_進場價位移=round(ent), 同訊號_出場與路徑=round(md - ent), 只在D0的交易筆=len(only_a), 只在D0的淨=round(only_a.net.sum()), 只在情境的交易筆=len(only_b), 只在情境的淨=round(only_b.net.sum()), 交易集合變動=round(only_b.net.sum() - only_a.net.sum()), 參考_D0全部交易只換進場價=round(swap_all)))
                if side != 'LS': ep.append(dict(相位=f':{ph:02d}', 邊=side, 情境=f'D{k}', D0筆=len(a), 情境筆=len(b), 同訊號筆=len(j), 平均進場價不利bp=round(float((sg * (j.entry_price_m / j.entry_price - 1)).mean() * 1e4), 2), 中位數bp=round(float(np.median(sg * (j.entry_price_m / j.entry_price - 1)) * 1e4), 2), 同訊號出場類型相同比例=round(float((j.exit_reason == j.exit_reason_m).mean()), 3)))
    DC = pd.DataFrame(dec); DC.to_csv(os.path.join(OUT, 'decomp.csv'), index=False); EP = pd.DataFrame(ep); EP.to_csv(os.path.join(OUT, 'delay_entry_shift.csv'), index=False); print(DC[DC.相位 == ':00'].to_string(index=False)); print(EP.to_string(index=False))
    # ---- 整點後各分鐘的平均漂移（所有相位 0 的 D0 進場訊號；描述用）
    dr = []
    for ph in U.PH:
        for side in ('L', 'S'):
            a = L.closed(B[(0, ph, side)]); sg = 1.0 if side == 'L' else -1.0
            dr.append(dict(相位=f':{ph:02d}', 邊=side, 筆數=len(a), **{f'T+{k}分_bp': round(float((sg * (U.px_at(a.t_sig, k) / a.entry_price - 1)).mean() * 1e4), 2) for k in (1, 2, 5, 15, 30, 60)}))
    DR = pd.DataFrame(dr); DR.to_csv(os.path.join(OUT, 'post_signal_drift.csv'), index=False); print(DR.to_string(index=False))
    # ---- 歧義與資料限制
    amb = {}
    for k in U.DELAYS:
        t = B[(k, 0, 'LS')]; c = L.closed(t); i0 = U.mi(c.t_in); fl = M['low'][i0 - 1]; fh = M['high'][i0 - 1]; stp = np.where(c.side == 'L', c.entry_price * (1 - L.eng.L_SN), c.entry_price * (1 + L.eng.S_SN))
        zin = int((M['volume'][U.mi(c.t_in) - 1] == 0).sum()) if k > 0 else 0; zout = int((M['volume'][U.mi(c.t_out) - 1] == 0).sum())
        amb[f'D{k}'] = dict(等待期間改為止損出場筆=int(c.dsn.sum()), 成交那一分鐘極值已越過止損價筆=int(np.where(c.side == 'L', fl <= stp, fh >= stp).sum()), 成交分鐘為零成交量_進場=zin, 成交分鐘為零成交量_出場=zout)
    A['歧義與資料限制_相位0_LS'] = amb; print(amb)
    bad = pd.read_csv(os.path.join(OUT, 'data_15m_mismatch.csv')); bt = pd.to_datetime(bad.t); hit = []
    for k in U.DELAYS:
        for ph in U.PH:
            t = B[(k, ph, 'LS')]
            for b0 in bt:
                h = t[(t.t_sig <= b0 + pd.Timedelta(minutes=15)) & (t.t_out >= b0)]
                if len(h): hit.append(dict(情境=f'D{k}', 相位=ph, 不一致15分K開盤UTC=str(b0), 涵蓋交易=';'.join(f'{r.side}@{str(r.t_in)[:16]}→{r.exit_reason}' for r in h.itertuples())))
    pd.DataFrame(hit).to_csv(os.path.join(OUT, 'mismatch_bars_in_trades.csv'), index=False); A['15分K與1分K不一致的K落在持倉期間'] = len(hit); print('不一致K落在持倉期間', len(hit))
    # ---- J2：成對每週差值
    wi = pd.period_range('2021-01-04', '2026-11-01', freq='W-SUN'); per = np.where(wi.end_time < '2024-07-01', 'A', np.where(wi.end_time < '2026-05-01', 'B', 'C')); J2 = {}
    for k in U.DELAYS:
        for side in ('L', 'S'):
            W = pd.DataFrame({ph: L.closed(B[(k, ph, side)]).pipe(lambda c: c.groupby(c.t_out.dt.to_period('W-SUN')).net.sum()).reindex(wi).fillna(0.0) for ph in U.PH}); Dw = (W[0] - W[[15, 30, 45]].mean(axis=1)).values
            J2[f'D{k}_{side}'] = dict(全期=boot(Dw), 研究期內=boot(Dw[per == 'B']), 研究期外=boot(Dw[per != 'B']))
    A['J2_每週差值'] = J2
    print(pd.DataFrame([dict(組=kk, **{f'{p}_{q}': v[p][q] for p in ('全期', '研究期內', '研究期外') for q in ('合計', '合計90區間', 'p_置中虛無')}) for kk, v in J2.items()]).to_string(index=False))
    g = lambda k, ph, s, c='淨利': float(T[(T.情境 == f'D{k}') & (T.相位 == f':{ph:02d}') & (T.重播 == NM[s])][c].iloc[0]); n0 = g(0, 0, 'LS')
    def lab(d): return '高度敏感' if d < -1000 else ('不敏感' if abs(d) <= 300 else '中度')
    J = dict(J1={f'D{k}': dict(淨利=g(k, 0, 'LS'), 對D0差=g(k, 0, 'LS') - n0, 判定=lab(g(k, 0, 'LS') - n0)) for k in U.DELAYS[1:]}, D0=n0,
             J2_須更正V79=bool(any(J2[f'D{k}_L']['研究期外']['合計90區間'][0] > 0 for k in U.DELAYS)),
             J4=dict(整點三組_D15主語意=[g(15, 0, s) for s in ('L', 'S', 'LS')], V80_F2=[-300, 2403, 2103]),
             各相位={f'D{k}': dict(多方=[g(k, ph, 'L') for ph in U.PH], 空方=[g(k, ph, 'S') for ph in U.PH], LS=[g(k, ph, 'LS') for ph in U.PH]) for k in U.DELAYS})
    A['判定'] = J; print(json.dumps(J, ensure_ascii=False, indent=1)); json.dump(A, open(os.path.join(OUT, 'v81_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str)
