# 第7輪 V：修復舊SampEn實驗後的可辨識性

U只有4／1事件群，未計收益。本輪最多考慮SampEn舊實驗缺陷、已有PE／Hurst重測、事件冷卻；後兩者已有研究且無新證據，選SampEn舊實驗缺陷，不任意加入新的熵種類。

具體舊缺陷：explore_v7_r1_indicator_sweep.py的m模板用N-m個，m+1模板卻只用N-m-1個，丟掉最後可延伸模板；又把A=0且B>0的無限大熵與B=0的不可估計混成NaN，100根完整rolling因NaN擴散無法輸出。doc/v7_research.md把全NaN歸於r_factor與報酬std太小，但原函數r已乘std，正比例縮放理應不變。R2註解說normalize，實際全庫只有R1的sample_entropy函數，沒有修复實作。先用合成固定資料驗證這些問題，沒有藉舊損益挑選新閾值。

參考定義：[PhysioNet SampEn](https://physionet.org/content/sampen/1.0.0/)使用匹配m點之後仍匹配下一點的條件機率；[r參數說明](https://archive.physionet.org/physiotools/gmse/tutorial/node2.html)說明r乘std和先正規化等價；[Richman/Moorman原文](https://journals.physiology.org/doi/10.1152/ajpheart.2000.278.6.h2039)區分B=0未定義和A=0無限大。這些是統計定義，不能推論交易alpha。

主公式在品質、事件和PnL前固定：沿用舊N=20個log close returns、m=2、r=.2*population std，不加長窗口或放寬r。對同一N-m個可延伸起點，以不同起點的無序配對計數B（m點Chebyshev距離<=r）與A（同對再加下一點亦<=r）。B=0→undefined，B>0/A=0→+inf，A=B>0→0，恆值序列依<=規則為0；不填補任何undefined。

raw[i]取returns[i-20:i]，再shift(1)後對100根完整窗口做strict rank=100*count(value<last)/99；這修正舊名為percentile實為minmax的介面，明列為新候選定義，並非宣稱原版重現。inf是可比較的有定義擴展值，NaN不可用，100根任何undefined即特徵invalid。L/S都只在rank<20才允許原進場，主通過才鄰域15/25；其他進出場和曝險不變。

先檢查所有基準eligible gates的特徵皆可估計；若有undefined立即DATA LIMITED，不計直接事件或候選PnL，不能改N/r/缺值政策救援。只有品質全過，才30／15獨立事件門檻、三成本no-op／物理前綴及完整狀態經濟門檻。其他依continuous契約。此次只新增修正研究，不改舊函數、旧報告或正式策略。
