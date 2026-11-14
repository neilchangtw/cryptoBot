"""V80：資料與成交稽核、F0／F1／F2 × 四相位 × 三種重播（完整狀態機）、期末未平倉、同根歧義、成對每週差值。
用法：python backtest/research/v80_run.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C
OUT = L.OUT; pd.set_option('display.width', 330); pd.set_option('display.max_columns', 70); pd.set_option('display.max_rows', 300); E = L.eng
NM = dict(L='多方單獨', S='空方單獨', LS='L＋S')

def boot(x, block=4, n=5000, seed=80):
    """區塊拔靴：回傳平均、未置中重抽的尾端比例（重抽平均≤0 的比例）、置中後虛無檢定 p（重抽平均≥觀測）、90% 區間（合計）。"""
    x = np.asarray(x, float); Ln = len(x); rng = np.random.default_rng(seed); k = int(np.ceil(Ln / block)); st = rng.integers(0, Ln - block + 1, (n, k)); idx = (st[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :Ln]
    bm = x[idx].mean(axis=1); m = x.mean(); return dict(週數=Ln, 合計=round(float(x.sum())), 每週平均=round(float(m), 2), 尾端比例_未置中=round(float((bm <= 0).mean()), 4), p_置中虛無=round(float(((bm - m) >= m).mean()), 4), 合計90區間=[round(float(np.quantile(bm, .05) * Ln)), round(float(np.quantile(bm, .95) * Ln))])

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); A = {}; IND = {ph: L9.indicators(P[ph]) for ph in L.PH}
    # ---- 資料稽核
    full = d15; g = full.datetime.diff().dropna(); z = full[full.volume <= 0]
    A['資料'] = dict(根數=len(full), 重複時間=int(full.datetime.duplicated().sum()), 非15分間隔=int((g != pd.Timedelta(minutes=15)).sum()), 起訖UTC8=[str(full.datetime.min()), str(full.datetime.max())], 高低矛盾=int(((full.high < full[['open', 'close']].max(axis=1)) | (full.low > full[['open', 'close']].min(axis=1))).sum()), 零成交量根=[str(x) for x in z.datetime], 零成交量根是否平盤=[bool(r.high == r.low) for r in z.itertuples()],
                   各相位=dict((ph, dict(根數=len(P[ph]), 末根收盤UTC=str(P[ph].datetime.iloc[-1] - pd.Timedelta(hours=7)), 首根可交易UTC=str(P[ph].datetime.iloc[E.WARMUP] - pd.Timedelta(hours=7)))) for ph in L.PH))
    # ---- 36 組重播
    B = {}; rows = []; eqs = {}
    for m in L.MODELS:
        for ph in L.PH:
            for side in ('L', 'S', 'LS'):
                t = L.book(d15, ct15, P[ph], K[ph], IND[ph], m, side, fund=fund); t['ph'] = ph; t['mode'] = side; B[(m, ph, side)] = t
                rows.append(dict(成交模型=m, 相位=f':{ph:02d}', 重播=NM[side], **L.metrics(t, d15, ct15))); eqs[f'{m}_{ph:02d}_{side}'] = L.mtm_hourly(t, d15, ct15).round(2)
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'fill_phase_side_table.csv'), index=False); pd.concat(B.values()).to_csv(os.path.join(OUT, 'fill_trades.csv.gz'), index=False); pd.DataFrame(eqs).to_csv(os.path.join(OUT, 'fill_equity_hourly.csv.gz'))
    cols = ['成交模型', '相位', '重播', '筆數', '勝', '敗', '勝率', '淨利', '平均贏', '平均輸', '每筆淨利', 'MH筆', 'MH淨', '止損筆', '止損淨', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '研究期內', '研究期外', '最差連敗段', '最差滾動12月_五年口徑', '回撤_五年已實現口徑', '回撤_全期逐時市價權益', '期末未平倉筆', '含期末估值淨利']; print(T[cols].to_string(index=False))
    mo = pd.DataFrame({f'{m}_{ph:02d}_{s}': L.closed(t).groupby(L.closed(t).t_out.dt.strftime('%Y-%m')).net.sum().round(1) for (m, ph, s), t in B.items()}).fillna(0.0); mo.to_csv(os.path.join(OUT, 'fill_monthly.csv'))
    # 進場價變動
    ep = []
    for ph in L.PH:
        for side in ('L', 'S'):
            a = B[('F0', ph, side)]; sg = 1.0 if side == 'L' else -1.0
            for m in ('F1', 'F2'):
                b = B[(m, ph, side)]; j = a.merge(b, on=['t_sig', 'side'], suffixes=('', '_m')); ep.append(dict(相位=f':{ph:02d}', 邊=side, 模型=m, F0筆=len(a), 模型筆=len(b), 同訊號筆=len(j), 平均進場價不利bp=round(float((sg * (j.entry_price_m / j.entry_price - 1)).mean() * 1e4), 2), 同訊號出場類型相同比例=round(float((j.exit_reason == j.exit_reason_m).mean()), 3)))
    EP = pd.DataFrame(ep); EP.to_csv(os.path.join(OUT, 'fill_entry_shift.csv'), index=False); print(EP.to_string(index=False))
    A['F2延遲期內改為止損出場筆數'] = {f'{ph:02d}_{s}': int(((B[('F2', ph, s)].exit_reason == 'SN') & (B[('F2', ph, s)].t_out.dt.minute != (ph % 60))).sum()) for ph in L.PH for s in ('L', 'S')}
    # ---- 零成交量與成交量差異 K 是否落在關鍵時點（F0 帳本，四相位 L＋S）
    zt = pd.DatetimeIndex((z.datetime - pd.Timedelta(hours=8)).values); vd = pd.DatetimeIndex(['2025-01-29 01:00', '2025-01-29 02:00']); hit = []
    for ph in L.PH:
        t = B[('F0', ph, 'LS')]
        for nm, bars, span in (('零成交量15分K', zt, pd.Timedelta(minutes=15)), ('成交量不同的1小時K', vd, pd.Timedelta(hours=1))):
            for b0 in bars:
                o = t[(t.t_sig == b0) | (t.t_out == b0)]; hld = t[(t.t_in < b0 + span) & (t.t_out > b0)]; hit.append(dict(相位=ph, 類別=nm, K開盤UTC=str(b0), 為委託時點的交易數=len(o), 持倉期間涵蓋的交易數=len(hld), 涵蓋交易=';'.join(f'{r.side}@{str(r.t_in)[:16]}→{r.exit_reason}' for r in hld.itertuples())))
    H = pd.DataFrame(hit); H.to_csv(os.path.join(OUT, 'special_bars_check.csv'), index=False); A['特殊K線'] = dict(零量K為委託時點=int(H[H.類別 == '零成交量15分K'].為委託時點的交易數.sum()), 零量K落在持倉期間=int(H[H.類別 == '零成交量15分K'].持倉期間涵蓋的交易數.sum()), 量不同K為委託時點=int(H[H.類別 != '零成交量15分K'].為委託時點的交易數.sum()), 量不同K落在持倉期間=int(H[H.類別 != '零成交量15分K'].持倉期間涵蓋的交易數.sum())); print(A['特殊K線']); print(H[(H.為委託時點的交易數 > 0) | (H.持倉期間涵蓋的交易數 > 0)].to_string(index=False))
    # ---- 期末未平倉
    op = pd.concat([t[t.exit_reason == 'OPEN'] for t in B.values()]); opl = op[['model', 'ph', 'mode', 'side', 't_in', 'entry_price', 'exit_price', 'ntl', 'gross', 'cost', 'fund', 'net', 'bars_held']].copy(); opl['扣出場費後'] = (opl.net - 0.001125 * opl.ntl).round(2); opl.to_csv(os.path.join(OUT, 'open_at_cutoff.csv'), index=False); print('期末未平倉'); print(opl.round(2).to_string(index=False)); A['期末未平倉筆數'] = len(opl)
    # ---- 同根歧義：止損出場的交易，在出場那根 1 小時 K 內，止盈價位是否先於止損價位被觸及（以 15 分 K 判斷）
    h15, l15 = d15.high.values, d15.low.values; amb = []
    for ph in L.PH:
        t = B[('F0', ph, 'LS')]; sn = t[t.exit_reason == 'SN']
        for r in sn.itertuples():
            k = K[ph][r.exit_bar]; ks = range(k, k + 4)
            if r.side == 'L': tp = r.entry_price * (1 + (0.04 if r.entry_regime == 'DOWN' else E.L_TP)); snl = r.entry_price * (1 - E.L_SN); ft = [q for q in ks if h15[q] >= tp]; fs = [q for q in ks if l15[q] <= snl]
            else: tp = r.entry_price * (1 - E.S_TP); snl = r.entry_price * (1 + E.S_SN); ft = [q for q in ks if l15[q] <= tp]; fs = [q for q in ks if h15[q] >= snl]
            if ft: amb.append(dict(相位=ph, side=r.side, t_in=r.t_in, t_out=r.t_out, 止盈價位先到=bool(fs and ft[0] < fs[0]), 同一根15分K=bool(fs and ft[0] == fs[0]), 主結果淨=round(r.net, 1), 若以止盈價成交淨=round(r.ntl * abs(tp / r.entry_price - 1) - r.cost - r.fund, 1)))
    AM = pd.DataFrame(amb); AM.to_csv(os.path.join(OUT, 'same_bar_ambiguity.csv'), index=False); A['同根歧義'] = {int(ph): dict(止損出場筆=int((B[('F0', ph, 'LS')].exit_reason == 'SN').sum()), 同根亦觸及止盈價位=int((AM.相位 == ph).sum()) if len(AM) else 0, 其中止盈價位先到=int(AM[AM.相位 == ph].止盈價位先到.sum()) if len(AM) else 0, 有利邊界增加淨利=round(float((AM[AM.相位 == ph].若以止盈價成交淨 - AM[AM.相位 == ph].主結果淨).sum()), 1) if len(AM) else 0.0) for ph in L.PH}; print(A['同根歧義']); print(AM.to_string(index=False) if len(AM) else '無')
    # ---- 成對每週差值（J2）
    wi = pd.period_range('2021-01-04', '2026-11-01', freq='W-SUN'); per = np.where(wi.end_time < '2024-07-01', 'A', np.where(wi.end_time < '2026-05-01', 'B', 'C')); J2 = {}
    for m in L.MODELS:
        for side in ('L', 'S'):
            W = pd.DataFrame({ph: L.closed(B[(m, ph, side)]).pipe(lambda c: c.groupby(c.t_out.dt.to_period('W-SUN')).net.sum()).reindex(wi).fillna(0.0) for ph in L.PH}); Dw = (W[0] - W[[15, 30, 45]].mean(axis=1)).values
            J2[f'{m}_{side}'] = dict(全期=boot(Dw), 研究期內=boot(Dw[per == 'B']), 研究期外=boot(Dw[per != 'B']), 逐年_按平倉週={int(y): round(float(Dw[wi.end_time.year == y].sum())) for y in range(2021, 2027)})
    A['J2_每週差值'] = J2; print(json.dumps(J2, ensure_ascii=False, indent=1))
    # ---- 判定
    g = lambda m, ph, s, k='淨利': float(T[(T.成交模型 == m) & (T.相位 == f':{ph:02d}') & (T.重播 == NM[s])][k].iloc[0]); n0, n1, n2 = g('F0', 0, 'LS'), g('F1', 0, 'LS'), g('F2', 0, 'LS')
    J = dict(J1=dict(F0=n0, F1=n1, F2=n2, F1差=n1 - n0, F2差=n2 - n0, 判定='對 15 分鐘延遲高度敏感' if n2 - n0 < -1000 else ('對成交時點不敏感' if max(abs(n1 - n0), abs(n2 - n0)) <= 300 else '中度敏感')),
             J3=dict(最大期末估值影響=float((T.含期末估值淨利 - T.淨利).abs().max())),
             J4={m: dict(多方各相位=[g(m, ph, 'L') for ph in L.PH], 空方各相位=[g(m, ph, 'S') for ph in L.PH], LS各相位=[g(m, ph, 'LS') for ph in L.PH]) for m in L.MODELS})
    A['判定'] = J; print(json.dumps(J, ensure_ascii=False, indent=1)); json.dump(A, open(os.path.join(OUT, 'v80_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str)
