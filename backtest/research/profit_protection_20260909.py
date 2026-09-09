"""固定 1h 進場、子棒出場研究；不修改正式引擎。"""
from pathlib import Path
import inspect
import json
import numpy as np
import pandas as pd
import intrahour_entry_20260908 as intra
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review

ROOT=intra.ROOT
OUT=ROOT/'data/profit_protection_20260909'
RULES=['hourly','control']+[f'{family}{m}' for family in ['tp','protect','both'] for m in [5,15]]


def simulator(engine):
    src=inspect.getsource(engine.simulate_v14_detailed)
    def patch(a,b):
        nonlocal src
        assert src.count(a)==1,(a,src.count(a))
        src=src.replace(a,b)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None):',
          'realistic=False, slip_bps=0.0, margin_schedule=None, minute=60, family="hourly"):')
    patch('    sim_start = max(start_bar, WARMUP) if start_bar is not None else WARMUP',
          '    sim_start = max(start_bar, WARMUP*12+11) if start_bar is not None else WARMUP*12+11')
    patch('        hr = hours[i]', '''        endhour = i % 12 == 11
        valid = bool(ind['valid'][i])
        tick = valid and (i % 12 + 1) % (minute // 5) == 0
        tp_tick = endhour or (tick and family in ['tp','both'])
        protect_tick = endhour or (tick and family in ['protect','both'])
        sn_tick = endhour or (valid and family != 'hourly')
        sn_hi = hi if family == 'hourly' or not valid else ind['sub_h'][i]
        sn_lo = li if family == 'hourly' or not valid else ind['sub_l'][i]
        hr = hours[i]''')
    for pos in ['lp','sp']:
        patch(f'            {pos}_held += 1',f'            {pos}_held += int(endhour)')
        patch(f'                        {pos}_ext_bars += 1' if pos=='lp' else f'                    {pos}_ext_bars += 1',
              f'                        {pos}_ext_bars += int(endhour)' if pos=='lp' else f'                    {pos}_ext_bars += int(endhour)')
        a=f"                    'margin': round({pos}_ntl / 20.0, 2),"
        patch(a,a+f"\n                    'qty_exact':{pos}_ntl/ep,'fee_exact':{pos}_fee,'entry_exact':ep,")
    patch('            if li <= sn_lv:', '            if sn_tick and sn_lo <= sn_lv:')
    patch('sn_lv - (sn_lv - li) * L_SN_SLIP','sn_lv - (sn_lv - sn_lo) * L_SN_SLIP')
    patch('            elif hi >= ep * (1 + l_tp_eff):','            elif tp_tick and hi >= ep * (1 + l_tp_eff):')
    patch('                if lp_mfe >= L_MFE_ACT and (lp_mfe - cpnl) >= L_MFE_TR and bh >= 1:',
          '                if protect_tick and lp_mfe >= L_MFE_ACT and (lp_mfe - cpnl) >= L_MFE_TR:')
    patch('                    if bh == L_CMH_BAR and cpnl <= L_CMH_TH:', '                    if endhour and bh == L_CMH_BAR and cpnl <= L_CMH_TH:')
    patch('                        if bh >= mh:', '                        if endhour and bh >= mh:')
    patch('                        if li <= ep:', '                        if protect_tick and li <= ep:')
    patch('                        elif lp_ext_bars >= L_EXT:', '                        elif endhour and lp_ext_bars >= L_EXT:')
    patch('            if hi >= sn_lv:', '            if sn_tick and sn_hi >= sn_lv:')
    patch('sn_lv + (hi - sn_lv) * S_SN_SLIP','sn_lv + (sn_hi - sn_lv) * S_SN_SLIP')
    patch('            elif li <= ep * (1 - S_TP):','            elif tp_tick and li <= ep * (1 - S_TP):')
    patch('                    if bh >= s_mh_eff:', '                    if endhour and bh >= s_mh_eff:')
    patch('                    if hi >= ep:', '                    if protect_tick and hi >= ep:')
    patch('                    elif sp_ext_bars >= S_EXT:', '                    elif endhour and sp_ext_bars >= S_EXT:')
    for side,pos in [('l','lp'),('s','sp')]:
        patch(f'        if (not {pos}_active and not {side}_cb and',f'        if (endhour and not {pos}_active and not {side}_cb and')
        patch(f'i - {side}_last_exit >= {side.upper()}_CD',f'i//12 - {side}_last_exit//12 >= {side.upper()}_CD')
    src=src.replace('i < consec_end','i//12 < consec_end').replace('consec_end = i + CB_CONSEC_CD','consec_end = i//12 + CB_CONSEC_CD')
    patch('    return trades',"    return trades, {'L':lp_active,'S':sp_active}")
    ns=dict(engine.__dict__);exec(src,ns)
    return ns['simulate_v14_detailed']


