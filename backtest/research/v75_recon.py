"""V75 前置：V74 成本與比較口徑核對。① 2026-11 資金費以溢價指數估計並做敏感度；② 策略與資金配置貢獻（相同曝險規則、固定基數、複利完整重播）。
用法：python backtest/research/v75_recon.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC, v74_run as R74
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v75'); os.makedirs(OUT, exist_ok=True); RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0)

def fund_variants(fund):
    e = pd.read_csv(os.path.join(C.ROOT, 'data', 'ETHUSDT_funding_est_2026_11.csv'), index_col=0, parse_dates=True).est_funding; e = e[e.index > fund.index.max()]
    mk = lambda x: pd.concat([fund, x]).sort_index()
    return {'缺值以0代入': fund, '溢價指數估計': mk(e), '估計+1bp/8h': mk(e + 0.0001), '估計-1bp/8h': mk(e - 0.0001), '估計+3bp/8h': mk(e + 0.0003), '估計-3bp/8h': mk(e - 0.0003)}

if __name__ == '__main__':
    X2 = R74.setup('2026-11-14'); df2 = X2['df']; rows = []
    cfg = {'現行原配置': dict(base=X2['base'], base_mode=FIX, cross=False), 'Q20W30＋固定風險': dict(base=X2['q20'], base_mode=RISK, cross=True), 'V70': dict(base=X2['q20'], base_mode=RISK, cross=True, sleeve=R74.sl(RC.trend_sig(X2['dc'], 200))), 'V74': dict(base=X2['q20'], base_mode=RISK, cross=True, sleeve=R74.sl(R74.ens_sig(X2['dc'])))}
    for fn, f in fund_variants(X2['fund']).items():
        for nm, kw in cfg.items():
            R = E.replay(df2, f, end=R74.LATEST, **kw); e = R['acc'].equity; t = R['tr']; rows.append(dict(資金費假設=fn, 版本=nm, 全期淨利=round(e.iloc[-1] - E.CAP, 1), 年初至今2026=round(e.iloc[-1] - e[:'2025-12-31 23:00'].iloc[-1], 1), 十一月淨=round(e.iloc[-1] - e[:'2026-10-31 23:00'].iloc[-1], 1), 十一月資金費=round(t[t.t_out > '2026-11-01'].fund.sum(), 2), 最低權益=round(e.min(), 1)))
    F = pd.DataFrame(rows); F.to_csv(os.path.join(OUT, 'funding_sensitivity.csv'), index=False); print(F.pivot(index='資金費假設', columns='版本', values='全期淨利').to_string()); print(F.pivot(index='資金費假設', columns='版本', values='十一月淨').to_string())
    # ② 貢獻分解（正式期間，截至 2026-10-31，資金費完整）
    X = R74.setup('2026-11-01'); df, fund, sig = X['df'], X['fund'], R74.ens_sig(X['dc']); out = []
    def one(nm, book, mode, sleeve, fixed, cross=True):
        kw = dict(cross=cross, fixed_basis=fixed, base=X[book], base_mode=mode)
        if sleeve: kw['sleeve'] = R74.sl(sig)
        R = E.replay(df, fund, **kw); e = R['acc'].equity; t = R['tr']; b = t[t.leg == 'base']; s = t[t.leg == 'trend']; pk = np.maximum(e.cummax(), E.CAP); y = R74.yearly(e)
        d = dict(配置=nm, 訊號='全部' if book == 'base' else 'Q20W30', 下單='固定4,000U名目20倍' if mode[0] == 'fixed' else '固定風險3%', 基數='固定1,500U' if fixed else ('不適用' if mode[0] == 'fixed' and not sleeve else '錢包複利'), 趨勢腿='有' if sleeve else '無', 淨利=round(e.iloc[-1] - E.CAP), 現行腿淨=round(b.net.sum()), 趨勢腿淨=round(s.net.sum()) if len(s) else 0, **{f'y{k}': round(v) for k, v in y.items()},
                 筆數=len(t), 勝率=round((t.net > 0).mean() * 100, 2), 平均名目_現行腿=round(b.ntl.mean()), 最低權益=round(e.min()), 最大百分比回撤=round(((pk - e) / pk).max() * 100, 1), 跳過=len(R['skips']), 強平=R['liq_acct'] if cross else int(t.liq.sum()), 最低可用資金=round(R['acc'].free.min()))
        out.append(d); return d
    one('A 現行原配置', 'base', FIX, False, False, cross=False); one('B Q20W30 訊號＋原名目規則', 'q20', FIX, False, False, cross=False)
    for fixed in (True, False):
        for book in ('base', 'q20'):
            for sl in (False, True): one(f"{'固定基數' if fixed else '複利'}｜{'全部訊號' if book == 'base' else 'Q20W30'}｜{'含趨勢腿' if sl else '無趨勢腿'}", book, RISK, sl, fixed)
    T = pd.DataFrame(out); T.to_csv(os.path.join(OUT, 'contribution.csv'), index=False); pd.set_option('display.width', 250); print(T[['配置', '淨利', '現行腿淨', '趨勢腿淨', 'y2021', 'y2022', 'y2023', 'y2024', 'y2025', 'y2026', '筆數', '勝率', '平均名目_現行腿', '最低權益', '最大百分比回撤', '跳過', '強平', '最低可用資金']].to_string(index=False))
