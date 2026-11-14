"""V61 可信度補查：滑價對帳、強平與止損（分鐘線＋標記價格）、資金情境。不新增假說。
用法：python backtest/research/v61_audit.py"""
import os, sys, io, zipfile, json, warnings, urllib.request
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v60_d0 as S, v61_run as R
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v61'); W = slice('2021-01-01', '2026-10-31')

def k1m(day, kind):
    """kind: klines（最新成交價）或 markPriceKlines（標記價格）；來源 data.binance.vision 日檔。"""
    cache = os.path.join(C.ROOT, 'data', 'audit_1m'); os.makedirs(cache, exist_ok=True); p = os.path.join(cache, f'{kind}_{day}.csv')
    if not os.path.exists(p):
        u = f'https://data.binance.vision/data/futures/um/daily/{kind}/ETHUSDT/1m/ETHUSDT-1m-{day}.zip'
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read())); raw = z.read(z.namelist()[0]).decode()
        d = pd.read_csv(io.StringIO(raw), header=None if raw[0].isdigit() else 0).iloc[:, :5]; d.columns = ['t', 'o', 'h', 'l', 'c']; d.to_csv(p, index=False)
    d = pd.read_csv(p); d['t'] = pd.to_datetime(d.t.astype('int64') // (1000 if d.t.iloc[0] > 1e14 else 1), unit='ms'); return d.set_index('t')

if __name__ == '__main__':
    df = C.load_1h(end=R.CUT); fund = C.load_funding(); sim = V.build_sim(); okq = ~(V.vol_rank(df) < 20); incq, cq = V.run_masked(df, okq, okq, sim); empty = cq.iloc[0:0]
    sig = R.cal_sig(S.daily_skew((7, 45))); res = {}
    # ---------- 1. 滑價 ----------
    runs = {}
    for slip in (0.0, 2.0, 5.0):
        trs = {k: R.mod(sig, df, s, empty, slip)[0] for k, s in R.CFG.items()}; w, picks = R.wf(trs); trs['WF'] = w; mt = {}
        for k, t in trs.items():
            im, cm = C.mtm_equity(t, df, fund); mt[k] = (im[W], cm.assign(reason=t.reason.values, cfg=t.cfg.values if 'cfg' in t else k))
        runs[slip] = dict(picks=dict(picks), mt=mt)
    base = incq[W].sum(); rows = []
    for k in ('S4', 'S8', 'S12', 'WF'):
        c0 = runs[0.0]['mt'][k][1]
        for slip in (0.0, 2.0, 5.0):
            fixed = runs[0.0]['mt'][k][0].sum() - (c0.fee * 0 + (c0.gross * 0 + 1) * 0).sum() - (2 * slip / 1e4 * (c0.fee / 4 * 4000)).sum()  # 固定同一組交易，只加 2×slip×名目
            c = runs[slip]['mt'][k][1]; rows.append(dict(配置=k, 滑價bp=slip, 固定交易_模組=round(fixed), 完整重播_模組=round(runs[slip]['mt'][k][0].sum()), 固定交易_B組合=round(base + fixed), 完整重播_B組合=round(base + runs[slip]['mt'][k][0].sum()), 筆數=len(c), 止損筆=int((c.reason == 'STOP').sum())))
    t1 = pd.DataFrame(rows); t1.to_csv(os.path.join(OUT, 'audit_slippage.csv'), index=False); print('=== 1. 滑價：固定交易 vs 完整重播 ===\n' + t1.to_string(index=False))
    pk = pd.DataFrame({f'{int(s)}bp': runs[s]['picks'] for s in runs}); per = []
    for a in pk.index:
        b = str((pd.Timestamp(a) + pd.DateOffset(months=6)).date()); r = dict(半年=a, **pk.loc[a].to_dict())
        for s in runs:
            for k in ('S4', 'S8', 'S12'):
                c = runs[s]['mt'][k][1]; r[f'{k}@{int(s)}bp'] = round(c[(c.t_in >= a) & (c.t_in < b)].net.sum())
            c = runs[s]['mt']['WF'][1]; r[f'WF@{int(s)}bp'] = round(c[(c.t_in >= a) & (c.t_in < b)].net.sum())
        for s in runs:
            sc = {k: round(runs[s]['mt'][k][1].pipe(lambda c: c[c.t_out < pd.Timestamp(a)]).gross.sum() - 9.0 / 4000 * (runs[s]['mt'][k][1].pipe(lambda c: c[c.t_out < pd.Timestamp(a)]).fee.sum() / 4 * 4000)) for k in ('S4', 'S8', 'S12')}; r[f'選擇分數@{int(s)}bp'] = json.dumps(sc)
        per.append(r)
    t2 = pd.DataFrame(per); t2.to_csv(os.path.join(OUT, 'audit_slippage_wf_periods.csv'), index=False)
    print('\n=== 前推各半年選用與該半年實現淨利（依進場歸屬、含 funding）===\n' + t2[['半年', '0bp', '2bp', '5bp', 'WF@0bp', 'WF@2bp', 'WF@5bp', 'S4@2bp', 'S8@2bp', 'S4@5bp', 'S8@5bp']].to_string(index=False))
    div = t2[(t2['2bp'] != t2['5bp'])]; print('\n2bp 與 5bp 選用不同的半年：\n' + div[['半年', '2bp', '5bp', 'WF@2bp', 'WF@5bp', '選擇分數@2bp', '選擇分數@5bp']].to_string(index=False))
    # 同一配置內，滑價是否改變交易集合
    for k in ('S4', 'S8'):
        a = runs[0.0]['mt'][k][1]; b = runs[5.0]['mt'][k][1]; ka = set(zip(a.side, a.t_in, a.t_out)); kb = set(zip(b.side, b.t_in, b.t_out)); print(f'{k}：0bp 與 5bp 交易集合 相同 {len(ka & kb)}、僅0bp {len(ka - kb)}、僅5bp {len(kb - ka)}')
    # ---------- 2. 強平與止損 ----------
    t = R.mod(sig, df, 0.04, empty, 0.0)[0]; ct = (df.datetime - pd.Timedelta(hours=7)).values; pos = {x: i for i, x in enumerate(ct)}; hi = df.high.values; lo = df.low.values; L = 12.5; rows = []
    for r in t[t.reason == 'STOP'].itertuples():
        i0 = pos[np.datetime64(r.t_in)]; i1 = pos[np.datetime64(r.t_out)]; s = 1 if r.side == 'L' else -1
        adv = (r.ep - lo[i0 + 1:i1 + 1].min()) / r.ep if s == 1 else (hi[i0 + 1:i1 + 1].max() - r.ep) / r.ep
        if adv < 1 / L - R.MMR: continue
        stop = r.ep * (1 - s * 0.04); out = dict(side=r.side, t_in=r.t_in, t_out_bar=r.t_out, ep=r.ep, stop=round(stop, 2), 回測成交=round(r.xp, 2), 小時極值逆向=round(adv, 4))
        for mmr in (0.004, 0.005, 0.0065):
            out[f'強平價_mmr{mmr}'] = round(r.ep * (1 - s / L) / (1 - s * mmr), 2)
        try:
            days = sorted({str(d.date()) for d in pd.date_range(r.t_in.floor('D'), r.t_out.floor('D'), freq='D')}); last = pd.concat([k1m(d, 'klines') for d in days]); mark = pd.concat([k1m(d, 'markPriceKlines') for d in days])
            a = r.t_in; b = r.t_out - pd.Timedelta(minutes=1); last = last[a:b]; mark = mark[a:b]  # 更正：t_in／t_out 已是小時線收盤時刻（UTC），不需再加一小時
            hit = last[(last.l <= stop)] if s == 1 else last[(last.h >= stop)]; t0 = hit.index[0]; bar = last.loc[t0]; nxt = last[last.index > t0].iloc[0]
            lq = out['強平價_mmr0.005']; mk = mark[t0:]; mhit = mk[mk.l <= lq] if s == 1 else mk[mk.h >= lq]; lhit = last[t0:]; lhit = lhit[lhit.l <= lq] if s == 1 else lhit[lhit.h >= lq]
            ext_same = bar.l if s == 1 else bar.h
            out.update(止損觸發分鐘=str(t0), 觸發分鐘開盤=bar.o, 觸發分鐘極值=ext_same, 觸發分鐘極值逆向=round(s * (r.ep - ext_same) / r.ep, 4), 下一分鐘開盤=nxt.o, 開盤即跳過止損=bool((bar.o <= stop) if s == 1 else (bar.o >= stop)),
                       樂觀成交_止損價損失=round(3750 * 0.04, 1), 保守成交_觸發分鐘極值損失=round(3750 * s * (r.ep - ext_same) / r.ep, 1), 保守成交_下一分鐘極值損失=round(3750 * s * (r.ep - (nxt.l if s == 1 else nxt.h)) / r.ep, 1),
                       最新價首次到強平價=str(lhit.index[0]) if len(lhit) else '未到', 標記價首次到強平價=str(mhit.index[0]) if len(mhit) else '未到', 止損到標記強平相隔分鐘=(int((mhit.index[0] - t0).total_seconds() // 60) if len(mhit) else None),
                       持倉期間標記價最大逆向=round(s * (r.ep - (mark.l.min() if s == 1 else mark.h.max())) / r.ep, 4))
        except Exception as e:
            out['分鐘線'] = f'DATA_LIMITED：{type(e).__name__} {e}'
        rows.append(out)
    t3 = pd.DataFrame(rows); t3.to_csv(os.path.join(OUT, 'audit_liquidation.csv'), index=False); print('\n=== 2. S4（12.5x、4% 止損）小時極值達估算強平距離的交易 ===\n' + t3.T.to_string())
    # ---------- 4. 資金情境 ----------
    rows = []
    for nm, k in (('Q20W30 單獨', None), ('B＋S8', 'S8'), ('B＋S12', 'S12'), ('B＋前推', 'WF'), ('B＋S4', 'S4')):
        parts = [(cq, 200.0)]; inc = incq.copy()
        if k: parts.append((runs[0.0]['mt'][k][1], 300.0)); inc = inc.add(runs[0.0]['mt'][k][0].reindex(inc.index).fillna(0), fill_value=0)
        mg, ex = R.margin_use(parts, df); mg = mg[W]; ex = ex[W]; eq = inc[W].cumsum(); idx = eq.index; ip = {x: i for i, x in enumerate(idx)}
        for cap in (500, 1000, 1500, 2000, 3000, 5000):
            free = cap + eq - mg; ref = {200.0: 0, 300.0: 0}; first = None
            for c, m in parts:
                for r in c.itertuples():
                    if r.t_in in ip and ip[r.t_in] > 0 and cap + eq.iloc[ip[r.t_in] - 1] - mg.iloc[ip[r.t_in] - 1] < m:
                        ref[m] += 1; first = min(first, r.t_in) if first is not None else r.t_in
            rows.append(dict(版本=nm, 起始資金=cap, 最低可用餘額=round(free.min()), 可用餘額為負小時數=int((free < 0).sum()), 進場時餘額不足_現行=ref[200.0], 進場時餘額不足_模組=ref[300.0], 首次不足=str(first) if first is not None else '無', 最低淨值=round(cap + eq.min())))
    t4 = pd.DataFrame(rows); t4.to_csv(os.path.join(OUT, 'audit_capital_scenarios.csv'), index=False); print('\n=== 4. 資金情境（事後路徑估算；不足筆數未回饋到後續交易）===\n' + t4.to_string(index=False))
