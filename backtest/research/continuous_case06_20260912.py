"""U: continuing Path R eligibility, isolated original-state exit callback."""
import argparse
import inspect
import numpy as np
import pandas as pd
import continuous_strategy_20260912 as c
import continuous_case05_20260912 as t

NUMBER=6;ID='U_regime_invalidation';NAME='持倉Path R資格失效'
PREREG='doc/continuous_round06_regime_exit_20260912.md';TEST='tests/test_continuous_case06_20260912.py'
NEIGHBORS=[2,3]

def features(d,parameter=None):
    sma=d.close.rolling(200,min_periods=200).mean()
    slope=((sma-sma.shift(100))/sma.shift(100)).shift(1)
    valid=np.isfinite(slope)
    return pd.DataFrame({'decision_ts':d.datetime+pd.Timedelta(hours=1),'source_last_close':d.datetime,
        'valid':valid,'allow_L':valid,'allow_S':valid,'slope':slope,
        'block_L':valid&slope.gt(.045),'block_S':valid&slope.abs().lt(.010)})

def regime_exit(d,required=1):
    assert required in [1,2,3]
    f=features(d);assert f.valid.iloc[310:].all()
    def fail(i,side,entry):
        assert 0<=entry<i<len(d)
        if i-entry<required:return False
        a=f.iloc[i-required+1:i+1]
        assert a.valid.all()
        return bool(a['block_'+side].all())
    return fail

def direct(d,states,engine,required=1):
    source=inspect.getsource(t.direct).replace('required=2','required=1').replace('fail=anchored_exit(d,required)','fail=regime_exit(d,required)')
    scope={**t.__dict__,'regime_exit':regime_exit};exec(compile(source,'<regime-original-actions>','exec'),scope)
    return scope['direct'](d,states,engine,required)

def patched(engine,d,required=1):
    source=inspect.getsource(t.patched).replace('required=2','required=1').replace('anchored_exit(d,required)','regime_exit(d,required)').replace("ex_reason = 'ACX'","ex_reason = 'RGX'")
    scope={**t.__dict__,'regime_exit':regime_exit};exec(compile(source,'<regime-exit-factory>','exec'),scope)
    return scope['patched'](engine,d,required)

class RegimeReplay(c.Replay):
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        self.fn=c.a.previous.simulator(self.engine) if name=='base' else patched(self.engine,d,1 if self.parameter is None else self.parameter)
        return super().run(name,slip,cut,save,metrics)

def diagnose():
    c.verify(c.read(c.folder(t)/'registration.json')['sha256'])
    study=c.a.Study()
    np.testing.assert_allclose(features(study.d).slope.to_numpy(),study.ind['slope'],rtol=0,atol=1e-14,equal_nan=True)
    source=inspect.getsource(t.diagnose).replace('if not f.valid.all():','if not f.valid.iloc[310:].all():')
    source=source.replace('Invalid closed price/volume; no filling','Invalid required lagged slope; no filling')
    source=source.replace('PASS inherited completed futures K/close proxy; weighted close is not actual VWAP','PASS inherited completed futures K; slope matches original engine at every bar')
    scope={**t.__dict__,**globals()};exec(compile(source,'<regime-diagnostic>','exec'),scope)
    scope['diagnose']()

def evaluate():
    source=inspect.getsource(c.evaluate).replace('s=Replay(case,out)','s=RegimeReplay(case,out)')
    scope={**c.__dict__,'RegimeReplay':RegimeReplay};exec(compile(source,'<regime-evaluation>','exec'),scope)
    scope['evaluate'](__import__(__name__))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    {'diagnose':diagnose,'evaluate':evaluate}[args.phase]()
