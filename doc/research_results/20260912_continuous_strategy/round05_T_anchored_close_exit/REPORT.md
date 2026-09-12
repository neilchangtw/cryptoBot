# T_anchored_close_exit 研究結果

**REJECTED**。原狀態直接事件群 103／後期 38；3成本，主候選1次，鄰域0次。

0bp net 7926.70→4331.75、mark MDD 368.53→538.98、loss 3983.94→4621.07。

0bp失敗門檻：net_gain_5pct, loss_total_decreases, mark_mdd_not_worse, worst30_not_worse, early_net_not_worse, late_net_not_worse。2/5bp詳見results.json。

歸因：{"counts": {"common": 247, "new": 19, "removed": 22, "changed_common": 117, "affected": 158, "improved": 47, "worse": 69, "old_mh_to_profit": 2, "old_mh_reasons": {"ACX": 60, "MH": 7}, "removed_winners": 11, "winning_to_losing": 39}, "parts": {"avoided_losses": 447.86999708505886, "missed_winners": -569.5930876890916, "added_net": -76.66708404336748, "common_delta": -3396.5561898801457, "terminal_delta": -6.366462912410498e-12}, "net_delta": -3594.946364527551, "trading_delta": -3591.9300000000003, "funding_delta": -3.0163645275458144, "closed_fee_delta": -12.0}

完整逐筆／funding／mark／gate／state存於ledgers，三成本no-op逐欄一致，8個物理前綴含未平倉截點一致；另從保存帳本獨立重建6套淨值及指標。主規則失敗即停止，不跑鄰域／延遲／WF／bootstrap／多重比較救援。
