# 持續研究進度（不設三輪結案限制）

使用者要求繼續；目前研究保持進行，沒有合格改善、沒有部署。以下最佳只按已評估0bp收益排序，不代表通過風險／收益門檻。

|方案／額外bp|交易數|事件群 全／後|淨收益U|PF|勝率|mark MDD U|最差30日U|虧損總額U|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|base／0|269|—|7,926.70|2.99|63.20%|368.53|-296.11|3,983.94|
|base／2|269|—|7,582.92|2.85|62.45%|381.27|-308.42|4,104.32|
|base／5|267|—|7,014.88|2.66|61.42%|400.37|-340.75|4,237.38|
|R_breakout_renewal／0|267|83／32|8,076.45|2.88|63.67%|444.56|-314.82|4,291.40|
|R_breakout_renewal／2|267|83／32|7,668.86|2.72|63.30%|464.82|-329.49|4,450.63|
|R_breakout_renewal／5|267|83／32|7,494.72|2.61|63.30%|507.47|-363.74|4,647.78|

- P_extreme_age：REJECTED；106／39群；0bp net 4594.57、loss 3735.70、mark MDD 456.63。失敗=net_gain_5pct,mark_mdd_not_worse,worst30_not_worse,early_net_not_worse,late_net_not_worse。
- Q_volume_clock：REJECTED；106／37群；0bp net 6911.63、loss 4570.34、mark MDD 354.77。失敗=net_gain_5pct,loss_total_decreases,worst30_not_worse,early_net_not_worse。
- R_breakout_renewal：REJECTED；83／32群；0bp net 8076.45、loss 4291.40、mark MDD 444.56。失敗=net_gain_5pct,loss_total_decreases,mark_mdd_not_worse,worst30_not_worse,early_net_not_worse。
- T_anchored_close_exit：REJECTED；103／38群；0bp net 4331.75、loss 4621.07、mark MDD 538.98。失敗=net_gain_5pct,loss_total_decreases,mark_mdd_not_worse,worst30_not_worse,early_net_not_worse,late_net_not_worse。
- S_onchain_basefee：DATA_LIMITED；候選收益未計算。S的3個區塊日期小樣本有base fee／時間欄位，但沒有歷史finality／availability證據；block取樣7829 bytes。另finality補查3GET/340 bytes遇歷史state HTTP403即停，未擴大資料或用假定延遲放行。
- U_regime_invalidation：INSUFFICIENT_SAMPLE；候選收益未計算。原始事件群 4／後期 1。
- V_entropy_repair：DATA_LIMITED；候選收益未計算。

固定200U/20x/每倉4000U，原凍結17,519根，0bp仍有費用與funding。所有收益皆完整狀態，包含替代交易、冷卻、熔斷及未平倉；每輪3成本no-op、8前綴及6帳本獨立核對。原策略／.env／狀態／VPS／Git不變。

已計收益的方向均經濟淘汰；其他方向因資料或樣本未准入，不代表其經濟效果已被否定。不再跑失敗方向的鄰域；尚未執行延遲、連續WF、區塊bootstrap、多重比較及新資料前瞻驗證。歷史不是未見資料。下一方向仍先查重與資料可用時點，未以持續研究為理由放寬標準。各輪預登記、原事件、結果、歸因與完整帳本都在子目錄。
