# 持續研究第4輪 S：Ethereum鏈上需求趨勢／資料預登記

R新突破續期已REJECTED，不調硬上限或加風控條件救援。考慮Ethereum base fee、gas利用率、地址活動；選base fee，屬區塊header欄位且可由公開鏈驗證，預期不需全交易下載。另兩者未計事件或PnL。V19/V21只有Glassnode付費鏈上來源未取得的記錄，本次使用新的免費公開區塊資料，不重跑舊OHLCV。

主假說：鏈上資源需求趨勢若與期貨突破方向相反，該突破較可能缺乏延續。不是把fee單位當美元或把地址等同使用者。

預定主規則：對每個UTC決策日D，取D-1到D-7每天UTC00:00之前最後一個canonical Ethereum block的base_fee_per_gas中位數A，及D-8到D-14同法中位數B；score=log(A/B)。原L進場須score>=0，S須score<=0。零score雙向可用。所有block至少早於當天決策24h，不用D當天block、不使用區塊內交易名單／交易所訊息。只是固定時點取樣，不冒充每日均值。若主候選通過才測5/10日兩鄰域；不調方向／時點救援。

先小樣本驗證，未計事件或PnL：只抓2024-09-07、2025-09-01、2026-09-06各UTC00:00的lookup、block、next block；最多9次公開GET，總回應上限100KB，不下載交易。完整階段只有證據合格後才可另登記範圍／上限，不能自行擴大下載。

來源：
- [Blockscout timestamp查block](https://docs.blockscout.com/api-reference/block/get-block-number-by-timestamp)：closest=before。
- [Blockscout block詳情](https://docs.blockscout.com/api-reference/get-block-info)：hash、height、timestamp、base_fee_per_gas、gas欄位。
- [Ethereum PoS finality](https://ethereum.org/developers/docs/consensus-mechanisms/pos/)及[攻擊／防護](https://ethereum.org/developers/docs/consensus-mechanisms/pos/attack-and-defense)：finality是協議狀態，不是單憑block timestamp後過若干分鐘就能保證。

准入：時間必須由前後block括住取樣時點、欄位合法且hash可對應；更重要的是需能合理證明該版本在當時決策之前已canonical/finalized及可取得。**現在查到的canonical hash／timestamp或現在的finalized=true不等於歷史finalized/received時間**。24h僅是預留緩衝，不能代替資料。若樣本來源不能提供歷史時點證據，先記DATA LIMITED，不下載全期、不算暫事件或候選PnL。不得因block不可改寫的一般描述而跳過實際可用時點確認。

任何缺值、future-block、樣本coverage不足、非正basefee或API不可用則停止，不填0、不就近選別日。若通過以上並取得全期合格資料，才按原狀態直接拒絕的24h全30／後15群准入，三成本完整狀態及所有風險／收益門檻遵循continuous契約。
