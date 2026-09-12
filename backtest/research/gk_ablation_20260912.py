"""Independent, preregistered complete GK-gate removal using the frozen stateful engine."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import session_ablation_20260912 as a

ROOT=a.ROOT
DOC=ROOT/'doc/research_results/20260912_gk_ablation'
OUT=ROOT/'data/gk_ablation_20260912'
PLAN=ROOT/'doc/gk_ablation_plan_20260912.md'
dump=a.dump
read=a.read
sha=a.sha


def register():
    DOC.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    inputs={a.relative(p):sha(p) for p in [PLAN,Path(__file__),ROOT/'tests/test_gk_ablation_20260912.py',
            ROOT/'backtest/research/session_ablation_20260912.py',a.DOC/'registration.json',a.DOC/'implementation_revision.json',
            a.DOC/'diagnostic_status.json',a.DOC/'noop.json']}
    if (DOC/'registration.json').exists():
        reg=read(DOC/'implementation_revision.json') if (DOC/'implementation_revision.json').exists() else read(DOC/'registration.json')
        assert inputs==reg['sha256'];a.prior_audit.verify_map(reg['protected_sha256']);return
    a.register()
    protected=read(a.DOC/'registration.json')['protected_sha256']
    protected.update(inputs)
    protected.update({a.relative(p):sha(p) for p in a.OUT.rglob('*') if p.is_file()})
    dump(DOC/'registration.json',{'registered_utc':a.now(),'sha256':inputs,'protected_sha256':protected,
        'candidate_pnl_trials_before_registration':0,'earlier_E_pnl_trials':0,'main':'no_gk','maximum_main':1,
        'historical_unseen':False,'download_bytes':0,'scope':'Second separately preregistered experiment under the new user ablation request; E remains stopped.'})


def direct_opportunities(ind,states,engine):
    events=[]
    for row in states.itertuples():
        i=int(row.bar);hr=int(ind['hours'][i]);dw=int(ind['dows'][i])
        for side,p,key in [('L','lp','l'),('S','sp','s')]:
            pct=ind['pctile_'+side][i]
            if not np.isfinite(pct) or pct<getattr(engine,side+'_GK_TH'): continue
            if getattr(row,p+'_active'):continue
            if row.d_pnl<=engine.CB_DAILY or getattr(row,key+'_m_pnl')<=getattr(engine,'CB_'+side+'_MONTH') or i<row.consec_end:continue
            if i-getattr(row,key+'_last_exit')<getattr(engine,side+'_CD') or getattr(row,key+'_m_entries')>=getattr(engine,side+'_CAP'):continue
            if hr in getattr(engine,side+'_BLK_H') or dw in getattr(engine,side+'_BLK_D'):continue
            if bool(ind['regime_block_'+key][i]):continue
            if not bool(ind['brk_up' if side=='L' else 'brk_dn'][i]):continue
            events.append({'bar':i,'side':side,'gk_percentile':pct})
    return pd.DataFrame(events,columns=['bar','side','gk_percentile'])


class Study(a.Study):
    def diagnose(self):
        d=self.d
        assert len(d)==17519 and d.datetime.is_unique and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
        assert np.isfinite(d[['open','high','low','close']].to_numpy()).all()
        for side in ['L','S']:assert np.isfinite(self.ind['pctile_'+side][310:]).all()
        events=direct_opportunities(self.ind,pd.read_csv(a.OLD/'base_0bp_states.csv'),self.engine)
        events['decision_ts']=(d.datetime+pd.Timedelta(hours=1)).iloc[events.bar.astype(int)].to_numpy()
        events.to_csv(OUT/'direct_opportunities_pre_pnl.csv',index=False)
        n=a.previous.previous.event_counts(events,'decision_ts')
        result={'recorded_utc':a.now(),'status':'READY' if n['clusters24h']>=30 and n['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
            'quality':'PASS','counts':n,'candidate_pnl_trials':0}
        dump(DOC/'diagnostic_status.json',result);return result

    def run(self,name='base',slip=0,cut=None,save=True):
        assert name in ['base','no_gk','L_only','S_only']
        self.fn.__globals__['L_GK_TH']=np.inf if name in ['no_gk','L_only'] else self.engine.L_GK_TH
        self.fn.__globals__['S_GK_TH']=np.inf if name in ['no_gk','S_only'] else self.engine.S_GK_TH
        try: f,eq,row,ev,st=super().run('base',slip,cut,False)
        finally:
            self.fn.__globals__['L_GK_TH']=self.engine.L_GK_TH
            self.fn.__globals__['S_GK_TH']=self.engine.S_GK_TH
        row['name']=name
        if save:
            # Parent produced no output. Keep complete ledger in this experiment only.
            fund=self.source.fund[self.source.fund.nominal<=self.d.datetime.iloc[len(eq)-1]+pd.Timedelta(hours=1)]
            _,_,ledger,_=a.previous.account_terminal(f,self.d.iloc[:len(eq)],self.source.mark.iloc[:len(eq)],fund,row['end_state'])
            for suffix,frame in [('trades',f),('equity',eq),('funding',ledger),('gates',ev),('states',st)]:frame.to_csv(OUT/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=f,eq,row,ev,st
        return f,eq,row,ev,st

    def noop(self):
        checks=[]
        for slip in [0,2,5]:
            self.run('base',slip)
            for suffix in ['trades','equity','funding','gates','states']:
                x=pd.read_csv(OUT/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                y=pd.read_csv(a.OLD/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                pd.testing.assert_frame_equal(x,y,check_dtype=False,atol=1e-8,rtol=0)
            checks.append({'slip':slip,'all_saved_trade_equity_funding_gate_state_fields':'PASS'})
        return checks


def attribution(s,slip):
    f,eq,row,*_=s.cache['no_gk',slip];bf,be,br,*_=s.cache['base',slip]
    counts,p=a.previous.cost.paired(f,bf);p.to_csv(OUT/f'no_gk_{slip}bp_paired.csv',index=False)
    removed=p[p._merge=='right_only'];added=p[p._merge=='left_only'];common=p[p._merge=='both']
    parts={'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),
        'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),'added_net':float(added.net_c.sum()),
        'common_delta':float((common.net_c-common.net_b).sum()),'terminal_delta':row['terminal_net']-br['terminal_net']}
    delta=float(eq.equity.iloc[-1]-be.equity.iloc[-1]);assert abs(sum(parts.values())-delta)<1e-7
    return {'counts':counts,'parts':parts,'net_delta':delta,'trading_delta':float(f.pnl.sum()-bf.pnl.sum()),
        'funding_delta':float(f.funding.sum()-bf.funding.sum()),'closed_fee_delta':float(f.fee_exact.sum()-bf.fee_exact.sum())}


def evaluate():
    register(); diag=read(DOC/'diagnostic_status.json');assert diag['status']=='READY'
    s=Study();noop=s.noop();dump(DOC/'noop.json',noop)
    dump(DOC/'evaluation_started.json',{'utc':a.now(),'noop_passed':True,'candidate_pnl_trials_before':0})
    for slip in [0,2,5]:s.run('no_gk',slip)
    failures={str(slip):a.previous.failures(s.cache['no_gk',slip][2],s.cache['base',slip][2]) for slip in [0,2,5]}
    prefix=s.prefix('base')+s.prefix('no_gk')
    result={'status':'REJECTED' if any(failures.values()) else 'BASIC_PASS_REQUIRES_VALIDATION','counts':diag['counts'],
        'runs':[s.cache[name,slip][2] for name in ['base','no_gk'] for slip in [0,2,5]],'noop':noop,'prefix':prefix,
        'failed_gates':failures,'attribution':{str(slip):attribution(s,slip) for slip in [0,2,5]},'main_pnl_trials':1,'cost_scenarios':3,
        'controls_evaluated':0,'download_bytes':0,'preserved_files':a.prior_audit.verify_map(read(DOC/'implementation_revision.json' if (DOC/'implementation_revision.json').exists() else DOC/'registration.json')['protected_sha256']),
        'unperformed':['factor_controls','execution_delay','continuous_state_WF','block_bootstrap','multiplicity','concentration','genuine_prospective'],
        'stop_reason':'Basic economic gates failed' if any(failures.values()) else None}
    dump(DOC/'results.json',result)
    pd.DataFrame([{'name':v['name'],'slip':v['slip'],**v['full']} for v in result['runs']]).to_csv(DOC/'summary.csv',index=False)
    print(json.dumps({'status':result['status'],'counts':diag['counts'],'failed_gates':failures,'candidate_0bp':s.cache['no_gk',0][2]['full']},default=str))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['diagnose','evaluate'],required=True);args=p.parse_args()
    if args.phase=='diagnose':register();print(json.dumps(Study().diagnose(),default=str))
    else:evaluate()
