"""T: entry-anchored volume-weighted close exit; offline independent case."""
import argparse
import inspect
import json
import numpy as np
import pandas as pd
import continuous_strategy_20260912 as c
import continuous_case02_20260912 as q

NUMBER=5;ID='T_anchored_close_exit';NAME='進場後量加權收盤線'
PREREG='doc/continuous_round05_anchored_close_20260912.md';TEST='tests/test_continuous_case05_20260912.py'
NEIGHBORS=[1,3]

def features(d,parameter=None):
    valid=np.isfinite(d.close)&d.close.gt(0)&np.isfinite(d.volume)&d.volume.gt(0)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime+pd.Timedelta(hours=1),
        'valid':valid,'allow_L':valid,'allow_S':valid})

def anchored_exit(d,required=2):
    assert required in [1,2,3] and features(d).valid.all()
    close=d.close.to_numpy(dtype=float);volume=d.volume.to_numpy(dtype=float)
    def fail(i,side,entry):
        assert 0<=entry<i<len(d)
        if i-entry<max(3,required):return False
        # Local summation avoids accumulating unrelated pre-entry rounding error.
        for k in range(i-required+1,i+1):
            v=volume[entry+1:k+1]
            anchor=float(np.dot(close[entry+1:k+1],v)/v.sum())
            if not (close[k]<anchor if side=='L' else close[k]>anchor):return False
        return True
    return fail

def direct(d,states,engine,required=2):
    fail=anchored_exit(d,required);programs={side:q.exit_program(engine,side,False) for side in ['L','S']}
    seen=set();events=[]
    for state in states.to_dict('records'):
        i=int(state['bar'])+1
        if i>=len(d):continue
        hi,li,ci=d.loc[i,['high','low','close']].astype(float)
        for side,p in [('L','lp'),('S','sp')]:
            entry=int(state[p+'_bar']);root=(side,entry)
            if not state[p+'_active'] or root in seen:continue
            ep=state[p+'_entry']
            scope={**engine.__dict__,**state,'i':i,'ep':ep,'bh':state[p+'_held']+1,'hi':hi,'li':li,'ci':ci,'slip':0.,'realistic':True}
            scope[p+'_mfe']=max(state[p+'_mfe'],(hi-ep)/ep if side=='L' else (ep-li)/ep)
            exec(programs[side],scope)
            if scope['ex_price']==0 and fail(i,side,entry):
                seen.add(root);events.append({'bar':i,'side':side,'root_entry_bar':entry,'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1)})
    return pd.DataFrame(events,columns=['bar','side','root_entry_bar','decision_ts'])

def patched(engine,d,required=2):
    source=inspect.getsource(engine.simulate_v14_detailed)
    for side,p,mkt,sign in [('L','lp','l_mkt','(ex_price - ep)'),('S','sp','s_mkt','(ep - ex_price)')]:
        old=f'            if ex_price > 0:\n                pnl_pct = {sign} / ep'
        new=f"            if ex_price == 0 and _anchored_fail(i,'{side}',{p}_bar):\n                ex_price = {mkt}\n                ex_reason = 'ACX'\n"+old
        assert source.count(old)==1;source=source.replace(old,new)
    factory=inspect.getsource(c.a.previous.simulator)
    factory=factory.replace('src = inspect.getsource(engine.simulate_v14_detailed)','src = _source')
    factory=factory.replace('ns = dict(engine.__dict__, _snapshot=snapshot)','ns = dict(engine.__dict__, _snapshot=snapshot, _anchored_fail=_fail)')
    scope={**c.a.previous.__dict__,'_source':source,'_fail':anchored_exit(d,required)}
    exec(compile(factory,'<anchored-close-factory>','exec'),scope)
    return scope['simulator'](engine)

class AnchoredReplay(c.Replay):
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        self.fn=c.a.previous.simulator(self.engine) if name=='base' else patched(self.engine,d,2 if self.parameter is None else self.parameter)
        return super().run(name,slip,cut,save,metrics)

def diagnose():
    case=__import__(__name__);reg,out=c.register(case);assert not (out/'diagnostic.json').exists()
    c.verify(c.read(c.folder(q)/'registration.json')['sha256'])
    s=c.a.Study();d=s.d;f=features(d)
    if not f.valid.all():
        c.dump(out/'diagnostic.json',{'id':ID,'status':'DATA_LIMITED','counts':None,'candidate_pnl_trials':0,'reason':'Invalid closed price/volume; no filling'})
        return
    states=pd.read_csv(c.parent.old.OUT/'base_0bp_states.csv',keep_default_na=False)
    events=direct(d,states,s.engine);counts=c.a.previous.previous.event_counts(events,'decision_ts')
    prefixes=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(features(d.iloc[:cut]),f.iloc[:cut])
        pd.testing.assert_frame_equal(direct(d.iloc[:cut],states[states.bar<cut],s.engine),events[events.bar<cut].reset_index(drop=True));prefixes.append(cut)
    events.to_csv(out/'events_pre_pnl.csv',index=False)
    result={'utc':c.now(),'id':ID,'status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
        'counts':counts,'candidate_pnl_trials':0,'quality':'PASS inherited completed futures K/close proxy; weighted close is not actual VWAP',
        'feature_action_prefixes':prefixes,'protected_verified':c.verify(reg['protected_sha256'])}
    c.dump(out/'diagnostic.json',result);print(json.dumps({'id':ID,'status':result['status'],'counts':counts}))

def evaluate():
    case=__import__(__name__);source=inspect.getsource(c.evaluate);assert source.count('s=Replay(case,out)')==1
    source=source.replace('s=Replay(case,out)','s=AnchoredReplay(case,out)')
    scope={**c.__dict__,'AnchoredReplay':AnchoredReplay};exec(compile(source,'<anchored-close-evaluation>','exec'),scope)
    scope['evaluate'](case)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    {'diagnose':diagnose,'evaluate':evaluate}[args.phase]()
