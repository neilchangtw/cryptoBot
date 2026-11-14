"""V78 判定階段（D0）：四個起算相位的 1 小時 K 線各跑現行規則，檢查 K0~K3。
用法：python backtest/research/v78_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v69a as A, v57b_lowvol as V, v70_account as E
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v78'); PH = (0, 15, 30, 45); W1 = pd.Timestamp('2026-10-31 23:00')

def load_15m(end='2026-11-01'):
    d = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_15m_2020_2026.csv'), parse_dates=['datetime'])
    return d[d.datetime < pd.Timestamp(end) + pd.Timedelta(hours=8)].reset_index(drop=True)

def phase_df(d15, ph):
    """以 ph 分起算的 1 小時 K 線（4 根 15 分 K 合成，只留完整的）。datetime＝開盤時間 UTC+8。"""
    k = int(ph / 15); d = d15.iloc[k:].reset_index(drop=True); n = len(d) // 4; d = d.iloc[:n * 4]; g = np.arange(len(d)) // 4
    o = d.groupby(g).agg(datetime=('datetime', 'first'), open=('open', 'first'), high=('high', 'max'), low=('low', 'min'), close=('close', 'last'), volume=('volume', 'sum'))
    assert (o.datetime.dt.minute == ph).all(); return o.reset_index(drop=True)

def books(end='2026-11-01', q20=False):
    d15 = load_15m(end); sim = A.build_sim(); B = {}
    for ph in PH:
        df = phase_df(d15, ph); ind0 = V.B.eng.compute_indicators(df); ok = ~(V.vol_rank(df) < 20) if q20 else np.ones(len(df), bool)
        t = A.ref_book(df, ind0, sim, ok); t['ph'] = ph; B[ph] = t
    return d15, B

def net4k(t): return t.ntl * np.where(t.side == 'L', t.xp / t.ep - 1, 1 - t.xp / t.ep) - 0.00225 * t.ntl

def dstats(x):
    """每日淨損益序列：平倉日回撤、最差連續虧損日累計。"""
    cs = x.cumsum(); mdd = (np.maximum(cs.cummax(), 0) - cs).max(); w = 0.0; s = 0.0; n = 0; mn = 0
    for v in x.values:
        if v < 0: s += v; n += 1; w = min(w, s); mn = max(mn, n)
        elif v > 0: s = 0.0; n = 0
    return round(mdd), round(w), mn

if __name__ == '__main__':
    d15, B = books(); h = C.load_1h(end='2026-11-01'); p0 = phase_df(d15, 0)
    m = h.merge(p0, on='datetime', how='outer', suffixes=('', '_p'), indicator='mm'); same = (m.mm == 'both').all() and all(np.allclose(m[c], m[c + '_p']) for c in ('open', 'high', 'low', 'close')); print('1h 檔與 15m 合成相位0 價格全同：', bool(same), len(h), len(p0), '量最大相對差', float((m.volume / m.volume_p - 1).abs().max()))
    X = __import__('v74_run').setup('2026-11-01'); ref = X['base']; t0 = B[0]; k0 = len(ref) == len(t0) and (ref.t_in.values == t0.t_in.values).all() and (ref.t_out.values == t0.t_out.values).all() and (ref.side.values == t0.side.values).all() and np.allclose(ref.ep, t0.ep) and np.allclose(ref.xp, t0.xp)
    print('K0 相位0重現 690 筆：', bool(k0), len(ref), len(t0))
    rows = []; D = {}; allt = []
    for ph, t in B.items():
        t = t[t.t_out <= W1].copy(); t['net'] = net4k(t); t['year'] = t.t_out.dt.year; allt.append(t); y = t.groupby('year').net.sum().round(0); D[ph] = t.groupby(t.t_out.dt.floor('D')).net.sum()
        mdd, ws, wn = dstats(D[ph]); rows.append(dict(相位=f':{ph:02d}', 筆數=len(t), 淨利=round(t.net.sum()), 勝率=round((t.net > 0).mean() * 100, 2), **{f'y{k}': v for k, v in y.items()}, MH筆=int((t.reason == 'MH').sum()), MH淨=round(t[t.reason == 'MH'].net.sum()), SN筆=int((t.reason == 'SN').sum()), SN淨=round(t[t.reason == 'SN'].net.sum()), TP筆=int((t.reason == 'TP').sum()), 平倉日回撤=mdd, 最差連續虧損日累計=ws, 最長連續虧損日=wn))
    days = pd.date_range('2021-01-01', '2026-10-31'); DD = pd.DataFrame({ph: D[ph].reindex(days).fillna(0.0) for ph in PH}); ens = DD.mean(axis=1); A4 = pd.concat(allt); A4['net'] = A4.net / 4; y = A4.groupby('year').net.sum().round(0); mdd, ws, wn = dstats(ens[ens != 0] if False else ens)
    rows.append(dict(相位='四相位等權（各¼）', 筆數=len(A4), 淨利=round(A4.net.sum()), 勝率=round((A4.net > 0).mean() * 100, 2), **{f'y{k}': v for k, v in y.items()}, MH筆=int((A4.reason == 'MH').sum()), MH淨=round(A4[A4.reason == 'MH'].net.sum()), SN筆=int((A4.reason == 'SN').sum()), SN淨=round(A4[A4.reason == 'SN'].net.sum()), TP筆=int((A4.reason == 'TP').sum()), 平倉日回撤=mdd, 最差連續虧損日累計=ws, 最長連續虧損日=wn))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'd0_phases.csv'), index=False); pd.set_option('display.width', 300); pd.set_option('display.max_columns', 40); print(T.to_string(index=False))
    cor = DD.corr(); pc = float(cor.values[np.triu_indices(4, 1)].mean()); nz = DD[(DD != 0).any(axis=1)]; cor2 = nz.corr(); print('每日淨損益相關（全部日）'); print(cor.round(3).to_string()); print('平均', round(pc, 3), '；只算有平倉的日', round(float(cor2.values[np.triu_indices(4, 1)].mean()), 3))
    n0 = T.淨利[0]; off = T.淨利[1:4]; r0 = T.iloc[0]; re = T.iloc[4]
    K = dict(K0=bool(k0 and same), K1=bool(off.mean() >= 0.7 * n0 and (off >= 0.5 * n0).all() and (T.勝率[1:4] > 50).all()), K2=bool(all(re[f'y{y}'] > 0 for y in (2022, 2024, 2025, 2026)) and re.淨利 >= 0.8 * n0), K3=bool(pc <= 0.85 and abs(re.最差連續虧損日累計) <= 0.9 * abs(r0.最差連續虧損日累計) and re.平倉日回撤 <= 0.9 * r0.平倉日回撤))
    K['偏移相位平均除以相位0'] = round(float(off.mean() / n0), 3); K['相關平均'] = round(pc, 3); print(K); json.dump(K, open(os.path.join(OUT, 'd0_verdict.json'), 'w'), ensure_ascii=False, indent=1)
    A4.to_csv(os.path.join(OUT, 'd0_phase_trades.csv'), index=False)
