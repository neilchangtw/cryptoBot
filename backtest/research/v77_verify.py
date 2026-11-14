"""V77：Q20W30＋固定風險下單的執行一致性離線驗證（E1~E9）與公平比較。登記：doc/research_results/v77/v77_plan.md。
用法：python backtest/research/v77_verify.py [--fast]   （--fast 略過 E1/E2 的截斷重算）"""
import os, sys, json, copy, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import v53_common as C, v57b_lowvol as V, v69a as A, v70_account as E, v70_recon as RC, v70_run as RN, v74_run as R74, v71_run as R71, q20fr_core as QC, q20fr_exec as QX
OUT = os.path.join(C.ROOT, 'doc', 'research_results', 'v77'); os.makedirs(OUT, exist_ok=True); RISK = ('risk', 0.03, 5.0); FIX = ('fixed', 4000.0, 20.0); rng = np.random.default_rng(77)

def events(df, fund, book):
    px = C.close_utc(df); px = px[(px.index >= E.W0 - pd.Timedelta(hours=1)) & (px.index <= E.W1)]; idx = px.index; fr = fund.reindex(idx).fillna(0.0).values; ent, ext = {}, {}
    for k, r in enumerate(book.itertuples()):
        if r.t_in < E.W0 or r.t_in not in px.index: continue
        ent.setdefault(r.t_in, []).append(dict(ref=k, side=r.side, ep=float(r.ep), snm=float(getattr(r, 'snm', 1.0))))
        if r.t_out <= E.W1: ext.setdefault(r.t_out, []).append(dict(ref=k, side=r.side, xp=float(r.xp), reason=r.reason))
    return idx, px.values, fr, ent, ext

def drive(ev, ex_kw=None, restart_at=(), dup=False, crash_at=()):
    idx, c, fr, ent, ext = ev; ex = QX.SimExchange(E.CAP, **(ex_kw or {})); xr = QX.Executor(ex); issues = []; inv = 0; wal = []
    for i in range(1, len(idx)):
        t = idx[i]; ts = t.isoformat(); exits = ext.get(t, []); entries = ent.get(t, []); final = i == len(idx) - 1
        if i in restart_at: xr = QX.Executor.restore(ex, xr.dump()); issues += xr.reconcile_issues          # 重啟：狀態只從 JSON 與交易所端取得
        blob = xr.dump() if i in crash_at else None
        if fr[i]: ex.funding(fr[i], c[i])                                                                   # 交易所端：資金費
        reps = {d['ref']: ex.trigger_stop(d['side'], d['xp'], xr.open[str(d['ref'])]['ntl']) for d in exits if d['reason'] == 'SN' and str(d['ref']) in xr.open}   # 交易所端：止損觸發
        xr.on_bar(ts, float(c[i]), float(fr[i]), exits, entries, reps, final)
        if blob is not None: xr = QX.Executor.restore(ex, blob, strict=False); xr.on_bar(ts, float(c[i]), float(fr[i]), exits, entries, reps, final)   # 當機：送單後、存檔前中斷，重啟後重做同一根
        if dup: xr.on_bar(ts, float(c[i]), float(fr[i]), exits, entries, reps, final)                       # 重複事件
        for side in ('L', 'S'):                                                                              # 不變量：本地與交易所端持倉一致、持倉必有保護單、無持倉則無保護單
            loc = next((v for v in xr.open.values() if v['side'] == side), None); rem = ex.pos[side]
            if (loc is None) != (rem is None) or (loc and abs(loc['q'] - rem['q']) > 1e-9) or ((rem is not None) != (side in ex.stops)): inv += 1
        wal.append(ex.wallet)
    return xr, ex, issues, inv, pd.Series(wal, index=idx[1:])

