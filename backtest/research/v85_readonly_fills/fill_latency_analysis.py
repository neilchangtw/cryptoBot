#!/usr/bin/env python3
"""V85 成交時間分析（方法事前固定；在使用者提供 extract_fills_readonly.py 的輸出後才在研究環境執行）。
本輪只以合成資料自我測試，未用任何實盤資料執行。

配對與度量（固定，不因結果更改）：
1. 以交易所 orderId 為單位，把同一訂單的逐筆成交合併：成交量加權均價、第一筆與最後一筆成交毫秒時刻、總量、總費用。
2. 訂單類型分組：MARKET（策略整點後的市價進出場）與 STOP_MARKET／TAKE_PROFIT_MARKET 觸發單（SafeNet 等，不屬於整點決策，另列）。
3. 參考 K 線收盤時刻 T：市價單取「訂單建立時刻向下取整到整點」。訂單建立晚於整點 30 分鐘者不歸入任何 T（另列）。
4. 三段延遲（毫秒）：T→訂單建立（order.time）、訂單建立→最後成交、T→最後成交；另列 T→第一筆成交。
5. 方向性成交價差（bp）：買單 (均價÷P_T−1)×1e4，賣單取負號；P_T＝ETHUSDT 永續 1h K 在 T 的收盤價。正值代表對策略不利。
6. 與研究成交模型對照只報「落在哪一分鐘」與價差分布；F0（T 收盤）與 F2（延後 15 分）不是成交價的上下界。
7. 缺少本機時間戳（程式醒來、K 線取得完成、送單）時，「T→訂單建立」無法再拆成內部各段，報告中明標。
8. 實盤樣本只代表其期間與配置，不外推為 2021～2026 的獨立樣本外績效。
用法：python fill_latency_analysis.py TRADES.csv ORDERS.csv KLINE_1H.csv 輸出目錄
      python fill_latency_analysis.py --selftest
"""
import sys, os, json
import numpy as np, pandas as pd


def analyze(trades, orders, kl):
    t = trades.copy(); o = orders.copy()
    for c in ('price', 'qty', 'commission'): t[c] = t[c].astype(float)
    t['time'] = t['time'].astype('int64')
    g = t.groupby('orderId').apply(lambda d: pd.Series(dict(vwap=(d.price * d.qty).sum() / d.qty.sum(), qty=d.qty.sum(), fee=d.commission.sum(), n_fills=len(d),
                                                         first_ms=d.time.min(), last_ms=d.time.max(), side=d.side.iloc[0], positionSide=d.positionSide.iloc[0])), include_groups=False).reset_index()
    o = o[['orderId', 'time', 'updateTime', 'type', 'origType', 'status', 'reduceOnly']].rename(columns={'time': 'create_ms', 'updateTime': 'update_ms'})
    m = g.merge(o, on='orderId', how='left')
    m['kind'] = np.where(m.origType.fillna(m.type).isin(['STOP_MARKET', 'TAKE_PROFIT_MARKET', 'STOP', 'TAKE_PROFIT']), '觸發單', np.where(m.create_ms.isna(), '缺訂單資料', '市價單'))
    hour = 3600_000
    m['T_ms'] = np.where(m.kind == '市價單', (m.create_ms // hour) * hour, np.nan)
    late = (m.create_ms - m.T_ms) > 30 * 60_000; m.loc[late, 'kind'] = '市價單_非整點'; m.loc[late, 'T_ms'] = np.nan
    kl = kl.set_index('close_ms').close
    m['P_T'] = m.T_ms.map(kl)
    sgn = np.where(m.side == 'BUY', 1.0, -1.0)
    m['slip_bp'] = sgn * (m.vwap / m.P_T - 1) * 1e4
    m['T_to_create_ms'] = m.create_ms - m.T_ms; m['create_to_last_ms'] = m.last_ms - m.create_ms; m['T_to_last_ms'] = m.last_ms - m.T_ms; m['T_to_first_ms'] = m.first_ms - m.T_ms
    m['fill_minute_after_T'] = np.floor(m.T_to_last_ms / 60_000)
    mk = m[m.kind == '市價單']
    def d(x): x = x.dropna(); return dict(n=int(len(x)), 中位=float(x.median()) if len(x) else None, p90=float(x.quantile(.9)) if len(x) else None, 最大=float(x.max()) if len(x) else None)
    summ = dict(訂單數={k: int(v) for k, v in m.kind.value_counts().items()}, T到建立_ms=d(mk.T_to_create_ms), 建立到最後成交_ms=d(mk.create_to_last_ms), T到最後成交_ms=d(mk.T_to_last_ms),
                成交落在整點後第幾分鐘={int(k): int(v) for k, v in mk.fill_minute_after_T.value_counts().sort_index().items()}, 方向性價差_bp=d(mk.slip_bp),
                限制='缺少本機醒來、取得K線與送單時間戳時，T→訂單建立不可再拆；樣本只代表實盤期間與配置')
    return m, summ


def selftest():
    T0 = 1_762_000_000_000 // 3600_000 * 3600_000
    tr = pd.DataFrame([dict(time=T0 + 10_400, id=1, orderId=11, side='BUY', positionSide='LONG', price='2000.0', qty='1.0', commission='0.8'),
                       dict(time=T0 + 10_600, id=2, orderId=11, side='BUY', positionSide='LONG', price='2002.0', qty='1.0', commission='0.8'),
                       dict(time=T0 + 3600_000 * 5 + 9_900, id=3, orderId=12, side='SELL', positionSide='LONG', price='2010.0', qty='2.0', commission='1.6'),
                       dict(time=T0 + 3600_000 * 3 + 1_234_567, id=4, orderId=13, side='BUY', positionSide='SHORT', price='2050.0', qty='1.0', commission='0.8')])
    od = pd.DataFrame([dict(orderId=11, time=T0 + 10_300, updateTime=T0 + 10_600, type='MARKET', origType='MARKET', status='FILLED', reduceOnly=False),
                       dict(orderId=12, time=T0 + 3600_000 * 5 + 9_800, updateTime=T0 + 3600_000 * 5 + 9_900, type='MARKET', origType='MARKET', status='FILLED', reduceOnly=True),
                       dict(orderId=13, time=T0 + 3600_000 * 3 + 1_234_500, updateTime=T0 + 3600_000 * 3 + 1_234_567, type='MARKET', origType='STOP_MARKET', status='FILLED', reduceOnly=True)])
    kl = pd.DataFrame(dict(close_ms=[T0, T0 + 5 * 3600_000], close=[2000.0, 2012.0]))
    m, s = analyze(tr, od, kl)
    r11 = m.set_index('orderId').loc[11]
    assert abs(r11.vwap - 2001.0) < 1e-9 and r11.T_to_create_ms == 10_300 and r11.create_to_last_ms == 300 and abs(r11.slip_bp - 5.0) < 1e-6
    r12 = m.set_index('orderId').loc[12]; assert abs(r12.slip_bp - (-(2010 / 2012 - 1) * 1e4)) < 1e-6
    assert m.set_index('orderId').loc[13].kind == '觸發單' and s['訂單數']['市價單'] == 2
    print('selftest ok', json.dumps(s, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest': selftest(); sys.exit()
    tr, od, klp, out = sys.argv[1:5]
    kl = pd.read_csv(klp)
    m, s = analyze(pd.read_csv(tr), pd.read_csv(od), kl); os.makedirs(out, exist_ok=True)
    m.to_csv(os.path.join(out, 'fill_latency_by_order.csv'), index=False); json.dump(s, open(os.path.join(out, 'fill_latency_summary.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(s, ensure_ascii=False, indent=1))
