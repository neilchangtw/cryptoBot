"""固定現貨同步条件，重用原完整狀態引擎與成本帳本。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import intrahour_entry_20260908 as intra
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review
from fetch_spot_sync_20260909 import validate

ROOT=intra.ROOT
OUT=ROOT/'data/spot_futures_sync_20260909'
RULES=['base','both','long','short']

def masks(d,spot,window=15):
    assert pd.DatetimeIndex(d.datetime).equals(pd.DatetimeIndex(spot.datetime))
    assert spot.datetime.is_unique
    assert spot.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert np.isfinite(spot.close).all() and (spot.close>0).all()
    hi=spot.close.shift(1).rolling(window,min_periods=window).max()
    lo=spot.close.shift(1).rolling(window,min_periods=window).min()
    L=(spot.close>hi).to_numpy();S=(spot.close<lo).to_numpy();yes=np.ones(len(d),bool)
    return {'base':{'L':yes,'S':yes},'both':{'L':L,'S':S},
            'long':{'L':L,'S':yes},'short':{'L':yes,'S':S}}

def bucket(f):
    return {'n':len(f),'net':float(f.net.sum()),'wr':float((f.net>0).mean()*100) if len(f) else None,
            'mh':int(f.reason_code.eq('MH').sum()),'mh_net':float(f.loc[f.reason_code.eq('MH'),'net'].sum()),
            'wins':int((f.net>0).sum()),'winning_net':float(f.loc[f.net>0,'net'].sum()),
            'losing_net':float(f.loc[f.net<=0,'net'].sum())}

class Study:
    def __init__(self):
        self.source=cost.Study();self.d=self.source.d;self.engine=self.source.engine
        self.spot=pd.read_csv(OUT/'spot_1h.csv',parse_dates=['datetime'])
        m=json.loads((OUT/'manifest.json').read_text());raw=[]
        assert cost.sha(OUT/'spot_1h.csv')==m['csv_sha256']
        assert cost.sha(ROOT/'data/maxhold_review_20260908/candles.csv')==m['source_sha256']
        for r in m['requests']:
            p=OUT/r['file'];assert cost.sha(p)==r['sha256'];raw.extend(json.loads(p.read_text()))
        restored=validate(raw,self.d.datetime)
        restored['datetime']=restored.datetime.astype('datetime64[ns]')
        self.spot['datetime']=self.spot.datetime.astype('datetime64[ns]')
        pd.testing.assert_frame_equal(restored,self.spot,check_exact=False,rtol=1e-12)
        self.filters=masks(self.d,self.spot)
        self.fn=intra.simulator(self.engine);self.ind=self.engine.compute_indicators(self.d);self.cache={}

    def run(self,name,slip=0,hist=False,cut=None,policy=None):
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        events=[]
        def gate(i,side):
            rule=name if policy is None else policy[i]
            allowed=bool(self.filters[rule][side][i]);events.append({'bar':i,'side':side,'rule':rule,'allowed':allowed})
            return allowed
        raw,active=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,
            margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None,gate=gate)
        f=review.normalize(pd.DataFrame(raw))
        if cut is not None:return f,pd.DataFrame(events)
        assert not any(active.values()),'Terminal position requires extended accounting'
        f,eq,ledger=cost.account(f,self.d,self.source.mark,self.source.fund)
        row={'name':name,'slip':slip,'historical':hist,'full':cost.metrics(f,eq),
             'early':cost.metrics(f,eq,end='2026-01-01'),'late':cost.metrics(f,eq,start='2026-01-01'),
             'recent':cost.metrics(f,eq,start='2026-06-01'),'side':{s:bucket(f[f.side==s]) for s in ['L','S']},
             'checks':len(events),'rejected':sum(not e['allowed'] for e in events)}
        stem=f'{name}_{"hist" if hist else "flat"}_{slip}'
        f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
        ledger.to_csv(OUT/f'{stem}_funding.csv',index=False);pd.DataFrame(events).to_csv(OUT/f'{stem}_events.csv',index=False)
        self.cache[name,slip,hist]=(f,eq,row,pd.DataFrame(events))
        return f,eq,row

def main():
    paths=[Path(__file__),ROOT/'backtest/research/fetch_spot_sync_20260909.py',Path(intra.__file__),Path(cost.__file__),
        ROOT/'doc/spot_futures_sync_plan_20260909.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
        ROOT/'data/maxhold_review_20260908/candles.csv',OUT/'spot_1h.csv',OUT/'manifest.json',
        cost.HISTORY/'mark_1h_full.csv',cost.HISTORY/'funding_full.csv']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'rules':RULES,'hashes':hashes},indent=2),encoding='utf-8')
    s=Study();rows=[];checks=[];diagnostics={}
    for hist in [False,True]:
        for slip in [0,2,5]:
            for name in RULES:
                f,eq,row=s.run(name,slip,hist);rows.append(row)
                if name=='base':
                    original=pd.DataFrame(s.engine.simulate_v14_detailed(s.ind,s.d.datetime.to_numpy(),realistic=True,
                        slip_bps=slip,margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None))
                    raw,_=s.fn(s.ind,s.d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                        margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)
                    pd.testing.assert_frame_equal(pd.DataFrame(raw)[original.columns],original)
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
                    assert f[['side','entry_bar','exit_bar','reason_code']].equals(old[['side','entry_bar','exit_bar','reason_code']])
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'parity':[hist,slip],'status':'PASS'})
            print(json.dumps({'complete':[hist,slip]}),flush=True)
    for cut in [10000,16000]:
        m=masks(s.d.iloc[:cut],s.spot.iloc[:cut])
        for name in RULES:
            for side in ['L','S']:assert np.array_equal(m[name][side],s.filters[name][side][:cut])
            small,ev=s.run(name,cut=cut)
            full,_,_,events=s.cache[name,0,False];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(small.reset_index(drop=True),full[small.columns].reset_index(drop=True))
            pd.testing.assert_frame_equal(ev,events[events.bar<cut].reset_index(drop=True))
            checks.append({'prefix':[cut,name],'status':'PASS'})
    bf,be,br,events=s.cache['base',0,False]
    annotated=bf.copy()
    annotated['spot_sync']=[bool(s.filters['both'][t.side][int(t.entry_bar)]) for t in bf.itertuples()]
    annotated['spot_close']=s.spot.close.to_numpy()[bf.entry_bar.astype(int)]
    hi=s.spot.close.shift(1).rolling(15).max();lo=s.spot.close.shift(1).rolling(15).min()
    annotated['spot_boundary']=[float((hi if t.side=='L' else lo).iloc[int(t.entry_bar)]) for t in bf.itertuples()]
    annotated['spot_distance_bp']=[(1 if t.side=='L' else -1)*(t.spot_close/t.spot_boundary-1)*10000 for t in annotated.itertuples()]
    annotated.to_csv(OUT/'baseline_spot_labels.csv',index=False)
    buckets=[]
    for period,a,z in [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None),('recent','2026-06-01',None)]:
        f=annotated
        if a:f=f[f.exit_dt>=a]
        if z:f=f[f.exit_dt<z]
        for side in ['ALL','L','S']:
            sf=f if side=='ALL' else f[f.side==side]
            for sync in [True,False]:buckets.append({'period':period,'side':side,'sync':sync,**bucket(sf[sf.spot_sync==sync])})
    pd.DataFrame(buckets).to_csv(OUT/'buckets.csv',index=False)
    for name in RULES[1:]:
        f,eq,row,_=s.cache[name,0,False];pair,paired=cost.paired(f,bf)
        paired.to_csv(OUT/f'{name}_paired.csv',index=False)
        removed=paired[paired._merge=='right_only'];added=paired[paired._merge=='left_only'];common=paired[paired._merge=='both']
        decomposition={'removed_winner_net':float(removed.loc[removed.net_b>0,'net_b'].sum()),
            'removed_loss_net':float(removed.loc[removed.net_b<=0,'net_b'].sum()),
            'removed_mh_net':float(removed.loc[removed.reason_code_b=='MH','net_b'].sum()),
            'added_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum())}
        assert abs(row['full']['net_pnl']-br['full']['net_pnl']-(-decomposition['removed_winner_net']-decomposition['removed_loss_net']+decomposition['added_net']+decomposition['common_delta']))<1e-7
        late_affected=cost.paired(f[f.exit_dt>='2026-01-01'],bf[bf.exit_dt>='2026-01-01'])[0]['affected']
        fails=[]
        for slip in [0,2,5]:
            r=s.cache[name,slip,False][2];b=s.cache['base',slip,False][2]
            for period in ['full','early','late']:
                for metric in ['net_pnl','wr']:
                    if r[period][metric]<=b[period][metric]+1e-8:fails.append(f'{slip}:{period}:{metric}')
            if r['full']['mdd']>b['full']['mdd']*1.1:fails.append(f'{slip}:mdd')
            if r['full']['worst30']<b['full']['worst30']*1.1:fails.append(f'{slip}:worst30')
        economic=list(fails)
        if pair['affected']<30:fails.append('full_affected<30')
        if late_affected<30:fails.append('late_affected<30')
        diagnostics[name]={'pair':pair,'late_affected':late_affected,'decomposition':decomposition,
            'uncertainty':cost.uncertainty(eq,be),'failed_gates':fails,
            'status':'REJECTED' if economic else 'INSUFFICIENT_SAMPLE' if fails else 'NEEDS_FURTHER_VALIDATION'}
    ordered=sorted(diagnostics,key=lambda n:diagnostics[n]['uncertainty']['one_sided_centered_block_p']);peak=0
    for rank,n in enumerate(ordered):
        peak=max(peak,diagnostics[n]['uncertainty']['one_sided_centered_block_p']*(3-rank))
        diagnostics[n]['holm_p']=min(1,peak)
    survivors=[n for n,v in diagnostics.items() if not v['failed_gates']]
    result={'rows':rows,'diagnostics':diagnostics,'buckets':buckets,'checks':checks,'hashes':hashes,
        'survivors':survivors,'further_validation':'PENDING' if survivors else 'NOT_RUN_BASIC_GATES_FAILED',
        'status':'NO_PROMOTION','scenario_count':len(rows)}
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],'historical':r['historical'],**r['full']} for r in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'diagnostics':diagnostics,'flat0':[r for r in rows if not r['historical'] and r['slip']==0],
        'full_buckets':[b for b in buckets if b['period']=='full']}),flush=True)

if __name__=='__main__':main()
