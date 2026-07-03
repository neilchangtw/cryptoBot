"""
V28 R2c — 50% 獲利加倉方案：全交易明細回測
=============================================
規則：保證金 = 200U + 累計獲利 × 50%（封頂 1000U、地板 200U）、20x、
     名目 = 保證金 × 20；交易序列 = 線上引擎 V14+R+V25-D（realistic 成交）。
輸出格式對齊 run_backtest.py -t，多三欄：保證金 / 名目 / 出場後累計權益。
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import v27_engine as ve
import v28_compound_sizing as v28
import labels
import analysis_report

WALLET0 = 400.0      # 錢包起始（使用者實際資金）
FEE_RATE = 4.0 / 4000.0
FRAC = 0.5           # 獲利的 50% 加倉
CAP_MARGIN = 1000.0  # 保證金頂 1000U
LEV = 20.0


def margin_fn(cum):
    return min(200.0 + FRAC * max(0.0, cum), CAP_MARGIN)


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    tdf = pd.DataFrame(trades).reset_index(drop=True)
    events = v28.make_events(tdf)

    # 事件序重放：進場定倉、出場結算；逐筆檢查錢包保證金是否足夠
    cum = 0.0           # 累計已實現 PnL
    margin_of, notional_of = {}, {}
    r = tdf['pnl_pct'].to_numpy() / 100.0
    rows = []           # 依出場順序
    feas = {}           # idx -> 進場時保證金缺口（<0 = 不足）
    for bar, kind, so, idx in events:
        if kind == 1:
            m = margin_fn(cum)
            wallet = WALLET0 + cum
            free = wallet - sum(margin_of.values()) - m   # 扣掉已占用保證金
            feas[idx] = free
            margin_of[idx] = m
            notional_of[idx] = m * LEV
        else:
            N = notional_of.pop(idx)
            m = margin_of.pop(idx)
            pnl = N * r[idx] - N * FEE_RATE
            cum += pnl
            rows.append((idx, m, N, pnl, WALLET0 + cum))

    out = []
    for idx, m, N, pnl, wallet_after in rows:
        t = tdf.loc[idx]
        out.append({**t.to_dict(), 'margin': m, 'notional': N,
                    'pnl_scaled': pnl, 'eq_after': wallet_after,
                    'free_at_entry': feas[idx]})
    odf = pd.DataFrame(out)

    total = odf.pnl_scaled.sum()
    wins = odf[odf.pnl_scaled > 0]
    losses = odf[odf.pnl_scaled < 0]
    pf = wins.pnl_scaled.sum() / abs(losses.pnl_scaled.sum())
    curve = np.concatenate([[WALLET0], odf.eq_after.to_numpy()])
    peak = np.maximum.accumulate(curve)
    mdd = (curve - peak).min()
    mdd_pct = ((curve - peak) / peak).min() * 100
    ln = odf[odf.side == 'L']
    sn = odf[odf.side == 'S']
    n_infeasible = int((odf.free_at_entry < 0).sum())

    print("══════════════════════════════════════════")
    print(" 回測 ETHUSDT  V14+R + V25-D ＋ 50% 獲利加倉")
    print(" 規則：保證金 200U + 累計獲利×50%（頂 1000U / 地板 200U）× 20x")
    print(f" 錢包起始：{WALLET0:.0f}U")
    print(f" 範圍：{str(df['datetime'].iloc[0])[:16]} ~ {str(df['datetime'].iloc[-1])[:16]}")
    print("══════════════════════════════════════════")
    print(f" 期末錢包  : ${WALLET0 + total:,.2f}（起始 ${WALLET0:,.0f}）")
    print(f" 錢包最低  : ${curve.min():,.2f}")
    print(f" 保證金不足的進場：{n_infeasible} 筆（進場當下 錢包-已占用保證金 < 本筆保證金，"
          f"實盤會開不出來，明細以 ⚠ 標記）")
    print(f" 總 PnL    : $+{total:,.2f}（固定注碼版 $+{tdf.pnl_usd.sum():,.2f}）")
    print(f" 交易數    : {len(odf)}（L {len(ln)} / S {len(sn)}）")
    print(f" 勝率      : {(odf.pnl_scaled > 0).mean() * 100:.1f}%")
    print(f" 獲利因子  : {pf:.2f}")
    print(f" 最大回撤  : ${abs(mdd):,.2f}（{abs(mdd_pct):.1f}%）")
    print(f" 最佳/最差 : ${odf.pnl_scaled.max():+,.2f} / ${odf.pnl_scaled.min():+,.2f}")
    print(f" L 做多    : ${ln.pnl_scaled.sum():+,.2f}（{len(ln)} 筆，"
          f"WR {(ln.pnl_scaled > 0).mean() * 100:.0f}%）")
    print(f" S 做空    : ${sn.pnl_scaled.sum():+,.2f}（{len(sn)} 筆，"
          f"WR {(sn.pnl_scaled > 0).mean() * 100:.0f}%）")

    print("\n 進出場明細（時間=實際成交時刻 K 棒收盤；保證金=進場當下計算）")
    RSN_W = 14
    hdr = (f"{'#':>4} {'Dir':<3} {'Entry (UTC+8)':<16} {'EntryPx':>8} "
           f"{'Exit (UTC+8)':<16} {'ExitPx':>8} "
           f"{labels.ljust_disp('出場', RSN_W)} {'Hold':>4} "
           f"{'保證金':>7} {'名目':>7} {'PnL($)':>9} {'PnL%':>6} {'錢包':>10}")
    print(" " + hdr)
    print(" " + "-" * labels.disp_width(hdr))
    for k, row in enumerate(odf.itertuples(), 1):
        edt = analysis_report.to_exec_time(str(row.entry_dt).replace("T", " "))
        xdt = analysis_report.to_exec_time(str(row.exit_dt).replace("T", " "))
        rsn = labels.ljust_disp(labels.exit_label(row.exit_reason), RSN_W)
        warn = " ⚠保證金不足" if row.free_at_entry < 0 else ""
        print(f" {k:>4} {row.side:<3} {edt:<16} {row.entry_price:>8.2f} "
              f"{xdt:<16} {row.exit_price:>8.2f} "
              f"{rsn} {row.bars_held:>4} "
              f"{row.margin:>6.0f}U {row.notional / 1000:>6.1f}K "
              f"{row.pnl_scaled:>+9.2f} {row.pnl_pct:>+6.2f} {row.eq_after:>10,.2f}{warn}")

    print("\n 出場分佈：")
    for reason, cnt in odf.exit_reason.value_counts().items():
        sub = odf[odf.exit_reason == reason].pnl_scaled.sum()
        print(f"   {labels.ljust_disp(labels.exit_label(reason), 18)}: {cnt:3d} 筆"
              f"（${sub:+,.2f}）")

    odf['_month'] = odf.entry_dt.astype(str).str[:7]
    print("\n 月度 PnL：")
    monthly = odf.groupby('_month').agg(s=('pnl_scaled', 'sum'),
                                        c=('pnl_scaled', 'count'),
                                        e=('eq_after', 'last'))
    pos = (monthly.s > 0).sum()
    for mth, row in monthly.iterrows():
        bar = "🟢" if row.s > 0 else "🔴"
        print(f"   {mth}  {bar} ${row.s:>+10,.2f}（{int(row.c)} 筆）  月末錢包 ${row.e:>10,.2f}")
    print(f"\n 正報酬月份：{pos}/{len(monthly)}")


if __name__ == '__main__':
    main()
