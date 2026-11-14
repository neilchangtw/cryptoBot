"""V81：1,500U 帳戶回放（相位 0；現行原配置與 V77 配置；延遲 0／1／2／5／15 分鐘）。
口徑一「參考路徑＋拒單」：先產生不受資金限制的交易，再依成交時刻可用保證金拒單（V70／V77 既定做法）。
口徑二「資金受限自洽狀態機」（只做現行原配置）：事件帳本在成交時刻檢查保證金，被拒者不產生部位、損益、冷卻與熔斷。
用法：python backtest/research/v81_account.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v81_ledger as Z, v80_lib as L, v79_lib as L9, v53_common as C, v70_account as E, v57b_lowvol as V
OUT = U.OUT; RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

def grid_df(df, k):
    """回放格點：整點與整點＋k 分。close＝該時刻價格；high／low＝上一格點之後到本格點的 1 分 K 極值。datetime−7h＝格點 UTC。"""
    if k == 0: return df
    M = U.m1(); h = pd.date_range(U.W0 - pd.Timedelta(hours=2), U.W1 + pd.Timedelta(hours=1), freq='h'); t = h.append(h + pd.Timedelta(minutes=k)).sort_values(); t = t[t <= U.W1 + pd.Timedelta(minutes=30)]; i = U.mi(t); a = i[:-1]; b = i[1:]
    hi = np.maximum.reduceat(M['high'], a)[:len(a)]; lo = np.minimum.reduceat(M['low'], a)[:len(a)]
    # reduceat 以相鄰起點為界：a[j]..a[j+1]−1，正好是 (t[j], t[j+1]] 內的分鐘；最後一段另算
    hi[-1] = M['high'][a[-1]:b[-1]].max(); lo[-1] = M['low'][a[-1]:b[-1]].min()
    return pd.DataFrame(dict(datetime=t[1:] + pd.Timedelta(hours=7), close=M['close'][b - 1], high=hi, low=lo))

def to_book(t): return pd.DataFrame(dict(side=t.side.values, t_in=t.t_in.values, t_out=t.t_out.values, ep=np.round(t.entry_price.values.astype(float), 2), xp=np.round(t.exit_price.values.astype(float), 2), reason=t.exit_reason.values, snm=1.0))

def one(nm, k, bk, g, mode, fund, tag):
    R = E.replay(g, fund, base=bk, base_mode=mode, cross=False); full = R['acc']; R['acc'] = full[full.index.minute == 0]; d = E.summary(nm, R, g); tr = R['tr']; e = R['acc'].equity; pk = np.maximum(e.cummax(), E.CAP)
    d.update(情境=f'D{k}', 回撤_全期逐時市價權益=round((pk - e).max()), 全格點最低權益=round(full.equity.min()), 全格點保守最低權益=round(full.equity_intrahour.min()), 單筆最大虧損占錢包pct=round(float((-tr.net / tr.wallet).max() * 100), 2), 跳過筆=len(R['skips']), 參考交易筆=len(bk),
             零交易月=int(70 - tr.t_out.dt.to_period('M').nunique()), 最後成交進場=str(tr.t_in.max()), 第一筆跳過=str(R['skips'].t.min()) if len(R['skips']) else '')
    tr.to_csv(os.path.join(OUT, f'acct_{tag}_D{k}_trades.csv'), index=False); R['acc'].round(3).to_csv(os.path.join(OUT, f'acct_{tag}_D{k}_equity_hourly.csv.gz'))
    if len(R['skips']): R['skips'].to_csv(os.path.join(OUT, f'acct_{tag}_D{k}_skips.csv'), index=False)
    yw = [dict(版本=nm, 情境=f'D{k}', 年=int(y), 筆數=len(x), 勝=int((x.net > 0).sum()), 敗=int((x.net < 0).sum()), 平=int((x.net == 0).sum()), 勝率=round((x.net > 0).mean() * 100, 2), 淨利_按平倉年=round(x.net.sum())) for y, x in tr.groupby(tr.t_out.dt.year)]
    return d, yw, R

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); M = U.m1(); df = P[0]; ind = L9.indicators(df); okq = ~(V.vol_rank(df) < 20); rows = []; YW = []; SC = {}
    for k in U.DELAYS:
        g = grid_df(df, k)
        for nm, ok, mode, tag in (('現行原配置', None, FIX, 'orig'), ('V77 配置（Q20W30＋停損價差預算3%）', okq, RISK, 'v77')):
            t = L.closed(U.book(df, ind, k, 'LS', fund=fund, ok=ok)); d, yw, R = one(f'D{k}｜{nm}｜參考路徑＋拒單', k, to_book(t), g, mode, fund, tag); d['配置'] = nm; d['口徑'] = '參考路徑＋拒單'; rows.append(d); YW += yw
        z, ev, cand, rej = Z.run(df, ind, M, k, acct=dict(cap=E.CAP, lev=20.0, fund=fund, hc=E.HC, start=U.W0)); z = z[(z.exit_reason != 'OPEN') & (z.t_sig >= U.W0) & (z.t_out <= U.W1)].sort_values(['t_in', 'side']).reset_index(drop=True)
        rej = rej[rej.t_sig >= U.W0] if len(rej) else rej
        d, yw, R = one(f'D{k}｜現行原配置｜資金受限自洽狀態機', k, to_book(z), g, FIX, fund, 'orig_sc'); d['配置'] = '現行原配置'; d['口徑'] = '資金受限自洽狀態機'; d['狀態機拒單筆'] = len(rej); d['回放再拒單筆'] = len(R['skips']); rows.append(d); YW += yw
        if len(rej): rej.to_csv(os.path.join(OUT, f'acct_orig_sc_D{k}_rejects.csv'), index=False)
        SC[f'D{k}'] = dict(狀態機成交筆=len(z), 狀態機拒單筆=len(rej), 第一筆拒單=str(rej.t.min()) if len(rej) else '', 回放再拒單筆=len(R['skips']))
    T = pd.DataFrame(rows); T.to_csv(os.path.join(OUT, 'acct_summary.csv'), index=False); pd.DataFrame(YW).to_csv(os.path.join(OUT, 'acct_yearly_wl.csv'), index=False)
    pd.set_option('display.width', 330); pd.set_option('display.max_columns', 70); pd.set_option('display.max_rows', 200)
    cols = ['淨利', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝', '敗', '平', '勝率', '平均贏單', '平均輸單', '每筆平均淨利', 'MH筆', 'MH淨', 'SN筆', 'SN淨', '最長連敗', '連敗4以上每百筆', '最差連敗段_五年口徑', '最低權益', '全格點最低權益', '最大百分比回撤', '回撤_全期逐時市價權益', '五年MDD', '最差滾動12月', '月均', '零交易月', '強平觸及_逐倉估算', '參考交易筆', '跳過筆', '單筆最大虧損占錢包pct']
    print(T.set_index(['配置', '口徑', '情境'])[cols].T.to_string()); print(json.dumps(SC, ensure_ascii=False))
    base0 = float(T[(T.配置 == '現行原配置') & (T.口徑 == '參考路徑＋拒單') & (T.情境 == 'D0')].淨利.iloc[0]); G = {}
    for d in rows:
        G[d['版本']] = {'勝率>50%': d['勝率'] > 50, **{f'{y}年為正': d.get(f'y{y}', 0) > 0 for y in range(2021, 2026)}, '2026年初至10月為正': d.get('y2026', 0) > 0, '全期淨利≥現行D0': d['淨利'] >= base0, '最低權益≥1000': d['最低權益'] >= 1000, '百分比回撤≤40%': d['最大百分比回撤'] <= 40, '簡化強平線零觸及（不等於零強平已證明）': d['強平觸及_逐倉估算'] == 0, '五年MDD≤1400': d['五年MDD'] <= 1400, '最差滾動12月≥-900': d['最差滾動12月'] >= -900, '最差連敗段≥-500': d['最差連敗段_五年口徑'] >= -500}
    Gd = pd.DataFrame(G).replace({True: '通過', False: '未通過'}); Gd.to_csv(os.path.join(OUT, 'acct_gates.csv')); print(Gd.T.to_string()); json.dump(SC, open(os.path.join(OUT, 'acct_selfconsistent.json'), 'w'), ensure_ascii=False, indent=1)
