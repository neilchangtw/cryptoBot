"""Third bounded batch; deterministic NY clock, frozen research-only namespace."""
import argparse
import json
import subprocess
import importlib.metadata
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch2_20260912 as parent

old=parent.old;a=old.a
ROOT=old.ROOT;DOC=ROOT/'doc/research_results/20260912_strategy_batch3';OUT=DOC/'ledgers'
read=old.read;dump=old.dump;sha=old.sha;now=old.now;rel=old.rel;verify=old.verify

def initialize():
    DOC.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
    path=DOC/'batch_registration.json'
    if path.exists():
        reg=read(path);verify(reg['protected_sha256']);return reg
    prev=read(parent.DOC/'batch_registration.json');manifest=read(parent.DOC/'artifact_manifest.json')
    checks={'previous_protected':verify(prev['protected_sha256']),'previous_artifacts':verify(manifest['sha256'])}
    protected={**prev['protected_sha256'],**manifest['sha256']}
    files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    for path0 in files:
        if 'strategy_batch3' in path0:continue
        if (ROOT/path0).is_file():protected[path0]=sha(ROOT/path0)
    reg={'utc':now(),'protected_sha256':protected,'previous_checks':checks,'max_rounds':3,
        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'initial_git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
        'candidate_pnl_trials_before':0,'market_data_download_bytes':0,'web_background_calls':2}
    dump(path,reg)
    base=read(old.DOC/'baseline.json')
    dump(DOC/'baseline_reuse.json',{'utc':now(),'source_sha256':sha(old.DOC/'baseline.json'),
        'verification_sha256':sha(old.DOC/'verification.json'),'runs':base['runs'],
        'prior_noop':base['noop'],'prior_prefix':base['prefix'],'bars':base['bars']})
    return reg

def register_round(n,paths,name):
    reg=initialize();path=DOC/f'round{n}_registration.json'
    if n>1:
        previous=DOC/f'round{n-1}_results.json'
        if not previous.exists():previous=DOC/f'round{n-1}_diagnostic.json'
        assert read(previous)['status'] in ['REJECTED','INSUFFICIENT_SAMPLE','DATA_LIMITED']
    inputs={rel(p):sha(p) for p in paths}
    if path.exists():assert read(path)['sha256']==inputs
    else:dump(path,{'utc':now(),'round':n,'name':name,'sha256':inputs,'candidate_pnl_trials_before':0})
    return reg

def ny_clock(d,start=510):
    decision=pd.DatetimeIndex(d.datetime)+pd.Timedelta(hours=1)
    assert decision.notna().all() and decision.is_unique
    local=decision.tz_localize('Asia/Taipei').tz_convert('America/New_York')
    minutes=local.hour*60+local.minute
    blocked=(local.dayofweek<5)&(minutes>=start)&(minutes<960)
    return pd.DataFrame({'decision_ts':decision,'ny_time':local.astype(str),'blocked':blocked})

def diagnose_m():
    reg=register_round(1,[Path(__file__),ROOT/'doc/strategy_batch3_round1_20260912.md',
        ROOT/'tests/test_strategy_batch3_clock_20260912.py'],'M_ny_day_clock')
    s=a.Study();d=s.d
    assert len(d)==17519 and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    features=ny_clock(d);gates=pd.read_csv(old.OUT/'base_0bp_gates.csv',parse_dates=['decision_ts'])
    events=gates.loc[features.blocked.iloc[gates.bar].to_numpy(),['bar','side','decision_ts']].copy()
    counts=a.previous.previous.event_counts(events,'decision_ts')
    prefixes=[]
    for cut in [6000,11000,16000]:
        pd.testing.assert_frame_equal(ny_clock(d.iloc[:cut]),features.iloc[:cut]);prefixes.append(cut)
    features.to_csv(DOC/'round1_clock.csv',index=False);events.to_csv(DOC/'round1_events_pre_pnl.csv',index=False)
    status='READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE'
    result={'utc':now(),'round':1,'name':'M_ny_day_clock','status':status,'counts':counts,'candidate_pnl_trials':0,
        'quality':'PASS: pure ex-ante calendar clock; no new external received/revision requirement',
        'timezone_version':importlib.metadata.version('tzdata'),'feature_prefixes':prefixes,
        'inherited_limit':'Historical closed-kline/mark/funding assumptions and hourly proxy fills retained',
        'protected_verified':verify(reg['protected_sha256'])}
    path=DOC/'round1_diagnostic.json';assert not path.exists();dump(path,result)
    print(json.dumps({'status':status,'counts':counts,'candidate_pnl_trials':0}))

