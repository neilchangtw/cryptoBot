"""
V27 R1 — MH 結局可預測性檢定（meta-labeling 前置 gate）
=========================================================
問題：進場當下已知的特徵，能否預測「這筆最終會以 MH 虧損出場」？

方法（全部只用 IS = 前 50% bars 的交易，OOS 完全不碰）：
  1. Univariate rank-AUC（Mann-Whitney）：每個特徵單獨對 y_mh / y_loss 的分離力
  2. Walk-forward CV（IS 內 expanding window + 24-bar embargo）：
     Logistic / GBM 的 pooled out-of-fold AUC —— 這才是真實可用的預測力
  3. Permutation test：把標籤洗牌 200 次重跑同一 CV，得到 null AUC 分佈 → p-value

預先登記的判準（避免事後挑選）：
  - 任一 (side, label) 的 walk-forward AUC >= 0.60 且 permutation p < 0.05 → 有信號，進 R2
  - 否則 → 無信號，V27 提前 REJECT（與 V26 同精神：連 IS 門檻都過不了就不進 gates）
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

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

RNG = np.random.default_rng(42)
EMBARGO_BARS = 24
N_PERM = 200


def rank_auc(x, y):
    """Mann-Whitney AUC：P(x|y=1 > x|y=0)，NaN 略過。"""
    m = ~np.isnan(x)
    x, y = x[m], y[m]
    x1, x0 = x[y == 1], x[y == 0]
    if len(x1) == 0 or len(x0) == 0:
        return np.nan
    from scipy.stats import rankdata
    r = rankdata(np.concatenate([x1, x0]))
    return (r[:len(x1)].sum() - len(x1) * (len(x1) + 1) / 2) / (len(x1) * len(x0))


def make_folds(tdf, n_folds=4, init_frac=0.4):
    """IS 內 expanding-window 折：回傳 [(train_idx, test_idx), ...]（依 entry_bar 排序）。"""
    tdf = tdf.sort_values('entry_bar').reset_index(drop=True)
    n = len(tdf)
    init = int(n * init_frac)
    rest = n - init
    step = max(1, rest // n_folds)
    folds = []
    start = init
    while start < n:
        test_idx = np.arange(start, min(start + step, n))
        first_test_bar = tdf.loc[test_idx[0], 'entry_bar']
        train_idx = np.array([j for j in range(start)
                              if tdf.loc[j, 'exit_bar'] <= first_test_bar - EMBARGO_BARS])
        if len(train_idx) >= 20 and len(test_idx) > 0:
            folds.append((train_idx, test_idx))
        start += step
    return tdf, folds


def wf_cv_auc(tdf, cols, ycol, model_name, y_override=None):
    """Walk-forward pooled out-of-fold AUC。y_override 供 permutation 用。"""
    tdf, folds = make_folds(tdf)
    y_all = y_override if y_override is not None else tdf[ycol].to_numpy()
    X_all = tdf[cols].to_numpy(float)

    oof_y, oof_p = [], []
    for train_idx, test_idx in folds:
        Xtr, ytr = X_all[train_idx], y_all[train_idx]
        Xte, yte = X_all[test_idx], y_all[test_idx]
        if ytr.sum() < 3 or ytr.sum() > len(ytr) - 3:
            continue
        med = np.nanmedian(Xtr, axis=0)
        Xtr = np.where(np.isnan(Xtr), med, Xtr)
        Xte = np.where(np.isnan(Xte), med, Xte)
        if model_name == 'logit':
            sc = StandardScaler().fit(Xtr)
            clf = LogisticRegression(C=0.5, max_iter=2000)
            clf.fit(sc.transform(Xtr), ytr)
            p = clf.predict_proba(sc.transform(Xte))[:, 1]
        else:
            clf = GradientBoostingClassifier(n_estimators=80, max_depth=2,
                                             learning_rate=0.05, subsample=0.8,
                                             random_state=0)
            clf.fit(Xtr, ytr)
            p = clf.predict_proba(Xte)[:, 1]
        oof_y.extend(yte)
        oof_p.extend(p)

    oof_y, oof_p = np.array(oof_y), np.array(oof_p)
    if len(np.unique(oof_y)) < 2:
        return np.nan, 0
    return roc_auc_score(oof_y, oof_p), len(oof_y)


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    dts = df['datetime'].values
    trades = eng.simulate_v14_detailed(ind, dts, realistic=True)
    feats = ve.build_features(df, ind)
    tdf = ve.trades_with_features(trades, feats)

    mid = len(df) // 2
    tdf['is_is'] = tdf['entry_bar'] < mid

    print("=" * 72)
    print("V27 R1 — MH 可預測性檢定（IS only，OOS 不碰）")
    print(f"全期 {len(tdf)} 筆 / IS {tdf['is_is'].sum()} 筆"
          f"（L {((tdf.side == 'L') & tdf.is_is).sum()} / S {((tdf.side == 'S') & tdf.is_is).sum()}）")
    print("=" * 72)

    results = []
    for side in ('L', 'S'):
        sub = tdf[(tdf.side == side) & tdf.is_is].copy()
        cols = ve.feature_cols(side)
        n_mh, n_loss = sub.y_mh.sum(), sub.y_loss.sum()
        print(f"\n### {side} 側 IS：{len(sub)} 筆，MH {n_mh} / LOSS {n_loss}")

        # ---- 1) Univariate rank-AUC ----
        print(f"  {'feature':<18} {'AUC(y_mh)':>10} {'AUC(y_loss)':>12}")
        uni = []
        for f in cols:
            a_mh = rank_auc(sub[f].to_numpy(float), sub.y_mh.to_numpy())
            a_ls = rank_auc(sub[f].to_numpy(float), sub.y_loss.to_numpy())
            uni.append((f, a_mh, a_ls))
        # 依 |AUC-0.5| 排序印前 10
        uni.sort(key=lambda t: -abs((t[1] if not np.isnan(t[1]) else 0.5) - 0.5))
        for f, a_mh, a_ls in uni[:10]:
            print(f"  {f:<18} {a_mh:>10.3f} {a_ls:>12.3f}")

        # ---- 2) Walk-forward CV AUC ----
        for ycol in ('y_mh', 'y_loss'):
            for model in ('logit', 'gbm'):
                auc, n_oof = wf_cv_auc(sub, cols, ycol, model)
                results.append((side, ycol, model, auc, n_oof, sub))
                print(f"  WF-CV  {ycol:<7} {model:<6} AUC={auc:.3f}（oof n={n_oof}）")

    # 合併 L+S（side dummy）擴大樣本
    tdf['side_L'] = (tdf.side == 'L').astype(int)
    both = tdf[tdf.is_is].copy()
    both['gk_pctile'] = np.where(both.side == 'L', both.gk_pctile_L, both.gk_pctile_S)
    both['brk_margin'] = np.where(both.side == 'L', both.brk_margin_L, both.brk_margin_S)
    both['compress_len'] = np.where(both.side == 'L', both.compress_len_L, both.compress_len_S)
    cols_b = ['gk_pctile', 'brk_margin', 'compress_len', 'side_L'] + ve.FEATURE_COLS_COMMON
    print(f"\n### L+S 合併 IS：{len(both)} 筆，MH {both.y_mh.sum()} / LOSS {both.y_loss.sum()}")
    for ycol in ('y_mh', 'y_loss'):
        for model in ('logit', 'gbm'):
            auc, n_oof = wf_cv_auc(both, cols_b, ycol, model)
            results.append(('L+S', ycol, model, auc, n_oof, (both, cols_b)))
            print(f"  WF-CV  {ycol:<7} {model:<6} AUC={auc:.3f}（oof n={n_oof}）")

    # ---- 3) 對最佳組合做 permutation test ----
    best = max((r for r in results if not np.isnan(r[3])), key=lambda r: r[3])
    side, ycol, model, best_auc, n_oof, payload = best
    print(f"\n### Permutation test（最佳組合：{side} {ycol} {model} AUC={best_auc:.3f}）")
    if side == 'L+S':
        sub, cols = payload
    else:
        sub, cols = payload, ve.feature_cols(side)
    y = sub[ycol].to_numpy()
    null_aucs = []
    for _ in range(N_PERM):
        yp = RNG.permutation(y)
        a, _ = wf_cv_auc(sub, cols, ycol, model, y_override=yp)
        if not np.isnan(a):
            null_aucs.append(a)
    null_aucs = np.array(null_aucs)
    p = float((null_aucs >= best_auc).mean())
    print(f"  null AUC: mean={null_aucs.mean():.3f}, P95={np.percentile(null_aucs, 95):.3f}, "
          f"max={null_aucs.max():.3f}（{len(null_aucs)} 次洗牌）")
    print(f"  p-value = {p:.3f}")

    print("\n" + "=" * 72)
    verdict = (best_auc >= 0.60 and p < 0.05)
    print(f"判準：WF AUC>=0.60 且 perm p<0.05 → {'有信號，進 R2' if verdict else '無信號，V27 REJECT'}")
    print(f"最佳 WF AUC = {best_auc:.3f}（{side}/{ycol}/{model}），p = {p:.3f}")
    print("=" * 72)


if __name__ == '__main__':
    main()
