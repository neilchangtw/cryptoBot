"""V53 共用：資料載入、逐小時市價權益（UTC 年度歸屬）、成本逐項、年表／月表。

資料：data/ETHUSDT_1h_2020_2026.csv（Binance USDⓈ-M 永續 1h，data.binance.vision 月檔＋日檔，datetime=開盤時間 UTC+8）
      data/ETHUSDT_funding_2020_2026.csv（data.binance.vision fundingRate 月檔，calc_time=UTC ms）
交易規格：每筆 200U×20=名目 $4,000，固定不複利。
成本：手續費＋基本滑價 $4／筆（taker 0.04%×2＋0.01%×2，進出各半計入）；執行成本緩衝 $5／筆（專案 run_backtest 預設，進出各半）；
      另加 slip_bps（壓測用，已反映在成交價）；funding 於結算時點依持倉方向計入（多付正費率、空收）。三者不重疊：$4 不含 funding，$5 為實盤對帳緩衝。
"""
import os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NTL = 4000.0; FEE = 4.0; EXTRA = 5.0

def load_1h(end=None):
    df = pd.read_csv(os.path.join(ROOT, 'data', 'ETHUSDT_1h_2020_2026.csv'), parse_dates=['datetime'])
    if end is not None: df = df[df.datetime < pd.Timestamp(end) + pd.Timedelta(hours=8)].reset_index(drop=True)
    return df

def load_funding():
    f = pd.read_csv(os.path.join(ROOT, 'data', 'ETHUSDT_funding_2020_2026.csv'))
    t = pd.to_datetime(f.calc_time, unit='ms').dt.round('h')
    return pd.Series(f.last_funding_rate.values, index=t).sort_index()

def close_utc(df):
    """每根 1h 收盤的 UTC 時間與收盤價。"""
    return pd.Series(df.close.values, index=df.datetime - pd.Timedelta(hours=7))

def mtm_equity(trades, df, fund=None, fee=FEE, extra=EXTRA):
    """trades: side(L/S), t_in, t_out（UTC 成交時間，整點）, ep, xp, ntl(可省)。回傳逐小時權益增量 Series（UTC）與逐筆成本表。"""
    px = close_utc(df); idx = px.index; inc = pd.Series(0.0, index=idx); rows = []
    fund = load_funding() if fund is None else fund
    pos = {t: i for i, t in enumerate(idx)}; pv = px.values; iv = np.zeros(len(idx))
    for r in trades.itertuples():
        s = 1.0 if r.side == 'L' else -1.0; ntl = getattr(r, 'ntl', NTL); q = ntl / r.ep
        i0 = pos[r.t_in]; i1 = pos[r.t_out]
        path = pv[i0:i1 + 1].copy(); path[0] = r.ep; path[-1] = r.xp
        iv[i0 + 1:i1 + 1] += s * q * np.diff(path)
        c = (fee + extra) * ntl / NTL; iv[i0] -= c / 2; iv[i1] -= c / 2
        fr = fund[(fund.index > r.t_in) & (fund.index <= r.t_out)]; fpay = 0.0
        for ft, rate in fr.items():
            if ft in pos:
                pay = s * rate * q * pv[pos[ft]]; iv[pos[ft]] -= pay; fpay += pay
        gross = s * q * (r.xp - r.ep)
        rows.append(dict(side=r.side, t_in=r.t_in, t_out=r.t_out, ep=r.ep, xp=r.xp, gross=gross, fee=fee * ntl / NTL, extra=extra * ntl / NTL, funding=fpay, net=gross - c - fpay))
    return pd.Series(iv, index=idx), pd.DataFrame(rows)

def tables(inc, tr, last_full_month=None):
    """年表、月表（UTC，市價權益歸屬）＋已實現（依出場時間）。"""
    m = inc.groupby(inc.index.to_period('M')).sum()
    m = m[(m.index >= '2021-01') & ((m.index <= last_full_month) if last_full_month else True)]
    eq = inc[inc.index >= '2021-01-01'].cumsum(); 
    rows = []
    for y in sorted(set(m.index.year)):
        mm = m[m.index.year == y]; e = inc[inc.index.year == y]
        if last_full_month: e = e[e.index.to_period('M') <= last_full_month]
        c = e.cumsum(); dd = (c.cummax() - c).max()
        t = tr[tr.t_out.dt.year == y] if len(tr) else tr
        rows.append(dict(year=y, months=len(mm), net_mtm=round(mm.sum()), monthly_avg=round(mm.sum() / len(mm)), realized=round(t.net.sum()) if len(t) else 0, trades=len(t),
                         fee=round(t.fee.sum()) if len(t) else 0, extra=round(t.extra.sum()) if len(t) else 0, funding=round(t.funding.sum()) if len(t) else 0,
                         mdd_in_year=round(dd), worst_month=round(mm.min()), neg_months=int((mm < 0).sum()),
                         pass_year=bool(mm.sum() > 0 and (y == 2026 or mm.sum() / len(mm) >= 300))))
    return pd.DataFrame(rows), m
