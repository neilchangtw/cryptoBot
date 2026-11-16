"""V87 固定放大快篩更正：逐時點的放大後權益、已占用保證金與可用資金（固定路徑，不重跑引擎）。
來源：V81 帳戶輸出（V77 配置 D0／D1）與 V81 的 1 分 K（只用來取 :00／:01 價格重建入場前未實現損益）。
入場檢查沿用 V70 引擎：權益 − 已占用保證金 ≥ 新單保證金；出場先於入場；同一時刻多筆入場依序。
用法：python3 backtest/research/v87_scale_recheck.py [--m1 ETHUSDT_1m_2020_2026.npz]"""
import argparse, hashlib, json, os
import numpy as np, pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
V81 = os.path.join(ROOT, 'doc', 'research_results', 'v81'); OUT = os.path.join(ROOT, 'doc', 'research_results', 'v87')
S0 = 1500.0; HC = 0.001125; M1_SHA = '077f8f051346b52ada4e687e51d6ef445c8eb7db1aa3f206ca4c9bf40dac65cd'
DEFAULT_M1 = '/mnt/project-files/v81_research/ETHUSDT_1m_2020_2026.npz'


def k_limit(x):
    """1,500 + k·(x − 1,500) ≥ 0 的最大 k；x ≥ 1,500 時無上限。"""
    x = np.asarray(x, dtype=float); with_lim = x < S0
    out = np.full(x.shape, np.inf); out[with_lim] = S0 / (S0 - x[with_lim]); return out


