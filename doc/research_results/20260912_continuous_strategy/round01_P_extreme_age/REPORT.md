# P_extreme_age 研究結果

**REJECTED**。原狀態直接事件群 106／後期 39；3成本，主候選1次，鄰域0次。

0bp net 7926.70→4594.57、mark MDD 368.53→456.63、loss 3983.94→3735.70。

0bp失敗門檻：net_gain_5pct, mark_mdd_not_worse, worst30_not_worse, early_net_not_worse, late_net_not_worse。2/5bp詳見results.json。

歸因：{"counts": {"common": 131, "new": 73, "removed": 138, "changed_common": 0, "affected": 211, "improved": 0, "worse": 0, "old_mh_to_profit": 0, "old_mh_reasons": {"MH": 35}, "removed_winners": 89, "winning_to_losing": 0}, "parts": {"avoided_losses": 1862.6345766728919, "missed_winners": -6093.429488464675, "added_net": 898.6613491791992, "common_delta": 0.0, "terminal_delta": -2.7284841053187847e-12}, "net_delta": -3332.1335626125874, "trading_delta": -3338.1399999999994, "funding_delta": 6.006437387414671, "closed_fee_delta": -260.0}

完整逐筆／funding／mark／gate／state存於ledgers，三成本no-op逐欄一致，8個物理前綴含未平倉截點一致；另從保存帳本獨立重建6套淨值及指標。主規則失敗即停止，不跑鄰域／延遲／WF／bootstrap／多重比較救援。
