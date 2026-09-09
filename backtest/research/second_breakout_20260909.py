"""鎖定首次突破界線，完整重播等待／一次再進場。"""
from pathlib import Path
import inspect
import json
import numpy as np
import pandas as pd
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review

ROOT=cost.ROOT
OUT=ROOT/'data/second_breakout_20260909'
MAIN=['wait6','wait12','wait24','re12','re24','re48']
RULES=['base']+MAIN+['outside12','outside24','outside48']


class Episodes:
    def __init__(self,name):
        self.mode=name.rstrip('0123456789');self.limit=int(name[len(self.mode):] or 0)
        self.pending={};self.events=[]

    def log(self,i,side,event,ticket,**extra):
        self.events.append({'bar':i,'side':side,'event':event,**ticket,**extra})

    def closed(self,i,side,entry,ticket):
        if self.mode not in ['re','outside'] or ticket['kind']!='normal':return
        p={'origin':entry,'start':i,'boundary':ticket['boundary'],'inside':False,'inside_bar':-1}
        self.pending[side]=p;self.log(i,side,'created_after_exit',p)

    def step(self,i,side,safe,signal,boundary,close,blocks):
        p=self.pending.get(side)
        # 原策略新訊號優先；wait 則維持既有第一張票。
        if self.mode!='wait' and safe and signal:
            if p is not None:self.log(i,side,'replaced_by_normal',p);self.pending.pop(side)
            result={'kind':'normal','origin':i,'boundary':float(boundary)}
            self.log(i,side,'normal_entry',result)
            return result
        if p is not None:
            if i>p['start']+self.limit:
                self.log(i,side,'expired',p);self.pending.pop(side);return None
            if i<=p['start']:return None
            inside=close<=p['boundary'] if side=='L' else close>=p['boundary']
            if inside and not p['inside']:
                p['inside']=True;p['inside_bar']=i;self.log(i,side,'returned_inside',p)
            ready=not inside and (p['inside'] if self.mode!='outside' else safe)
            if ready:
                self.pending.pop(side)
                if not safe:self.log(i,side,'cross_blocked',p,blocks='|'.join(blocks));return None
                result={'kind':'wait' if self.mode=='wait' else 'retry','origin':p['origin'],'boundary':p['boundary']}
                self.log(i,side,'second_entry',p,kind=result['kind'])
                return result
            return None
        if self.mode=='wait' and safe and signal:
            if not np.isfinite(boundary):return None
            p={'origin':i,'start':i,'boundary':float(boundary),'inside':False,'inside_bar':-1}
            self.pending[side]=p;self.log(i,side,'created_wait',p)
        return None


def simulator(engine):
    src=inspect.getsource(engine.simulate_v14_detailed)
    def patch(a,b):
        nonlocal src
        assert src.count(a)==1,(a,src.count(a))
        src=src.replace(a,b)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None):',
          'realistic=False, slip_bps=0.0, margin_schedule=None, episodes=None):')
    for side,pos,lower,breakout in [('L','lp','l','up'),('S','sp','s','dn')]:
        old=f'''        if (not {pos}_active and not {lower}_cb and
            i - {lower}_last_exit >= {side}_CD and {lower}_m_entries < {side}_CAP and
            hr not in {side}_BLK_H and dw not in {side}_BLK_D and
            not (rblk_{lower} is not None and bool(rblk_{lower}[i])) and
            not np.isnan(p{side}[i]) and p{side}[i] < {side}_GK_TH and brk_{breakout}[i]):'''
        new=f'''        blocks = []
        if {pos}_active: blocks.append('occupied')
        if {lower}_cb: blocks.append('circuit_breaker')
        if i - {lower}_last_exit < {side}_CD: blocks.append('cooldown')
        if {lower}_m_entries >= {side}_CAP: blocks.append('monthly_cap')
        if hr in {side}_BLK_H or dw in {side}_BLK_D: blocks.append('session')
        if rblk_{lower} is not None and bool(rblk_{lower}[i]): blocks.append('regime')
        safe = not blocks
        signal = not np.isnan(p{side}[i]) and p{side}[i] < {side}_GK_TH and brk_{breakout}[i]
        decision = episodes.step(i,'{side}',safe,signal,ind['boundary_{side}'][i],ci,blocks)
        if decision is not None:
            {pos}_episode = decision'''
        patch(old,new)
        old=f"                    'margin': round({pos}_ntl / 20.0, 2),"
        patch(old,old+f"\n                    'qty_exact':{pos}_ntl/ep,'fee_exact':{pos}_fee,'entry_exact':ep,\n                    'entry_kind':{pos}_episode['kind'],'origin_bar':{pos}_episode['origin'],'locked_boundary':{pos}_episode['boundary'],")
        patch(f'                {pos}_active = False',f"                episodes.closed(i,'{side}',{pos}_bar,{pos}_episode)\n                {pos}_active = False")
    patch('    return trades',"    return trades, {'L':lp_active,'S':sp_active}")
    ns=dict(engine.__dict__);exec(src,ns)
    return ns['simulate_v14_detailed']