class Study(a.Study):
    def run(self,name='base',slip=0,cut=None,save=True,metrics=True):
        assert name in ['base','M']
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        clock=ny_clock(d);gates=[];states=[]
        def gate(i,side):
            allowed=name=='base' or not bool(clock.blocked.iloc[i])
            gates.append({'bar':i,'side':side,'allowed':allowed,'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1)})
            return allowed
        def observe(i,state):states.append({'bar':i,**state})
        raw,state=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,gate=gate,observe=observe)
        f=a.previous.review.normalize(pd.DataFrame(raw)) if raw else pd.read_csv(old.OLD/'base_0bp_trades.csv',nrows=0,parse_dates=['entry_dt','exit_dt'])
        fund=self.source.fund[self.source.fund.nominal<=d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        f,eq,ledger,terminal=a.previous.account_terminal(f,d,self.source.mark.iloc[:len(d)],fund,state)
        ev=pd.DataFrame(gates);st=pd.DataFrame(states)
        row={'name':name,'slip':slip,'end_state':state,'terminal_positions':terminal,
            'terminal_net':float(eq.equity.iloc[-1]-f.net.sum()),'fee_total_closed':float(f.fee_exact.sum()),
            'max_simultaneous':int((st.lp_active.astype(int)+st.sp_active.astype(int)).max())}
        assert row['max_simultaneous']<=2
        if cut is None and metrics: add_metrics(row,f,eq,self.d)
        if save:
            for suffix,frame in [('trades',f),('equity',eq),('funding',ledger),('gates',ev),('states',st)]:frame.to_csv(OUT/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=f,eq,row,ev,st
        return f,eq,row,ev,st

    def noop(self):
        checks=[]
        for slip in [0,2,5]:
            self.run('base',slip)
            for suffix in ['trades','equity','funding','gates','states']:
                x=pd.read_csv(OUT/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                y=pd.read_csv(old.OUT/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                pd.testing.assert_frame_equal(x,y,check_dtype=False,atol=1e-8,rtol=0)
            checks.append({'slip':slip,'trade_equity_funding_gate_state':'PASS'})
        return checks

def add_metrics(row,f,eq,d):
    for label,start,end in [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None)]:
        m=a.previous.prior.extra_metrics(f,eq,d,start,end);part=f
        if start:part=part[part.exit_dt>=start]
        if end:part=part[part.exit_dt<end]
        m['loss_total']=float(-part.loc[part.net<0,'net'].sum());row[label]=m

def attribution(s,slip):
    f,eq,row,*_=s.cache['M',slip];bf,be,br,*_=s.cache['base',slip]
    counts,p=a.previous.cost.paired(f,bf);p.to_csv(OUT/f'M_{slip}bp_paired.csv',index=False)
    removed=p[p._merge=='right_only'];added=p[p._merge=='left_only'];common=p[p._merge=='both']
    parts={'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),
        'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),
        'added_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum()),
        'terminal_delta':row['terminal_net']-br['terminal_net']}
    delta=float(eq.equity.iloc[-1]-be.equity.iloc[-1]);assert abs(sum(parts.values())-delta)<1e-7
    return {'counts':counts,'parts':parts,'net_delta':delta,'trading_delta':float(f.pnl.sum()-bf.pnl.sum()),
        'funding_delta':float(f.funding.sum()-bf.funding.sum()),'closed_fee_delta':float(f.fee_exact.sum()-bf.fee_exact.sum())}

def evaluate_m():
    reg=initialize();verify(read(DOC/'round1_registration.json')['sha256'])
    diagnostic=read(DOC/'round1_diagnostic.json');assert diagnostic['status']=='READY'
    assert not (DOC/'round1_results.json').exists()
    s=Study();noop=s.noop();dump(DOC/'round1_noop.json',{'utc':now(),'checks':noop})
    prefix=s.prefix('base')
    # Build candidate state trace first, with performance summary disabled; audit
    # causal prefixes including an open position before evaluating its metrics.
    s.run('M',0,metrics=False);prefix+=s.prefix('M')
    dump(DOC/'round1_prefix.json',{'utc':now(),'checks':prefix,'candidate_summary_evaluated':False})
    f,eq,row,*_=s.cache['M',0];add_metrics(row,f,eq,s.d)
    for slip in [2,5]:s.run('M',slip)
    failures={str(slip):a.previous.failures(s.cache['M',slip][2],s.cache['base',slip][2]) for slip in [0,2,5]}
    rows=[s.cache[name,slip][2] for name in ['base','M'] for slip in [0,2,5]]
    result={'utc':now(),'status':'REJECTED' if any(failures.values()) else 'BASIC_PASS_REQUIRES_VALIDATION',
        'counts':diagnostic['counts'],'runs':rows,'failed_gates':failures,'noop':noop,'prefix':prefix,
        'attribution':{str(slip):attribution(s,slip) for slip in [0,2,5]},'candidate_pnl_trials':1,'cost_scenarios':3,
        'neighborhood_runs':0,'protected_verified':verify(reg['protected_sha256'])}
    dump(DOC/'round1_results.json',result)
    print(json.dumps({'status':result['status'],'failed_gates':failures,'candidate0':s.cache['M',0][2]['full']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose-m','evaluate-m'],required=True);args=p.parse_args()
    {'diagnose-m':diagnose_m,'evaluate-m':evaluate_m}[args.phase]()
