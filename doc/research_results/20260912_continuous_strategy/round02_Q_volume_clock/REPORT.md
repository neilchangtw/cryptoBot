# Q_volume_clock 研究結果

**REJECTED**。原狀態直接事件群 106／後期 37；3成本，主候選1次，鄰域0次。

0bp net 7926.70→6911.63、mark MDD 368.53→354.77、loss 3983.94→4570.34。

0bp失敗門檻：net_gain_5pct, loss_total_decreases, worst30_not_worse, early_net_not_worse。2/5bp詳見results.json。

歸因：{"counts": {"common": 257, "new": 7, "removed": 12, "changed_common": 93, "affected": 112, "improved": 45, "worse": 48, "old_mh_to_profit": 4, "old_mh_reasons": {"MH": 61, "BE": 5, "TP": 2, "MFE": 1, "MHx": 1, "SN": 1}, "removed_winners": 8, "winning_to_losing": 8}, "parts": {"avoided_losses": 146.23026490902856, "missed_winners": -279.7469510755552, "added_net": -11.142867866824275, "common_delta": -870.4135147198034, "terminal_delta": -1.8189894035458565e-12}, "net_delta": -1015.0730687531559, "trading_delta": -1018.5799999999999, "funding_delta": 3.5069312468455562, "closed_fee_delta": -20.0}

完整逐筆／funding／mark／gate／state存於ledgers，三成本no-op逐欄一致，8個物理前綴含未平倉截點一致；另從保存帳本獨立重建6套淨值及指標。主規則失敗即停止，不跑鄰域／延遲／WF／bootstrap／多重比較救援。
