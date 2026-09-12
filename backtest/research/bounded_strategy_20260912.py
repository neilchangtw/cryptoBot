"""Offline bounded research, additive files only; no exchange or production imports."""
import argparse
import ast
import inspect
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import session_ablation_20260912 as a

ROOT=a.ROOT
DOC=ROOT/'doc/research_results/20260912_bounded_strategy'
OUT=DOC/'ledgers'
PLAN=ROOT/'doc/strategy_bounded_plan_20260912.md'
OLD=ROOT/'data/strategy_round2_20260911'
sha=a.sha
read=a.read
dump=a.dump
def now(): return datetime.now(timezone.utc).isoformat()
def rel(p): return p.relative_to(ROOT).as_posix()

def verify(mapping):
    bad=[p for p,h in mapping.items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
    assert not bad, bad
    return len(mapping)

def register():
    DOC.mkdir(parents=True,exist_ok=True); OUT.mkdir(exist_ok=True)
    p=DOC/'registration.json'
    if p.exists():
        reg=read(p);verify(reg['sha256']);verify(reg['protected_sha256']);return reg
    previous=read(ROOT/'doc/research_results/20260912_gk_ablation/implementation_revision.json')
    n=verify(previous['sha256']); n+=verify(previous['protected_sha256'])
    artifact=read(ROOT/'doc/research_results/20260912_gk_ablation/artifact_manifest.json')
    # Manifest format is independently recorded, not guessed.
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    protected=dict(previous['protected_sha256'])
    for f in paths:
        if f.startswith(rel(DOC)) or f in [rel(PLAN),rel(Path(__file__)),'tests/test_bounded_strategy_20260912.py']:continue
        if (ROOT/f).is_file():protected[f]=sha(ROOT/f)
    inputs={rel(p):sha(p) for p in [PLAN,Path(__file__),ROOT/'tests/test_bounded_strategy_20260912.py']}
    reg={'registered_utc':now(),'sha256':inputs,'protected_sha256':protected,'prior_verified':n,
         'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'initial_git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),
         'round':1,'main':'G_tp_close','candidate_pnl_trials_before':0,'max_rounds':3,'network_requests':0}
    dump(p,reg);return reg

def patched_engine(engine, mode='base'):
    """Reuse prior validated instrumentation; modify only TP boolean for G."""
    source=inspect.getsource(engine.simulate_v14_detailed)
    if mode=='G':
        for old,new in [
          ('elif hi >= ep * (1 + l_tp_eff):','elif hi >= ep * (1 + l_tp_eff) and ci >= ep * (1 + l_tp_eff):'),
          ('elif li <= ep * (1 - S_TP):','elif li <= ep * (1 - S_TP) and ci <= ep * (1 - S_TP):')]:
            assert source.count(old)==1;source=source.replace(old,new)
    # The existing factory gets source from inspect, so compile its documented
    # source once with the above input substituted, without writing old files.
    factory=inspect.getsource(a.previous.simulator)
    factory=factory.replace('src = inspect.getsource(engine.simulate_v14_detailed)','src = _source')
    scope={**a.previous.__dict__,'_source':source}
    exec(compile(factory,'<bounded-factory>','exec'),scope)
    return scope['simulator'](engine)

def fallback_program(engine,side):
    """Extract original exit branch, disable TP only, return no payoffs."""
    src=inspect.getsource(engine.simulate_v14_detailed)
    section=src.split('# --- '+side+' EXIT ---')[1].split('# ---')[0]
    section=section[section.index('            ex_price = 0.0'):section.index('            if ex_price > 0:')]
    section=section.replace('elif hi >= ep * (1 + l_tp_eff):','elif False:')
    section=section.replace('elif li <= ep * (1 - S_TP):','elif False:')
    return compile(inspect.cleandoc(section),'<original-exit-without-TP>','exec')