class Study:
    def __init__(self):
        self.source=cost.Study();self.d=self.source.d;self.engine=self.source.engine
        self.ind=self.engine.compute_indicators(self.d)
        self.ind['boundary_L']=self.d.close.shift(1).rolling(15).max().to_numpy()
        self.ind['boundary_S']=self.d.close.shift(1).rolling(15).min().to_numpy()
        self.fn=simulator(self.engine);self.cache={}

    def run(self,name,slip=0,hist=False,cut=None):
        ep=Episodes(name);d=self.d if cut is None else self.d.iloc[:cut]
        if cut is None:ind=self.ind
        else:
            ind=self.engine.compute_indicators(d)
            ind.update(boundary_L=d.close.shift(1).rolling(15).max().to_numpy(),boundary_S=d.close.shift(1).rolling(15).min().to_numpy())
        raw,active=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                          margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None,episodes=ep)
        f=review.normalize(pd.DataFrame(raw))
        if cut is not None:return f,ep.events
        assert not any(active.values()),active
        f,eq,ledger=cost.account(f,self.d,self.source.mark,self.source.fund)
        stem=f'{name}_{"hist" if hist else "flat"}_{slip}'
        f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
        ledger.to_csv(OUT/f'{stem}_funding.csv',index=False);pd.DataFrame(ep.events).to_csv(OUT/f'{stem}_events.csv',index=False)
        row={'name':name,'slip':slip,'historical':hist,'full':cost.metrics(f,eq),
             'early':cost.metrics(f,eq,end='2026-01-01'),'late':cost.metrics(f,eq,start='2026-01-01'),
             'side':{side:{'n':int((f.side==side).sum()),'net':float(f.loc[f.side==side,'net'].sum()),
                           'wr':float((f.loc[f.side==side,'net']>0).mean()*100)} for side in ['L','S']},
             'entry_kinds':{kind:{'n':len(q),'net':float(q.net.sum()),'wr':float((q.net>0).mean()*100)} for kind,q in f.groupby('entry_kind')},
             'funnel':pd.Series([e['event'] for e in ep.events]).value_counts().to_dict(),
             'pending_at_end':ep.pending}
        self.cache[name,slip,hist]=(f,eq,row,ep.events)
        return f,eq,row


def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),ROOT/'doc/second_breakout_plan_20260909.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
           ROOT/'data/maxhold_review_20260908/candles.csv',cost.HISTORY/'manifest.json']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'rules':RULES,'hashes':hashes},indent=2))
    s=Study();rows=[];checks=[];diagnostics={}
    for hist in [False,True]:
        for slip in [0,2,5]:
            for name in RULES:
                f,eq,row=s.run(name,slip,hist);rows.append(row)
                if name=='base':
                    orig=review.normalize(pd.DataFrame(s.engine.simulate_v14_detailed(s.ind,s.d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                        margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)))
                    pd.testing.assert_frame_equal(f[orig.columns],orig)
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'parity':[hist,slip],'status':'PASS'})
            print(json.dumps({'complete':[hist,slip]}),flush=True)
    for cut in [10000,16000]:
        for name in RULES:
            f,events=s.run(name,cut=cut);full=s.cache[name,0,False][0];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(f.reset_index(drop=True),full[f.columns].reset_index(drop=True))
            assert events==[e for e in s.cache[name,0,False][3] if e['bar']<cut]
        checks.append({'prefix':cut,'rules':len(RULES),'trades_and_events':'PASS'})
    survivors=[]
    for name in MAIN:
        f,eq,r,_=s.cache[name,0,False];bf,beq,_,_=s.cache['base',0,False]
        counts,pairs=cost.paired(f,bf);pairs.to_csv(OUT/f'{name}_paired.csv',index=False)
        removed=pairs[pairs['_merge']=='right_only'];new=pairs[pairs['_merge']=='left_only'];common=pairs[pairs['_merge']=='both']
        failed=[]
        for slip in [0,2,5]:
            cr=s.cache[name,slip,False][2];br=s.cache['base',slip,False][2]
            for period in ['full','early','late']:
                for metric in ['net_pnl','wr']:
                    if cr[period][metric]<=br[period][metric]:failed.append(f'{slip}/{period}/{metric}')
            if cr['full']['mdd']>br['full']['mdd']*1.1:failed.append(f'{slip}/mdd')
            if cr['full']['worst30']<br['full']['worst30']*1.1:failed.append(f'{slip}/worst30')
        if counts['affected']<30:failed.append('sample')
        if not failed:survivors.append(name)
        d={'paired':counts,'removed_winner_net':float(removed.loc[removed.net_b>0,'net_b'].sum()),
           'removed_loss_net':float(removed.loc[removed.net_b<=0,'net_b'].sum()),'new_net':float(new.net_c.sum()),
           'common_delta':float((common.net_c-common.net_b).sum()),'uncertainty':cost.uncertainty(eq,beq),
           'failures':failed,'status':'REJECTED' if failed else 'PENDING_WF'}
        delta=d['new_net']+d['common_delta']-d['removed_winner_net']-d['removed_loss_net']
        assert abs(delta-(r['full']['net_pnl']-s.cache['base',0,False][2]['full']['net_pnl']))<1e-7
        if name.startswith('re'):
            control=s.cache['outside'+name[2:],0,False][2]['full']
            d['outside_control_delta']={'net':r['full']['net_pnl']-control['net_pnl'],'wr':r['full']['wr']-control['wr']}
        diagnostics[name]=d
    peak=0
    for i,name in enumerate(sorted(diagnostics,key=lambda k:diagnostics[k]['uncertainty']['one_sided_centered_block_p'])):
        peak=max(peak,(6-i)*diagnostics[name]['uncertainty']['one_sided_centered_block_p']);diagnostics[name]['holm_p']=min(1,peak)
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    result={'hashes':hashes,'rows':rows,'checks':checks,'diagnostics':diagnostics,'survivors':survivors}
    (OUT/'results.json').write_text(json.dumps(result,indent=2))
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],'historical':r['historical'],**r['full']} for r in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'survivors':survivors,'flat0':[{'name':r['name'],**r['full'],'kinds':r['entry_kinds'],'funnel':r['funnel']} for r in rows if not r['historical'] and r['slip']==0]}),flush=True)


if __name__=='__main__':main()
