"""Preregistered offline hour-filter ablation; isolated engine namespace, unchanged production."""
import argparse
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import strategy_round2_20260911 as previous
import strategy_round3_20260911 as prior_audit

ROOT=previous.ROOT
DOC=ROOT/'doc/research_results/20260912_session_ablation'
OUT=ROOT/'data/session_ablation_20260912'
PLAN=ROOT/'doc/strategy_research_plan_20260912.md'
OLD=ROOT/'data/strategy_round2_20260911'
dump=previous.dump
read=previous.read
sha=previous.cost.sha
HOURS={0,1,2,12}


def now(): return datetime.now(timezone.utc).isoformat()
def relative(p): return p.relative_to(ROOT).as_posix()


def register():
    DOC.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    inputs={relative(p):sha(p) for p in [PLAN,Path(__file__),ROOT/'tests/test_session_ablation_20260912.py',
        ROOT/'backtest/research/strategy_round2_20260911.py',ROOT/'backtest/research/v14_export_trades.py',
        ROOT/'backtest/research/execute_optimization_plan_20260908.py',ROOT/'backtest/research/new_information_20260910.py',
        ROOT/'doc/research_results/20260910_new_information/history_index.json']}
    if (DOC/'registration.json').exists():
        reg=read(DOC/'registration.json'); assert inputs==reg['sha256']; prior_audit.verify_map(reg['protected_sha256']); return
    checked=prior_audit.audit_saved()
    prior_audit.initial_audit()
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    paths+=list(read(prior_audit.DOC/'registration.json')['protected_sha256'])
    paths += [x['path'] for x in read(prior_audit.DOC/'artifact_manifest.json')['files']]
    prefixes=[relative(DOC)+'/',relative(OUT)+'/',relative(PLAN),relative(Path(__file__)),
              'tests/test_session_ablation_20260912.py','backtest/research/report_session_ablation_20260912.py',
              'doc/strategy_research_results_20260912.md']
    protected={p:sha(ROOT/p) for p in sorted(set(paths)) if (ROOT/p).is_file() and not any(p.startswith(x) for x in prefixes)}
    dump(DOC/'registration.json',{'registered_utc':now(),'sha256':inputs,'protected_sha256':protected,
        'prior_reuse_audit':checked,'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'initial_git_status':subprocess.check_output(['git','status','--porcelain=v1'],cwd=ROOT,text=True),
        'main':'no_hour','main_trials_before_registration':0,'maximum_main':1,'neighborhoods':0,
        'conditional_controls':['L_only','S_only'],'historical_unseen':False,'download_bytes':0})


def direct_opportunities(ind, states, engine):
    """Baseline bars blocked on both sides by hour have no baseline entry after the saved state."""
    events=[]
    for row in states.itertuples():
        i=int(row.bar); hr=int(ind['hours'][i]); dw=int(ind['dows'][i])
        if hr not in HOURS: continue
        for side,p,key in [('L','lp','l'),('S','sp','s')]:
            if getattr(row,p+'_active'): continue
            if row.d_pnl<=engine.CB_DAILY or getattr(row,key+'_m_pnl')<=getattr(engine,'CB_'+side+'_MONTH') or i<row.consec_end: continue
            if i-getattr(row,key+'_last_exit')<getattr(engine,side+'_CD') or getattr(row,key+'_m_entries')>=getattr(engine,side+'_CAP'): continue
            if dw in getattr(engine,side+'_BLK_D'): continue
            if bool(ind['regime_block_'+key][i]): continue
            pct=ind['pctile_'+side][i]
            if not np.isfinite(pct) or pct>=getattr(engine,side+'_GK_TH'): continue
            if not bool(ind['brk_up' if side=='L' else 'brk_dn'][i]): continue
            events.append({'bar':i,'side':side,'signal_hour_taipei':hr})
    return pd.DataFrame(events,columns=['bar','side','signal_hour_taipei'])


def apply_hour_mode(fn, name):
    assert name in ['base','no_hour','L_only','S_only']
    fn.__globals__['L_BLK_H']=set() if name in ['no_hour','L_only'] else set(HOURS)
    fn.__globals__['S_BLK_H']=set() if name in ['no_hour','S_only'] else set(HOURS)