def entry_states(tr, px):
    """逐筆入場前狀態。px(t) 給該時刻價格（:00 或 :01 的 1 分 K 收盤，與 V81 回放格點相同）。"""
    tr = tr.sort_values(['t_in', 'id']).reset_index(drop=True); rows = []
    s = np.where(tr.side == 'L', 1.0, -1.0); q = tr.ntl.values / tr.ep.values; mg = tr.ntl.values / tr.lev.values
    for j, r in tr.iterrows():
        # 其他未平倉：較早入場（或同刻較早 id）且尚未出場；同刻出場先處理，所以 t_out == t_in 不算
        o = ((tr.t_in < r.t_in) | ((tr.t_in == r.t_in) & (tr.id < r.id))) & (tr.t_out > r.t_in)
        P = px(r.t_in); unreal = float(np.sum(s[o.values] * q[o.values] * (P - tr.ep.values[o.values])))
        wal_pre = float(r.wallet + HC * r.ntl)
        e_pre = wal_pre + unreal; used = float(mg[o.values].sum()); need = float(mg[j])
        rows.append(dict(id=int(r.id), t_in=r.t_in, side=r.side, 其他未平倉=int(o.sum()), 錢包_入場前=wal_pre, 未實現_其他=unreal,
                         權益_入場前=e_pre, 已占用=used, 需要=need, 可用_入場前=e_pre - used, k上限=float(k_limit([e_pre - used - need])[0]),
                         停損預算_原錢包=0.03 * wal_pre, net=float(r.net)))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--m1', default=DEFAULT_M1); a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    sha = hashlib.sha256(open(a.m1, 'rb').read()).hexdigest(); assert sha == M1_SHA, f'1 分 K 雜湊不符：{sha}'
    z = np.load(a.m1); ts = z['ts']; cl = z['close']; t0 = int(ts[0]); assert (np.diff(ts) == 60000).all()
    def px(t):
        i = (int(pd.Timestamp(t).value // 10**6) - t0) // 60000; return float(cl[i - 1])   # 開盤在 [t−1 分, t) 的 1 分 K 收盤＝t 時價格
    summ = pd.read_csv(os.path.join(V81, 'acct_summary.csv')); R = {'來源': {}, '重建核對': {}, '倍率表': [], '入場最緊十筆': {}}
    R['來源']['1分K'] = dict(路徑=a.m1, sha256=sha)
    for sc in ('D0', 'D1'):
        eq = pd.read_csv(os.path.join(V81, f'acct_v77_{sc}_equity_hourly.csv.gz'), parse_dates=['datetime'])
        tr = pd.read_csv(os.path.join(V81, f'acct_v77_{sc}_trades.csv'), parse_dates=['t_in', 't_out'])
        cur = pd.read_csv(os.path.join(V81, f'acct_orig_{sc}_trades.csv'))
        v77_net = float(tr.net.sum()); cur_net = float(cur.net.sum()); y2021 = float(tr[tr.t_out.dt.year == 2021].net.sum())   # 平倉年口徑，未四捨五入（摘要檔 y2021 已四捨五入）
        row = summ[(summ.情境 == sc) & (summ.配置.str.startswith('V77')) & (summ.口徑 == '參考路徑＋拒單')].iloc[0]
        es = entry_states(tr, px)
        # 重建核對（D0 才有同刻逐時權益可比）：整點權益＝該刻最後一筆入場後錢包＋所有未平倉未實現
        chk = {}
        if sc == 'D0':
            E = eq.set_index('datetime').equity; diffs = []
            s = np.where(tr.side == 'L', 1.0, -1.0); q = tr.ntl.values / tr.ep.values
            for t, g in tr.groupby('t_in'):
                last = g.sort_values('id').iloc[-1]; o = (tr.t_in <= t) & (tr.t_out > t); P = px(t)
                rec = float(last.wallet + np.sum(s[o.values] * q[o.values] * (P - tr.ep.values[o.values])))
                diffs.append(abs(rec - float(E.loc[t])))
            chk = dict(比對入場時刻=len(diffs), 最大絕對差=round(max(diffs), 4), 說明='逐時檔權益四捨五入到 0.001；差值上界約等於檔案精度＋ep 四捨五入（≤0.005×數量）')
        R['重建核對'][sc] = chk
        e_min = float(eq.equity.min()); ei_min = float(eq.equity_intrahour.min())
        free = (eq.equity - eq.margin).values; free_i = (eq.equity_intrahour - eq.margin).values
        k_entry = float(es.k上限.min()); k_hour = float(k_limit(free).min()); k_intra = float(k_limit(free_i).min())
        ks = {'k*＝追平同情境現行淨利（未四捨五入）': cur_net / v77_net, 'k_min＝逐時最低權益剛好 1,000U（未四捨五入）': 500.0 / (S0 - e_min), '對照 k=1': 1.0}
        R['來源'][sc] = dict(V77淨利=v77_net, 現行淨利=cur_net, 逐時最低權益=e_min, 最低權益時點=str(eq.datetime[eq.equity.idxmin()]), 小時內保守最低權益=ei_min,
                           摘要檔最低權益=float(row.最低權益), 入場筆=len(es), 有其他未平倉的入場筆=int((es.其他未平倉 > 0).sum()),
                           入場瞬間k上限=k_entry, 逐時k上限=k_hour, 小時內保守k上限=k_intra)
        for nm, k in ks.items():
            fk = S0 + k * (free - S0); fik = S0 + k * (free_i - S0); ek = S0 + k * (eq.equity.values - S0)
            ent_free = S0 + k * (es.可用_入場前.values - S0); ent_need = k * es.需要.values
            wk = S0 + k * (es.錢包_入場前.values - S0)
            R['倍率表'].append({'情境': sc, '倍率': nm, 'k': k, '淨利': k * v77_net, '與現行淨利差': k * v77_net - cur_net, '2021': k * y2021,
                              '逐時最低權益': float(ek.min()), '最高保證金占用_放大': k * float(eq.margin.max()),
                              '入場瞬間_最小餘裕（可用−需要）': float((ent_free - ent_need).min()), '入場瞬間_不足筆數': int((ent_free < ent_need).sum()),
                              '逐時_最低可用資金_放大': float(fk.min()), '逐時_可用資金為負小時': int((fk < 0).sum()),
                              '小時內保守_最低可用資金_放大': float(fik.min()), '小時內保守_為負小時': int((fik < 0).sum()),
                              '停損價差預算_以原路徑錢包計pct': 3.0 * k,
                              '停損價差預算_對放大後錢包_最大pct': float((k * es.停損預算_原錢包.values / wk).max() * 100),
                              '最大單筆實現虧損_對放大後入場前錢包pct': float((k * (-es.net.values) / wk).max() * 100)})
        R['入場最緊十筆'][sc] = es.nsmallest(10, 'k上限')[['id', 't_in', 'side', '其他未平倉', '權益_入場前', '已占用', '需要', '可用_入場前', 'k上限']].assign(t_in=lambda d: d.t_in.astype(str)).round(3).astype(str).to_dict('records')
    tb = pd.DataFrame(R['倍率表']); tb.round(4).to_csv(os.path.join(OUT, 'scale_recheck.csv'), index=False)
    R['判讀'] = ('固定路徑下 k>0 不改逐年符號，V77 2021 在 D0、D1 皆負，任何正倍率都過不了年度條件（保留）。'
               'V86「最高保證金大於 1,500U 即必跳單」撤回：入場瞬間與逐時的放大後可用資金見本表；未見不足時只表示在固定路徑與所列資料解析度下沒有看到，不等於完整可行。'
               '停損價差預算不是錢包風險保證：SafeNet 穿價、跳空與強平不在內。')
    json.dump(R, open(os.path.join(OUT, 'scale_recheck.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    print(json.dumps(R['來源'], ensure_ascii=False, indent=1, default=str)); print(json.dumps(R['重建核對'], ensure_ascii=False))
    print(tb.round(3).T.to_string())
    for sc in R['入場最緊十筆']: print(sc, pd.DataFrame(R['入場最緊十筆'][sc]).head(5).to_string())


if __name__ == '__main__':
    main()
