"""固定界線失效診斷與1/2根確認出場，完整狀態重播。"""
from pathlib import Path
import inspect
import json
import numpy as np
import pandas as pd
import intrahour_entry_20260908 as intra
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review

ROOT=intra.ROOT
OUT=ROOT/'data/breakout_failure_20260908'


def inside(side,price,boundary):return price<=boundary if side=='L' else price>=boundary


class Failure:
    def __init__(self,boundaries,valid_entry,valid_bar,close,required):
        self.b=boundaries;self.ve=valid_entry;self.vb=np.asarray(valid_bar);self.c=np.asarray(close)
        self.required=required;self.counts={};self.events=[]

    def __call__(self,i,side,entry):
        key=(side,entry)
        if i<=entry:return False
        valid=bool(self.ve[entry] and self.vb[i] and np.isfinite(self.b[side][entry]))
        hit=valid and inside(side,self.c[i],self.b[side][entry])
        previous,last=self.counts.get(key,(0,i-1))
        count=(previous if last==i-1 else 0)+1 if hit else 0
        self.counts[key]=(count,i)
        triggered=count>=self.required
        if triggered:self.events.append({'side':side,'entry_bar':entry,'bar':i,'boundary':float(self.b[side][entry]),'close':float(self.c[i])})
        return triggered


def simulator(engine):
    src=inspect.getsource(engine.simulate_v14_detailed)
    def patch(a,b):
        nonlocal src
        assert src.count(a)==1,a
        src=src.replace(a,b)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None):','realistic=False, slip_bps=0.0, margin_schedule=None, failure=None):')
    for side,pos,mkt in [('L','lp','l_mkt'),('S','sp','s_mkt')]:
        sign='(ex_price - ep)' if side=='L' else '(ep - ex_price)'
        old=f'            if ex_price > 0:\n                pnl_pct = {sign} / ep'
        new=f"            if ex_price == 0 and failure is not None and failure(i, '{side}', {pos}_bar):\n                ex_price = {mkt}\n                ex_reason = 'FAIL'\n\n"+old
        patch(old,new)
        a=f"                    'margin': round({pos}_ntl / 20.0, 2),"
        patch(a,a+f"\n                    'qty_exact':{pos}_ntl/ep,'fee_exact':{pos}_fee,'entry_exact':ep,")
    patch('    return trades',"    return trades, {'L':lp_active,'S':sp_active}")
    ns=dict(engine.__dict__);exec(src,ns)
    return ns['simulate_v14_detailed']


class Study:
    def __init__(self):
        self.origin=intra.Study();self.d=self.origin.d;self.engine=self.origin.engine
        self.boundaries={'L':self.d.close.shift(1).rolling(15).max().to_numpy(),
                         'S':self.d.close.shift(1).rolling(15).min().to_numpy()}
        self.valid_entry=self.origin.filters['quality']['L'];self.valid_bar=self.origin.q.valid_5m_alignment.to_numpy()
        self.fn=simulator(self.engine);self.cache={}

    def run(self,required=0,slip=0,hist=False,cut=None):
        d=self.d if cut is None else self.d.iloc[:cut]
        callback=Failure(self.boundaries,self.valid_entry,self.valid_bar,self.d.close,required) if required else None
        raw,active=self.fn(self.engine.compute_indicators(d),d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                           margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None,failure=callback)
        f=review.normalize(pd.DataFrame(raw))
        if cut is not None:return f
        assert not any(active.values())
        f,eq,ledger=cost.account(f,self.d,self.origin.source.mark,self.origin.source.fund)
        name='base' if required==0 else f'fail{required}'
        stem=f'{name}_{"hist" if hist else "flat"}_{slip}'
        f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
        ledger.to_csv(OUT/f'{stem}_funding.csv',index=False)
        pd.DataFrame(callback.events if callback else []).to_csv(OUT/f'{stem}_events.csv',index=False)
        row={'name':name,'slip':slip,'historical':hist,'full':cost.metrics(f,eq),
             'early':cost.metrics(f,eq,end='2026-01-01'),'late':cost.metrics(f,eq,start='2026-01-01'),
             'fail_exits':int((f.reason_code=='FAIL').sum())}
        self.cache[required,slip,hist]=(f,eq,row)
        return f,eq,row


