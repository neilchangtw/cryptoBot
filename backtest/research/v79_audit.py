"""V79 A、B、D、E：時間處理核對、四相位多空固定參數重播、區塊拔靴、機制對照 M1。
用法：python backtest/research/v79_audit.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v79_lib as L, v53_common as C, v57b_lowvol as V
OUT = L.OUT; pd.set_option('display.width', 320); pd.set_option('display.max_columns', 60); pd.set_option('display.max_rows', 400)

def weekly(t): return t.groupby(t.t_out.dt.to_period('W-SUN')).net.sum()
def wk_index(): return pd.period_range('2021-01-04', '2026-11-01', freq='W-SUN')

if __name__ == '__main__':
    d15, P = L.phases(); fund = C.load_funding(); h1 = C.load_1h(end='2026-11-01'); A = {}
    # ---- A1 資料
    g = d15.datetime.diff().dropna(); A['15m根數'] = len(d15); A['15m非15分間隔'] = int((g != pd.Timedelta(minutes=15)).sum()); A['15m零成交量根數'] = int((d15.volume <= 0).sum()); A['15m起訖(UTC+8)'] = [str(d15.datetime.min()), str(d15.datetime.max())]
    A['15m高低一致性違反'] = int(((d15.high < d15[['open', 'close']].max(axis=1)) | (d15.low > d15[['open', 'close']].min(axis=1))).sum())
    m = h1.merge(P[0], on='datetime', how='outer', suffixes=('', '_p'), indicator='mm'); rel = (m.volume / m.volume_p - 1).abs()
    A['整點組對既有1h檔'] = dict(根數=[len(h1), len(P[0])], 未對上=int((m.mm != 'both').sum()), 價格最大差=float(max((m[c] - m[c + '_p']).abs().max() for c in ('open', 'high', 'low', 'close'))), 量相對差超過千分之一的根數=int((rel > 1e-3).sum()), 量最大相對差=round(float(rel.max()), 4), 量差根數所在=[str(x) for x in m.datetime[rel > 1e-3].head(5)])
    A['各組'] = {ph: dict(根數=len(df), 首根開盤=str(df.datetime.iloc[0]), 末根開盤=str(df.datetime.iloc[-1]), 末根收盤UTC=str(df.datetime.iloc[-1] - pd.Timedelta(hours=7)), 暖機後首根可交易UTC=str(df.datetime.iloc[L.eng.WARMUP] - pd.Timedelta(hours=7))) for ph, df in P.items()}
    # ---- A2 截斷重算（無前瞻）
    IND = {ph: L.indicators(df) for ph, df in P.items()}; rng = np.random.default_rng(79); bad = 0; chk = 0
    for ph, df in P.items():
        for k in rng.integers(5000, len(df) - 1, 40):
            sub = df.iloc[k - 1500:k + 1].reset_index(drop=True); a = L.eng.compute_indicators(sub); f = IND[ph]
            for key in ('brk_up', 'brk_dn', 'regime_block_l', 'regime_block_s'): bad += int(bool(a[key][-1]) != bool(f[key][k])); chk += 1
            for key in ('pctile_L', 'pctile_S', 'slope'): bad += int(not np.isclose(a[key][-1], f[key][k], equal_nan=True)); chk += 1
    A['截斷重算'] = dict(檢查數=chk, 不一致=bad)
    A['程式行為'] = {'訊號與成交': '第 i 根收盤判定，以第 i 根收盤價成交（四組相同）', '時段與星期': 'K 線開盤時間（UTC+8）的小時與星期；偏移組開盤 hh:φ 記為 hh 時', '日與月風控重置': 'K 線開盤時間（UTC+8）的日期與月份', '持倉時間、冷卻、連虧冷卻': '以根數計，四組每根皆 60 分鐘', '同根事件排序': '止損（高低價觸及，穿價 25%）先於止盈（高低價觸及，收盤價成交），其餘以收盤判定', '期末': '只輸出已平倉交易；本輪另限制出場不晚於 UTC 2026-10-31 23:00', '資金費': 'V78 D0 未計；本輪逐筆計入'}
    # 偏移後下單時刻相對封鎖時段的位移：列出各組多方進場的下單時刻（UTC+8）小時分布
    # ---- B 固定參數重播
    rows = []; T = {}; Tc = {}
    for ph, df in P.items():
        vr = pd.Series(V.vol_rank(df), index=pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values)); c = pd.Series(df.close.values, index=vr.index); r30 = c / c.shift(720) - 1
        for side in ('L', 'S', 'LS'):
            t = L.book(df, IND[ph], side, fund=fund); t['ph'] = ph; t['mode'] = side; t['lowvol'] = (vr.reindex(t.t_in).values < 20); t['down30'] = (r30.reindex(t.t_in).values <= -0.15)
            t['per'] = np.where(t.t_out < '2024-07-01', 'A', np.where(t.t_out < '2026-05-01', 'B', 'C')); T[(ph, side)] = t
        for side, key in (('L', 'L'), ('S', 'S'), ('LS', 'LS')):
            t = T[(ph, side)]; rows.append(dict(相位=f':{ph:02d}', 重播=dict(L='多方單獨', S='空方單獨', LS='L＋S')[side], **L.stat(t), **{f'y{y}': round(v) for y, v in t.groupby('year').net.sum().items()}, **{f'期間{k}': round(v) for k, v in t.groupby('per').net.sum().items()}))
        ls = T[(ph, 'LS')]
        for s in ('L', 'S'): x = ls[ls.side == s]; rows.append(dict(相位=f':{ph:02d}', 重播=f'L＋S 中的{"多" if s == "L" else "空"}方', **L.stat(x), **{f'y{y}': round(v) for y, v in x.groupby('year').net.sum().items()}, **{f'期間{k}': round(v) for k, v in x.groupby('per').net.sum().items()}))
        # C1：時段星期與重置邊界改為進位
        if ph:
            ic = L.indicators(df, 'ceil')
            for side in ('L', 'S', 'LS'): Tc[(ph, side)] = L.book(df, ic, side, fund=fund)
    B = pd.DataFrame(rows); B.to_csv(os.path.join(OUT, 'phase_side_table.csv'), index=False); print(B.to_string(index=False))
    allt = pd.concat(T.values()); allt.to_csv(os.path.join(OUT, 'phase_trades.csv'), index=False)
    # 情境
    ctx = []
    for (ph, side), t in T.items():
        if side != 'L': continue
        for nm, gcol in (('趨勢分類', 'entry_regime'), ('低波動', 'lowvol'), ('30日跌逾15%', 'down30')):
            for k, x in t.groupby(gcol): ctx.append(dict(相位=f':{ph:02d}', 情境=nm, 值=str(k), 筆數=len(x), 勝率=round((x.net > 0).mean() * 100, 1), 淨利=round(x.net.sum()), 每筆=round(x.net.mean(), 1)))
    X = pd.DataFrame(ctx); X.to_csv(os.path.join(OUT, 'long_context.csv'), index=False); print(X.pivot_table(index=['情境', '值'], columns='相位', values=['筆數', '淨利']).to_string())
    # 多方下單時刻（UTC+8）小時分布
    hh = pd.DataFrame({f':{ph:02d}': (T[(ph, 'L')].t_in + pd.Timedelta(hours=8)).dt.hour.value_counts().sort_index() for ph in L.PH}).fillna(0).astype(int); hh.to_csv(os.path.join(OUT, 'long_order_hour.csv')); print('多方下單時刻小時分布（UTC+8）'); print(hh.T.to_string())
    # C1 對照
    c1 = [dict(相位=f':{ph:02d}', 重播=side, 主比較_捨去=round(T[(ph, side)].net.sum()), C1_進位=round(Tc[(ph, side)].net.sum()), 筆數_捨去=len(T[(ph, side)]), 筆數_進位=len(Tc[(ph, side)])) for ph in (15, 30, 45) for side in ('L', 'S', 'LS')]; C1 = pd.DataFrame(c1); C1.to_csv(os.path.join(OUT, 'c1_time_semantics.csv'), index=False); print(C1.to_string(index=False))
    # C2 延後一根 15 分 K 成交（只改進場價，出場路徑沿用）
    px15 = pd.Series(d15.close.values, index=pd.DatetimeIndex((d15.datetime - pd.Timedelta(hours=8) + pd.Timedelta(minutes=15)).values)); c2 = []
    for ph in L.PH:
        for side in ('L', 'S'):
            t = T[(ph, side)]; ep2 = px15.reindex(t.t_in + pd.Timedelta(minutes=15)).values; s = 1.0 if side == 'L' else -1.0; adj = t.ntl.values * s * (t.entry_price.values - ep2) / t.entry_price.values
            c2.append(dict(相位=f':{ph:02d}', 邊=side, 原淨利=round(t.net.sum()), 延後15分進場淨利=round(t.net.sum() + np.nansum(adj)), 平均進場價變動bp=round(float(np.nanmean(s * (ep2 / t.entry_price.values - 1)) * 1e4), 2)))
    C2 = pd.DataFrame(c2); C2.to_csv(os.path.join(OUT, 'c2_entry_delay.csv'), index=False); print(C2.to_string(index=False))
    # ---- D 統計：每週差值
    wi = wk_index(); res = {}
    for side in ('L', 'S'):
        W = pd.DataFrame({ph: weekly(T[(ph, side)]).reindex(wi).fillna(0.0) for ph in L.PH}); Dw = W[0] - W[[15, 30, 45]].mean(axis=1); per = np.where(wi.end_time < '2024-07-01', 'A', np.where(wi.end_time < '2026-05-01', 'B', 'C')); o = {}
        for nm, msk in (('全期', np.ones(len(wi), bool)), ('研究期外', per != 'B'), ('研究期內', per == 'B'), ('2021-01~2024-06', per == 'A')):
            mu, p = L.block_boot(Dw.values[msk]); o[nm] = dict(週數=int(msk.sum()), 每週平均差=round(mu, 2), 合計差=round(float(Dw.values[msk].sum())), p單尾=round(p, 4))
        o['逐年差'] = {int(y): round(float(v)) for y, v in Dw.groupby(wi.end_time.year).sum().items()}; res[side] = o
        if side == 'L': Dw.to_csv(os.path.join(OUT, 'long_weekly_diff.csv'))
    A['D_每週差值'] = res; print(json.dumps(res, ensure_ascii=False, indent=1))
    # ---- E 機制對照 M1
    M = []; WK = {}
    for ph, df in P.items():
        ind = IND[ph]; c = df.close.values; ct = pd.DatetimeIndex((df.datetime - pd.Timedelta(hours=7)).values); ok = (ct >= L.W0) & (ct <= L.W1)
        for nm, msk in (('M1a 突破', ind['brk_up']), ('M1b 突破＋壓縮', ind['brk_up'] & (ind['pctile_L'] < 25)), ('M1a空 跌破', ind['brk_dn']), ('M1b空 跌破＋壓縮', ind['brk_dn'] & (ind['pctile_S'] < 35))):
            sg = -1.0 if '空' in nm else 1.0
            for k in (1, 3, 6):
                f = np.full(len(c), np.nan); f[:-k] = sg * (c[k:] / c[:-k] - 1) * 1e4; s = pd.Series(f[msk & ok], index=ct[msk & ok]).dropna()
                M.append(dict(樣本=nm, 相位=ph, k=k, n=len(s), 平均bp=round(s.mean(), 2), **{f'y{y}': round(v, 1) for y, v in s.groupby(s.index.year).mean().items()}))
                g = s.groupby(s.index.to_period('W-SUN')); WK[(nm, ph, k)] = (g.sum().reindex(wi).fillna(0.0), g.count().reindex(wi).fillna(0.0))
    Mdf = pd.DataFrame(M); Mdf.to_csv(os.path.join(OUT, 'm1_forward.csv'), index=False); print(Mdf.to_string(index=False)); mres = {}
    for nm in ('M1a 突破', 'M1b 突破＋壓縮', 'M1a空 跌破', 'M1b空 跌破＋壓縮'):
        for k in (1, 3, 6):
            S0, N0 = WK[(nm, 0, k)]; So = sum(WK[(nm, p, k)][0] for p in (15, 30, 45)); No = sum(WK[(nm, p, k)][1] for p in (15, 30, 45)); obs = S0.sum() / N0.sum() - So.sum() / No.sum()
            rng = np.random.default_rng(79); n = len(wi); kk = int(np.ceil(n / 4)); bs = []
            for _ in range(3000):
                st = rng.integers(0, n - 3, kk); ix = (st[:, None] + np.arange(4)[None, :]).ravel()[:n]; bs.append(S0.values[ix].sum() / max(N0.values[ix].sum(), 1) - So.values[ix].sum() / max(No.values[ix].sum(), 1))
            bs = np.array(bs); yd = {}
            for y in range(2021, 2027):
                mk = wi.end_time.year == y; yd[y] = round(float(S0.values[mk].sum() / max(N0.values[mk].sum(), 1) - So.values[mk].sum() / max(No.values[mk].sum(), 1)), 1)
            mres[f'{nm} k={k}'] = dict(整點減偏移bp=round(float(obs), 2), 拔靴p單尾=round(float((bs <= 0).mean()), 4), 正年數=int(sum(v > 0 for v in yd.values())), 逐年=yd)
    A['E_M1'] = mres; print(json.dumps(mres, ensure_ascii=False, indent=1))
    json.dump(A, open(os.path.join(OUT, 'timing_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str); print(json.dumps({k: A[k] for k in list(A)[:8]}, ensure_ascii=False, indent=1, default=str))
