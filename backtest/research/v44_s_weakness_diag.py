"""V44 診斷：為什麼 2026-07 之後空單特別弱（純診斷，不是新規則、不設門檻）。

S 交易分三組：歷史正常期、歷史低波動期（第 15 輪 Q20W30 暫停期）、近期（2026-07 起）。
固定 200U、realistic、0bp、每筆 +$5，現行引擎。

用法（個人電腦 D:\\cryptoBot）：
    python backtest/research/v44_s_weakness_diag.py
"""

from __future__ import annotations

from math import comb
from pathlib import Path

import numpy as np
import pandas as pd

import v40_dvol_bookdepth as v40
from v44_r15_lowvolpause import ok_mask

ROOT = Path(__file__).resolve().parents[2]
BTC_CSV = ROOT / "data" / "v44" / "BTC_1h_long.csv"
FUND_CSV = ROOT / "data" / "v44" / "ETH_funding.csv"
RECENT = pd.Timestamp("2026-07-01")
GROUPS = ("歷史正常", "歷史低波動", "近期")
STEPS = (1, 3, 6, 10)
SEED = 0


def features(candles, ind, dt) -> dict:
    c, h, l = (candles[k].to_numpy(float) for k in ("close", "high", "low"))
    vol, tb = candles["volume"].to_numpy(float), candles["taker_buy_volume"].to_numpy(float)
    s = pd.Series(c)
    pc = np.roll(c, 1); pc[0] = np.nan
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    low15 = s.shift(1).rolling(15).min().to_numpy()
    b = pd.read_csv(BTC_CSV)
    b["datetime"] = pd.to_datetime(b["datetime"])
    btc = pd.Series(pd.to_datetime(dt).map(b.drop_duplicates("datetime").set_index("datetime")["close"]).to_numpy(float))
    f = pd.read_csv(FUND_CSV).sort_values("fundingTime")
    ft = pd.to_datetime(f.fundingTime, unit="ms").to_numpy()
    fr = f.fundingRate.astype(float).to_numpy()
    idx = np.searchsorted(ft, (pd.to_datetime(dt) - pd.Timedelta(hours=7)).to_numpy(), side="right") - 1
    funding = np.where(idx >= 0, fr[np.clip(idx, 0, None)], np.nan) * 100
    return {
        "突破幅度(ATR)": (low15 - c) / atr,
        "前24h報酬%": (s / s.shift(24) - 1).to_numpy() * 100,
        "前72h報酬%": (s / s.shift(72) - 1).to_numpy() * 100,
        "距SMA50%": (s / s.rolling(50).mean() - 1).to_numpy() * 100,
        "距SMA200%": (s / s.rolling(200).mean() - 1).to_numpy() * 100,
        "量比(前20)": vol / pd.Series(vol).shift(1).rolling(20).mean().to_numpy(),
        "taker買占比%": tb / vol * 100,
        "BTC前24h報酬%": (btc / btc.shift(24) - 1).to_numpy() * 100,
        "BTC前72h報酬%": (btc / btc.shift(72) - 1).to_numpy() * 100,
        "funding%": funding,
        "ATR14%": atr / c * 100,
    }, low15


def forward(c, i, n):
    return (c[i] / c[i + n] - 1) * 100 if i + n < len(c) else np.nan      # S 順向 = 跌為正


def binom_tail(k, n, p):
    return sum(comb(n, x) * p ** x * (1 - p) ** (n - x) for x in range(k + 1))


def path(c, h, l, i, bars=10):
    e = c[i]
    end = min(i + bars, len(c) - 1)
    if end <= i:
        return "資料不足"
    adv = (h[i + 1:end + 1] / e - 1) * 100
    fav = (1 - l[i + 1:end + 1] / e) * 100
    first = next((("先跌" if f >= 1 else "先漲") for f, a in zip(fav, adv) if f >= 1 or a >= 1), "未達±1%")
    return f"{first} 最大逆向 {adv.max():.2f}%@第{adv.argmax() + 1}根 最大順向 {fav.max():.2f}%@第{fav.argmax() + 1}根 第10根 {forward(c, i, min(bars, end - i)):+.2f}%"


