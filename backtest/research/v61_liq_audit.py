"""現行策略 20x 部位：安全網止損與估算強平的先後順序（分鐘線＋標記價格），以及有限帳戶報告的兩項對帳。
不新增假說、不調參。用法：python backtest/research/v61_liq_audit.py"""
import os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v61_audit as A
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v61'); LEV = 20.0; SN = {'L': 0.035, 'S': 0.040}; MMRS = (0.004, 0.005, 0.0065); CAP = 2000.0

def liq_px(ep, s, mmr): return ep * (1 - s / LEV) / (1 - s * mmr)   # 逐倉、保證金＝名目/20、不含其他持倉

def one(r, df, keep):
    ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); s = 1 if r.side == 'L' else -1; sn = r.ep * (1 - s * SN[r.side]); lq = {m: liq_px(r.ep, s, m) for m in MMRS}
    o = dict(side=r.side, 進場UTC=r.t_in, 出場小時線UTC=r.t_out, 進場價=r.ep, 保證金=200, 名目=4000, 回測出場=r.reason, 回測出場價=round(r.xp, 2), 回測毛損益=round(s * 4000 / r.ep * (r.xp - r.ep), 1), Q20W30保留=keep,
             止損價=round(sn, 2), 強平價_mmr04=round(lq[0.004], 2), 強平價_mmr05=round(lq[0.005], 2), 強平價_mmr065=round(lq[0.0065], 2))
    try:
        a = r.t_in; b = r.t_out; days = sorted({str(d.date()) for d in pd.date_range(a.floor('D'), b.floor('D'), freq='D')})
        last = pd.concat([A.k1m(d, 'klines') for d in days])[a:b - pd.Timedelta(minutes=1)]; mark = pd.concat([A.k1m(d, 'markPriceKlines') for d in days])[a:b - pd.Timedelta(minutes=1)]
        pre = pd.concat([A.k1m(str((r.t_in - pd.Timedelta(minutes=1)).date()), 'klines')]); o['進場前一分鐘收盤'] = float(pre.c.get(r.t_in - pd.Timedelta(minutes=1), np.nan))
        hit = lambda k, px: k[(k.l <= px)] if s == 1 else k[(k.h >= px)]
        hs = hit(last, sn); hms = hit(mark, sn); t_sn = hs.index[0] if len(hs) else None
        o.update(最新價首觸止損=str(t_sn), 標記價首觸止損=str(hms.index[0]) if len(hms) else '未到')
        for m, tag in ((0.004, '04'), (0.005, '05'), (0.0065, '065')):
            hm = hit(mark, lq[m]); hl = hit(last, lq[m]); o[f'標記價首觸強平_mmr{tag}'] = str(hm.index[0]) if len(hm) else '未到'; o[f'最新價首觸強平_mmr{tag}'] = str(hl.index[0]) if len(hl) else '未到'
        hm = hit(mark, lq[0.0065]); t_lq = hm.index[0] if len(hm) else None   # 最保守（最近的強平價）
        if t_sn is None: o['順序'] = '分鐘線未見止損觸發（與小時線不一致）'
        else:
            bar = last.loc[t_sn]; ext = bar.l if s == 1 else bar.h; o.update(止損觸發分鐘開盤=bar.o, 止損觸發分鐘極值=ext, 開盤即越過止損=bool(bar.o <= sn if s == 1 else bar.o >= sn),
                樂觀損失_止損價成交=round(4000 * SN[r.side], 1), 保守損失_觸發分鐘極值成交=round(s * 4000 / r.ep * (r.ep - ext), 1), 強平情境損失=200.0)
            if t_lq is None: o['順序'] = '止損先觸發；持倉期間標記價未到強平價'; o['相隔分鐘'] = None
            elif t_lq > t_sn: o['順序'] = '止損先觸發；標記價之後才到強平價'; o['相隔分鐘'] = int((t_lq - t_sn).total_seconds() // 60)
            elif t_lq == t_sn: o['順序'] = 'DATA_LIMITED：同一分鐘內觸及止損與強平價'; o['相隔分鐘'] = 0
            else: o['順序'] = '標記價先到強平價'; o['相隔分鐘'] = int((t_lq - t_sn).total_seconds() // 60)
    except Exception as e:
        o['順序'] = f'DATA_LIMITED：分鐘資料取得失敗 {type(e).__name__} {e}'
    return o

if __name__ == '__main__':
    df = C.load_1h(end='2026-11-01'); ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); pos = {t: i for i, t in enumerate(ct)}; hi = df.high.values; lo = df.low.values
    b = pd.read_csv(os.path.join(OUT, 'finite_基準_trades.csv'), parse_dates=['t_in', 't_out']); q = pd.read_csv(os.path.join(OUT, 'finite_Q20W30_trades.csv'), parse_dates=['t_in', 't_out']); qk = set(zip(q.side, q.t_in))
    rows = []
    for r in b.itertuples():
        s = 1 if r.side == 'L' else -1; i0 = pos[r.t_in]; i1 = pos[r.t_out]; lp = liq_px(r.ep, s, 0.005)
        if i1 > i0 and ((lo[i0 + 1:i1 + 1].min() <= lp) if s == 1 else (hi[i0 + 1:i1 + 1].max() >= lp)): rows.append(one(r, df, (r.side, r.t_in) in qk))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'liq_audit_baseline.csv'), index=False); pd.set_option('display.width', 250); print(T.T.to_string())
    print('\n順序統計\n', T.groupby(['順序', 'Q20W30保留']).size())
    for nm, m in (('基準 14 筆', T), ('Q20W30 8 筆', T[T.Q20W30保留])):
        g = m.回測毛損益.sum(); print(f'{nm}：回測毛損益合計 {g:.0f}；樂觀（止損價成交）{-m.樂觀損失_止損價成交.sum():.0f}；保守（觸發分鐘極值成交）{-m.保守損失_觸發分鐘極值成交.sum():.0f}；全部強平 {-200.0 * len(m):.0f}；'
              f'差額：樂觀 {-m.樂觀損失_止損價成交.sum() - g:+.0f}、保守 {-m.保守損失_觸發分鐘極值成交.sum() - g:+.0f}、全部強平 {-200.0 * len(m) - g:+.0f}')
    # ---- 報告對帳 ----
    print('\n=== MDD 兩種口徑 ===')
    for tag in ('基準', 'Q20W30', 'A_S8', 'B_S8', 'B_S12', 'B_前推'):
        eq = pd.read_csv(os.path.join(OUT, f'finite_{tag}_equity_hourly.csv'), index_col=0, parse_dates=True).equity; pk = eq.cummax().clip(lower=CAP); pk = np.maximum(eq.cummax(), CAP); dd = pk - eq; j = dd.idxmax(); pct = dd / pk; k = pct.idxmax()
        print(f'{tag}：最大金額回撤 {dd.max():.0f}U（谷底 {j.date()}，當時高點 {pk[j]:.0f}，占高點 {dd.max() / pk[j]:.1%}，占初始 2,000U {dd.max() / CAP:.1%}）；最大百分比回撤 {pct.max():.1%}（谷底 {k.date()}，金額 {dd[k]:.0f}U，高點 {pk[k]:.0f}）；期末 {eq.iloc[-1]:.1f}')
    eq = pd.read_csv(os.path.join(OUT, 'finite_B_S8_equity_hourly.csv'), index_col=0, parse_dates=True).equity; print('\nB_S8 2021-05-18~20 淨值：\n', eq['2021-05-18 20:00':'2021-05-19 04:00'].round(2).to_string())
    for tag in ('基準', 'Q20W30', 'B_S12'):
        tr = pd.read_csv(os.path.join(OUT, f'finite_{tag}_trades.csv'), parse_dates=['t_in', 't_out']); eq = pd.read_csv(os.path.join(OUT, f'finite_{tag}_equity_hourly.csv'), index_col=0, parse_dates=True).equity; mins = []
        for r in tr.sort_values(['t_in', 'pri']).itertuples():
            used = tr[(tr.t_in < r.t_in) & (tr.t_out > r.t_in)].mg.sum() + tr[(tr.t_in == r.t_in) & (tr.pri < r.pri)].mg.sum(); mins.append(eq.get(r.t_in, np.nan) - used - r.mg)
        print(f'{tag}：進場檢查最小餘裕（可用資金 − 所需保證金）{np.nanmin(mins):.1f}U')