def diagnose(s,baseline):
    sub=s.origin.sub.close.to_numpy().reshape(len(s.d),12); closes=s.d.close.to_numpy();fund=s.origin.source.fund
    rows=[]
    for t in baseline.itertuples():
        a,b=int(t.entry_bar),int(t.exit_bar);boundary=s.boundaries[t.side][a]
        end=b if t.reason_code!='SN' else b-1
        indices=np.arange(a+1,end+1)
        valid=s.valid_entry[a] and bool(s.valid_bar[indices].all())
        result={'side':t.side,'entry_bar':a,'exit_bar':b,'entry_dt':str(t.entry_dt),'reason':t.reason_code,
                'group':'MH' if t.reason_code=='MH' else ('WIN' if t.net>0 else 'OTHER_LOSS'),
                'net':t.net,'boundary':boundary,'valid':valid,'sn_exit_hour_excluded':t.reason_code=='SN'}
        if not valid or len(indices)==0:rows.append(result);continue
        h=np.array([inside(t.side,closes[i],boundary) for i in indices])
        five=np.array([inside(t.side,x,boundary) for x in sub[indices].ravel()])
        result.update(ever_1h=bool(h.any()),ever_5m=bool(five.any()))
        for required in [1,2]:
            count=0;first=None
            for i,hit in zip(indices,h):
                count=count+1 if hit else 0
                if count>=required:first=int(i);break
            result[f'first_{required}']=first
            early=first is not None and first<b
            result[f'early_{required}']=early
            if early:
                time=s.d.datetime.iloc[first]+pd.Timedelta(hours=1)
                sign=1 if t.side=='L' else -1
                flows=fund[(fund.nominal>t.entry_dt)&(fund.nominal<=time)]
                funding=float((-sign*t.qty_exact*flows.markPrice*flows.fundingRate).sum())
                hypothetical=round(sign*(closes[first]-t.entry_exact)*t.qty_exact-t.fee_exact,2)+funding
                result[f'exit_now_net_{required}']=hypothetical
                result[f'hold_minus_exit_{required}']=t.net-hypothetical
        rows.append(result)
    f=pd.DataFrame(rows);f.to_csv(OUT/'baseline_failure_paths.csv',index=False)
    groups=[]
    for side in ['ALL','L','S']:
        ss=f if side=='ALL' else f[f.side==side]
        for group in ['MH','WIN','OTHER_LOSS']:
            q=ss[ss.group==group];v=q[q.valid]
            groups.append({'side':side,'group':group,'n':len(q),'valid':len(v),
                           'ever_1h':int(v.ever_1h.fillna(False).sum()),'ever_5m':int(v.ever_5m.fillna(False).sum()),
                           'early_1':int(v.early_1.fillna(False).sum()),'early_2':int(v.early_2.fillna(False).sum())})
    conditional=[]
    for required in [1,2]:
        for side in ['ALL','L','S']:
            ss=f if side=='ALL' else f[f.side==side]
            for period in ['all','early','late']:
                q=ss[ss[f'early_{required}'].fillna(False)]
                if period=='early':q=q[pd.to_datetime(q.entry_dt)<'2026-01-01']
                if period=='late':q=q[pd.to_datetime(q.entry_dt)>='2026-01-01']
                delta=q[f'hold_minus_exit_{required}']
                conditional.append({'confirmation':required,'side':side,'period':period,'n':len(q),
                                    'original_net':float(q.net.sum()),'exit_now_net':float(q[f'exit_now_net_{required}'].sum()),
                                    'hold_minus_exit':float(delta.sum()),'hold_minus_exit_avg':float(delta.mean()) if len(q) else None,
                                    'hold_better_n':int((delta>0).sum())})
    pd.DataFrame(groups).to_csv(OUT/'groups.csv',index=False)
    pd.DataFrame(conditional).to_csv(OUT/'conditional.csv',index=False)
    return groups,conditional


