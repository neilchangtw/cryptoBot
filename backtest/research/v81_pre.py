"""V81 下載前稽核：F2 因果語意（T+15 才知道的損益是否影響 T 的進場決策）、V80 帳戶回放自洽性、成本口徑、55 筆實盤成交價差。
只用既有 15 分 K 與已保存的離線檔。用法：python backtest/research/v81_pre.py"""
import os, sys, json, inspect, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v80_lib as L, v79_lib as L9, v53_common as C, v70_account as E
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v81'); os.makedirs(OUT, exist_ok=True); pd.set_option('display.width', 320); pd.set_option('display.max_columns', 60)
KEY = ['side', 't_sig']

def bookd(d15, ct15, df, k0, ind0, model, side, fund, defer):
    return L.book(d15, ct15, df, k0, dict(ind0, defer=True) if defer else ind0, model, side, fund=fund)

if __name__ == '__main__':
    d15, ct15, P, K = L.load(); fund = C.load_funding(); R = {}; IND = {ph: L9.indicators(P[ph]) for ph in L.PH}
    # ---- 成本口徑
    R['成本口徑'] = dict(FEE=L.eng.FEE, extra_cost=5.0, 每筆合計U=L.eng.FEE + 5.0, 名目=L.eng.NOTIONAL, 折合bp=round((L.eng.FEE + 5.0) / L.eng.NOTIONAL * 1e4, 2), 風控用損益='毛損益−FEE−extra_cost（日虧、月虧、連虧皆用此值）', 說明='4,000U 名目下 9U＝22.5bp，未漏計；逐筆表另扣資金費（引擎風控不含資金費）')
    # ---- F2 因果：V80 原 F2（T+15 成交的損益立即用於同一根的進場判斷）對 修正版（延後入帳）
    rows = []; diffs = []
    for ph in L.PH:
        for side in ('L', 'S', 'LS'):
            f0 = L.closed(bookd(d15, ct15, P[ph], K[ph], IND[ph], 'F0', side, fund, False)); f0d = L.closed(bookd(d15, ct15, P[ph], K[ph], IND[ph], 'F0', side, fund, True))
            a = L.closed(bookd(d15, ct15, P[ph], K[ph], IND[ph], 'F2', side, fund, False)); b = L.closed(bookd(d15, ct15, P[ph], K[ph], IND[ph], 'F2', side, fund, True))
            ka = set(map(tuple, a[KEY].values)); kb = set(map(tuple, b[KEY].values)); k0 = set(map(tuple, f0[KEY].values)); k0d = set(map(tuple, f0d[KEY].values))
            rows.append(dict(相位=f':{ph:02d}', 重播=side, F0=round(f0.net.sum()), F0延後入帳=round(f0d.net.sum()), F0交易集合差異=len(k0 ^ k0d), V80_F2=round(a.net.sum()), F2延後入帳=round(b.net.sum()), 差=round(b.net.sum() - a.net.sum()), 筆數_V80_F2=len(a), 筆數_修正=len(b), 只在V80_F2=len(ka - kb), 只在修正版=len(kb - ka)))
            for nm, x, ks in (('只在V80_F2', a, ka - kb), ('只在修正版', b, kb - ka)):
                y = x[[tuple(v) in ks for v in x[KEY].values]]; diffs.append(y.assign(相位=ph, 重播=side, 差異=nm)[['相位', '重播', '差異', 'side', 't_sig', 't_in', 't_out', 'entry_price', 'exit_price', 'exit_reason', 'net']])
    Cz = pd.DataFrame(rows); Cz.to_csv(os.path.join(OUT, 'f2_causal_compare.csv'), index=False); print(Cz.to_string(index=False)); Dz = pd.concat(diffs); Dz.to_csv(os.path.join(OUT, 'f2_causal_trade_diffs.csv'), index=False); R['F2因果'] = dict(受影響組數=int((Cz.差 != 0).sum()), 整點LS_V80=int(Cz[(Cz.相位 == ':00') & (Cz.重播 == 'LS')].V80_F2.iloc[0]), 整點LS_修正=int(Cz[(Cz.相位 == ':00') & (Cz.重播 == 'LS')].F2延後入帳.iloc[0]), 差異交易筆數=len(Dz))
    # ---- V80 帳戶回放自洽性（現行原配置 F2）
    g = d15.copy(); g['datetime'] = g.datetime + pd.Timedelta(minutes=15) - pd.Timedelta(hours=1); t = L.closed(L.book(d15, ct15, P[0], K[0], IND[0], 'F2', 'LS', fund=fund)); bk = pd.DataFrame(dict(side=t.side, t_in=t.t_in, t_out=t.t_out, ep=t.entry_price, xp=t.exit_price, reason=t.exit_reason, snm=1.0)).reset_index(drop=True)
    Rp = E.replay(g, fund, base=bk, base_mode=('fixed', 4000.0, 20.0), cross=False); sk = Rp['skips']; tr = Rp['tr']; acc = Rp['acc']; last_fill = tr.t_in.max(); first_skip = sk.t.min(); after = acc[acc.index > last_fill]
    R['V80帳戶F2自洽性'] = dict(參考路徑交易數=len(bk), 帳戶成交筆數=len(tr), 跳過筆數=len(sk), 第一筆跳過=str(first_skip), 最後一筆成交進場=str(last_fill), 最後一筆平倉=str(tr.t_out.max()), 第一筆跳過是否晚於最後成交=bool(first_skip > last_fill), 最後成交後權益最大值=round(float(after.equity.max()), 2), 最後成交後可用資金最大值=round(float(after.free.max()), 2), 之後是否永久低於200U=bool(after.free.max() < 200), 期末權益=round(float(acc.equity.iloc[-1]), 2), 跳過原因=sk.原因.value_counts().to_dict(),
                              說明='跳過的 554 筆是「不受資金限制的參考路徑」上的後續候選；若第一筆跳過晚於最後成交且之後可用資金永久不足，則資金受限的自洽狀態機也不會再有任何成交，已成交 97 筆與期末權益相同')
    print(json.dumps(R['V80帳戶F2自洽性'], ensure_ascii=False, indent=1))
    # ---- 55 筆實盤成交價
    lv = pd.read_csv(os.path.join(C.ROOT, 'doc', 'research_results', 'v57', 'live_trades_viewer_20261113.csv')); lv['t_in'] = pd.to_datetime(lv.entry_time_utc).dt.tz_localize(None); lv['t_out'] = pd.to_datetime(lv.exit_time_utc).dt.tz_localize(None); lv['exit_reason'] = lv.exit_reason.replace({'MaxHold': 'MH', 'MFE-trail': 'MFE', 'SafeNet': 'SN', 'MH-ext': 'MHx', 'MaxHold-ext': 'MHx'})
    f0 = L.book(d15, ct15, P[0], K[0], IND[0], 'F0', 'LS', fund=fund); f2 = L.book(d15, ct15, P[0], K[0], IND[0], 'F2', 'LS', fund=fund)
    m = lv.merge(f0[['side', 't_sig', 't_out', 'entry_price', 'exit_price', 'exit_reason']].rename(columns=dict(t_sig='t_in', t_out='t_out_f0', entry_price='ep_f0', exit_price='xp_f0', exit_reason='reason_f0')), on=['side', 't_in'], how='left')
    m = m.merge(f2[['side', 't_sig', 'entry_price', 'exit_price', 'exit_reason']].rename(columns=dict(t_sig='t_in', entry_price='ep_f2', exit_price='xp_f2', exit_reason='reason_f2')), on=['side', 't_in'], how='left'); sg = np.where(m.side == 'L', 1.0, -1.0)
    m['進場不利bp_對F0'] = sg * (m.entry_price / m.ep_f0 - 1) * 1e4; same_exit = (m.t_out == m.t_out_f0) & (m.exit_reason == m.reason_f0); m['出場同根同類型'] = same_exit; m['出場不利bp_對F0'] = np.where(same_exit, -sg * (m.exit_price / m.xp_f0 - 1) * 1e4, np.nan)
    lo = np.minimum(m.ep_f0, m.ep_f2); hi = np.maximum(m.ep_f0, m.ep_f2); both = m.ep_f0.notna() & m.ep_f2.notna(); m['進場價在F0與F2之外'] = both & ((m.entry_price < lo) | (m.entry_price > hi)); m['月'] = m.t_in.dt.strftime('%Y-%m')
    m.to_csv(os.path.join(OUT, 'live_price_audit.csv'), index=False)
    def dist(x): x = x.dropna(); return dict(n=len(x), 平均=round(float(x.mean()), 2), 中位數=round(float(x.median()), 2), p10=round(float(x.quantile(.1)), 2), p90=round(float(x.quantile(.9)), 2), 最小=round(float(x.min()), 2), 最大=round(float(x.max()), 2), 不利比例=round(float((x > 0).mean()), 3))
    Rl = dict(實盤筆數=len(lv), 與F0配對=int(m.ep_f0.notna().sum()), 與F0及F2都配對=int(both.sum()), 進場價落在F0與F2之外=int(m['進場價在F0與F2之外'].sum()), 未配對=m[m.ep_f0.isna()][['side', 't_in', 'exit_reason']].astype(str).to_dict('records'), 進場不利bp=dist(m['進場不利bp_對F0']), 進場不利bp_多=dist(m[m.side == 'L']['進場不利bp_對F0']), 進場不利bp_空=dist(m[m.side == 'S']['進場不利bp_對F0']), 出場同根同類型筆數=int(same_exit.sum()), 出場不利bp=dist(m['出場不利bp_對F0']),
              出場不利bp_依類型={k: dist(v['出場不利bp_對F0']) for k, v in m[same_exit].groupby('exit_reason')}, 進場不利bp_依月={k: dict(n=len(v), 平均=round(float(v.mean()), 2)) for k, v in m.groupby('月')['進場不利bp_對F0'] if v.notna().any()}, 進場價絕對偏離超過10bp=m[m['進場不利bp_對F0'].abs() > 10][['side', 't_in', 'entry_price', 'ep_f0', '進場不利bp_對F0']].round(2).astype(str).to_dict('records'), 時間欄限制='檢視器的進出場時間只記到整點，無法量測整點後幾秒成交')
    R['實盤55筆'] = Rl; print(json.dumps(Rl, ensure_ascii=False, indent=1)); json.dump(R, open(os.path.join(OUT, 'pre_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str)