def compare(xr, ex, Rb, tol=1e-6):
    a = pd.DataFrame(xr.done); b = Rb['tr'].copy(); b['q'] = b.ntl / b.ep; m = b.merge(a, on='ref', how='outer', suffixes=('_bt', '_ex'), indicator='mm'); rows = []
    for r in m.itertuples():
        if r.mm != 'both': rows.append(dict(ref=r.ref, 差異='只存在於' + ('回測' if r.mm == 'left_only' else '執行器'))); continue
        d = []
        if r.side_bt != r.side_ex: d.append('方向')
        if pd.Timestamp(r.t_in_ex) != r.t_in_bt: d.append('進場時間')
        if pd.Timestamp(r.t_out_ex) != r.t_out_bt: d.append('出場時間')
        if abs(r.q_bt - r.q_ex) > tol: d.append(f'數量 {r.q_bt:.4f}/{r.q_ex:.4f}')
        if abs(r.ep_bt - r.ep_ex) > tol or abs(r.xp_bt - r.xp_ex) > tol: d.append('價格')
        if r.reason_bt != r.reason_ex: d.append('出場原因')
        if abs(r.net_bt - r.net_ex) > tol: d.append(f'淨損益 {r.net_bt:.6f}/{r.net_ex:.6f}')
        if d: rows.append(dict(ref=r.ref, t_in=r.t_in_bt, 差異='、'.join(d)))
    wb = float(Rb['acc'].wallet.iloc[-1]); return rows, dict(回測筆數=len(b), 執行器筆數=len(a), 差異筆數=len(rows), 回測期末錢包=round(wb, 6), 執行器期末錢包=round(ex.wallet, 6), 錢包差=round(ex.wallet - wb, 9), 委託違規=len(ex.violations), 跳過=len(xr.skips), 數量不符紀錄=sum(1 for l in xr.log if l[0] == 'exit_qty_mismatch'))

