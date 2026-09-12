"""Reusable sequential local research runner; no production imports or network."""
import argparse
import importlib
import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch3_20260912 as parent

ROOT=parent.ROOT;DOC=ROOT/'doc/research_results/20260912_continuous_strategy'
read=parent.read;dump=parent.dump;sha=parent.sha;now=parent.now;rel=parent.rel;verify=parent.verify
a=parent.a

def folder(case):return DOC/f'round{case.NUMBER:02d}_{case.ID}'

def initialize():
    DOC.mkdir(exist_ok=True)
    path=DOC/'registration.json'
    if path.exists():
        reg=read(path);verify(reg['protected_sha256']);verify(reg['framework_sha256']);return reg
    prior=read(parent.DOC/'batch_registration.json');manifest=read(parent.DOC/'artifact_manifest.json')
    previous={'protected':verify(prior['protected_sha256']),'artifacts':verify(manifest['sha256'])}
    protected={**prior['protected_sha256'],**manifest['sha256']}
    current=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    for f in current:
        if 'continuous_' in f:continue
        if (ROOT/f).is_file():protected[f]=sha(ROOT/f)
    reg={'utc':now(),'protected_sha256':protected,'previous_verified':previous,'user_removed_three_round_cap':True,
        'framework_sha256':{rel(Path(__file__)):sha(Path(__file__)),
            'doc/continuous_strategy_contract_20260912.md':sha(ROOT/'doc/continuous_strategy_contract_20260912.md')},
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'initial_git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
        'fixed_notional':4000,'market_data_download_bytes':0,'historical_unseen':False}
    dump(path,reg);return reg

def register(case):
    reg=initialize();out=folder(case);out.mkdir(exist_ok=True);(out/'ledgers').mkdir(exist_ok=True)
    if case.NUMBER>1:
        prevs=list(DOC.glob(f'round{case.NUMBER-1:02d}_*'))
        assert len(prevs)==1
        status=read(prevs[0]/('results.json' if (prevs[0]/'results.json').exists() else 'diagnostic.json'))['status']
        assert status in ['REJECTED','INSUFFICIENT_SAMPLE','DATA_LIMITED']
    inputs={rel(p):sha(p) for p in [Path(case.__file__),ROOT/case.PREREG,ROOT/case.TEST]}
    path=out/'registration.json'
    if path.exists():assert read(path)['sha256']==inputs
    else:dump(path,{'utc':now(),'round':case.NUMBER,'id':case.ID,'sha256':inputs,'candidate_pnl_trials_before':0})
    return reg,out

def diagnose(case):
    reg,out=register(case);assert not (out/'diagnostic.json').exists()
    s=a.Study();d=s.d;f=case.features(d)
    assert len(f)==len(d) and d.datetime.is_unique and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert (f.source_last_close<f.decision_ts).all()
    gates=pd.read_csv(parent.old.OUT/'base_0bp_gates.csv',parse_dates=['decision_ts'])
    valid=f.valid.iloc[gates.bar].to_numpy()
    allowed=np.array([f.loc[int(row.bar),'allow_'+row.side] for row in gates.itertuples()])
    events=gates.loc[valid&~allowed,['bar','side','decision_ts']].copy()
    counts=a.previous.previous.event_counts(events,'decision_ts')
    prefixes=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(case.features(d.iloc[:cut]),f.iloc[:cut]);prefixes.append(cut)
    f.to_csv(out/'features.csv',index=False);events.to_csv(out/'events_pre_pnl.csv',index=False)
    status='DATA_LIMITED' if not valid.all() else ('READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE')
    result={'utc':now(),'id':case.ID,'status':status,'counts':counts,'candidate_pnl_trials':0,
        'invalid_original_gates':int((~valid).sum()),'feature_prefixes':prefixes,
        'quality':'Prior closed-bar features; inherited base data/receipt assumptions, no new external series',
        'protected_verified':verify(reg['protected_sha256'])}
    dump(out/'diagnostic.json',result);print(json.dumps({'id':case.ID,'status':status,'counts':counts}))

