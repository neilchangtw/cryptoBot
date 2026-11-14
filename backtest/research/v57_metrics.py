"""V57 共用驗收指標：年表／月表／勝率／滾動 12 個月／回撤／連敗／分塊抽樣。口徑固定於 doc/research_results/v57/v57_plan.md。
輸入：inc（逐小時市價權益增量，UTC）、cost（逐筆：side,t_in,t_out,net,...；已完整平倉）。主比較窗 UTC 2021-01~2026-10（70 個月），近五年 2021-11~2026-10。
連敗定義：依平倉時間排序（同時平倉者依進場時間、再依 L 先 S 後）；net<0 為虧損；net>=0（含打平）中斷連敗；勝率分母含打平，net>0 才算贏。
用法（基準）：python backtest/research/v57_metrics.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C
W0, W1 = '2021-01', '2026-10'; F0 = '2021-11'

def order(c):
    c = c[(c.t_out >= '2021-01-01') & (c.t_out < '2026-11-01')].copy(); c['_s'] = (c.side == 'S').astype(int)
    return c.sort_values(['t_out', 't_in', '_s']).reset_index(drop=True)

def wr_table(c, key):
    g = c.groupby(key); w = c[c.net > 0].groupby(key).net.mean(); l = c[c.net < 0].groupby(key).net.mean()
    return pd.DataFrame({'trades': g.size(), 'wr': g.net.apply(lambda x: (x > 0).mean()).round(4), 'net': g.net.sum().round(0), 'avg_win': w.round(1), 'avg_loss': l.round(1)})

def streaks(c):
    """回傳每段連敗：n、loss、start、end、days。"""
    out = []; n = 0; loss = 0.0; st = None
    for r in c.itertuples():
        if r.net < 0:
            if n == 0: st = r.t_in
            n += 1; loss += r.net; en = r.t_out
        else:
            if n: out.append(dict(n=n, loss=loss, start=st, end=en, days=(en - st) / pd.Timedelta(days=1)))
            n = 0; loss = 0.0
    if n: out.append(dict(n=n, loss=loss, start=st, end=en, days=(en - st) / pd.Timedelta(days=1)))
    return pd.DataFrame(out)

def dd_stats(eq):
    """eq：累計權益（日頻）。回傳 MDD、最長回撤持續天數、最大回撤的復原天數（未復原記 -1）。"""
    peak = eq.cummax(); dd = peak - eq; mdd = dd.max(); t_trough = dd.idxmax(); t_peak = eq[:t_trough].idxmax()
    rec = eq[t_trough:][eq[t_trough:] >= peak[t_trough]]; rec_days = (rec.index[0] - t_trough).days if len(rec) else -1
    under = dd > 1e-9; longest = 0; cur = None
    for t, u in under.items():
        if u and cur is None: cur = t
        if not u and cur is not None: longest = max(longest, (t - cur).days); cur = None
    if cur is not None: longest = max(longest, (under.index[-1] - cur).days)
    return float(mdd), int(longest), int(rec_days), t_peak, t_trough

def evaluate(inc, cost, label='', boot=2000, seed=57):
    c = order(cost); m = inc.groupby(inc.index.to_period('M')).sum(); m = m[(m.index >= W0) & (m.index <= W1)]
    daily = inc[(inc.index >= '2021-01-01') & (inc.index < '2026-11-01')].groupby(lambda t: t.floor('D')).sum()
    R = {'label': label, 'months': len(m), 'total': round(m.sum(), 2), 'monthly_avg': round(m.mean(), 1), 'trades': len(c), 'wr': round((c.net > 0).mean(), 4), 'breakeven': int((c.net == 0).sum()),
         'avg_win': round(c[c.net > 0].net.mean(), 1), 'avg_loss': round(c[c.net < 0].net.mean(), 1), 'neg_months': int((m < 0).sum()), 'zero_trade_months': int(len(m) - c.t_out.dt.to_period('M').nunique())}
    y = m.groupby(m.index.year).sum(); R['yearly'] = {int(k): round(v) for k, v in y.items()}; R['years_positive'] = int((y > 0).sum())
    m5 = m[m.index >= F0]; r12 = m5.rolling(12).sum().dropna(); R['r12_windows'] = len(r12); R['r12_pos_share'] = round((r12 > 0).mean(), 4); R['r12_worst'] = round(r12.min()); 
    R['r12_worst_window'] = f'{r12.idxmin() - 11}~{r12.idxmin()}'; R['r12_median'] = round(r12.median())
    R['half1_2021-11~2024-04'] = round(m5.iloc[:30].sum()); R['half2_2024-05~2026-10'] = round(m5.iloc[30:].sum())
    d5 = daily[daily.index >= '2021-11-01']; r90 = d5.rolling(90).sum().dropna(); R['r90_worst'] = round(r90.min()); R['r90_worst_end'] = str(r90.idxmin().date())
    for nm, dd in (('full', daily), ('5y', d5)):
        mdd, longest, rec, tp, tt = dd_stats(dd.cumsum()); R[f'mdd_{nm}'] = round(mdd); R[f'dd_longest_days_{nm}'] = longest; R[f'mdd_recovery_days_{nm}'] = rec; R[f'mdd_span_{nm}'] = f'{tp.date()}~{tt.date()}'
    s = streaks(c); R['streak_max_n'] = int(s.n.max()); R['streak_worst_loss'] = round(s.loss.min()); w = s.loc[s.loss.idxmin()]; R['streak_worst_span'] = f'{w.start.date()}~{w.end.date()}（{int(w.n)} 筆）'
    R['streak4_per100'] = round((s.n >= 4).sum() / len(c) * 100, 2); R['streak6_per100'] = round((s.n >= 6).sum() / len(c) * 100, 2); R['streak_max_days'] = round(s[s.n >= 4].days.max(), 1) if (s.n >= 4).any() else 0
    s['year'] = s.end.dt.year; R['streak_by_year'] = {int(k): dict(max_n=int(v.n.max()), worst_loss=round(v.loss.min()), n4=int((v.n >= 4).sum()), n6=int((v.n >= 6).sum())) for k, v in s.groupby('year')}
    # 分塊抽樣（月為塊，保留月內時間相依；抽 70 個月）：MDD 與最差連敗段的尾部
    rng = np.random.default_rng(seed); c['ym'] = c.t_out.dt.to_period('M'); by = {k: v.net.values for k, v in c.groupby('ym')}; months = list(m.index); bm = []; bs = []; bn = []
    for _ in range(boot):
        pick = rng.integers(0, len(months) - 2, len(months) // 3 + 1); seq = np.concatenate([np.concatenate([by.get(months[p + j], np.empty(0)) for j in range(3)]) for p in pick])
        if len(seq) == 0: continue   # V80：抽到的月份全無交易時略過（避免空陣列錯誤；亂數序列不變）
        e = np.cumsum(seq); bm.append((np.maximum.accumulate(e) - e).max()); n = 0; l = 0.0; wl = 0.0; wn = 0
        for x in seq:
            if x < 0: n += 1; l += x; wl = min(wl, l); wn = max(wn, n)
            else: n = 0; l = 0.0
        bs.append(wl); bn.append(wn)
    R['boot_mdd_p50_p95'] = [round(np.quantile(bm, .5)), round(np.quantile(bm, .95))]; R['boot_streakloss_p50_p95'] = [round(np.quantile(bs, .5)), round(np.quantile(bs, .05))]; R['boot_streakn_p50_p95'] = [int(np.quantile(bn, .5)), int(np.quantile(bn, .95))]
    tabs = dict(year=wr_table(c, c.t_out.dt.year), side=wr_table(c, 'side'), year_side=wr_table(c, [c.t_out.dt.year, 'side']), monthly=m, r12=r12, streaks=s)
    return R, tabs

def baseline():
    import v53_baseline as B
    df = C.load_1h(end='2026-11-01'); tr = B.baseline_trades(df); inc, cost = C.mtm_equity(tr, df); cost['reason'] = tr.reason.values
    return df, inc, cost

if __name__ == '__main__':
    OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v57'); os.makedirs(OUT, exist_ok=True)
    df, inc, cost = baseline(); R, T = evaluate(inc, cost, '基準 V14+R+V25-D')
    print(json.dumps(R, ensure_ascii=False, indent=1, default=str)); print(T['year'].to_string()); print(T['side'].to_string()); print(T['year_side'].to_string())
    json.dump(R, open(os.path.join(OUT, 'baseline_metrics.json'), 'w'), ensure_ascii=False, indent=1, default=str)
    T['year_side'].to_csv(os.path.join(OUT, 'baseline_year_side.csv')); T['monthly'].round(2).to_csv(os.path.join(OUT, 'baseline_monthly.csv')); T['r12'].round(2).to_csv(os.path.join(OUT, 'baseline_rolling12.csv')); T['streaks'].to_csv(os.path.join(OUT, 'baseline_streaks.csv'), index=False)
    cost.to_csv(os.path.join(OUT, 'baseline_trades.csv'), index=False)
