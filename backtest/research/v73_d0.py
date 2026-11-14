"""V73 D0：選擇權偏斜方向腿（獨立、固定 750U 名目、不設止損）。登記：doc/research_results/v73/v73_plan.md 第 5 節。
用法：python backtest/research/v73_d0.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v70_account as E, v70_recon as RC, v60_d0 as S, v53_common as C
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v73'); G = 0.5; NTL = G * E.CAP

def skew_sig(dte=(7, 45), R=180, q=0.2):
    sk = S.daily_skew(dte); sig = S.signal(sk, R=R, q=q); full = pd.date_range('2020-10-01', '2026-11-01', freq='D'); return sig.reindex(full).fillna(0).astype(int)

def hourly_pos(sig, idx, delay=2):
    a = pd.Series(sig.values, index=sig.index + pd.Timedelta(hours=delay)); return a.reindex(idx, method='ffill').fillna(0).values.astype(float)

def fnet(q, r, fr, mask=None):
    """q[i]＝第 i 根收盤後持有的方向；損益記在下一根。回傳每 1U 名目的淨額。"""
    h = np.r_[0.0, q[:-1]]; pnl = h * r - h * fr - 0.001125 * np.abs(np.diff(np.r_[0.0, q]))
    return pnl.sum() if mask is None else pnl[mask].sum()

if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; sig = skew_sig()
    R = E.replay(df, fund, sleeve=dict(sig=sig, mode='hold', g=G, lev=5.0, delay=2), cross=True, fixed_basis=True)
    t = R['tr']; a = R['acc']; e = a.equity; de = e.diff().fillna(0.0); yr = de.groupby(de.index.year).sum().round(1)
    weak = (de.index.year == 2021) | (de.index.year == 2023) | ((de.index >= '2026-07-01') & (de.index < '2026-11-01'))
    vr = X['st'].volrank.reindex(de.index).ffill(); pause = (vr < 20).values
    L = t[t.side == 'L']; Sh = t[t.side == 'S']; top5 = t.net.nlargest(5).sum(); tot = float(e.iloc[-1] - E.CAP)
    # 環狀平移
    idx = e.index; v = C.close_utc(df).reindex(idx).values; r = np.r_[0.0, np.diff(v) / v[:-1]]; fr = fund.reindex(idx).fillna(0).values; q = hourly_pos(sig, idx)
    obs = fnet(q, r, fr) * NTL; obsw = fnet(q, r, fr, weak) * NTL; obsp = fnet(q, r, fr, pause) * NTL
    rng = np.random.default_rng(73); ks = rng.integers(30 * 24, len(q) - 30 * 24, 2000); sim = np.array([[fnet(np.roll(q, k), r, fr), fnet(np.roll(q, k), r, fr, weak), fnet(np.roll(q, k), r, fr, pause)] for k in ks]) * NTL
    p_all = float((sim[:, 0] >= obs).mean()); p_weak = float((sim[:, 1] >= obsw).mean()); p_pause = float((sim[:, 2] >= obsp).mean())
    # 參考：均線 200 趨勢腿縮放到相同平均絕對曝險
    tq = hourly_pos(RC.trend_sig(X['dc'], 200).dropna(), idx, delay=1); sc = np.abs(q).mean() / max(np.abs(tq).mean(), 1e-9)
    res = dict(筆數=len(t), 勝率=round((t.net > 0).mean() * 100, 2), 全期淨=round(tot, 1), 逐年=yr.to_dict(), 弱勢期淨=round(float(de[weak].sum()), 1), 暫停時段淨=round(float(de[pause].sum()), 1), 暫停時段占比=round(float(pause.mean()), 3),
               多方=dict(筆=len(L), 淨=round(L.net.sum(), 1), 勝率=round((L.net > 0).mean() * 100, 1)), 空方=dict(筆=len(Sh), 淨=round(Sh.net.sum(), 1), 勝率=round((Sh.net > 0).mean() * 100, 1)),
               成本=round(t.cost.sum(), 1), 資金費=round(t.fund.sum(), 1), 扣最大5筆=round(tot - top5, 1), 最大單年占比=round(float(yr.max() / tot), 2) if tot > 0 else None, 持倉時間占比=round(float(np.abs(q).mean()), 3), 淨方向=round(float(q.mean()), 3),
               公式口徑_全期=round(obs, 1), 公式口徑_弱勢=round(obsw, 1), 公式口徑_暫停=round(obsp, 1), p全期=p_all, p弱勢=p_weak, p暫停=p_pause, 平移中位_全期=round(float(np.median(sim[:, 0])), 1), 平移中位_弱勢=round(float(np.median(sim[:, 1])), 1),
               參考_趨勢腿同曝險_全期=round(fnet(tq, r, fr) * NTL * sc, 1), 參考_趨勢腿同曝險_弱勢=round(fnet(tq, r, fr, weak) * NTL * sc, 1),
               最低權益=round(float(a.equity_intrahour.min()), 1), 最大回撤金額=round(float((e.cummax() - e).max()), 1), 最差單筆=round(float(t.net.min()), 1), 強平=R['liq_acct'], 跳過=len(R['skips']))
    pos = sum(1 for y in range(2021, 2027) if yr.get(y, 0) > 0)
    K = dict(K1=bool(tot > 0 and pos >= 5 and yr.get(2021, 0) > 0 and yr.get(2023, 0) > 0), K2=bool(p_all < 0.05), K3=bool(res['弱勢期淨'] > 0 and p_weak < 0.10), K4=bool(res['暫停時段淨'] > 0), K5=bool(L.net.sum() > 0 and Sh.net.sum() > 0), K6=bool(tot - top5 > 0 and tot > 0 and yr.max() / tot <= 0.5))
    res.update(K); res['判定'] = 'PASS→D1' if all(K.values()) else 'REJECTED（D0）：' + ','.join(k for k, v in K.items() if not v)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str)); json.dump(res, open(os.path.join(OUT, 'v73_d0.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    t.to_csv(os.path.join(OUT, 'v73_d0_trades.csv'), index=False); a[['equity', 'wallet', 'notional']].to_csv(os.path.join(OUT, 'v73_d0_account_hourly.csv'))
