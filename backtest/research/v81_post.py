"""V81 補充稽核（登記後追加的描述性診斷，已列入試驗紀錄）：
(1) 55 筆實盤成交價對 1 分 K：實盤進場／出場價落在整點後第幾分鐘的高低範圍內；極端值查核。
(2) 帳戶回放中觸及簡化強平線的交易：以 1 分 K 判斷止損價與強平線何者先被觸及。
用法：python backtest/research/v81_post.py"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v81_lib as U, v53_common as C
OUT = U.OUT; M = U.m1(); A = {}
lv = pd.read_csv(os.path.join(OUT, 'live_price_audit.csv')); print(lv.columns.tolist())
live = pd.read_csv(os.path.join(C.ROOT, 'doc', 'research_results', 'v57', 'live_trades_viewer_20261113.csv'))
live['t_in'] = pd.to_datetime(live.entry_time_utc, utc=True).dt.tz_localize(None).dt.floor('h'); live['t_out'] = pd.to_datetime(live.exit_time_utc, utc=True).dt.tz_localize(None).dt.floor('h'); live['t_in_raw'] = live.entry_time_utc; live['t_out_raw'] = live.exit_time_utc; live = live[live.t_out <= U.W1].reset_index(drop=True)
def first_minute_in_range(t, px, span=60):
    i = U.mi([t])[0]
    for m in range(span):
        if M['low'][i + m] - 0.011 <= px <= M['high'][i + m] + 0.011: return m
    return -1
rows = []
for r in live.itertuples():
    sg = 1.0 if r.side == 'L' else -1.0; i = U.mi([r.t_in])[0]; j = U.mi([r.t_out])[0]
    rows.append(dict(side=r.side, t_in=r.t_in, t_out=r.t_out, exit_reason=r.exit_reason, 實盤進場價=r.entry_price, T收盤價=M['close'][i - 1], 進場不利bp_對T收盤=round(sg * (r.entry_price / M['close'][i - 1] - 1) * 1e4, 2), 進場不利bp_對T加1分=round(sg * (r.entry_price / M['close'][i] - 1) * 1e4, 2),
                     進場價首次落入的分鐘=first_minute_in_range(r.t_in, r.entry_price), 實盤出場價=r.exit_price, 出場T收盤價=M['close'][j - 1], 出場不利bp_對T收盤=round(-sg * (r.exit_price / M['close'][j - 1] - 1) * 1e4, 2), 出場價首次落入的分鐘=first_minute_in_range(r.t_out, r.exit_price)))
X = pd.DataFrame(rows); X.to_csv(os.path.join(OUT, 'live_vs_1m.csv'), index=False); pd.set_option('display.width', 300); pd.set_option('display.max_rows', 100); print(X.to_string(index=False))
def dist(x): x = np.asarray(x, float); return dict(n=len(x), 平均=round(float(x.mean()), 2), 中位數=round(float(np.median(x)), 2), p10=round(float(np.quantile(x, .1)), 2), p90=round(float(np.quantile(x, .9)), 2), 最小=round(float(x.min()), 2), 最大=round(float(x.max()), 2), 不利比例=round(float((x > 0).mean()), 3))
A['實盤筆數_截止前'] = len(X); A['進場_對T收盤bp'] = dist(X.進場不利bp_對T收盤); A['進場_對T加1分bp'] = dist(X.進場不利bp_對T加1分); A['出場_對T收盤bp_全部'] = dist(X.出場不利bp_對T收盤)
A['出場_對T收盤bp_依類型'] = {k: dist(g.出場不利bp_對T收盤) for k, g in X.groupby('exit_reason')}
A['進場價首次落入分鐘_分布'] = {str(k): int(v) for k, v in X.進場價首次落入的分鐘.value_counts().sort_index().items()}; A['出場價首次落入分鐘_分布'] = {str(k): int(v) for k, v in X.出場價首次落入的分鐘.value_counts().sort_index().items()}
A['進場_依邊'] = {k: dist(g.進場不利bp_對T收盤) for k, g in X.groupby('side')}; A['進場_依月'] = {k: dist(g.進場不利bp_對T收盤) for k, g in X.groupby(X.t_in.dt.strftime('%Y-%m'))}
# 極端值：2026-06-25 空單、2026-08-20 多單
for t, px in (('2026-06-25 14:00', 1549.84), ('2026-08-20 11:00', 2283.13)):
    i = U.mi([pd.Timestamp(t)])[0]; w = pd.DataFrame(dict(分鐘=range(-3, 8), open=M['open'][i - 3:i + 8], high=M['high'][i - 3:i + 8], low=M['low'][i - 3:i + 8], close=M['close'][i - 3:i + 8], volume=M['volume'][i - 3:i + 8])); print(t, px); print(w.to_string(index=False)); A[f'極端值_{t}'] = dict(實盤價=px, 首次落入分鐘=first_minute_in_range(pd.Timestamp(t), px, 120), 前一小時內落入=bool(((M['low'][i - 60:i] <= px) & (M['high'][i - 60:i] >= px)).any()))
# (2) 強平線與止損先後
liq = []
for k in U.DELAYS:
    p = os.path.join(OUT, f'acct_orig_D{k}_trades.csv'); t = pd.read_csv(p, parse_dates=['t_in', 't_out']); t = t[t.liq]
    for r in t.itertuples():
        s = 1 if r.side == 'L' else -1; sn = 0.035 if s > 0 else 0.04; stop = r.ep * (1 - s * sn); lq = r.ep * (1 - s / 20.0) / (1 - s * 0.005); a = U.mi([r.t_in])[0]; b = U.mi([r.t_out])[0] + (60 if r.reason == 'SN' and r.t_out.minute == 0 and False else 0)
        if r.reason == 'SN' and r.t_out == r.t_out.floor('h') + pd.Timedelta(minutes=0): a2 = max(a, b - 60)
        lo = M['low'][a:b]; hi = M['high'][a:b]; adv = lo if s > 0 else hi; hs = np.where((adv <= stop) if s > 0 else (adv >= stop))[0]; hl = np.where((adv <= lq) if s > 0 else (adv >= lq))[0]
        fs = int(hs[0]) if len(hs) else -1; fl = int(hl[0]) if len(hl) else -1
        cls = '持倉期間未觸及強平線（觸及發生在止損出場之後的同一格點）' if fl < 0 else ('止損價先於強平線（不同分鐘）' if 0 <= fs < fl else ('同一分鐘：資料不足以判斷先後' if fs == fl else '強平線先於止損價'))
        liq.append(dict(情境=f'D{k}', side=r.side, t_in=r.t_in, t_out=r.t_out, reason=r.reason, ep=r.ep, 止損價=round(stop, 2), 簡化強平價=round(lq, 2), 止損首次觸及分鐘=fs, 強平線首次觸及分鐘=fl, 分類=cls))
LQ = pd.DataFrame(liq); LQ.to_csv(os.path.join(OUT, 'liq_order_1m.csv'), index=False); A['強平線先後_現行原配置'] = {k: g.分類.value_counts().to_dict() for k, g in LQ.groupby('情境')}; print(json.dumps(A, ensure_ascii=False, indent=1))
json.dump(A, open(os.path.join(OUT, 'post_audit.json'), 'w'), ensure_ascii=False, indent=1, default=str)
