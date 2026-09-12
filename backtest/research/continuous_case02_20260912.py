"""Q: cumulative observed volume changes MH progress only; offline, additive."""
import argparse
import inspect
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
import continuous_strategy_20260912 as c

NUMBER=2;ID='Q_volume_clock';NAME='成交量持倉時鐘'
PREREG='doc/continuous_round02_volume_clock_20260912.md'
TEST='tests/test_continuous_case02_20260912.py';NEIGHBORS=[12,48]

def features(d,parameter=None):
    window=24 if parameter is None else parameter
    ref=d.volume.shift(1).rolling(window,min_periods=window).mean()
    valid=np.isfinite(ref)&ref.gt(0)&np.isfinite(d.volume)&d.volume.ge(0)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime,
        'ref_volume':ref,'valid':valid,'allow_L':valid,'allow_S':valid})

def clock_function(d,window=24):
    ref=features(d,window).ref_volume.to_numpy();v=d.volume.to_numpy(dtype=float)
    assert np.isfinite(v).all() and (v>=0).all()
    cumulative=np.r_[0.,np.cumsum(v)]
    def clock(i,side,entry,bh,mh):
        assert 0<=entry<i<len(d) and np.isfinite(ref[entry]) and ref[entry]>0
        progress=(cumulative[i+1]-cumulative[entry+1])/ref[entry]
        return bool(bh>=math.ceil(mh/2) and (progress>=mh or bh>=2*mh))
    return clock

def exit_program(engine,side,changed):
    src=inspect.getsource(engine.simulate_v14_detailed)
    section=src.split('# --- '+side+' EXIT ---')[1].split('# ---')[0]
    section=section[section.index('            ex_price = 0.0'):section.index('            if ex_price > 0:')]
    if changed:
        before='if bh >= mh:' if side=='L' else 'if bh >= s_mh_eff:'
        after="if due(i,'L',lp_bar,bh,mh):" if side=='L' else "if due(i,'S',sp_bar,bh,s_mh_eff):"
        assert section.count(before)==1;section=section.replace(before,after)
    return compile(inspect.cleandoc(section),'<MH-clock-direct-action>','exec')

def direct(d,states,engine,window=24):
    due=clock_function(d,window);programs={(side,changed):exit_program(engine,side,changed) for side in ['L','S'] for changed in [False,True]}
    seen=set();events=[]
    for state in states.to_dict('records'):
        i=int(state['bar'])+1
        if i>=len(d):continue
        hi,li,ci=d.loc[i,['high','low','close']].astype(float)
        for side,p in [('L','lp'),('S','sp')]:
            root=(side,int(state[p+'_bar']))
            if not state[p+'_active'] or root in seen:continue
            ep=state[p+'_entry']
            scope={**engine.__dict__,**state,'i':i,'ep':ep,'bh':state[p+'_held']+1,'hi':hi,'li':li,'ci':ci,'slip':0.,'realistic':True,'due':due}
            # Running MFE only serves unchanged original high-priority exit rules.
            scope[p+'_mfe']=max(state[p+'_mfe'],(hi-ep)/ep if side=='L' else (ep-li)/ep)
            outputs=[]
            for changed in [False,True]:
                current=dict(scope);exec(programs[side,changed],current)
                action=('CLOSE',float(current['ex_price'])) if current['ex_price']>0 else ('EXTEND' if current[p+'_ext'] and not state[p+'_ext'] else 'HOLD',0.)
                outputs.append(action)
            if outputs[0]!=outputs[1]:
                seen.add(root);events.append({'bar':i,'side':side,'root_entry_bar':root[1],
                    'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1),'base_action':outputs[0][0],'candidate_action':outputs[1][0]})
    return pd.DataFrame(events,columns=['bar','side','root_entry_bar','decision_ts','base_action','candidate_action'])

def patched(engine,d,window=24):
    source=inspect.getsource(engine.simulate_v14_detailed)
    for old,new in [('if bh >= mh:',"if _clock_due(i,'L',lp_bar,bh,mh):"),
        ('if bh >= s_mh_eff:',"if _clock_due(i,'S',sp_bar,bh,s_mh_eff):")]:
        assert source.count(old)==1;source=source.replace(old,new)
    factory=inspect.getsource(c.a.previous.simulator)
    factory=factory.replace('src = inspect.getsource(engine.simulate_v14_detailed)','src = _source')
    factory=factory.replace('ns = dict(engine.__dict__, _snapshot=snapshot)','ns = dict(engine.__dict__, _snapshot=snapshot, _clock_due=_due)')
    scope={**c.a.previous.__dict__,'_source':source,'_due':clock_function(d,window)}
    exec(compile(factory,'<volume-clock-factory>','exec'),scope)
    return scope['simulator'](engine)

class VolumeReplay(c.Replay):
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        self.fn=c.a.previous.simulator(self.engine) if name=='base' else patched(self.engine,d,24 if self.parameter is None else self.parameter)
        return super().run(name,slip,cut,save,metrics)

def diagnose():
    case=__import__(__name__);reg,out=c.register(case);assert not (out/'diagnostic.json').exists()
    s=c.a.Study();d=s.d;f=features(d)
    assert f.valid.iloc[310:].all()
    states=pd.read_csv(c.parent.old.OUT/'base_0bp_states.csv',keep_default_na=False)
    events=direct(d,states,s.engine);counts=c.a.previous.previous.event_counts(events,'decision_ts')
    prefix=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(features(d.iloc[:cut]),f.iloc[:cut])
        pe=direct(d.iloc[:cut],states[states.bar<cut],s.engine)
        pd.testing.assert_frame_equal(pe,events[events.bar<cut].reset_index(drop=True));prefix.append(cut)
    f.to_csv(out/'features.csv',index=False);events.to_csv(out/'events_pre_pnl.csv',index=False)
    result={'utc':c.now(),'id':ID,'status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
        'counts':counts,'candidate_pnl_trials':0,'quality':'PASS under inherited closed futures K model',
        'feature_action_prefixes':prefix,'event_definition':'first original-root close/extension action difference only',
        'protected_verified':c.verify(reg['protected_sha256'])}
    c.dump(out/'diagnostic.json',result);print(json.dumps({'id':ID,'status':result['status'],'counts':counts}))

def evaluate():
    # Bind a dedicated subclass for this invocation only. Original runner file and
    # other cases remain unchanged; its preregistered evaluation sequence is reused.
    case=__import__(__name__)
    source=inspect.getsource(c.evaluate);assert source.count('s=Replay(case,out)')==1
    source=source.replace('s=Replay(case,out)','s=VolumeReplay(case,out)')
    scope={**c.__dict__,'VolumeReplay':VolumeReplay};exec(compile(source,'<volume-clock-evaluation>','exec'),scope)
    scope['evaluate'](case)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    {'diagnose':diagnose,'evaluate':evaluate}[args.phase]()
