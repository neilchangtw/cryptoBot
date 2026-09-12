"""R: held-position close-breakout renewal; independent from volume clock."""
import argparse
import inspect
import json
import math
import numpy as np
import pandas as pd
import continuous_strategy_20260912 as c
import continuous_case02_20260912 as q

NUMBER=3;ID='R_breakout_renewal';NAME='新突破續期'
PREREG='doc/continuous_round03_renewal_20260912.md';TEST='tests/test_continuous_case03_20260912.py'
NEIGHBORS=[1.5,2.5]

def features(d,parameter=None):
    hi=d.close.shift(1).rolling(15,min_periods=15).max()
    lo=d.close.shift(1).rolling(15,min_periods=15).min()
    valid=np.isfinite(hi)&np.isfinite(lo)&np.isfinite(d.close)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime+pd.Timedelta(hours=1),
        'valid':valid,'allow_L':valid,'allow_S':valid,'renew_L':d.close.gt(hi)&valid,'renew_S':d.close.lt(lo)&valid})

def renewal_clock(d,cap=2.):
    f=features(d);idx=np.arange(len(d))
    last={side:np.maximum.accumulate(np.where(f['renew_'+side],idx,-1)) for side in ['L','S']}
    def due(i,side,entry,bh,mh):
        assert 0<=entry<i<len(d)
        confirmation=max(entry,int(last[side][i]))
        return bool(i-confirmation>=mh or bh>=math.ceil(cap*mh))
    return due

def direct(d,states,engine,cap=2.):
    source=inspect.getsource(q.direct)
    source=source.replace('def direct(d,states,engine,window=24):','def direct(d,states,engine,cap=2.):')
    assert source.count('due=clock_function(d,window)')==1
    source=source.replace('due=clock_function(d,window)','due=renewal_clock(d,cap)')
    scope={**q.__dict__,'renewal_clock':renewal_clock};exec(compile(source,'<renewal-original-state-actions>','exec'),scope)
    return scope['direct'](d,states,engine,cap)

def patched(engine,d,cap=2.):
    source=inspect.getsource(engine.simulate_v14_detailed)
    for old,new in [('if bh >= mh:',"if _clock_due(i,'L',lp_bar,bh,mh):"),
        ('if bh >= s_mh_eff:',"if _clock_due(i,'S',sp_bar,bh,s_mh_eff):")]:
        assert source.count(old)==1;source=source.replace(old,new)
    factory=inspect.getsource(c.a.previous.simulator)
    factory=factory.replace('src = inspect.getsource(engine.simulate_v14_detailed)','src = _source')
    factory=factory.replace('ns = dict(engine.__dict__, _snapshot=snapshot)','ns = dict(engine.__dict__, _snapshot=snapshot, _clock_due=_due)')
    scope={**c.a.previous.__dict__,'_source':source,'_due':renewal_clock(d,cap)}
    exec(compile(factory,'<renewal-factory>','exec'),scope)
    return scope['simulator'](engine)

class RenewalReplay(c.Replay):
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        self.fn=c.a.previous.simulator(self.engine) if name=='base' else patched(self.engine,d,2. if self.parameter is None else self.parameter)
        return super().run(name,slip,cut,save,metrics)

def diagnose():
    case=__import__(__name__);reg,out=c.register(case);assert not (out/'diagnostic.json').exists()
    c.verify(c.read(c.folder(q)/'registration.json')['sha256'])
    s=c.a.Study();d=s.d;f=features(d);assert f.valid.iloc[310:].all()
    states=pd.read_csv(c.parent.old.OUT/'base_0bp_states.csv',keep_default_na=False)
    events=direct(d,states,s.engine);counts=c.a.previous.previous.event_counts(events,'decision_ts')
    prefix=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(features(d.iloc[:cut]),f.iloc[:cut])
        pd.testing.assert_frame_equal(direct(d.iloc[:cut],states[states.bar<cut],s.engine),events[events.bar<cut].reset_index(drop=True));prefix.append(cut)
    f.to_csv(out/'features.csv',index=False);events.to_csv(out/'events_pre_pnl.csv',index=False)
    result={'utc':c.now(),'id':ID,'status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
        'counts':counts,'candidate_pnl_trials':0,'quality':'PASS under inherited completed K-bar/close-proxy assumption',
        'feature_action_prefixes':prefix,'protected_verified':c.verify(reg['protected_sha256'])}
    c.dump(out/'diagnostic.json',result);print(json.dumps({'id':ID,'status':result['status'],'counts':counts}))

def evaluate():
    case=__import__(__name__);source=inspect.getsource(c.evaluate);assert source.count('s=Replay(case,out)')==1
    source=source.replace('s=Replay(case,out)','s=RenewalReplay(case,out)')
    scope={**c.__dict__,'RenewalReplay':RenewalReplay};exec(compile(source,'<renewal-evaluation>','exec'),scope)
    scope['evaluate'](case)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    {'diagnose':diagnose,'evaluate':evaluate}[args.phase]()
