"""V70 第一部分：同一有限帳戶引擎重跑現行原配置、Q20W30＋固定風險下單、V69-B；強平情境；勝率比較。
用法：python backtest/research/v70_recon.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v69a as A, v69b as B, v69_baseline as S69, v70_account as E
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v70'); os.makedirs(OUT, exist_ok=True)
DL5 = ['2021-01-05 06:00', '2021-02-24 00:00', '2022-11-22 10:00', '2022-12-16 08:00', '2023-08-29 07:00']

def setup():
    df = C.load_1h(end='2026-11-01'); fund = C.load_funding(); sim = A.build_sim(); ind0 = V.B.eng.compute_indicators(df); ones = np.ones(len(df), bool); okq = ~(V.vol_rank(df) < 20)
    px, dc = B.daily_close(df); st = S69.states(df); st['m'] = pd.Series(A.V66.vol_mult(df, 99.0), index=st.index)
    return dict(df=df, fund=fund, sim=sim, ind0=ind0, ones=ones, okq=okq, px=px, dc=dc, st=st, base=A.ref_book(df, ind0, sim, ones), q20=A.ref_book(df, ind0, sim, okq))

def trend_sig(dc, n=200):
    sma = dc.rolling(n, min_periods=n).mean(); return np.sign(dc - sma).replace(0, np.nan).ffill()

def tag(tr, st):
    s = st.reindex(tr.t_in.values); tr = tr.copy(); tr['volrank'] = s.volrank.values; tr['r30'] = s.r30.values; tr['m'] = s.m.values; tr['lowvol'] = tr.volrank < 20; tr['down30'] = tr.r30 <= -0.15; tr['year'] = tr.t_out.dt.year; return tr

def wr_tables(name, tr, base_wr):
    rows = [E.wr_row(f'{name}｜全期', tr, base_wr)]
    for y, g in tr.groupby('year'): rows.append(E.wr_row(f'{name}｜{y}', g))
    for s, g in tr.groupby('side'): rows.append(E.wr_row(f'{name}｜{"多" if s == "L" else "空"}', g))
    rows.append(E.wr_row(f'{name}｜低波動期進場', tr[tr.lowvol])); rows.append(E.wr_row(f'{name}｜持續下跌期進場(30日≤-15%)', tr[tr.down30]))
    if 'leg' in tr and (tr.leg == 'trend').any(): rows.append(E.wr_row(f'{name}｜現行腿', tr[tr.leg == 'base'])); rows.append(E.wr_row(f'{name}｜趨勢腿', tr[tr.leg == 'trend']))
    return rows

def save(name, R, key):
    R['tr'].to_csv(os.path.join(OUT, f'{key}_trades.csv'), index=False); R['acc'].round(4).to_csv(os.path.join(OUT, f'{key}_account_hourly.csv'))
    if len(R['skips']): R['skips'].to_csv(os.path.join(OUT, f'{key}_skips.csv'), index=False)

if __name__ == '__main__':
    X = setup(); df, fund = X['df'], X['fund']; sig = trend_sig(X['dc']); rows = []; wr = []; base_wr = None
    runs = [('現行原配置（200U×20 逐倉）', 'orig', dict(base=X['base'], base_mode=('fixed', 4000.0, 20.0))),
            ('Q20W30＋固定風險下單（f=3%、5倍逐倉）', 'q20risk', dict(base=X['q20'], base_mode=('risk', 0.03, 5.0))),
            ('現行＋固定風險下單（共同曝險對照）', 'origrisk', dict(base=X['base'], base_mode=('risk', 0.03, 5.0))),
            ('V69-B（Q20W30 固定風險＋趨勢腿 g25% 持有不調整）', 'v69b', dict(base=X['q20'], base_mode=('risk', 0.03, 5.0), sleeve=dict(sig=sig, mode='hold', g=0.25, band=0, lev=2.0)))]
    for name, key, kw in runs:
        R = E.replay(df, fund, **kw); R['tr'] = tag(R['tr'], X['st']); d = E.summary(name, R, df); rows.append(d); save(name, R, key)
        if base_wr is None: base_wr = d['勝率']
        wr += wr_tables(name, R['tr'], base_wr); print(json.dumps(d, ensure_ascii=False), flush=True)
        if key == 'orig':
            lq = R['tr'][R['tr'].liq]; lq[['side', 't_in', 't_out', 'ep', 'xp', 'reason', 'net']].to_csv(os.path.join(OUT, 'orig_liq_touch_14.csv'), index=False); print('強平估算線觸及', len(lq), '筆；同時另一側持倉筆數', sum(((R['tr'].t_in < r.t_out) & (R['tr'].t_out > r.t_in) & (R['tr'].side != r.side)).any() for r in lq.itertuples()))
            # 情境：資料不足的 5 筆／全部 14 筆視為強平（損失整筆保證金 200U＝價格不利 5%），不回饋熔斷
            for sc, sel in (('情境A：5 筆資料不足者視為強平', set(pd.to_datetime(DL5))), ('情境B：14 筆全部視為強平', set(lq.t_in))):
                b = X['base'].copy(); m = b.t_in.isin(sel) & (b.reason == 'SN'); b.loc[m, 'xp'] = np.where(b[m].side == 'L', b[m].ep * 0.95, b[m].ep * 1.05); b.loc[m, 'reason'] = 'LIQ'
                R2 = E.replay(df, fund, base=b, base_mode=('fixed', 4000.0, 20.0)); R2['tr'] = tag(R2['tr'], X['st']); d2 = E.summary(sc, R2, df); rows.append(d2); print(json.dumps({k: d2[k] for k in ('版本', '淨利', '最低權益', '最低權益時刻', '最低可用資金', '最大百分比回撤', '跳過', '筆數')}, ensure_ascii=False), int(m.sum()), '筆改為強平', R2['skips'].head(3).to_dict('records') if len(R2['skips']) else '')
        if key == 'v69b':
            a = R['acc']; e = a.equity; pk = np.maximum(e.cummax(), E.CAP); k = ((pk - e) / pk).idxmax(); p = e[:k].idxmax(); print('V69-B 回撤拆解', {t: dict(時刻=str(x), 權益=round(a.equity[x], 1), 錢包餘額=round(a.wallet[x], 1), 未實現=round(a.unrealized[x], 1)) for t, x in (('高點', p), ('谷底', k))})
            t = R['tr']; seg = t[(t.t_out > p) & (t.t_out <= k)]; print('高點到谷底間平倉：', seg.groupby('leg').net.agg(['count', 'sum']).round(1).to_dict()); print('合併部位檢查', E.merged_check(R, df))
    pd.DataFrame(rows).to_csv(os.path.join(OUT, 'recon_summary.csv'), index=False); pd.DataFrame(wr).to_csv(os.path.join(OUT, 'recon_winrate.csv'), index=False)
    w = pd.DataFrame(wr); print(w[w.版本.str.contains('全期')].to_string())
