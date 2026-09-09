"""已收盤 5m 的量差變化和價格反應；只增加研究進場 gate。"""
from pathlib import Path
from types import FunctionType
import json
import numpy as np
import pandas as pd
import spot_futures_sync_20260909 as harness

cost=harness.cost
ROOT=harness.ROOT
OUT=ROOT/'data/flow_price_20260909'
SUB=ROOT/'data/eth_5m_monthly_20260908'
RULES=['base','quality','pair','pair_long','pair_short','price','flow','hour']
MAIN=['pair','pair_long','pair_short']

def features(d,sub,quality):
    expected=pd.date_range(d.datetime.iloc[0],d.datetime.iloc[-1]+pd.Timedelta(minutes=55),freq='5min')
    assert pd.DatetimeIndex(sub.datetime).equals(expected),'Missing, duplicate or shifted 5m'
    assert len(quality)==len(d)
    c,v,b=[sub[k].to_numpy(float).reshape(len(d),12) for k in ['close','volume','taker_buy_volume']]
    assert np.isfinite(c).all() and np.isfinite(v).all() and np.isfinite(b).all()
    assert (c>0).all() and (v>=0).all() and (b>=0).all() and (b<=v).all()
    v45=v[:,:9].sum(axis=1);v15=v[:,9:].sum(axis=1)
    b45=b[:,:9].sum(axis=1);b15=b[:,9:].sum(axis=1)
    valid=pd.Series(np.asarray(quality,bool)).rolling(16,min_periods=16).min().fillna(0).astype(bool).to_numpy()
    valid=valid&(v45>0)&(v15>0)
    def ratio(buy,volume):
        return 2*np.divide(buy,volume,out=np.full(len(d),np.nan),where=volume>0)-1
    f45=ratio(b45,v45);f15=ratio(b15,v15);f60=ratio(b45+b15,v45+v15)
    r15=c[:,-1]/c[:,8]-1
    out={};masks={name:{} for name in RULES}
    for side,sign in [('L',1),('S',-1)]:
        F45=sign*f45;F15=sign*f15;F60=sign*f60;R15=sign*r15
        A=(F15>0)&(F15>F45);P=R15<=0;D=A&P
        out[side]=pd.DataFrame({'valid':valid,'F45':F45,'F15':F15,'F60':F60,'R15':R15,
            'strong':A,'weak':P,'divergent':D,'V45':v45,'V15':v15,'B45':b45,'B15':b15})
        for name in RULES:
            blocked=D if name=='pair' or (name=='pair_long' and side=='L') or (name=='pair_short' and side=='S') else (
                P if name=='price' else A if name=='flow' else (F60>0)&P if name=='hour' else np.zeros(len(d),bool))
            masks[name][side]=np.ones(len(d),bool) if name=='base' else valid&~blocked
    return out,masks

def audit_aggregation(d,sub):
    a=sub.set_index('datetime').resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','taker_buy_volume':'sum'})
    ref=d.set_index('datetime')[a.columns]
    assert a.index.equals(ref.index)
    delta=(a-ref).abs()
    return (delta.max(axis=1)<=1e-7).to_numpy(),delta

class Study:
    def __init__(self):
        self.source=cost.Study();self.d=self.source.d;self.engine=self.source.engine
        manifest=json.loads((SUB/'manifest.json').read_text())
        assert cost.sha(SUB/'ETHUSDT_5m_full.csv')==manifest['csv_sha256']
        assert cost.sha(ROOT/'data/maxhold_review_20260908/candles.csv')==manifest['baseline_sha256']
        for src in manifest['source_files']:assert cost.sha(ROOT/src['path'])==src['sha256']
        self.sub=pd.read_csv(SUB/'ETHUSDT_5m_full.csv',parse_dates=['datetime'])
        self.q=pd.read_csv(SUB/'hour_quality.csv',parse_dates=['datetime'])
        assert pd.DatetimeIndex(self.q.datetime).equals(pd.DatetimeIndex(self.d.datetime))
        quality,delta=audit_aggregation(self.d,self.sub)
        np.testing.assert_array_equal(quality,self.q.valid_5m_alignment)
        assert (~quality).sum()==manifest['mismatched_1h']
        self.feat,self.filters=features(self.d,self.sub,quality)
        self.quality_audit={'rows_5m':len(self.sub),'hours':len(self.d),'mismatches':int((~quality).sum()),
            'invalid_feature_hours':int((~self.feat['L'].valid).sum()),
            'bad_hours':[str(t) for t in self.d.datetime[~quality]],'max_abs_difference':delta.max().to_dict()}
        self.fn=harness.intra.simulator(self.engine);self.ind=self.engine.compute_indicators(self.d);self.cache={}

    # 重用上一輪已驗證的完整執行，僅將輸出路徑綁定到本研究。
    run=FunctionType(harness.Study.run.__code__,{**harness.Study.run.__globals__,'OUT':OUT},
        argdefs=harness.Study.run.__defaults__)

