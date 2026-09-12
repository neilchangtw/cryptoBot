# S歷史定案證據補查（不增加候選或收益試驗）

S原公式、24h緩衝及停止條件全部保留。此補查只問：公開Beacon節點是否提供指定歷史slot的finality checkpoints；不是把現在finalized欄位當歷史證據。

在查資料前固定範圍：PublicNode mainnet Beacon API，先GET `/eth/v1/beacon/genesis`，再對S三個既定reference dates（2024-09-07、2025-09-01、2026-09-06）加23h計算slot=(unix-genesis_time)//12，GET `/eth/v1/beacon/states/{slot}/finality_checkpoints`。最多4個GET，回應合計上限32KB，timeout15s；HTTP失敗、狀態不存在、網路／格式錯誤即停，不換供應商重試。sandbox網路限制只可由標準權限流程執行同一命令，保留每次嘗試。

若全部回傳有效歷史checkpoint，仍只算來源初步可用；後續要另登記execution block hash至checkpoint的連結證據，不能直接放行S。未通過維持DATA LIMITED，不下載全期、不計事件或PnL。

官方語義：[Ethereum Beacon API getStateFinalityCheckpoints](https://raw.githubusercontent.com/ethereum/beacon-APIs/master/apis/beacon/states/finality_checkpoints.yaml)回傳指定state的checkpoints，並定義404 state not found；[PublicNode](https://ethereum.publicnode.com/)提供mainnet Beacon API，但頁面的archive標示並不保證所有歷史state可取。
