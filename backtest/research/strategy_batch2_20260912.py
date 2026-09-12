"""Second bounded batch: frozen hashes, sequential pre-payoff diagnoses."""
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import bounded_strategy_20260912 as old

ROOT=old.ROOT
DOC=ROOT/'doc/research_results/20260912_strategy_batch2'
SPOT=ROOT/'data/spot_futures_sync_20260909'
sha=old.sha;dump=old.dump;read=old.read;now=old.now;rel=old.rel;verify=old.verify

def initialize():
    DOC.mkdir(exist_ok=True)
    p=DOC/'batch_registration.json'
    if p.exists():
        reg=read(p);verify(reg['protected_sha256']);return reg
    prev=read(old.DOC/'registration.json');manifest=read(old.DOC/'artifact_manifest.json')
    counts={'previous_protected':verify(prev['protected_sha256']),'previous_artifacts':verify(manifest['sha256'])}
    protected={**prev['protected_sha256'],**manifest['sha256']}
    current=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    for path in current:
        if 'strategy_batch2' in path or 'batch2_round' in path:continue
        if (ROOT/path).is_file():protected[path]=sha(ROOT/path)
    reg={'utc':now(),'protected_sha256':protected,'verified_previous':counts,
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'initial_git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
        'max_new_rounds':3,'prior_batch_rounds':3,'candidate_pnl_trials_before':0,'network_requests':0}
    dump(p,reg)
    base=read(old.DOC/'baseline.json')
    dump(DOC/'baseline_reuse.json',{'utc':now(),'method':'Hash-matched preceding validated full-state baseline; no old candidate reruns',
        'baseline_sha256':sha(old.DOC/'baseline.json'),'verification_sha256':sha(old.DOC/'verification.json'),
        'verified_previous':counts,'bars':base['bars'],'first_open':base['first_open'],'last_close':base['last_close'],
        'runs':base['runs'],'noop_reused':base['noop'],'physical_prefixes_reused':base['prefix'],
        'current_production_indicators_reused':base['current_production_indicators']})
    return reg

def register_round(number,paths,main):
    reg=initialize();p=DOC/f'round{number}_registration.json'
    if number>1:
        assert read(DOC/f'round{number-1}_diagnostic.json')['status'] in ['DATA_LIMITED','INSUFFICIENT_SAMPLE','REJECTED']
    inputs={rel(x):sha(x) for x in paths}
    if p.exists():assert read(p)['sha256']==inputs
    else:dump(p,{'utc':now(),'round':number,'main':main,'sha256':inputs,'candidate_pnl_trials_before':0})
    return reg

def lead_features(d,spot,window=168):
    assert pd.DatetimeIndex(d.datetime).equals(pd.DatetimeIndex(spot.datetime))
    s=np.log(spot.close).diff();f=np.log(d.close).diff()
    forward=s.shift(1).rolling(window,min_periods=window).corr(f).shift(1)
    backward=f.shift(1).rolling(window,min_periods=window).corr(s).shift(1)
    score=forward-backward
    source_close=d.datetime # prior open i-1 +1h = current open i
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_close':source_close,
        'assumed_available':source_close+pd.Timedelta(minutes=5),'forward':forward,'backward':backward,
        'score':score,'valid':np.isfinite(score),'allowed':score.ge(0)&np.isfinite(score)})

def diagnose_j():
    paths=[Path(__file__),ROOT/'doc/strategy_batch2_round1_20260912.md',ROOT/'tests/test_strategy_batch2_20260912.py',
           SPOT/'manifest.json',SPOT/'spot_1h.csv',ROOT/'doc/research_results/20260911_strategy_round2/sources.json']
    reg=register_round(1,paths,'J_spot_futures_price_lead')
    source=old.a.previous.cost.Study();d=source.d
    m=read(SPOT/'manifest.json');assert sha(SPOT/'spot_1h.csv')==m['csv_sha256']
    assert sha(ROOT/'data/maxhold_review_20260908/candles.csv')==m['source_sha256']
    for r in m['requests']:assert sha(SPOT/r['file'])==r['sha256']
    spot=pd.read_csv(SPOT/'spot_1h.csv',parse_dates=['datetime'])
    assert len(spot)==17519 and spot.datetime.is_unique and spot.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert (spot.close>0).all() and np.isfinite(spot.close).all()
    feat=lead_features(d,spot)
    assert (feat.assumed_available<feat.decision_ts).all()
    gates=pd.read_csv(old.OUT/'base_0bp_gates.csv',parse_dates=['decision_ts'])
    valid=feat.valid.iloc[gates.bar].to_numpy();allowed=feat.allowed.iloc[gates.bar].to_numpy()
    events=gates.loc[valid&~allowed,['bar','side','decision_ts']].copy()
    counts=old.a.previous.previous.event_counts(events,'decision_ts')
    prefix=[]
    for cut in [6000,11000,16000]:
        f=lead_features(d.iloc[:cut],spot.iloc[:cut]);pd.testing.assert_frame_equal(f,feat.iloc[:cut],atol=1e-10,rtol=0)
        prefix.append({'cut':cut,'features_and_masks':'PASS'})
    feat.to_csv(DOC/'round1_features.csv',index=False);events.to_csv(DOC/'round1_provisional_events.csv',index=False)
    temporal=read(ROOT/'doc/research_results/20260911_strategy_round2/sources.json')
    timing_valid=temporal.get('historical_received_ts') not in [None,'unavailable'] and temporal.get('revision_status')=='verified point-in-time'
    quality={'rows':len(spot),'hash_verified_raw_requests':len(m['requests']),
        'invalid_after_warmup':int((~feat.valid.iloc[310:]).sum()),'invalid_original':int((~valid).sum()),
        'historical_received_ts':temporal.get('historical_received_ts'),'revision_status':temporal.get('revision_status'),
        'downloaded_utc':m['downloaded_utc'],'point_in_time_gate':timing_valid,
        'reason':'Download timestamp is a later retrieval, not event-time receipt; 55-minute assumed lag does not establish revision history.'}
    status='DATA_LIMITED' if not timing_valid or (~valid).any() else ('READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE')
    result={'utc':now(),'round':1,'name':'J_spot_futures_price_lead','status':status,'counts_provisional':counts,
        'quality':quality,'prefix':prefix,'candidate_pnl_trials':0,'protected_verified':verify(reg['protected_sha256'])}
    out=DOC/'round1_diagnostic.json';assert not out.exists();dump(out,result)
    print(json.dumps({'status':status,'provisional_counts':counts,'candidate_pnl_trials':0,'timing_gate':timing_valid}))

def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['initialize','diagnose-j'],required=True);args=p.parse_args()
    if args.phase=='initialize':print(json.dumps(initialize()['verified_previous']))
    else:diagnose_j()

if __name__=='__main__':main()