def direct_tp_events(d,states,engine):
    events=[]; branches={s:fallback_program(engine,s) for s in ['L','S']}
    for state in states.to_dict('records'):
        i=int(state['bar'])+1
        if i>=len(d):continue
        hi,li,ci=d.loc[i,['high','low','close']].astype(float)
        for side,p in [('L','lp'),('S','sp')]:
            if not state[p+'_active']:continue
            ep=state[p+'_entry'];tp=engine._L_TP_BR.get(state['lp_regime'],engine.L_TP) if side=='L' else engine.S_TP
            safe=li<=ep*(1-engine.L_SN) if side=='L' else hi>=ep*(1+engine.S_SN)
            touch=hi>=ep*(1+tp) if side=='L' else li<=ep*(1-tp)
            confirm=ci>=ep*(1+tp) if side=='L' else ci<=ep*(1-tp)
            if safe or not touch or confirm:continue
            scope={**engine.__dict__,**state,'ep':ep,'bh':state[p+'_held']+1,'hi':hi,'li':li,'ci':ci,
                   'slip':0.,'realistic':True}
            # Only original unchanged exit rules read running MFE, no new feature.
            scope[p+'_mfe']=max(state[p+'_mfe'],(hi-ep)/ep if side=='L' else (ep-li)/ep)
            exec(branches[side],scope)
            if scope['ex_price']>0:continue  # same-price close, no action difference
            events.append({'bar':i,'side':side,'root_entry_bar':state[p+'_bar'],
                'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1),'tp':tp,'close_return':(ci/ep-1)*(1 if side=='L' else -1)})
    return pd.DataFrame(events,columns=['bar','side','root_entry_bar','decision_ts','tp','close_return'])

class Study(a.Study):
    def run(self,name='base',slip=0,cut=None,save=True):
        self.fn=patched_engine(self.engine,name)
        f,eq,row,ev,st=super().run('base',slip,cut,False)
        row['name']=name
        if save:
            fund=self.source.fund[self.source.fund.nominal<=self.d.datetime.iloc[len(eq)-1]+pd.Timedelta(hours=1)]
            _,_,ledger,_=a.previous.account_terminal(f,self.d.iloc[:len(eq)],self.source.mark.iloc[:len(eq)],fund,row['end_state'])
            for suffix,frame in [('trades',f),('equity',eq),('funding',ledger),('gates',ev),('states',st)]:
                frame.to_csv(OUT/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=f,eq,row,ev,st
        return f,eq,row,ev,st

    def noop(self):
        checks=[]
        for slip in [0,2,5]:
            self.run('base',slip)
            for suffix in ['trades','equity','funding','gates','states']:
                x=pd.read_csv(OUT/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                y=pd.read_csv(OLD/f'base_{slip}bp_{suffix}.csv',keep_default_na=False)
                pd.testing.assert_frame_equal(x,y,check_dtype=False,atol=1e-8,rtol=0)
            checks.append({'slip':slip,'all_trade_equity_funding_gate_state_fields':'PASS'})
        return checks

def baseline():
    reg=register();s=Study();d=s.d
    assert len(d)==17519 and d.datetime.is_unique and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert np.isfinite(d[['open','high','low','close','volume']].to_numpy()).all()
    assert (d.low>0).all() and (d.high>=d[['open','close']].max(axis=1)).all() and (d.low<=d[['open','close']].min(axis=1)).all()
    assert s.engine.NOTIONAL==4000 and s.engine.FEE==4 and s.engine.L_BRK==s.engine.S_BRK==15
    checks=s.noop();prefix=s.prefix('base')
    # Re-read current pure production functions without dotenv, executor or API side effects.
    tree=ast.parse((ROOT/'strategy.py').read_text(encoding='utf-8'))
    selected=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in ['compute_indicators','classify_regime']:selected.append(node)
        elif isinstance(node,ast.Assign):
            try:ast.literal_eval(node.value)
            except (ValueError,TypeError):continue
            selected.append(node)
    ns={'np':np,'pd':pd};exec(compile(ast.Module(body=selected,type_ignores=[]),'<current-pure-strategy>','exec'),ns)
    prod=ns['compute_indicators'](d)
    pairs={'gk_pctile':'pctile_L','gk_pctile_s':'pctile_S','breakout_long':'brk_up','breakout_short':'brk_dn','sma_slope':'slope','regime_block_l':'regime_block_l','regime_block_s':'regime_block_s'}
    for pk,ek in pairs.items():np.testing.assert_allclose(prod[pk].to_numpy()[310:],s.ind[ek][310:],atol=1e-10,rtol=0,equal_nan=True)
    b=s.cache['base',0][2]['full'];assert b['n']==269 and abs(b['net_pnl']-7926.701053608433)<1e-7 and abs(b['mdd']-368.53)<.01
    result={'utc':now(),'bars':len(d),'first_open':str(d.datetime.iloc[0]),'last_close':str(d.datetime.iloc[-1]+pd.Timedelta(hours=1)),
            'noop':checks,'prefix':prefix,'current_production_indicators':'PASS','runs':[s.cache['base',k][2] for k in [0,2,5]],
            'protected_verified':verify(reg['protected_sha256']),'historical_received_ts':'not archived; original closed-kline availability assumption retained',
            'candidate_return_runs':0}
    dump(DOC/'baseline.json',result);print(json.dumps({'baseline':'PASS','n':b['n'],'net':b['net_pnl'],'mdd':b['mdd']}))

def diagnose_g():
    reg=register();assert (DOC/'baseline.json').exists()
    s=Study();states=pd.read_csv(OUT/'base_0bp_states.csv',keep_default_na=False)
    events=direct_tp_events(s.d,states,s.engine)
    events.to_csv(DOC/'round1_events_pre_pnl.csv',index=False)
    counts=a.previous.previous.event_counts(events,'decision_ts')
    prefixes=[]
    for cut in [6000,11000,16000]:
        pe=direct_tp_events(s.d.iloc[:cut],states[states.bar<cut],s.engine)
        pd.testing.assert_frame_equal(pe,events[events.bar<cut].reset_index(drop=True));prefixes.append(cut)
    result={'utc':now(),'round':1,'name':'G_tp_close','status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
            'counts':counts,'feature_prefix_cuts':prefixes,'quality':'PASS under inherited closed-kline assumption','candidate_pnl_trials':0,
            'event_definition':'Original state TP touched, close unconfirmed, no unchanged exit at same close; no outcome columns read',
            'protected_verified':verify(reg['protected_sha256'])}
    p=DOC/'round1_diagnostic.json';assert not p.exists(),'diagnostic record is immutable';dump(p,result)
    print(json.dumps(result))

def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['baseline','diagnose-g'],required=True);args=p.parse_args()
    {'baseline':baseline,'diagnose-g':diagnose_g}[args.phase]()

if __name__=='__main__':main()
