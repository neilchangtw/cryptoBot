"""固定5/15/30m接受度比較，沿用原1h交易引擎與完整成本帳本。"""
from pathlib import Path
import inspect
import json
import numpy as np
import pandas as pd
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/intrahour_entry_20260908'
RULES=[f'm{m}_w{w}' for m in [5,15,30] for w in [30,60]]
NAMES=['base','quality']+RULES


def masks(d,sub,quality):
    expected=pd.date_range(d.datetime.iloc[0],d.datetime.iloc[-1]+pd.Timedelta(minutes=55),freq='5min')
    assert pd.DatetimeIndex(sub.datetime).equals(expected)
    closes=sub.close.to_numpy().reshape(len(d),12)
    valid=pd.Series(np.asarray(quality,bool)).rolling(16,min_periods=16).min().fillna(0).astype(bool).to_numpy()
    hi=d.close.shift(1).rolling(15).max().to_numpy()
    lo=d.close.shift(1).rolling(15).min().to_numpy()
    out={'base':{'L':np.ones(len(d),bool),'S':np.ones(len(d),bool)},'quality':{'L':valid,'S':valid}}
    for minute in [5,15,30]:
        # 子K聚合只需收盤；15m取第3/6/9/12根，30m取第6/12根。
        c=closes[:,minute//5-1::minute//5]
        for window in [30,60]:
            last=c[:,-window//minute:]
            out[f'm{minute}_w{window}']={'L':valid&(last>hi[:,None]).all(axis=1),
                                         'S':valid&(last<lo[:,None]).all(axis=1)}
    return out


def simulator(engine):
    src=inspect.getsource(engine.simulate_v14_detailed)
    def patch(a,b):
        nonlocal src
        assert src.count(a)==1,a
        src=src.replace(a,b)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None,\n'
          '                          extra_cost=0.0):',
          'realistic=False, slip_bps=0.0, margin_schedule=None,\n'
          '                          extra_cost=0.0, gate=None):')
    patch('and brk_up[i]):',"and brk_up[i] and (gate is None or gate(i,'L'))):")
    patch('and brk_dn[i]):',"and brk_dn[i] and (gate is None or gate(i,'S'))):")
    for pos in ['lp','sp']:
        a=f"                    'margin': round({pos}_ntl / 20.0, 2),"
        patch(a,a+f"\n                    'qty_exact':{pos}_ntl/ep,'fee_exact':{pos}_fee,'entry_exact':ep,")
    patch('    return trades',"    return trades, {'L':lp_active,'S':sp_active}")
    ns=dict(engine.__dict__);exec(src,ns)
    return ns['simulate_v14_detailed']


class Study:
    def __init__(self):
        self.source=cost.Study();self.d=self.source.d;self.engine=self.source.engine
        folder=ROOT/'data/eth_5m_monthly_20260908'
        manifest=json.loads((folder/'manifest.json').read_text())
        assert cost.sha(folder/'ETHUSDT_5m_full.csv')==manifest['csv_sha256']
        self.sub=pd.read_csv(folder/'ETHUSDT_5m_full.csv',parse_dates=['datetime'])
        self.q=pd.read_csv(folder/'hour_quality.csv',parse_dates=['datetime'])
        assert self.q.datetime.equals(self.d.datetime)
        self.filters=masks(self.d,self.sub,self.q.valid_5m_alignment)
        self.fn=simulator(self.engine);self.ind=self.engine.compute_indicators(self.d);self.cache={}

    def run(self,name,slip=0,hist=False,policy=None,cut=None,save=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        events=[]
        def gate(i,side):
            rule=name if policy is None else policy[i]
            allowed=bool(self.filters[rule][side][i])
            events.append({'bar':i,'side':side,'rule':rule,'allowed':allowed})
            return allowed
        raw,active=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                           margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None,gate=gate)
        f=review.normalize(pd.DataFrame(raw))
        if cut is not None:return f
        assert not any(active.values()),'Open terminal position requires extended accounting'
        f,eq,funding=cost.account(f,self.d,self.source.mark,self.source.fund)
        row={'name':name,'slip':slip,'historical':hist,'full':cost.metrics(f,eq),
             'early':cost.metrics(f,eq,end='2026-01-01'),'late':cost.metrics(f,eq,start='2026-01-01'),
             'side':{s:{'n':int((f.side==s).sum()),'net':float(f.loc[f.side==s,'net'].sum())} for s in ['L','S']},
             'eligible_checks':len(events),'rejected_checks':sum(not x['allowed'] for x in events)}
        if save:
            stem=f'{name}_{"hist" if hist else "flat"}_{slip}'
            f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
            funding.to_csv(OUT/f'{stem}_funding.csv',index=False);pd.DataFrame(events).to_csv(OUT/f'{stem}_events.csv',index=False)
        self.cache[name,slip,hist]=(f,eq,row)
        return f,eq,row


def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),ROOT/'doc/intrahour_entry_plan_20260908.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
           ROOT/'data/maxhold_review_20260908/candles.csv',ROOT/'data/eth_5m_monthly_20260908/ETHUSDT_5m_full.csv',
           ROOT/'data/eth_5m_monthly_20260908/hour_quality.csv']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'rules':RULES,'hashes':hashes},indent=2))
    s=Study();rows=[];checks=[];diagnostics={}
    for hist in [False,True]:
        for slip in [0,2,5]:
            for name in NAMES:
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
                    checks.append({'baseline_parity':[hist,slip],'result':'PASS'})
                if name=='m30_w30':
                    pd.testing.assert_frame_equal(f,s.cache['quality',slip,hist][0])
                    checks.append({'redundant_control':[hist,slip],'result':'PASS'})
            print(json.dumps({'completed_margin':hist,'slip':slip}),flush=True)
    for cut in [10000,16000]:
        fm=masks(s.d.iloc[:cut],s.sub.iloc[:cut*12],s.q.valid_5m_alignment.iloc[:cut])
        for name in NAMES:
            for side in ['L','S']:assert np.array_equal(fm[name][side],s.filters[name][side][:cut])
            small=s.run(name,cut=cut,save=False)
            full=s.cache[name,0,False][0];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(small.reset_index(drop=True),full[small.columns].reset_index(drop=True))
        checks.append({'prefix':cut,'rules':len(NAMES),'result':'PASS'})
    basef,baseeq,_=s.cache['quality',0,False]
    for name in RULES:
        f,eq,row=s.cache[name,0,False]
        counts,paired=cost.paired(f,basef)
        paired.to_csv(OUT/f'{name}_paired.csv',index=False)
        removed=paired[paired['_merge']=='right_only']; added=paired[paired['_merge']=='left_only']; common=paired[paired['_merge']=='both']
        monthly=(eq.set_index('time').equity-baseeq.set_index('time').equity).resample('ME').last().diff()
        monthly.iloc[0]=(eq.set_index('time').equity-baseeq.set_index('time').equity).resample('ME').last().iloc[0]
        diagnostics[name]={'paired':counts,'uncertainty':cost.uncertainty(eq,baseeq),
            'removed_mh_net':float(removed.loc[removed.reason_code_b=='MH','net_b'].sum()),
            'removed_other_loss':float(removed.loc[(removed.reason_code_b!='MH')&(removed.net_b<=0),'net_b'].sum()),
            'removed_winner_net':float(removed.loc[removed.net_b>0,'net_b'].sum()),
            'new_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum()),
            'delta_without_best_month':float(monthly.sum()-monthly.max())}
    ordered=sorted(RULES,key=lambda n:diagnostics[n]['uncertainty']['one_sided_centered_block_p'])
    peak=0
    for i,n in enumerate(ordered):
        peak=max(peak,diagnostics[n]['uncertainty']['one_sided_centered_block_p']*(len(RULES)-i))
        diagnostics[n]['holm_p']=min(1,peak)
    choices=[];policy=np.full(len(s.d),'quality',dtype=object)
    for a,z in zip(cost.FOLDS[:-1],cost.FOLDS[1:]):
        cutoff=a-pd.Timedelta(hours=48); bt=basef[basef.exit_dt<cutoff];eligible=[];training=[]
        for name in RULES:
            ft=s.cache[name,0,False][0];ft=ft[ft.exit_dt<cutoff]
            affected=cost.paired(ft,bt)[0]['affected'] if len(bt) and len(ft) else 0
            delta=float(ft.net.sum()-bt.net.sum())
            training.append({'name':name,'affected':affected,'net_delta':delta})
            if len(bt)>=100 and affected>=30 and delta>0:eligible.append((delta,name))
        chosen=max(eligible)[1] if eligible else 'quality'
        policy[(s.d.datetime+pd.Timedelta(hours=1)>=a).to_numpy()]=chosen
        choices.append({'start':str(a),'end':str(z),'baseline_training':len(bt),'chosen':chosen,'training':training})
    wf=[]
    for hist in [False,True]:
        for slip in [0,2,5]:
            f,eq,row=s.run('wf',slip,hist,policy=policy);qf,qe,_=s.cache['quality',slip,hist]
            folds=[]
            for a,z in zip(cost.FOLDS[:-1],cost.FOLDS[1:]):
                delta=cost.metrics(f,eq,a,z)['net_pnl']-cost.metrics(qf,qe,a,z)['net_pnl']
                folds.append({'start':str(a),'end':str(z),'delta':delta})
            wf.append({'slip':slip,'historical':hist,'full':row['full'],'folds':folds,'paired':cost.paired(f,qf)[0]})
    wfsmall=s.run('wf',policy=policy,cut=16000,save=False)
    wffull=s.cache['wf',0,False][0];wffull=wffull[wffull.exit_bar<16000]
    pd.testing.assert_frame_equal(wfsmall.reset_index(drop=True),wffull[wfsmall.columns].reset_index(drop=True))
    checks.append({'wf_prefix':16000,'result':'PASS'})
    qualified=[]
    for name in RULES:
        fails=[]
        for slip in [0,2,5]:
            row=s.cache[name,slip,False][2]
            for control in ['quality','base']:
                b=s.cache[control,slip,False][2]
                for period in ['full','early','late']:
                    if row[period]['net_pnl']<=b[period]['net_pnl']+1e-8:fails.append(f'{control}:{slip}:{period}:pnl')
                if row['full']['mdd']>b['full']['mdd']*1.1:fails.append(f'{control}:{slip}:mdd')
                if row['full']['worst30']<b['full']['worst30']*1.1:fails.append(f'{control}:{slip}:worst30')
        if fails:status='CONTROL_NO_NEW_INFORMATION' if name=='m30_w30' else 'REJECTED'
        else:status='INCONCLUSIVE';qualified.append(name)
        diagnostics[name].update(status=status,failed_economic_gates=fails)
    # 合格才需要後續壓測；保留中斷點，不把未執行檢查誤稱通過。
    result={'rows':rows,'diagnostics':diagnostics,'wf_choices':choices,'wf':wf,'checks':checks,
            'economic_survivors':qualified,'quality_invalid_hours':int((~s.filters['quality']['L']).sum()),
            'hashes':hashes,'test_scenarios':48,'wf_scenarios':6,'stress_random_pending':qualified}
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    summary=[]
    for row in rows:
        summary.append({'name':row['name'],'slip':row['slip'],'historical':row['historical'],**row['full']})
    pd.DataFrame(summary).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'survivors':qualified,'diagnostics':diagnostics,'wf_choices':[x['chosen'] for x in choices]}),flush=True)


if __name__=='__main__':main()
