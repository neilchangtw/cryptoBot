"""V: preserve old SampEn scale settings, repair counts, gate estimability first."""
import argparse
import ast
import json
from pathlib import Path
import numpy as np
import pandas as pd
import continuous_strategy_20260912 as c

NUMBER=7;ID='V_entropy_repair';NAME='樣本熵舊實驗修復'
PREREG='doc/continuous_round07_entropy_repair_20260912.md';TEST='tests/test_continuous_case07_20260912.py'
NEIGHBORS=[15,25]

def legacy_function():
    path=c.ROOT/'backtest/research/explore_v7_r1_indicator_sweep.py'
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='sample_entropy')
    scope={'np':np};exec(compile(ast.Module(body=[f],type_ignores=[]),str(path),'exec'),scope)
    return scope['sample_entropy']

def entropy(x,m=2,r_factor=.2):
    x=np.asarray(x,dtype=float)
    if len(x)<m+2 or not np.isfinite(x).all():return np.nan,0,0
    w=np.lib.stride_tricks.sliding_window_view(x,m+1)
    dist=np.abs(w[:,None,:]-w[None,:,:]);upper=np.triu(np.ones(dist.shape[:2],dtype=bool),1)
    r=r_factor*np.std(x)
    b=int((upper&(dist[:,:,:m].max(axis=2)<=r)).sum())
    a=int((upper&(dist.max(axis=2)<=r)).sum())
    assert 0<=a<=b
    return (np.nan if b==0 else (np.inf if a==0 else float(-np.log(a/b)))),a,b

def entropy_series(d):
    returns=np.log(d.close/d.close.shift(1)).to_numpy()
    raw=np.full(len(d),np.nan);aa=np.zeros(len(d),int);bb=aa.copy()
    for i in range(20,len(d)):raw[i],aa[i],bb[i]=entropy(returns[i-20:i])
    return raw,aa,bb

def features(d,parameter=None):
    limit=20 if parameter is None else parameter
    raw,aa,bb=entropy_series(d);lagged=np.r_[np.nan,raw[:-1]];rank=np.full(len(d),np.nan)
    for i in range(99,len(d)):
        x=lagged[i-99:i+1]
        if not np.isnan(x).any():rank[i]=100*float((x<x[-1]).sum())/99
    valid=np.isfinite(rank)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime-pd.Timedelta(hours=1),
        'raw':raw,'A':aa,'B':bb,'rank':rank,'valid':valid,'allow_L':valid&(rank<limit),'allow_S':valid&(rank<limit)})

def diagnose():
    case=__import__(__name__);reg,out=c.register(case);assert not (out/'diagnostic.json').exists()
    study=c.a.Study();d=study.d;f=features(d)
    gates=pd.read_csv(c.parent.old.OUT/'base_0bp_gates.csv')
    invalid=int((~f.valid.iloc[gates.bar]).sum());prefix=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(features(d.iloc[:cut]),f.iloc[:cut]);prefix.append(cut)
    f.to_csv(out/'features.csv',index=False)
    quality={'raw_defined':int(f.raw.notna().sum()),'raw_finite':int(np.isfinite(f.raw).sum()),
        'raw_infinite':int(np.isinf(f.raw).sum()),'raw_undefined':int(f.raw.isna().sum()),
        'valid_complete_rank_bars':int(f.valid.sum()),'invalid_original_gates':invalid,
        'base_eligible_gates':len(gates),'prefixes':prefix}
    c.dump(out/'quality.json',quality)
    if invalid:
        result={'utc':c.now(),'id':ID,'status':'DATA_LIMITED','counts':None,'candidate_pnl_trials':0,'quality':quality,
            'reason':'SampEn estimator undefined in required 20-return windows; complete historical rank unavailable at original gates. No parameter/missingness rescue or payoff test.',
            'protected_verified':c.verify(reg['protected_sha256'])}
        c.dump(out/'diagnostic.json',result);print(json.dumps({'id':ID,'status':result['status'],**quality}));return
    c.diagnose(case)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    if args.phase=='diagnose':diagnose()
    else:c.evaluate(__import__(__name__))