class Study:
    def __init__(self):
        self.source=previous.cost.Study(); self.d=self.source.d; self.engine=self.source.engine
        self.ind=self.source.ind; self.fn=previous.simulator(self.engine); self.cache={}
        assert self.engine.NOTIONAL==4000 and self.engine.FEE==4
        assert self.engine.L_BRK==self.engine.S_BRK==15
        assert self.engine.L_BLK_H==self.engine.S_BLK_H==HOURS

    def diagnose(self):
        d=self.d
        assert len(d)==17519 and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
        assert d.datetime.is_unique and np.isfinite(d[['open','high','low','close']].to_numpy()).all()
        assert (d.low>0).all() and (d.high>=d[['open','close']].max(axis=1)).all() and (d.low<=d[['open','close']].min(axis=1)).all()
        states=pd.read_csv(OLD/'base_0bp_states.csv')
        events=direct_opportunities(self.ind,states,self.engine)
        events['decision_ts']=(d.datetime+pd.Timedelta(hours=1)).iloc[events.bar.astype(int)].to_numpy()
        events.to_csv(OUT/'direct_opportunities_pre_pnl.csv',index=False)
        n=previous.previous.event_counts(events,'decision_ts')
        status='READY' if n['clusters24h']>=30 and n['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE'
        result={'recorded_utc':now(),'status':status,'quality':'PASS','counts':n,'candidate_pnl_trials':0,
                'event_definition':'Only-hour-blocked opportunities in baseline state; no candidate trades or outcomes used.'}
        dump(DOC/'diagnostic_status.json',result); return result

    def run(self,name='base',slip=0,cut=None,save=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        gates=[]; states=[]
        def gate(i,side):
            gates.append({'bar':i,'side':side,'allowed':True,'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1)}); return True
        def observe(i,state): states.append({'bar':i,**state})
        apply_hour_mode(self.fn,name)
        try:
            raw,state=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,gate=gate,observe=observe)
        finally: apply_hour_mode(self.fn,'base')
        f=previous.review.normalize(pd.DataFrame(raw))
        fund=self.source.fund[self.source.fund.nominal<=d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        f,eq,ledger,terminal=previous.account_terminal(f,d,self.source.mark.iloc[:len(d)],fund,state)
        ev=pd.DataFrame(gates); st=pd.DataFrame(states)
        row={'name':name,'slip':slip,'end_state':state,'terminal_positions':terminal,
             'terminal_net':float(eq.equity.iloc[-1]-f.net.sum()),'fee_total_closed':float(f.fee_exact.sum()),
             'max_simultaneous':int((st.lp_active.astype(int)+st.sp_active.astype(int)).max())}
        if cut is None:
            for label,start,end in [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None),('recent','2026-06-01',None)]:
                m=previous.prior.extra_metrics(f,eq,d,start,end); part=f
                if start: part=part[part.exit_dt>=start]
                if end: part=part[part.exit_dt<end]
                m['loss_total']=float(-part.loc[part.net<0,'net'].sum()); row[label]=m
        assert row['max_simultaneous']<=2
        if save:
            for suffix,frame in [('trades',f),('equity',eq),('funding',ledger),('gates',ev),('states',st)]: frame.to_csv(OUT/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=f,eq,row,ev,st
        return f,eq,row,ev,st

    def noop(self):
        checks=[]; old=read(previous.DOC/'results.json')
        for slip in [0,2,5]:
            f,eq,row,ev,st=self.run('base',slip)
            for suffix,cur,dates in [('trades',f,['entry_dt','exit_dt']),('equity',eq,['time']),('gates',ev,['decision_ts']),('states',st,None)]:
                prev=pd.read_csv(OLD/f'base_{slip}bp_{suffix}.csv',parse_dates=dates)
                pd.testing.assert_frame_equal(cur[prev.columns],prev,check_dtype=False,atol=1e-8,rtol=0)
            prev=next(x for x in old['runs'] if x['name']=='base' and x['slip']==slip)
            for k in ['net_pnl','wr','pf','mdd','worst30','loss_total']: assert abs(row['full'][k]-prev['full'][k])<1e-8
            expected=pd.read_csv(OLD/f'base_{slip}bp_funding.csv')
            current=pd.read_csv(OUT/f'base_{slip}bp_funding.csv')
            pd.testing.assert_frame_equal(current,expected,check_dtype=False,atol=1e-8,rtol=0)
            checks.append({'slip':slip,'all_trade_equity_gate_state_funding_fields':'PASS'})
        return checks

    def prefix(self,name):
        f,eq,_,ev,st=self.cache[name,0]; results=[]
        cuts=sorted(set([6000,11000,16000,int(f.iloc[0].entry_bar)+2]))
        for cut in cuts:
            cf,ce,cr,cv,cs=self.run(name,cut=cut,save=False)
            pd.testing.assert_frame_equal(f[f.exit_bar<cut].reset_index(drop=True),cf.reset_index(drop=True),check_dtype=False,atol=1e-8,rtol=0)
            pd.testing.assert_frame_equal(ev[ev.bar<cut].reset_index(drop=True),cv.reset_index(drop=True),check_dtype=False)
            pd.testing.assert_frame_equal(st[st.bar<cut].reset_index(drop=True),cs.reset_index(drop=True),check_dtype=False)
            pd.testing.assert_frame_equal(eq.iloc[:cut].reset_index(drop=True),ce.reset_index(drop=True),check_dtype=False,atol=.011,rtol=0)
            pind=self.engine.compute_indicators(self.d.iloc[:cut])
            for k,v in pind.items(): np.testing.assert_equal(v,self.ind[k][:cut])
            results.append({'name':name,'cut':cut,'status':'PASS','terminal_positions':cr['terminal_positions'],
                            'equity_max_error':float((ce.equity-eq.equity.iloc[:cut]).abs().max())})
        assert any(x['terminal_positions'] for x in results)
        return results


def attribution(study,slip):
    f,eq,row,*_=study.cache['no_hour',slip]; bf,be,br,*_=study.cache['base',slip]
    counts,p=previous.cost.paired(f,bf); p.to_csv(OUT/f'no_hour_{slip}bp_paired.csv',index=False)
    removed=p[p._merge=='right_only']; added=p[p._merge=='left_only']; common=p[p._merge=='both']
    parts={'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),
        'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),'added_net':float(added.net_c.sum()),
        'common_delta':float((common.net_c-common.net_b).sum()),'terminal_delta':row['terminal_net']-br['terminal_net']}
    delta=float(eq.equity.iloc[-1]-be.equity.iloc[-1]); assert abs(sum(parts.values())-delta)<1e-7
    return {'counts':counts,'parts':parts,'net_delta':delta,'trading_delta':float(f.pnl.sum()-bf.pnl.sum()),
            'funding_delta':float(f.funding.sum()-bf.funding.sum()),'closed_fee_delta':float(f.fee_exact.sum()-bf.fee_exact.sum())}


def evaluate():
    register(); status=read(DOC/'diagnostic_status.json'); assert status['status']=='READY','Event/quality gate not passed'
    s=Study(); noop=s.noop(); dump(DOC/'noop.json',noop)
    # No-op must pass before a candidate is evaluated.
    rows=[s.cache['base',slip][2] for slip in [0,2,5]]
    for slip in [0,2,5]: rows.append(s.run('no_hour',slip)[2])
    failures={str(slip):previous.failures(s.cache['no_hour',slip][2],s.cache['base',slip][2]) for slip in [0,2,5]}
    prefix=s.prefix('base')+s.prefix('no_hour')
    result={'status':'REJECTED' if any(failures.values()) else 'BASIC_PASS_REQUIRES_VALIDATION',
        'counts':status['counts'],'runs':rows,'failed_gates':failures,'noop':noop,'prefix':prefix,
        'attribution':{str(slip):attribution(s,slip) for slip in [0,2,5]},
        'main_pnl_trials':1,'cost_scenarios':3,'controls_evaluated':0,'download_bytes':0,
        'unperformed':['factor_controls','execution_delay','continuous_state_WF','block_bootstrap','multiplicity','concentration','genuine_prospective'],
        'stop_reason':'Basic economic gate failure; no further selection.' if any(failures.values()) else None,
        'protected_files_checked':prior_audit.verify_map(read(DOC/'registration.json')['protected_sha256'])}
    dump(DOC/'results.json',result)
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],**r['full']} for r in rows]).to_csv(DOC/'summary.csv',index=False)
    print(json.dumps({'status':result['status'],'counts':result['counts'],'failed_gates':failures,
        'candidate_0bp':s.cache['no_hour',0][2]['full']},default=str))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['register','diagnose','evaluate'],required=True);args=p.parse_args()
    if args.phase=='register':register();print('REGISTERED')
    elif args.phase=='diagnose':register();print(json.dumps(Study().diagnose(),default=str))
    else:evaluate()