def expand(d,sub,quality,engine):
    original=engine.compute_indicators(d)
    out={k:np.repeat(v,12) for k,v in original.items()}
    valid=np.repeat(np.asarray(quality,bool),12)
    # 失配小時中途不執行出口，也不讓不可信子棒污染 MFE。
    for field,key,fn in [('high','h',np.maximum.accumulate),('low','l',np.minimum.accumulate)]:
        a=sub[field].to_numpy().reshape(-1,12)
        cum=fn(a,axis=1).ravel()
        cum[~valid]=np.repeat(d.open.to_numpy(),12)[~valid]
        cum[11::12]=d[field].to_numpy()
        out[key]=cum
    out['o']=sub.open.to_numpy().copy();out['c']=sub.close.to_numpy().copy()
    out['c'][11::12]=d.close.to_numpy()
    out['valid']=valid;out['sub_h']=sub.high.to_numpy();out['sub_l']=sub.low.to_numpy()
    # normalize() 會加一小時，此處預先轉換為實際子棒收盤減一小時。
    times=(sub.datetime+pd.Timedelta(minutes=5)-pd.Timedelta(hours=1)).to_numpy()
    return out,times


class Study:
    def __init__(self):
        self.origin=intra.Study();self.d=self.origin.d;self.engine=self.origin.engine
        self.ind,self.times=expand(self.d,self.origin.sub,self.origin.q.valid_5m_alignment,self.engine)
        self.fn=simulator(self.engine);self.cache={}

    def run(self,name,slip=0,hist=False,cut=None):
        family=name.rstrip('015') if name not in ['hourly','control'] else name
        minute=int(name[len(family):]) if name not in ['hourly','control'] else 60
        ind=self.ind if cut is None else {k:v[:cut*12] for k,v in self.ind.items()}
        times=self.times if cut is None else self.times[:cut*12]
        # 排程沿用原訊號小時日期，不使用編碼後的子棒日期。
        raw,active=self.fn(ind,times,realistic=True,slip_bps=slip,minute=minute,family=family,
                          margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)
        f=review.normalize(pd.DataFrame(raw))
        for col in ['entry_bar','exit_bar']:f[col]=f[col]//12
        if cut is not None:return f
        assert not any(active.values()),active
        f,eq,ledger=cost.account(f,self.d,self.origin.source.mark,self.origin.source.fund)
        row={'name':name,'slip':slip,'historical':hist,
             'full':cost.metrics(f,eq),'early':cost.metrics(f,eq,end='2026-01-01'),
             'late':cost.metrics(f,eq,start='2026-01-01'),
             'invalid_position_hours':sum(not self.origin.q.valid_5m_alignment.iloc[j] for t in f.itertuples() for j in range(t.entry_bar+1,t.exit_bar+1))}
        stem=f'{name}_{"hist" if hist else "flat"}_{slip}'
        f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
        ledger.to_csv(OUT/f'{stem}_funding.csv',index=False)
        self.cache[name,slip,hist]=(f,eq,row)
        return f,eq,row