def reclaim(c, low15, idx, bars=24):
    """訊號後 bars 根內收盤回到 15 根低點之上；回傳 (比例, 回到的根數中位)。"""
    hits = []
    n = 0
    for i in idx:
        if i + bars >= len(c):
            continue
        n += 1
        over = np.flatnonzero(c[i + 1:i + bars + 1] > low15[i])
        if len(over):
            hits.append(over[0] + 1)
    return len(hits) / max(n, 1) * 100, float(np.median(hits)) if hits else np.nan, n


def main() -> None:
    candles, engine, ind, dt = v40.load_long()
    f = v40.replay(v40.build_sim(engine), ind, dt).sort_values("entry_dt").reset_index(drop=True)
    c, h, l = (candles[k].to_numpy(float) for k in ("close", "high", "low"))
    feats, low15 = features(candles, ind, dt)
    ok = ok_mask(candles, 20, 30)
    s = f[f.side == "S"].copy()
    s["i"] = s.entry_bar.astype(int)
    s["group"] = np.where(s.entry_dt >= RECENT, "近期", np.where(ok[s.i.to_numpy()], "歷史正常", "歷史低波動"))
    for k, a in feats.items():
        s[k] = a[s.i.to_numpy()]
    print(f"K 線 {dt.iloc[0]:%Y-%m-%d}～{dt.iloc[-1]:%Y-%m-%d %H:%M}；S {len(s)} 筆；近期 = 進場 >= {RECENT:%Y-%m-%d}")

    print("\n[1] 三組績效")
    for g in GROUPS:
        x = s[s.group == g]
        p = x.pnl_usd
        fw = [np.nanmedian([forward(c, i, n) for i in x.i]) for n in STEPS]
        print(f"  {g:6s} {len(x):3d}筆 WR {(p > 0).mean() * 100:3.0f}% ${p.sum():7,.0f} 均${p.mean():6.1f} TP {(x.exit_reason == 'TP').mean() * 100:3.0f}%"
              f" MH {(x.exit_reason == 'MH').mean() * 100:3.0f}% SN {(x.exit_reason == 'SN').mean() * 100:3.0f}%"
              f" MFE中位 {x.mfe_pct.median():.2f}% MAE中位 {x.mae_pct.median():.2f}%｜順向中位 " +
              " ".join(f"{n}根 {v:+.2f}%" for n, v in zip(STEPS, fw)))

    print("\n[2] 進場情境")
    hist = s[s.group != "近期"]
    rec = s[s.group == "近期"]
    rng = np.random.default_rng(SEED)
    rows = []
    cols = ["gk_pctile"] + list(feats)
    print(f"  {'特徵':14s} {'歷史正常':>8s} {'歷史低波':>8s} {'近期':>8s}  近期中位在全歷史分位  隨機同數量抽樣 p")
    for k in cols:
        a = hist[k].dropna().to_numpy()
        m = rec[k].median()
        pct = (a < m).mean() * 100
        sims = np.array([np.median(rng.choice(a, len(rec), replace=False)) for _ in range(4000)])
        p = min(1.0, 2 * min((sims >= m).mean(), (sims <= m).mean()))
        rows.append((abs(pct - 50), k, pct, p))
        print(f"  {k:14s} {s[s.group == '歷史正常'][k].median():8.2f} {s[s.group == '歷史低波動'][k].median():8.2f} {m:8.2f}  {pct:5.1f}%  p={p:.3f}")
    print("  差最多：" + "、".join(f"{k}（第 {pct:.0f} 分位，p={p:.3f}）" for _, k, pct, p in sorted(rows, reverse=True)[:5]))
    print(f"  （{len(cols)} 個特徵一起看，單項 p<0.05 預期約有 {len(cols) * 0.05:.1f} 個純屬偶然）")

    for name, key in (("regime", "entry_regime"), ("星期", "dow"), ("台北小時段", "hb")):
        if key == "dow":
            hist = hist.assign(dow=hist.entry_dt.dt.day_of_week + 1); rec = rec.assign(dow=rec.entry_dt.dt.day_of_week + 1)
        if key == "hb":
            hist = hist.assign(hb=hist.entry_dt.dt.hour // 6 * 6); rec = rec.assign(hb=rec.entry_dt.dt.hour // 6 * 6)
        hv, rv = hist[key].value_counts(normalize=True) * 100, rec[key].value_counts(normalize=True) * 100
        print(f"  {name}（歷史→近期）：" + " ".join(f"{k} {hv.get(k, 0):.0f}%→{rv.get(k, 0):.0f}%" for k in sorted(set(hv.index) | set(rv.index), key=str)))
    print("  近期各 regime：" + " ".join(f"{k} {len(g)}筆 WR {(g.pnl_usd > 0).mean() * 100:.0f}% ${g.pnl_usd.sum():,.0f}" for k, g in rec.groupby("entry_regime")))

    print("\n[3] 近期逐筆")
    for x in rec.itertuples():
        print(f"  {x.entry_dt:%m-%d %H:%M} {x.entry_regime:8s} {x.exit_reason:3s} ${x.pnl_usd:6.1f} MFE {x.mfe_pct:4.2f}% MAE {x.mae_pct:5.2f}% 持{x.bars_held:2d}根 | {path(c, h, l, x.i)}")

    print("\n[4] 行情與假跌破")
    t = pd.to_datetime(dt)
    m = (t >= RECENT).to_numpy()
    sma200 = pd.Series(c).rolling(200).mean().to_numpy()
    print(f"  2026-07 起 ETH {c[m][0]:,.0f}→{c[m][-1]:,.0f}（{(c[m][-1] / c[m][0] - 1) * 100:+.1f}%），最高 {h[m].max():,.0f}，最低 {l[m].min():,.0f}；收在 SMA200 上方時間 {(c[m] > sma200[m]).mean() * 100:.0f}%")
    for ym, g in pd.DataFrame({"c": c[m], "t": t[m]}).groupby(t[m].dt.strftime("%Y-%m").to_numpy()):
        print(f"    {ym}：{g.c.iloc[0]:,.0f}→{g.c.iloc[-1]:,.0f}（{(g.c.iloc[-1] / g.c.iloc[0] - 1) * 100:+.1f}%）")
    print("  S 進場後 24h 內收回 15 根低點之上（已成交的單）")
    for g in GROUPS:
        rate, med, n = reclaim(c, low15, s[s.group == g].i.to_numpy())
        print(f"    {g:6s} {n:3d}筆：{rate:4.1f}%，收回中位第 {med:.0f} 根")
    sig = np.flatnonzero((np.nan_to_num(ind["pctile_S"], nan=100) < engine.S_GK_TH) & np.asarray(ind["brk_dn"], bool))
    sb = int(np.searchsorted(dt.to_numpy(), np.datetime64(pd.Timestamp(v40.START))))
    sig = sig[sig >= sb]
    lab = np.where((t.iloc[sig] >= RECENT).to_numpy(), "近期", np.where(ok[sig], "歷史正常", "歷史低波動"))
    print("  全部 S 訊號（不論是否成交）")
    for g in GROUPS:
        rate, med, n = reclaim(c, low15, sig[lab == g])
        print(f"    {g:6s} {n:3d}個：{rate:4.1f}%，收回中位第 {med:.0f} 根")

    print("\n[5] 樣本檢查")
    n, k = len(rec), int((rec.pnl_usd > 0).sum())
    for g in ("歷史正常", "歷史低波動"):
        p = (s[s.group == g].pnl_usd > 0).mean()
        print(f"  若真實勝率 = {g} {p * 100:.0f}%，{n} 筆中 ≤{k} 勝的機率 {binom_tail(k, n, p):.4f}")
    wins = (s[s.group != "近期"].pnl_usd.to_numpy() > 0).astype(int)
    roll = np.convolve(wins, np.ones(n, int), "valid")
    print(f"  歷史 S 連續 {n} 筆的滾動勝數最低 {roll.min()}（{roll.argmin() + 1} 起），≤{k} 的窗 {(roll <= k).sum()}/{len(roll)}")


if __name__ == "__main__":
    main()
