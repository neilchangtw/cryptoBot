"""V71 執行一致性離線最小驗證：獨立的交易所端模型（Hedge Mode 合併部位、平均成本、全倉錢包、指定數量市價／止損單），
把 V70 帳戶引擎的全部成交當作訂單重播，逐小時對帳；另跑 5 個單元情境。用法：python backtest/research/v71_exec_check.py"""
import os, sys, json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v70_account as E, v70_recon as RC
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v71')

class Exchange:
    """只依交易所規則記帳：每個 positionSide 一個合併部位；減倉以平均成本實現損益；全倉錢包；減倉量不得超過部位。"""
    def __init__(s, wallet, mmr=0.005): s.w = wallet; s.pos = {'LONG': [0.0, 0.0], 'SHORT': [0.0, 0.0]}; s.mmr = mmr; s.rej = 0
    def order(s, ps, dq, px, close_position=False):
        q, a = s.pos[ps]; sg = 1 if ps == 'LONG' else -1
        if close_position: dq = -q
        if dq >= 0: s.pos[ps] = [q + dq, (a * q + px * dq) / (q + dq) if q + dq > 0 else 0.0]; return dq
        if -dq > q + 1e-9: s.rej += 1; return 0.0
        s.w += sg * (-dq) * (px - a); s.pos[ps] = [q + dq, a if q + dq > 1e-12 else 0.0]; return dq
    def funding(s, rate, px):
        for ps, sg in (('LONG', 1), ('SHORT', -1)): s.w -= sg * rate * s.pos[ps][0] * px
    def unreal(s, px): return s.pos['LONG'][0] * (px - s.pos['LONG'][1]) - s.pos['SHORT'][0] * (px - s.pos['SHORT'][1])
    def margin_balance(s, px): return s.w + s.unreal(px)
    def maint(s, px): return s.mmr * (s.pos['LONG'][0] + s.pos['SHORT'][0]) * px
    def liquidated(s, px): return s.margin_balance(px) <= s.maint(px)

def reconcile(R, df, fund, cap=E.CAP):
    acc = R['acc']; idx = acc.index; px = C.close_utc(df).reindex(idx).values; fr = fund.reindex(idx).fillna(0).values; f = R['fills'].sort_values('t', kind='stable'); g = {t: x for t, x in f.groupby('t')}
    ex = Exchange(cap); diff = np.zeros(len(idx)); mx = {'LONG': 0, 'SHORT': 0}; both = 0
    for i, t in enumerate(idx):
        if fr[i] != 0: ex.funding(fr[i], px[i])
        if t in g:
            x = g[t]
            for r in pd.concat([x[x.dq < 0], x[x.dq > 0]]).itertuples(): ex.order('LONG' if r.s > 0 else 'SHORT', r.dq, r.px)
            legs = x.groupby('s').leg.nunique(); both += int((legs > 1).any())
        diff[i] = ex.margin_balance(px[i]) - acc.equity.values[i]
    cost = R['tr'].cost.sum(); d = np.diff(np.r_[0, diff])
    return dict(期末差額=round(diff[-1], 6), 引擎成本合計=round(cost, 6), 差額等於累計成本=bool(abs(diff[-1] - cost) < 1e-6), 差額單調不減=bool((d > -1e-7).all()), 被拒訂單=ex.rej, 期末部位=[round(ex.pos['LONG'][0], 6), round(ex.pos['SHORT'][0], 6)], 成交筆數=len(f), 同一小時同方向兩腿都有成交的小時數=both)

def unit_cases():
    out = []
    ex = Exchange(1500); ex.order('LONG', 0.5, 1000); ex.order('LONG', 1.2, 1100); q0 = ex.pos['LONG'][0]; ex.order('LONG', -1.2, 1061.5)   # 現行腿止損：指定數量
    out.append(dict(情境='① 同向合併後，指定數量止損只平現行腿', 合併數量=q0, 止損後剩餘=round(ex.pos['LONG'][0], 6), 預期剩餘=0.5, 通過=abs(ex.pos['LONG'][0] - 0.5) < 1e-9))
    ex2 = Exchange(1500); ex2.order('LONG', 0.5, 1000); ex2.order('LONG', 1.2, 1100); ex2.order('LONG', 0, 1061.5, close_position=True)
    out.append(dict(情境='② 現行實盤的 closePosition 止損會把趨勢腿一起平掉', 止損後剩餘=ex2.pos['LONG'][0], 通過=ex2.pos['LONG'][0] == 0, 說明='證明實盤現有止損方式不能直接沿用'))
    ex.order('LONG', -0.5, 1200); leg = 0.5 * (1200 - 1000) + 1.2 * (1061.5 - 1100); out.append(dict(情境='③ 全部平倉後，合併平均成本的已實現＝分腿帳本', 交易所已實現=round(ex.w - 1500, 6), 分腿帳本=round(leg, 6), 通過=abs(ex.w - 1500 - leg) < 1e-9))
    ex3 = Exchange(1500); ex3.order('LONG', 1.0, 1000); ex3.order('LONG', -0.2, 1300); out.append(dict(情境='④ 部分減倉：減 0.2 後數量與已實現', 剩餘=round(ex3.pos['LONG'][0], 6), 已實現=round(ex3.w - 1500, 6), 通過=abs(ex3.pos['LONG'][0] - 0.8) < 1e-9 and abs(ex3.w - 1560) < 1e-9))
    r = ex3.order('LONG', -5.0, 1300); out.append(dict(情境='⑤ 減倉量超過部位會被拒絕', 被拒=ex3.rej, 通過=ex3.rej == 1 and r == 0.0))
    ex4 = Exchange(1500); ex4.order('LONG', 3.0, 1000); ex4.order('SHORT', 1.0, 1000); p = 250.0; out.append(dict(情境='⑥ 全倉帳戶風險：多 3、空 1（1000 進場、錢包 1500），理論強平價 252.5；價格 250 時的保證金餘額與維持保證金', 保證金餘額=round(ex4.margin_balance(p), 2), 維持保證金=round(ex4.maint(p), 2), 觸發強平=ex4.liquidated(p), 價格750時觸發=ex4.liquidated(750.0), 通過=ex4.liquidated(p) and not ex4.liquidated(750.0)))
    return out

if __name__ == '__main__':
    X = RC.setup(); df, fund = X['df'], X['fund']; sig = RC.trend_sig(X['dc'])
    R = E.replay(df, fund, base=X['q20'], base_mode=('risk', 0.03, 5.0), sleeve=dict(sig=sig, mode='rebal', g=0.25, band=0.20, lev=5.0), cross=True)
    rec = reconcile(R, df, fund); print(json.dumps(rec, ensure_ascii=False)); u = unit_cases()
    for x in u: print(json.dumps(x, ensure_ascii=False))
    a = R['acc']; print('全倉風險：小時內保守權益÷總維持保證金 最小值', round((a.equity_intrahour / (0.005 * a.notional.replace(0, np.nan))).min(), 1), '；名目÷權益 最大', round((a.notional / a.equity).max(), 2))
    json.dump(dict(reconcile=rec, units=u), open(os.path.join(OUT, 'exec_check.json'), 'w'), ensure_ascii=False, indent=1, default=str)