def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),ROOT/'doc/profit_protection_plan_20260909.md',ROOT/'strategy.py',ROOT/'executor.py',cost.base.ENGINE_PATH,
           ROOT/'data/maxhold_review_20260908/candles.csv',ROOT/'data/eth_5m_monthly_20260908/ETHUSDT_5m_full.csv']
    hashes={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'rules':RULES,'hashes':hashes},indent=2))
    s=Study();rows=[];checks=[];diagnostics={}
    for hist in [False,True]:
        for slip in [0,2,5]:
            for name in RULES:
                f,eq,row=s.run(name,slip,hist);rows.append(row)
                if name=='hourly':
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv',parse_dates=['entry_dt','exit_dt'])
                    for col in ['side','entry_dt','exit_dt','reason_code','bars_held','entry_bar','exit_bar']:
                        assert f[col].equals(old[col]),(hist,slip,col)
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'parity':[hist,slip],'status':'PASS'})
            print(json.dumps({'complete':[hist,slip]}),flush=True)
    for cut in [10000,16000]:
        # 指標也以截短行情重算，不只切現成陣列。
        ind,times=expand(s.d.iloc[:cut],s.origin.sub.iloc[:cut*12],s.origin.q.valid_5m_alignment.iloc[:cut],s.engine)
        for k in ind:np.testing.assert_equal(ind[k],s.ind[k][:cut*12])
        for name in RULES:
            f=s.run(name,cut=cut);full=s.cache[name,0,False][0];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(f.reset_index(drop=True),full[f.columns].reset_index(drop=True))
        checks.append({'prefix':cut,'rules':len(RULES),'status':'PASS'})
    survivors=[]
    for name in RULES[2:]:
        f,eq,row=s.cache[name,0,False];basef,baseeq,_=s.cache['hourly',0,False]
        counts,pairs=cost.paired(f,basef);pairs.to_csv(OUT/f'{name}_paired.csv',index=False)
        common=pairs[pairs['_merge']=='both']
        failed=[]
        for slip in [0,2,5]:
            r=s.cache[name,slip,False][2]
            for base in ['hourly','control']:
                b=s.cache[base,slip,False][2]
                for period in ['full','early','late']:
                    for metric in ['net_pnl','wr']:
                        if r[period][metric]<=b[period][metric]:failed.append(f'{base}/{slip}/{period}/{metric}')
                if r['full']['mdd']>b['full']['mdd']*1.1:failed.append(f'{base}/{slip}/mdd')
                if r['full']['worst30']<b['full']['worst30']*1.1:failed.append(f'{base}/{slip}/worst30')
        if counts['affected']<30:failed.append('sample')
        if not failed:survivors.append(name)
        diagnostics[name]={'paired':counts,'rescued':int(((common.net_b<=0)&(common.net_c>0)).sum()),
            'spoiled':int(((common.net_b>0)&(common.net_c<=0)).sum()),
            'old_winner_delta':float((common.loc[common.net_b>0,'net_c']-common.loc[common.net_b>0,'net_b']).sum()),
            'old_loss_delta':float((common.loc[common.net_b<=0,'net_c']-common.loc[common.net_b<=0,'net_b']).sum()),
            'uncertainty':cost.uncertainty(eq,baseeq),'failures':failed,'status':'REJECTED' if failed else 'PENDING_WF'}
    peak=0
    for i,name in enumerate(sorted(diagnostics,key=lambda k:diagnostics[k]['uncertainty']['one_sided_centered_block_p'])):
        peak=max(peak,(6-i)*diagnostics[name]['uncertainty']['one_sided_centered_block_p'])
        diagnostics[name]['holm_p']=min(1,peak)
    result={'hashes':hashes,'rows':rows,'checks':checks,'diagnostics':diagnostics,'survivors':survivors}
    assert hashes=={str(p.relative_to(ROOT)):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2))
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],'historical':r['historical'],**r['full']} for r in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'survivors':survivors,'diagnostics':diagnostics}),flush=True)


if __name__=='__main__':main()
