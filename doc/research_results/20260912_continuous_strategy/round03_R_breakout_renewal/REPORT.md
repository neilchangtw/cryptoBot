# R_breakout_renewal 研究結果

**REJECTED**。原狀態直接事件群 83／後期 32；3成本，主候選1次，鄰域0次。

0bp net 7926.70→8076.45、mark MDD 368.53→444.56、loss 3983.94→4291.40。

0bp失敗門檻：net_gain_5pct, loss_total_decreases, mark_mdd_not_worse, worst30_not_worse, early_net_not_worse。2/5bp詳見results.json。

歸因：{"counts": {"common": 263, "new": 4, "removed": 6, "changed_common": 66, "affected": 76, "improved": 29, "worse": 37, "old_mh_to_profit": 3, "old_mh_reasons": {"MH": 65, "MFE": 2, "SN": 2, "BE": 1, "MHx": 1, "TP": 1}, "removed_winners": 3, "winning_to_losing": 5}, "parts": {"avoided_losses": 133.95198013262274, "missed_winners": -57.1031251750174, "added_net": 27.76788790010294, "common_delta": 45.12934601250083, "terminal_delta": -5.4569682106375694e-12}, "net_delta": 149.7460888702044, "trading_delta": 145.5, "funding_delta": 4.246088870209032, "closed_fee_delta": -8.0}

完整逐筆／funding／mark／gate／state存於ledgers，三成本no-op逐欄一致，8個物理前綴含未平倉截點一致；另從保存帳本獨立重建6套淨值及指標。主規則失敗即停止，不跑鄰域／延遲／WF／bootstrap／多重比較救援。
