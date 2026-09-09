"""進場條件描述性對照；不以事後出場原因決定禁單。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import execute_optimization_plan_20260908 as cost

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/maxhold_entry_diagnostic_20260908'
NAMES = {
    'gk':'GK百分位', 'breakout_pct':'突破距離%', 'compression':'壓縮持續棒數',
    'slope':'原始SMA200斜率%', 'signed_ret4':'順向4h報酬%', 'signed_ret24':'順向24h報酬%',
    'signed_dist200':'順向距SMA200%', 'atr_pct':'ATR14占價格%',
    'body':'實體占振幅', 'rejection_wick':'突破方向影線占比', 'close_strength':'順向收盤位置',
    'vol20':'相對前20棒量比', 'signed_taker':'順向主動量差',
    'signed_taker6':'順向6h主動量差', 'adx':'ADX14', 'signed_di':'順向DI差',
}


def features(d, engine, side):
    ind = engine.compute_indicators(d)
    aux = cost.features(d)
    o,h,l,c,v,tb = [d[k].astype(float) for k in ['open','high','low','close','volume','taker_buy_volume']]
    sign = 1 if side == 'L' else -1
    boundary = c.shift(1).rolling(15).max() if side == 'L' else c.shift(1).rolling(15).min()
    span = (h-l).replace(0,np.nan)
    gk = np.asarray(ind['pctile_'+side]); run=0; compression=[]
    for p in gk:
        run=run+1 if np.isfinite(p) and p < (25 if side=='L' else 35) else 0
        compression.append(run)
    return pd.DataFrame({
        'gk':gk, 'breakout_pct':sign*(c-boundary)/boundary*100, 'compression':compression,
        'slope':np.asarray(ind['slope'])*100,
        'signed_ret4':sign*(c/c.shift(4)-1)*100, 'signed_ret24':sign*(c/c.shift(24)-1)*100,
        'signed_dist200':sign*(c/c.rolling(200).mean()-1)*100, 'atr_pct':aux['atr']/c*100,
        'body':abs(c-o)/span,
        'rejection_wick':(h-pd.concat([o,c],axis=1).max(axis=1))/span if side=='L' else (pd.concat([o,c],axis=1).min(axis=1)-l)/span,
        'close_strength':(c-l)/span if side=='L' else (h-c)/span,
        'vol20':v/v.shift(1).rolling(20).mean().replace(0,np.nan),
        'signed_taker':sign*(2*tb/v.replace(0,np.nan)-1),
        'signed_taker6':sign*(2*tb.rolling(6).sum()/v.rolling(6).sum().replace(0,np.nan)-1),
        'adx':aux['adx'], 'signed_di':sign*(aux['pdi']-aux['mdi']),
    })


def stats(f):
    mh=f.reason_code.eq('MH'); wins=f.net>0; other=(~mh)&(~wins)
    return {'n':len(f), 'mh':int(mh.sum()), 'mh_rate':float(mh.mean()*100) if len(f) else None,
            'net':float(f.net.sum()), 'avg':float(f.net.mean()) if len(f) else None,
            'mh_net':float(f.loc[mh,'net'].sum()), 'winners':int(wins.sum()),
            'winner_net':float(f.loc[wins,'net'].sum()),'other_loss_n':int(other.sum()),
            'other_loss_net':float(f.loc[other,'net'].sum()),
            'static_skip_delta':float(-f.net.sum())}


def auc(x,y):
    if not len(x) or y.sum()==0 or y.sum()==len(y):return None
    ranks=pd.Series(x).rank().to_numpy(); n=int(y.sum())
    return float((ranks[y].sum()-n*(n+1)/2)/(n*(len(y)-n)))


def write_report(f,b,r,medians,result):
    lines=['# MaxHold進場條件比較結果（2026-09-08）','',
           '**結論：NO PROMOTION。本輪沒有找到可據以禁單的穩定負收益條件。這是描述性診斷，不是新過濾器回測。**','',
           '## 比較範圍與方法','',
           '- 使用凍結的17,519根ETHUSDT 1h行情，2024-09-08 11:00～2026-09-08 09:00（UTC+8開盤標籤）。固定200U、20x、原成交成本及完整公開funding，額外滑價0bp。',
           '- 重跑269筆基準，進出場bar、方向、原因及扣funding淨利與前輪保存結果一致；總淨利7,926.70美元。',
           '- L/S分開比較16個連續特徵；各按2026前、已於分界48h之前出場交易的特徵三分位切低／中／高，切點不用交易損益，後期不重算。另按regime、訊號棒小時、星期分組。',
           '- early／late按實際進場日分為2026前與2026年。個別交易損益全部歸進場期，與前輪按每日mark淨值差的期間收益口徑不同。兩段都已被研究過，不稱未見OOS。',
           '- 全部特徵只用訊號棒收盤時已知資料；16欄在269筆均無缺值。6組截斷核對（L/S各6000、11000、16000根）全部通過。未使用未來MFE/MAE、最終funding rate或出場原因當特徵。',
           '- 本輪不使用可能缺少尾端覆蓋的BTC快取、不下載新資料、不讀私人帳戶、不做ML組合搜尋。','',
           '## 交易結果總覽','',
           '|方向|交易數|MH數／率|MH淨損益|其他非正交易數／淨損益|贏單數／淨損益|全部淨利|',
           '|---|---:|---:|---:|---:|---:|---:|']
    for side in ['L','S']:
        s=stats(f[f.side.eq(side)])
        lines.append(f"|{side}|{s['n']}|{s['mh']}／{s['mh_rate']:.1f}%|{s['mh_net']:+,.2f}|{s['other_loss_n']}／{s['other_loss_net']:+,.2f}|{s['winners']}／{s['winner_net']:+,.2f}|{s['net']:+,.2f}|")
    lines+=['','## 開單當下的差異','',
            '下表是各組中位數，不是禁單閾值。MH與贏家都可能有相同特徵；完整四分位範圍見medians.csv。','',
            '|條件|L：MH／贏單|S：MH／贏單|','|---|---:|---:|']
    m=pd.DataFrame(medians)
    for name in ['gk','breakout_pct','compression','atr_pct','vol20','signed_taker','close_strength','adx']:
        vals=[]
        for side in ['L','S']:
            q=m[(m.side==side)&(m.period=='all')&(m.feature==name)].set_index('group')
            vals.append(f"{q.loc['MH','median']:.3f}／{q.loc['WIN','median']:.3f}")
        lines.append(f"|{NAMES[name]}|{'|'.join(vals)}|")
    lines+=['','MH交易的突破距離中位數在L/S兩邊都沒有比贏單更小；空單MH的量比與主動賣量反而較大。因此不能把「突破較弱、量不夠」直接當成此次MH的共同原因。中位數差異也不能證明耗竭或因果機制。','',
            '## MH多，是否值得全部不開？','',
            '|條件（全期）|交易／MH|MH率|MH淨損益|贏單收益|其他虧損|分組總淨利|',
            '|---|---:|---:|---:|---:|---:|---:|']
    examples=[('L','regime_code','SIDE','L／SIDE'),('S','regime_code','MILD_UP','S／MILD_UP'),
              ('S','signed_taker','high','S／主動賣量差高組'),('S','adx','mid','S／ADX中組')]
    for side,name,label,title in examples:
        q=b[(b.side==side)&(b.feature==name)&(b.bucket==label)&(b.period=='all')].iloc[0]
        lines.append(f"|{title}|{int(q.n)}／{int(q.mh)}|{q.mh_rate:.1f}%|{q.mh_net:+,.2f}|{q.winner_net:+,.2f}|{q.other_loss_net:+,.2f}|{q.net:+,.2f}|")
    threshold=result['thresholds']['S_signed_taker'][1]
    lines+=['',f'空單主動量差定義為1−2×taker買量／總量，高組>{threshold:.6f}（主動賣量占比>{(1+threshold)/2*100:.2f}%）。ADX中組為19.260982<ADX≤26.694771。這些是早期特徵分位數，不是全期挑出來的最佳門檻。',
            '', '以上條件組彼此重疊，不能相加。靜態刪掉整组的收益差額等於該組總淨利取負；例如刪掉S／MILD_UP可避開813.64美元MH虧損及52.03美元其他虧損，但失去1,976.44美元贏單收益，原交易表淨少1,110.77美元。這不包含拒單後新增交易與冷卻／熔斷變動，不能當作完整策略回測。','',
            '## 時期穩定性與小樣本','',
            '|S／主動賣量差高組|交易數|MH數／率|分組淨利|','|---|---:|---:|---:|']
    for period in ['early','late']:
        q=b[(b.side=='S')&(b.feature=='signed_taker')&(b.bucket=='high')&(b.period==period)].iloc[0]
        lines.append(f"|{period}|{int(q.n)}|{int(q.mh)}／{q.mh_rate:.1f}%|{q.net:+,.2f}|")
    lines+=['',
            '- 16特徵×L/S×3分組，共96個連續特徵組，全期總淨利全部為正。這些組互相重疊，不是96份獨立證據。',
            '- 加上6個regime、39個非空小時、9個非空星期組，共150個非空組；沒有前後期各至少10筆且兩期淨利均負的組。事實上早期至少10筆的組均為正，沒有依早期負收益可選出的禁單候選。',
            '- 全期負收益僅4個訊號小時組，樣本各1～7筆，不能據此設定禁單時段。','',
            '|方向／訊號棒小時（UTC+8）|交易数|分組淨利|','|---|---:|---:|']
    for q in b[(b.period=='all')&(b.net<0)].itertuples():
        lines.append(f'|{q.side}／{q.bucket}|{q.n}|{q.net:+,.2f}|')
    lines+=['','時段是訊號K棒開盤標籤，真正成交約在下一小時；例如21點訊號棒对应22點進場，不能混用。','',
            '## 關聯檢查與查重','',
            '以MH對非MH計算rank-AUC，表示特徵排序與MH標籤的關聯，不是模型預測準確率。對32個L/S特徵檢查，在每個進場季度內置換標籤2000次，雙尾p值作Holm調整。季度內置換仍不保留所有連續交易依賴，因此只作探索性敏感度，不是嚴格因果或未來有效性檢定。','',
            '|特徵|全期AUC|早期AUC|後期AUC|原始p|Holm p|','|---|---:|---:|---:|---:|---:|']
    for q in r.sort_values('p').head(4).itertuples():
        lines.append(f'|{q.side}／{NAMES[q.feature]}|{q.auc_all:.3f}|{q.auc_early:.3f}|{q.auc_late:.3f}|{q.p:.4f}|{q.holm_p:.4f}|')
    lines+=['',
            '32項沒有任何Holm p<0.05。最值得後續觀察的是S主動量差，但它在V27已有相近特徵，不能包裝成全新發現；即使與MH有關聯，也不代表該組的期望收益為負。未對類別分組宣稱顯著性。',
            '', 'ATR採本輪Wilder14、量比採不含訊號棒的前20根平均；V27 ATR為簡單平均、量比分母包含訊號棒，不能當成精確同一規則的重現。主動量差與V27 imbalance為方向調整及線性換算；本輪不再搜尋更細量能閾值。','',
            '## 是否有改善辦法','',
            '現有資料不支持直接禁止上述高MH組。這不等於證明任意過濾策略都無效，而是本輪沒有取得值得升級到候選完整回測的負期望條件。依計畫停止在診斷階段，不為了得到改善而切更多交集、挑近期虧損或降低樣本門檻。',
            '', '本輪候選禁單回測為0組，未進行候選滑價壓測、WF或隨機拒單對照；這些只在有事前可選出的候選時執行。若後續研究5m形成過程，需另登記新資訊假說，維持進場當下可得與未來樣本驗證。棒內風險、出場調整與本題分開。','',
            '## 重現與檔案','',
            '```powershell',r'.\.venv\Scripts\python.exe backtest/research/maxhold_entry_diagnostic_20260908.py','```','',
            '本機data/maxhold_entry_diagnostic_20260908保存manifest、逐筆entry_features、groups、medians、buckets、ranks、negative_both_periods與results（含來源SHA256）。需既有凍結行情及完整公開成本資料，無自動下載。研究計畫見[maxhold_entry_filter_plan_20260908.md](maxhold_entry_filter_plan_20260908.md)。正式策略與VPS未修改。']
    text='\n'.join(lines)+'\n'
    for old,new in [('整组','整組'),('交易数','交易數'),('对应','對應')]:text=text.replace(old,new)
    (ROOT/'doc/maxhold_entry_diagnostic_20260908.md').write_text(text,encoding='utf-8')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={'status':'DESCRIPTIVE_ONLY','features':NAMES,'split':'2026-01-01',
              'bins':'per-side thirds from entries exited before split minus 48h; frozen later',
              'min_bucket_each_period':10,'thresholds_not_selected_by_pnl':True,
              'historical_data_already_seen':True,'candidate_backtests':0,
              'exclude':'future MFE/MAE/exit labels from entry features; BTC stale cache excluded',
              'rank_test':'2000 label permutations within side and entry calendar quarter; two-sided; Holm over 32 tests',
              'seed':20260908}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    study=cost.Study(); trades,eq,baseline=study.run(save=False)
    old=pd.read_csv(ROOT/'data/optimization_execution_20260908/base_flat_0_trades.csv')
    assert len(trades)==269 and np.allclose(trades.net,old.net,atol=1e-8)
    assert trades[['side','entry_bar','exit_bar','reason_code']].equals(old[['side','entry_bar','exit_bar','reason_code']])
    all_features={s:features(study.d,study.engine,s) for s in ['L','S']}
    checks=[]
    for cut in [6000,11000,16000]:
        for side in ['L','S']:
            small=features(study.d.iloc[:cut],study.engine,side)
            pd.testing.assert_frame_equal(small,all_features[side].iloc[:cut])
            checks.append({'cut':cut,'side':side,'feature_prefix':'PASS'})
    f=trades.copy()
    for side in ['L','S']:
        mask=f.side.eq(side); bars=f.loc[mask,'entry_bar'].astype(int)
        assert (study.d.datetime.iloc[bars].to_numpy()+np.timedelta64(1,'h')==f.loc[mask,'entry_dt'].to_numpy()).all()
        for name in NAMES:f.loc[mask,name]=all_features[side].iloc[bars][name].to_numpy()
    f['period']=np.where(f.entry_dt<pd.Timestamp('2026-01-01'),'early','late')
    f['group']=np.where(f.reason_code.eq('MH'),'MH',np.where(f.net>0,'WIN','OTHER_LOSS'))
    assert not ((f.reason_code=='MH')&(f.net>0)).any(), 'MH可獲利時需更改互斥分組口徑'
    assert np.isfinite(f[list(NAMES)].to_numpy()).all()
    # 時段分組按訊號棒標籤，另存成交時間，兩者不混用。
    signal_time=f.entry_dt-pd.Timedelta(hours=1)
    f['signal_hour']=signal_time.dt.hour; f['signal_dow']=signal_time.dt.dayofweek
    f.to_csv(OUT/'entry_features.csv',index=False)
    groups=[]; medians=[]; buckets=[]; ranks=[]; thresholds={}
    for side in ['L','S']:
        fs=f[f.side.eq(side)]; train=fs[fs.exit_dt<pd.Timestamp('2026-01-01')-pd.Timedelta(hours=48)]
        for period in ['all','early','late']:
            q=fs if period=='all' else fs[fs.period.eq(period)]
            for group in ['ALL','MH','WIN','OTHER_LOSS']:
                g=q if group=='ALL' else q[q.group.eq(group)]
                groups.append({'side':side,'period':period,'group':group,**stats(g)})
                for name in NAMES:
                    medians.append({'side':side,'period':period,'group':group,'feature':name,
                                    'n_valid':int(g[name].notna().sum()),'median':g[name].median(),
                                    'q25':g[name].quantile(.25),'q75':g[name].quantile(.75)})
        for name in NAMES:
            lo,hi=train[name].quantile([1/3,2/3]).to_numpy(); thresholds[side+'_'+name]=[float(lo),float(hi)]
            labels=np.where(fs[name]<=lo,'low',np.where(fs[name]<=hi,'mid','high'))
            labels=np.where(fs[name].isna(),'missing',labels)
            for label in ['low','mid','high','missing']:
                sub=fs[labels==label]
                for period in ['early','late','all']:
                    q=sub if period=='all' else sub[sub.period.eq(period)]
                    buckets.append({'side':side,'feature':name,'bucket':label,'period':period,**stats(q)})
            valid=fs[fs[name].notna()]; x=valid[name].to_numpy(); y=valid.reason_code.eq('MH').to_numpy()
            observed=auc(x,y); rng=np.random.default_rng(20260908)
            quarter=valid.entry_dt.dt.to_period('Q').astype(str).to_numpy()
            blocks=[np.flatnonzero(quarter==k) for k in np.unique(quarter)]
            xr=pd.Series(x).rank().to_numpy(); n=int(y.sum()); exceed=0
            for _ in range(2000):
                yp=y.copy()
                for ix in blocks:yp[ix]=rng.permutation(y[ix])
                ap=(xr[yp].sum()-n*(n+1)/2)/(n*(len(y)-n))
                exceed+=abs(ap-.5)>=abs(observed-.5)
            row={'side':side,'feature':name,'auc_all':observed,'p':(1+exceed)/2001}
            for period in ['early','late']:
                q=valid[valid.period.eq(period)]; row['auc_'+period]=auc(q[name].to_numpy(),q.reason_code.eq('MH').to_numpy())
            ranks.append(row)
        for name in ['regime_code','signal_hour','signal_dow']:
            for label,sub in fs.groupby(name):
                for period in ['early','late','all']:
                    q=sub if period=='all' else sub[sub.period.eq(period)]
                    buckets.append({'side':side,'feature':name,'bucket':str(label),'period':period,**stats(q)})
    r=pd.DataFrame(ranks); order=np.argsort(r.p.to_numpy()); adj=np.maximum.accumulate(r.p.to_numpy()[order]*(len(r)-np.arange(len(r))))
    r.loc[order,'holm_p']=np.minimum(adj,1)
    for name,rows in [('groups',groups),('medians',medians),('buckets',buckets),('ranks',r)]:
        pd.DataFrame(rows).to_csv(OUT/f'{name}.csv',index=False)
    b=pd.DataFrame(buckets); pivot=b.pivot(index=['side','feature','bucket'],columns='period',values=['n','net','mh_rate'])
    stable=pivot[(pivot['n']['early']>=10)&(pivot['n']['late']>=10)&(pivot['net']['early']<0)&(pivot['net']['late']<0)]
    stable.to_csv(OUT/'negative_both_periods.csv')
    result={'baseline':baseline,'checks':checks,'missing_features':f[list(NAMES)].isna().sum().to_dict(),
            'thresholds':thresholds,'stable_negative_buckets':len(stable),
            'significant_rank_tests':int((r.holm_p<.05).sum()),
            'hashes':{str(p.relative_to(ROOT)):cost.sha(p) for p in [Path(__file__),ROOT/'data/maxhold_review_20260908/candles.csv',cost.HISTORY/'funding_full.csv',cost.HISTORY/'mark_1h_full.csv']}}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    write_report(f,b,r,medians,result)
    print(json.dumps({'stable_negative_buckets':len(stable),'significant_rank_tests':int((r.holm_p<.05).sum()),'groups':groups[:4]},ensure_ascii=True))
    print(stable.to_string())


if __name__=='__main__':main()
