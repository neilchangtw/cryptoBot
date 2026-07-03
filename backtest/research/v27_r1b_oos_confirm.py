"""
V27 R1b — OOS 單次確認測試（把門關死用，非挑選）
====================================================
R1 已依預先登記判準 REJECT（最佳 WF AUC 0.614, perm p=0.130）。
本腳本做最後一個誠實測試：模型/特徵全部在 IS 上定案（train on IS 全部交易），
對 OOS 交易做「一次性」預測 → OOS AUC。這是 meta-labeling 能拿到的最好情境
（IS 樣本全用、無 CV 折損）。若 OOS AUC 也 ~0.5，方向徹底封死。

另附經濟量化：就算按 IS 訓練的模型在 OOS 過濾 P(MH) 最高的 K% 進場，
PnL 差多少（靜態估計，不重跑序列——只是 ceiling 示意）。
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


def fit_predict(sub_is, sub_oos, cols, ycol, model):
    Xtr = sub_is[cols].to_numpy(float)
    Xte = sub_oos[cols].to_numpy(float)
    ytr = sub_is[ycol].to_numpy()
    med = np.nanmedian(Xtr, axis=0)
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    if model == 'logit':
        sc = StandardScaler().fit(Xtr)
        clf = LogisticRegression(C=0.5, max_iter=2000).fit(sc.transform(Xtr), ytr)
        return clf.predict_proba(sc.transform(Xte))[:, 1]
    clf = GradientBoostingClassifier(n_estimators=80, max_depth=2, learning_rate=0.05,
                                     subsample=0.8, random_state=0).fit(Xtr, ytr)
    return clf.predict_proba(Xte)[:, 1]


def main():
    df = ve.load_data()
    eng = ve.load_engine()
    ind = eng.compute_indicators(df)
    trades = eng.simulate_v14_detailed(ind, df['datetime'].values, realistic=True)
    feats = ve.build_features(df, ind)
    tdf = ve.trades_with_features(trades, feats)
    mid = len(df) // 2

    print("=" * 72)
    print("V27 R1b — train on IS 全部、單次預測 OOS（最後確認）")
    print("=" * 72)

    for side in ('L', 'S'):
        sub = tdf[tdf.side == side]
        sub_is = sub[sub.entry_bar < mid]
        sub_oos = sub[sub.entry_bar >= mid]
        cols = ve.feature_cols(side)
        print(f"\n### {side} 側：IS {len(sub_is)} 筆（MH {sub_is.y_mh.sum()}）→ "
              f"OOS {len(sub_oos)} 筆（MH {sub_oos.y_mh.sum()}）")
        for ycol in ('y_mh', 'y_loss'):
            for model in ('logit', 'gbm'):
                p = fit_predict(sub_is, sub_oos, cols, ycol, model)
                y = sub_oos[ycol].to_numpy()
                auc = roc_auc_score(y, p) if len(np.unique(y)) > 1 else np.nan
                print(f"  OOS AUC  {ycol:<7} {model:<6} = {auc:.3f}")

        # 經濟 ceiling 示意：过滤 P(MH) 最高的 20% / 30% OOS 進場（靜態，不重跑序列）
        p = fit_predict(sub_is, sub_oos, cols, 'y_mh', 'logit')
        oos = sub_oos.copy()
        oos['p_mh'] = p
        base = oos.pnl_usd.sum()
        for frac in (0.2, 0.3):
            th = np.quantile(p, 1 - frac)
            kept = oos[oos.p_mh < th]
            removed = oos[oos.p_mh >= th]
            print(f"  靜態過濾 top{int(frac*100)}% P(MH)：移除 {len(removed)} 筆"
                  f"（其中真 MH {removed.y_mh.sum()} 筆，被移除 PnL ${removed.pnl_usd.sum():+.0f}）"
                  f" → OOS PnL ${base:+.0f} → ${kept.pnl_usd.sum():+.0f}")


if __name__ == '__main__':
    main()