def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),Path(harness.__file__),Path(harness.intra.__file__),Path(cost.__file__),
        ROOT/'doc/flow_price_plan_20260909.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
        ROOT/'data/maxhold_review_20260908/candles.csv',SUB/'ETHUSDT_5m_full.csv',SUB/'manifest.json',SUB/'hour_quality.csv',
        cost.HISTORY/'mark_1h_full.csv',cost.HISTORY/'funding_full.csv']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'rules':RULES,'main':MAIN,'hashes':hashes},indent=2),encoding='utf-8')
    s=Study();rows=[];checks=[];diagnostics={}
    for hist in [False,True]:
        for slip in [0,2,5]:
            for name in RULES:
                f,eq,row=s.run(name,slip,hist);rows.append(row)
                if name=='base':
                    orig=pd.DataFrame(s.engine.simulate_v14_detailed(s.ind,s.d.datetime.to_numpy(),realistic=True,
                        slip_bps=slip,margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None))
                    raw,_=s.fn(s.ind,s.d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                        margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)
                    pd.testing.assert_frame_equal(pd.DataFrame(raw)[orig.columns],orig)
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
                    assert f[['side','entry_bar','exit_bar','reason_code']].equals(old[['side','entry_bar','exit_bar','reason_code']])
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'parity':[hist,slip],'status':'PASS'})
                if name=='quality':
                    bf,be,_,_=s.cache['base',slip,hist]
                    counts,_=cost.paired(f,bf)
                    checks.append({'quality_control':[hist,slip],'paired':counts,'net_delta':row['full']['net_pnl']-s.cache['base',slip,hist][2]['full']['net_pnl']})
            print(json.dumps({'complete':[hist,slip]}),flush=True)
    for cut in [10000,16000]:
        feat,filters=features(s.d.iloc[:cut],s.sub.iloc[:cut*12],s.q.valid_5m_alignment.iloc[:cut])
        for side in ['L','S']:pd.testing.assert_frame_equal(feat[side],s.feat[side].iloc[:cut])
        for name in RULES:
            for side in ['L','S']:np.testing.assert_array_equal(filters[name][side],s.filters[name][side][:cut])
            small,events=s.run(name,cut=cut)
            full,_,_,ev=s.cache[name,0,False];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(small.reset_index(drop=True),full[small.columns].reset_index(drop=True))
            pd.testing.assert_frame_equal(events,ev[ev.bar<cut].reset_index(drop=True))
            checks.append({'prefix':[cut,name],'status':'PASS'})
    bf,be,br,_=s.cache['base',0,False];qf,qe,qr,_=s.cache['quality',0,False]
    annotated=bf.copy()
    for column in s.feat['L'].columns:
        annotated[column]=[s.feat[t.side].iloc[int(t.entry_bar)][column] for t in bf.itertuples()]
    annotated.to_csv(OUT/'baseline_flow_labels.csv',index=False)
    buckets=[]
    for period,a,z in [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None),('recent','2026-06-01',None)]:
        f=annotated[annotated.valid]
        if a:f=f[f.exit_dt>=a]
        if z:f=f[f.exit_dt<z]
        for side in ['ALL','L','S']:
            sf=f if side=='ALL' else f[f.side==side]
            for A in [False,True]:
                for P in [False,True]:
                    buckets.append({'period':period,'side':side,'strong':A,'weak':P,**harness.bucket(sf[(sf.strong==A)&(sf.weak==P)])})
    pd.DataFrame(buckets).to_csv(OUT/'buckets.csv',index=False)
    for name in RULES[2:]:
        f,eq,row,_=s.cache[name,0,False];pair,paired=cost.paired(f,bf)
        paired.to_csv(OUT/f'{name}_paired.csv',index=False)
        removed=paired[paired._merge=='right_only'];added=paired[paired._merge=='left_only'];common=paired[paired._merge=='both']
        d={'removed_winner_net':float(removed.loc[removed.net_b>0,'net_b'].sum()),
           'removed_loss_net':float(removed.loc[removed.net_b<=0,'net_b'].sum()),
           'removed_mh_net':float(removed.loc[removed.reason_code_b=='MH','net_b'].sum()),
           'added_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum())}
        delta=row['full']['net_pnl']-br['full']['net_pnl']
        assert abs(delta-(-d['removed_winner_net']-d['removed_loss_net']+d['added_net']+d['common_delta']))<1e-7
        late_affected=cost.paired(f[f.exit_dt>='2026-01-01'],bf[bf.exit_dt>='2026-01-01'])[0]['affected']
        original_rejects=[not bool(s.filters[name][t.side][int(t.entry_bar)]) for t in bf.itertuples()]
        fails=[]
        for slip in [0,2,5]:
            r=s.cache[name,slip,False][2]
            for control in ['base','quality']:
                b=s.cache[control,slip,False][2]
                for period in ['full','early','late']:
                    for metric in ['net_pnl','wr']:
                        if r[period][metric]<=b[period][metric]+1e-8:fails.append(f'{control}:{slip}:{period}:{metric}')
                if r['full']['mdd']>b['full']['mdd']*1.1:fails.append(f'{control}:{slip}:mdd')
                if r['full']['worst30']<b['full']['worst30']*1.1:fails.append(f'{control}:{slip}:worst30')
        economic=list(fails)
        if pair['affected']<30:fails.append('full_affected<30')
        if late_affected<30:fails.append('late_affected<30')
        diagnostics[name]={'pair':pair,'late_affected':late_affected,'original_rejects':sum(original_rejects),
            'decomposition':d,'uncertainty':cost.uncertainty(eq,be),'failed_gates':fails,
            'status':('REJECTED' if economic else 'INSUFFICIENT_SAMPLE' if fails else 'NEEDS_FURTHER_VALIDATION') if name in MAIN else 'DIAGNOSTIC_CONTROL'}
    ordered=sorted(diagnostics,key=lambda n:diagnostics[n]['uncertainty']['one_sided_centered_block_p']);peak=0
    for rank,n in enumerate(ordered):
        peak=max(peak,diagnostics[n]['uncertainty']['one_sided_centered_block_p']*(len(ordered)-rank))
        diagnostics[n]['holm_p']=min(1,peak)
    comparisons=[]
    for control in ['price','flow','hour']:
        for slip in [0,2,5]:
            a=s.cache['pair',slip,False][2];b=s.cache[control,slip,False][2]
            comparisons.append({'control':control,'slip':slip,
                'net_delta':{p:a[p]['net_pnl']-b[p]['net_pnl'] for p in ['full','early','late']},
                'full_wr_delta':a['full']['wr']-b['full']['wr']})
    survivors=[n for n in MAIN if not diagnostics[n]['failed_gates']]
    result={'rows':rows,'diagnostics':diagnostics,'comparisons':comparisons,'buckets':buckets,
        'quality_audit':s.quality_audit,'unknown_baseline_labels':int((~annotated.valid).sum()),
        'checks':checks,'hashes':hashes,'survivors':survivors,'status':'NO_PROMOTION',
        'further_validation':'PENDING' if survivors else 'NOT_RUN_BASIC_GATES_FAILED','scenario_count':len(rows)}
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],'historical':r['historical'],**r['full']} for r in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'quality':s.quality_audit,'diagnostics':diagnostics,
        'flat0':[r for r in rows if not r['historical'] and r['slip']==0],
        'buckets':[q for q in buckets if q['period']=='full' and q['side']=='ALL']}),flush=True)

if __name__=='__main__':main()