if __name__ == '__main__':
    fast = '--fast' in sys.argv; X = R74.setup('2026-11-01'); df, fund = X['df'], X['fund']; book = X['q20']; res = {}
    Rb = E.replay(df, fund, base=book, base_mode=RISK, cross=False); Rb['tr'] = RC.tag(Rb['tr'], X['st']); dsum = E.summary('Q20W30＋固定風險（逐倉 5 倍）', Rb, df); G = RN.gates(dsum)
    res['重現'] = dict(淨利=dsum['淨利'], 最大百分比回撤=dsum['最大百分比回撤'], 最低權益=dsum['最低權益'], y2021=dsum['y2021'], 筆數=dsum['筆數'], 勝率=dsum['勝率'], 強平觸及_逐倉=dsum['強平觸及_逐倉估算'], 與待重現一致=bool(dsum['淨利'] == 3757 and dsum['最大百分比回撤'] == 21.8 and dsum['最低權益'] == 1174 and dsum['y2021'] == -231)); print('重現', res['重現'], flush=True)
    ev = events(df, fund, book); n = len(ev[0])
    # E4 逐筆一致
    xr, ex, iss, inv, wal = drive(ev); rows, s4 = compare(xr, ex, Rb); pd.DataFrame(rows or [dict(ref=None, 差異='無差異')]).to_csv(os.path.join(OUT, 'e4_trade_diffs.csv'), index=False); wd = float((wal - Rb['acc'].wallet.reindex(wal.index)).abs().max()); s4.update(逐小時錢包最大差=round(wd, 9), 不變量違反=inv); res['E4'] = dict(s4, 通過=bool(not rows and abs(s4['錢包差']) < 1e-6 and wd < 1e-6 and inv == 0)); print('E4', res['E4'], flush=True)
    pd.DataFrame(xr.done).to_csv(os.path.join(OUT, 'executor_trades.csv'), index=False)
    # E3 下單限制
    a = pd.DataFrame(xr.done); ntl = a.q * a.ep; stepok = ((a.q / QC.STEP - (a.q / QC.STEP).round()).abs() < 1e-6).all(); acc = Rb['acc']; res['E3'] = dict(委託數=ex.n_orders, 數量精度全符=bool(stepok), 最小名目=round(float(ntl.min()), 1), 最大名目=round(float(ntl.max()), 1), 最大名目對錢包=round(float((Rb['tr'].ntl / Rb['tr'].wallet).max()), 3), 最高保證金占用對權益=round(float((acc.margin / acc.equity).max()), 3), 最低可用資金=round(float(acc.free.min()), 1), 交易所端違規=len(ex.violations), 槓桿=ex.lev, 保證金模式=ex.margin_type, 通過=bool(stepok and ntl.min() >= QC.MIN_NTL and not ex.violations and acc.free.min() > 0)); print('E3', res['E3'], flush=True)
    # 每筆風險的實際分布
    t = Rb['tr']; sg = np.where(t.side == 'L', 1, -1); stopd = np.where(t.side == 'L', 0.035, 0.040); plan = t.ntl * stopd / t.wallet; real = -t.net / t.wallet; sn = t.reason == 'SN'
    res['每筆風險'] = dict(計畫止損價差占錢包_最大=round(float(plan.max()) * 100, 3), 計畫止損價差占錢包_中位=round(float(np.median(plan)) * 100, 3), 取整造成的最大減少bp=round(float((0.03 - plan).max()) * 1e4, 2), 來回費用占錢包_多=round(0.03 / 0.035 * 0.00225 * 100, 3), 來回費用占錢包_空=round(0.03 / 0.040 * 0.00225 * 100, 3), 止損出場筆數=int(sn.sum()), 止損出場實際虧損占錢包_中位=round(float(real[sn].median()) * 100, 2), 止損出場實際虧損占錢包_最大=round(float(real[sn].max()) * 100, 2), 全部交易最大單筆虧損占錢包=round(float(real.max()) * 100, 2), 超過3pct的筆數=int((real > 0.03).sum()), 多空同時持倉最大名目對權益=round(float((acc.notional / acc.equity).max()), 3)); print('每筆風險', res['每筆風險'], flush=True)
    # E5 部分成交
    x5, e5, _, inv5, _ = drive(ev, dict(partial=0.6)); r5, s5 = compare(x5, e5, Rb); x5b, e5b, _, inv5b, _ = drive(ev, dict(partial=0.6, cancel_rest=True)); b5 = pd.DataFrame(x5b.done)
    res['E5'] = dict(分兩次成交_差異筆數=len(r5), 分兩次成交_錢包差=s5['錢包差'], 分兩次成交_兩筆成交的交易數=int((pd.DataFrame(x5.done).nfill == 2).sum()), 分兩次_不變量違反=inv5, 剩餘取消_交易數=len(b5), 剩餘取消_出場數量不符=sum(1 for l in x5b.log if l[0] == 'exit_qty_mismatch'), 剩餘取消_不變量違反=inv5b, 剩餘取消_期末部位=str(e5b.pos), 剩餘取消_殘留止損=len(e5b.stops), 剩餘取消_違規=len(e5b.violations), 剩餘取消_期末錢包=round(e5b.wallet, 1),
                     通過=bool(not r5 and abs(s5['錢包差']) < 1e-6 and inv5 == 0 and inv5b == 0 and not e5b.stops and e5b.pos == {'L': None, 'S': None} and not e5b.violations and not any(l[0] == 'exit_qty_mismatch' for l in x5b.log))); print('E5', res['E5'], flush=True)
    # E6 拒單與重試
    x6, e6, _, inv6, _ = drive(ev, dict(reject_every=7)); r6, s6 = compare(x6, e6, Rb); bk2 = book.copy(); ev2 = events(df, fund, bk2); x6b, e6b, _, inv6b, _ = drive(ev2, dict(reject_every=1))   # 永久拒單：每一筆都被拒
    res['E6'] = dict(暫時拒單次數=e6.rejects, 重試紀錄=sum(1 for l in x6.log if l[0] == 'retry'), 差異筆數=len(r6), 錢包差=s6['錢包差'], 成交筆數相同=bool(len(e6.fills) == len(ex.fills)), 不變量違反=inv6, 永久拒單_成交數=len(e6b.fills), 永久拒單_跳過數=len(x6b.skips), 永久拒單_錢包=e6b.wallet, 永久拒單_不變量違反=inv6b, 通過=bool(not r6 and abs(s6['錢包差']) < 1e-6 and len(e6.fills) == len(ex.fills) and inv6 == 0 and len(e6b.fills) == 0 and e6b.wallet == E.CAP and inv6b == 0)); print('E6', res['E6'], flush=True)
    # E7 重啟
    held = [i for i in range(1, n) if Rb['acc'].margin.iloc[i - 1] > 0]; pts = set(rng.choice(held, 13, replace=False).tolist() + rng.choice(np.arange(1, n), 12, replace=False).tolist()); x7, e7, iss7, inv7, _ = drive(ev, restart_at=pts); r7, s7 = compare(x7, e7, Rb)
    res['E7'] = dict(重啟次數=len(pts), 其中持倉中=sum(1 for p in pts if p in set(held)), 對帳問題=len(iss7), 差異筆數=len(r7), 錢包差=s7['錢包差'], 不變量違反=inv7, 通過=bool(not r7 and not iss7 and abs(s7['錢包差']) < 1e-6 and inv7 == 0)); print('E7', res['E7'], flush=True)
    # E8 重複事件、當機重做
    x8, e8, _, inv8, _ = drive(ev, dup=True); r8, s8 = compare(x8, e8, Rb); act = [i for i in range(1, n) if ev[0][i] in ev[3] or ev[0][i] in ev[4]]; cp = set(rng.choice(act, 40, replace=False).tolist()); x8b, e8b, _, inv8b, _ = drive(ev, crash_at=cp); r8b, s8b = compare(x8b, e8b, Rb)
    res['E8'] = dict(重複事件略過次數=sum(1 for l in x8.log if l[0] == 'dup_bar'), 重複_差異筆數=len(r8), 重複_錢包差=s8['錢包差'], 重複_成交筆數相同=bool(len(e8.fills) == len(ex.fills)), 當機重做次數=len(cp), 當機_取回既有委託次數=sum(1 for l in x8b.log if l[0] == 'found_existing'), 當機_差異筆數=len(r8b), 當機_錢包差=s8b['錢包差'], 當機_成交筆數相同=bool(len(e8b.fills) == len(ex.fills)), 不變量違反=inv8 + inv8b,
                     通過=bool(not r8 and not r8b and abs(s8['錢包差']) < 1e-6 and abs(s8b['錢包差']) < 1e-6 and len(e8.fills) == len(ex.fills) and len(e8b.fills) == len(ex.fills) and inv8 + inv8b == 0)); print('E8', res['E8'], flush=True)
    # E1/E2 截斷重算
    if not fast:
        full = V.vol_rank(df); cuts = sorted(rng.choice(np.arange(200 * 24, len(df)), 60, replace=False).tolist()); bad = 0
        for k in cuts:
            v = V.vol_rank(df.iloc[:k + 1].reset_index(drop=True))[-1]; f = full[k]; bad += not ((np.isnan(v) and np.isnan(f)) or abs(v - f) < 1e-9)
        ct = df.datetime - pd.Timedelta(hours=7); ps = pd.Series(full < 20, index=ct.values); ps = ps[(ps.index >= E.W0) & (ps.index <= E.W1)]; chg = ps[ps != ps.shift(1)]; sched = pd.DataFrame(dict(時間UTC=chg.index, 狀態=np.where(chg.values, '暫停', '恢復'))); sched.to_csv(os.path.join(OUT, 'q20_pause_schedule.csv'), index=False)
        res['E1'] = dict(抽樣時點=len(cuts), 不一致=int(bad), 暫停時數占比=round(float(ps.mean()), 3), 狀態切換次數=len(sched) - 1, 暖機不足730天的期間='2022-08 以前（資料自 2020-09-01 起，百分位以至少 180 天、不足 730 天的歷史計算）', 通過=bool(bad == 0)); print('E1', res['E1'], flush=True)
        sim = A.build_sim(); okq_full = ~(full < 20); cuts2 = sorted(rng.choice(np.arange(len(df) - 50000, len(df) - 10), 40, replace=False).tolist()); bad2 = 0; chk = 0
        for k in cuts2:
            d2 = df.iloc[:k + 1].reset_index(drop=True); ind2 = V.B.eng.compute_indicators(d2); b2 = A.ref_book(d2, ind2, sim, ~(V.vol_rank(d2) < 20)); tcut = ct.iloc[k]; f = book[book.t_out <= tcut].reset_index(drop=True); p = b2[b2.t_out <= tcut].reset_index(drop=True)
            # 截點時仍持倉者：截斷版會在最後一根被引擎收尾，僅比對進場
            fo = book[(book.t_in <= tcut) & (book.t_out > tcut)]; po = b2[b2.t_in.isin(fo.t_in)]
            closed_same = p[~p.t_in.isin(fo.t_in)][['side', 't_in', 't_out', 'ep', 'xp', 'reason']].reset_index(drop=True).equals(f[['side', 't_in', 't_out', 'ep', 'xp', 'reason']].reset_index(drop=True)); bad2 += not (closed_same and len(po) == len(fo) and np.allclose(po.ep.values, fo.ep.values)); chk += len(f)
        res['E2'] = dict(抽樣截點=len(cuts2), 不一致截點=int(bad2), 累計比對交易筆次=int(chk), 通過=bool(bad2 == 0)); print('E2', res['E2'], flush=True)
    # E9 保守情境
    tr = Rb['tr']; lp = np.where(tr.side == 'L', tr.ep * (1 - 1 / 5) / (1 - 0.005), tr.ep * (1 + 1 / 5) / (1 + 0.005)); res['E9'] = dict(逐倉強平線觸及筆數_含小時內高低點=int(tr.liq.sum()), 強平線距進場價pct=dict(多=round((1 - (1 - 1 / 5) / (1 - 0.005)) * 100, 2), 空=round(((1 + 1 / 5) / (1 + 0.005) - 1) * 100, 2)), 止損距離pct=dict(多=3.5, 空=4.0), 止損出場最差成交偏離進場pct=round(float((np.abs(tr.xp / tr.ep - 1))[tr.reason == 'SN'].max()) * 100, 2), 同根止損與止盈='引擎固定止損優先（保守）', 跳過訊號=len(Rb['skips'])); print('E9', res['E9'], flush=True)
    for nm, hc_add in (('成本每次成交+5bp', 0.0005),):
        hc = E.HC; E.HC = hc + hc_add; Rs = E.replay(df, fund, base=book, base_mode=RISK, cross=False); E.HC = hc; res['E9'][nm] = R74.brief(nm, Rs)
    worst = book.copy(); m = worst.reason == 'SN'; worst.loc[m, 'xp'] = np.where(worst.side[m] == 'L', worst.xp[m] * (1 - 0.005), worst.xp[m] * (1 + 0.005)); Rw = E.replay(df, fund, base=worst, base_mode=RISK, cross=False); res['E9']['止損成交再差50bp'] = R74.brief('止損成交再差50bp', Rw)
    res['判定'] = '執行一致性已驗證（離線）' if all(res[k]['通過'] for k in res if k.startswith('E') and '通過' in res[k]) and not fast else ('部分項目未跑（--fast）' if fast else '未通過：' + '、'.join(k for k in res if k.startswith('E') and '通過' in res[k] and not res[k]['通過']))
    print('判定', res['判定']); json.dump(res, open(os.path.join(OUT, 'v77_verify.json'), 'w'), ensure_ascii=False, indent=1, default=str)