def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),ROOT/'doc/breakout_failure_plan_20260908.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
           ROOT/'data/maxhold_review_20260908/candles.csv',ROOT/'data/eth_5m_monthly_20260908/ETHUSDT_5m_full.csv']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'hashes':hashes,'rules':[1,2]},indent=2))
    s=Study();rows=[];checks=[]
    for hist in [False,True]:
        for slip in [0,2,5]:
            for required in [0,1,2]:
                f,eq,row=s.run(required,slip,hist);rows.append(row)
                if required==0:
                    original=pd.DataFrame(s.engine.simulate_v14_detailed(s.engine.compute_indicators(s.d),s.d.datetime.to_numpy(),
                        realistic=True,slip_bps=slip,margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None))
                    norm=review.normalize(original)
                    pd.testing.assert_frame_equal(f[norm.columns],norm)
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'baseline':[hist,slip],'result':'PASS'})
            print(json.dumps({'margin':hist,'slip':slip,'complete':True}),flush=True)
    for cut in [10000,16000]:
        for required in [0,1,2]:
            small=s.run(required,cut=cut);full=s.cache[required,0,False][0];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(small.reset_index(drop=True),full[small.columns].reset_index(drop=True))
        checks.append({'cut':cut,'rules':3,'result':'PASS'})
    basef,baseeq,_=s.cache[0,0,False];groups,conditional=diagnose(s,basef)
    diagnostics={};survivors=[]
    for required in [1,2]:
        f,eq,_=s.cache[required,0,False];counts,pairs=cost.paired(f,basef);pairs.to_csv(OUT/f'fail{required}_paired.csv',index=False)
        common=pairs[pairs['_merge']=='both'];mh=common[common.reason_code_b=='MH'];win=common[common.net_b>0]
        failures=[]
        for slip in [0,2,5]:
            candidate=s.cache[required,slip,False][2];b=s.cache[0,slip,False][2]
            for period in ['full','early','late']:
                if candidate[period]['net_pnl']<=b[period]['net_pnl']:failures.append(f'{slip}:{period}:pnl')
            if candidate['full']['mdd']>b['full']['mdd']*1.1:failures.append(f'{slip}:mdd')
            if candidate['full']['worst30']<b['full']['worst30']*1.1:failures.append(f'{slip}:worst30')
        if not failures:survivors.append(required)
        diagnostics[str(required)]={'paired':counts,'uncertainty':cost.uncertainty(eq,baseeq),
            'same_entry_old_mh_delta':float((mh.net_c-mh.net_b).sum()),'same_entry_old_winner_delta':float((win.net_c-win.net_b).sum()),
            'failures':failures,'status':'REJECTED' if failures else 'PENDING_WF'}
    order=sorted(diagnostics,key=lambda k:diagnostics[k]['uncertainty']['one_sided_centered_block_p']);peak=0
    for i,k in enumerate(order):
        peak=max(peak,(2-i)*diagnostics[k]['uncertainty']['one_sided_centered_block_p']);diagnostics[k]['holm_p']=min(1,peak)
    result={'hashes':hashes,'rows':rows,'groups':groups,'conditional':conditional,'diagnostics':diagnostics,
            'checks':checks,'economic_survivors':survivors,'wf_stress_pending':survivors}
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2))
    pd.DataFrame([{'name':x['name'],'slip':x['slip'],'historical':x['historical'],'fail_exits':x['fail_exits'],**x['full']} for x in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'groups':groups,'conditional':conditional[:3],'diagnostics':diagnostics,'survivors':survivors}),flush=True)


if __name__=='__main__':main()