class Replay(a.Study):
    def __init__(self,case,out):
        super().__init__();self.case=case;self.out=out;self.parameter=None
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        assert name in ['base',self.case.ID]
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        ftr=self.case.features(d,self.parameter);gates=[];states=[]
        def gate(i,side):
            assert bool(ftr.valid.iloc[i])
            allowed=name=='base' or bool(ftr.loc[i,'allow_'+side])
            gates.append({'bar':i,'side':side,'allowed':allowed,'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1)})
            return allowed
        def observe(i,state):states.append({'bar':i,**state})
        raw,state=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,gate=gate,observe=observe)
        t=a.previous.review.normalize(pd.DataFrame(raw)) if raw else pd.read_csv(parent.old.OLD/'base_0bp_trades.csv',nrows=0,parse_dates=['entry_dt','exit_dt'])
        fund=self.source.fund[self.source.fund.nominal<=d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        t,eq,ledger,terminal=a.previous.account_terminal(t,d,self.source.mark.iloc[:len(d)],fund,state)
        ev=pd.DataFrame(gates);st=pd.DataFrame(states)
        row={'name':name,'slip':slip,'end_state':state,'terminal_positions':terminal,
            'terminal_net':float(eq.equity.iloc[-1]-t.net.sum()),'fee_total_closed':float(t.fee_exact.sum()),
            'max_simultaneous':int((st.lp_active.astype(int)+st.sp_active.astype(int)).max())}
        assert row['max_simultaneous']<=2
        if cut is None and metrics:parent.add_metrics(row,t,eq,self.d)
        if save:
            for suffix,frame in [('trades',t),('equity',eq),('funding',ledger),('gates',ev),('states',st)]:
                frame.to_csv(self.out/'ledgers'/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=t,eq,row,ev,st
        return t,eq,row,ev,st
    def noop(self):
        checks=[]
        for slip in [0,2,5]:
            self.run('base',slip)
            for suffix in ['trades','equity','funding','gates','states']:
                x=pd.read_csv(self.out/'ledgers'/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                y=pd.read_csv(parent.old.OUT/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                pd.testing.assert_frame_equal(x,y,check_dtype=False,atol=1e-8,rtol=0)
            checks.append({'slip':slip,'all_ledger_fields':'PASS'})
        return checks

def attribution(s,slip):
    t,eq,row,*_=s.cache[s.case.ID,slip];bt,be,br,*_=s.cache['base',slip]
    counts,p=a.previous.cost.paired(t,bt);p.to_csv(s.out/'ledgers'/f'{s.case.ID}_{slip}bp_paired.csv',index=False)
    removed=p[p._merge=='right_only'];added=p[p._merge=='left_only'];common=p[p._merge=='both']
    parts={'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),
        'added_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum()),'terminal_delta':row['terminal_net']-br['terminal_net']}
    delta=float(eq.equity.iloc[-1]-be.equity.iloc[-1]);assert abs(sum(parts.values())-delta)<1e-7
    return {'counts':counts,'parts':parts,'net_delta':delta,'trading_delta':float(t.pnl.sum()-bt.pnl.sum()),
        'funding_delta':float(t.funding.sum()-bt.funding.sum()),'closed_fee_delta':float(t.fee_exact.sum()-bt.fee_exact.sum())}

def evaluate(case):
    reg,out=register(case);diagnostic=read(out/'diagnostic.json');assert diagnostic['status']=='READY'
    assert not (out/'results.json').exists()
    s=Replay(case,out);noop=s.noop();dump(out/'noop.json',{'utc':now(),'checks':noop})
    prefix=s.prefix('base');s.run(case.ID,0,metrics=False);prefix+=s.prefix(case.ID)
    dump(out/'prefix.json',{'utc':now(),'checks':prefix,'candidate_summary_evaluated':False})
    t,eq,row,*_=s.cache[case.ID,0];parent.add_metrics(row,t,eq,s.d)
    for slip in [2,5]:s.run(case.ID,slip)
    failures={str(slip):a.previous.failures(s.cache[case.ID,slip][2],s.cache['base',slip][2]) for slip in [0,2,5]}
    rows=[s.cache[name,slip][2] for name in ['base',case.ID] for slip in [0,2,5]]
    result={'utc':now(),'id':case.ID,'status':'REJECTED' if any(failures.values()) else 'BASIC_PASS_REQUIRES_VALIDATION',
        'counts':diagnostic['counts'],'runs':rows,'failed_gates':failures,'noop':noop,'prefix':prefix,
        'attribution':{str(slip):attribution(s,slip) for slip in [0,2,5]},'candidate_pnl_trials':1,'cost_scenarios':3,
        'neighborhood_runs':0,'protected_verified':verify(reg['protected_sha256'])}
    dump(out/'results.json',result)
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],**r['full']} for r in rows]).to_csv(out/'summary.csv',index=False)
    print(json.dumps({'id':case.ID,'status':result['status'],'failed_gates':failures,'candidate0':s.cache[case.ID,0][2]['full']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    case=importlib.import_module(args.case)
    {'diagnose':diagnose,'evaluate':evaluate}[args.phase](case)
