"""鎖定計畫的離線完整成本、參數、進場確認與連續狀態WF研究。"""
from pathlib import Path
import hashlib
import inspect
import json
import subprocess
import numpy as np
import pandas as pd
import price_candle_trade_analysis as base
import maxhold_review_20260908 as review

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data'/'optimization_execution_20260908'
HISTORY=ROOT/'data'/'public_cost_history_20260908'
CONFIGS={'base':{},'tp25':{'tp':.025},'tp30':{'tp':.03},'tp35':{'tp':.035},
         'mh2':{'mh':2},'mh3':{'mh':3},'mh4':{'mh':4},
         'vol10':{'volume':1.},'vol15':{'volume':1.5},
         'adx20':{'adx':20},'adx25':{'adx':25},
         'retest1':{'mode':'retest','wait':1},'retest3':{'mode':'retest','wait':3},
         'delay1':{'mode':'delay','wait':1},'delay3':{'mode':'delay','wait':3}}
FAMILIES={'tp':['tp25','tp30','tp35'],'mh':['mh2','mh3','mh4'],
          'volume':['vol10','vol15'],'adx':['adx20','adx25'],
          'retest':['retest1','retest3'],'delay_control':['delay1','delay3']}
FOLDS=pd.date_range('2025-09-01','2026-09-01',freq='2MS')

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def wilder(x,period=14,first=1):
    x=np.asarray(x,float); out=np.full(len(x),np.nan); seed=first+period-1
    if len(x)<=seed:return out
    out[seed]=np.mean(x[first:seed+1])
    for i in range(seed+1,len(x)): out[i]=(out[i-1]*(period-1)+x[i])/period
    return out

def features(d):
    h,l,c=d.high.to_numpy(),d.low.to_numpy(),d.close.to_numpy()
    prev=np.r_[np.nan,c[:-1]]
    tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
    up=np.r_[np.nan,np.diff(h)]; down=np.r_[np.nan,-np.diff(l)]
    plus=np.where((up>down)&(up>0),up,0.); minus=np.where((down>up)&(down>0),down,0.)
    atr=wilder(tr)
    with np.errstate(divide='ignore',invalid='ignore'):
        pdi=100*wilder(plus)/atr; mdi=100*wilder(minus)/atr
        dx=np.where(pdi+mdi>0,100*abs(pdi-mdi)/(pdi+mdi),0.)
    adx=wilder(dx,first=14)
    return {'atr':atr,'adx':adx,'pdi':pdi,'mdi':mdi,
            'boundary':d.close.shift(1).rolling(15).min().to_numpy(),
            'volume':(d.volume/d.volume.shift(1).rolling(20).mean()).to_numpy()}

def entry_step(i,cfg,pending,safe,signal,hi,ci,boundary,atr,vol,adx,pdi,mdi):
    """pending保留訊號當下規則；取消／觸發當棒不另建新事件。"""
    if pending is not None:
        age=i-pending['bar']; pcfg=pending['cfg']; wait=pcfg['wait']
        if age>wait:return False,None,cfg,'expired'
        if pcfg['mode']=='retest':
            if ci>=pending['boundary']:return False,None,cfg,'invalidated'
            ready=hi>=pending['boundary']-.1*pending['atr'] and ci<pending['boundary']
        else:ready=age==wait
        if age>=1 and ready:return safe,None,pcfg,'filled' if safe else 'safety_rejected'
        if age==wait:return False,None,cfg,'expired'
        return False,pending,cfg,'waiting'
    if not(safe and signal):return False,None,cfg,'no_signal'
    if 'volume' in cfg and not vol>=cfg['volume']:return False,None,cfg,'volume_rejected'
    if 'adx' in cfg and not(adx>=cfg['adx'] and mdi>pdi):return False,None,cfg,'adx_rejected'
    if 'mode' in cfg:
        if not np.isfinite(atr+boundary):return False,None,cfg,'feature_missing'
        return False,{'bar':i,'boundary':boundary,'atr':atr,'cfg':dict(cfg)},cfg,'pending_created'
    return True,None,cfg,'filled'

def build_simulator(engine):
    src=inspect.getsource(engine.simulate_v14_detailed)
    def patch(old,new):
        nonlocal src
        assert src.count(old)==1,old[:90]
        src=src.replace(old,new)
    patch('    sp_regime = "NA"\n','    sp_regime = "NA"\n    sp_tp = S_TP\n    sp_mh = S_MH\n    pending = None\n    entry_policy = "base"\n    events = []\n')
    patch('            s_mh_eff = _S_MH_BR.get(sp_regime, S_MH)','            s_mh_eff = sp_mh')
    patch('            elif li <= ep * (1 - S_TP):','            elif li <= ep * (1 - sp_tp):')
    patch('                ex_price = s_mkt if realistic else ep * (1 - S_TP)','                ex_price = s_mkt if realistic else ep * (1 - sp_tp)')
    old='''        if (not sp_active and not s_cb and
            i - s_last_exit >= S_CD and s_m_entries < S_CAP and
            hr not in S_BLK_H and dw not in S_BLK_D and
            not (rblk_s is not None and bool(rblk_s[i])) and
            not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):'''
    new='''        safe = (not sp_active and not s_cb and
                i - s_last_exit >= S_CD and s_m_entries < S_CAP and
                hr not in S_BLK_H and dw not in S_BLK_D and
                not (rblk_s is not None and bool(rblk_s[i])))
        signal = not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]
        cfg = ind['configs'][int(ind['policy'][i])]
        enter, pending, entry_cfg, event = entry_step(
            i, cfg, pending, safe, signal, hi, ci, ind['boundary'][i],
            ind['atr'][i], ind['volume'][i], ind['adx'][i], ind['pdi'][i], ind['mdi'][i])
        if event not in ['no_signal', 'waiting']:
            events.append({'bar':i,'event':event,'policy':cfg.get('name','base')})
        if enter:'''
    patch(old,new)
    patch('            sp_regime = _classify_regime(slope[i]) if slope is not None else "NA"',
          '            sp_regime = _classify_regime(slope[i]) if slope is not None else "NA"\n'
          '            sp_tp = entry_cfg.get("tp", S_TP) if sp_regime == "DOWN" else S_TP\n'
          '            sp_mh = _S_MH_BR.get(sp_regime, S_MH) + entry_cfg.get("mh", 0)\n'
          '            entry_policy = entry_cfg.get("name", "base")')
    patch("                    'margin': round(lp_ntl / 20.0, 2),", "                    'margin': round(lp_ntl / 20.0, 2),\n                    'qty_exact':lp_ntl/ep,'fee_exact':lp_fee,'entry_exact':ep,'policy':'L_baseline',")
    patch("                    'margin': round(sp_ntl / 20.0, 2),", "                    'margin': round(sp_ntl / 20.0, 2),\n                    'qty_exact':sp_ntl/ep,'fee_exact':sp_fee,'entry_exact':ep,'policy':entry_policy,'locked_tp':sp_tp,'locked_mh':sp_mh,")
    patch('    return trades', "    return trades, {'L_active':bool(lp_active),'S_active':bool(sp_active)}, events")
    ns=dict(engine.__dict__); ns['entry_step']=entry_step; exec(src,ns)
    return ns['simulate_v14_detailed']

def dd(eq):
    a=np.r_[0.,np.asarray(eq,float)]
    return float((np.maximum.accumulate(a)-a).max())

def account(trades,d,mark,fund):
    n=len(d); cash=np.zeros(n); floating=np.zeros(n); funding=np.zeros(n)
    times=pd.DatetimeIndex(d.datetime+pd.Timedelta(hours=1)); ft=fund.nominal.to_numpy()
    fidx=times.get_indexer(fund.nominal); assert (fidx>=0).all()
    amounts=[]; low=[]; high=[]; ledger=[]
    for j,t in enumerate(trades.itertuples()):
        a,b=int(t.entry_bar),int(t.exit_bar); q=t.qty_exact; fee=t.fee_exact
        cash[a]-=fee/2; cash[b]+=t.pnl+fee/2
        sign=1 if t.side=='L' else -1
        floating[a:b]+=sign*q*(mark.close.to_numpy()[a:b]-t.entry_exact)
        mask=(ft>=t.entry_dt.to_datetime64())&(ft<=t.exit_dt.to_datetime64())
        total=lo=hi=0.
        for k in np.flatnonzero(mask):
            nominal=fund.nominal.iloc[k]; value=-sign*q*fund.markPrice.iloc[k]*fund.fundingRate.iloc[k]
            boundary=nominal in [t.entry_dt,t.exit_dt]
            # 整點前已持倉、結算後才市價出場；SN盤中平倉通常不跨出場收盤結算。
            include=nominal>t.entry_dt and (nominal<t.exit_dt or t.reason_code!='SN')
            flow=value if include else 0.
            funding[fidx[k]]+=flow; total+=flow
            lo+=min(0,value) if boundary else flow; hi+=max(0,value) if boundary else flow
            ledger.append({'trade_id':j,'time':nominal,'side':t.side,'cashflow':flow,
                           'possible_cashflow':value,'boundary':boundary,'rate':fund.fundingRate.iloc[k],
                           'markPrice':fund.markPrice.iloc[k],'included':include})
        amounts.append(total); low.append(lo); high.append(hi)
    f=trades.copy(); f['funding']=amounts; f['funding_low']=low; f['funding_high']=high; f['net']=f.pnl+f.funding
    eq=pd.DataFrame({'time':times,'trading_cash':cash.cumsum(),'funding_cash':funding.cumsum(),'unrealized_mark':floating})
    eq['cash']=eq.trading_cash+eq.funding_cash; eq['equity']=eq.cash+eq.unrealized_mark
    assert abs(eq.cash.iloc[-1]-f.net.sum())<1e-7
    assert abs(eq.unrealized_mark.iloc[-1])<1e-8
    return f,eq,pd.DataFrame(ledger)

def metrics(f,eq,start=None,end=None):
    q=eq
    before=0.
    if start is not None:
        earlier=eq[eq.time<pd.Timestamp(start)]
        before=float(earlier.equity.iloc[-1]) if len(earlier) else 0.
        q=q[q.time>=pd.Timestamp(start)]; f=f[f.exit_dt>=pd.Timestamp(start)]
    if end is not None:q=q[q.time<pd.Timestamp(end)]; f=f[f.exit_dt<pd.Timestamp(end)]
    equity=q.equity.to_numpy()-before
    losses=-f.loc[f.net<0,'net'].sum()
    s=pd.Series(equity,index=pd.DatetimeIndex(q.time))
    # 只使用完整720小時窗口，不以部分月份充數。
    roll=s-s.shift(720)
    return {'n':len(f),'net_pnl':float(equity[-1]) if len(equity) else 0.,'closed_net':float(f.net.sum()),
            'funding':float(f.funding.sum()),'pf':float(f.loc[f.net>0,'net'].sum()/losses) if losses else None,
            'wr':float((f.net>0).mean()*100) if len(f) else None,'mdd':dd(equity),
            'worst30':float(roll.min()) if roll.notna().any() else None,'mh':int(f.reason_code.eq('MH').sum()),
            'mh_net':float(f.loc[f.reason_code.eq('MH'),'net'].sum()),'sn':int(f.reason_code.eq('SN').sum()),
            'worst_trade':float(f.net.min()) if len(f) else None,'hours':int(f.bars_held.sum())}

def paired(candidate,baseline):
    m=candidate.merge(baseline,on=['side','entry_dt'],how='outer',suffixes=('_c','_b'),indicator=True)
    common=m[m['_merge'].eq('both')]
    changed=(common.exit_dt_c!=common.exit_dt_b)|(abs(common.net_c-common.net_b)>.005)
    orig_mh=common[common.reason_code_b.eq('MH')]
    counts={'common':len(common),'new':int(m['_merge'].eq('left_only').sum()),'removed':int(m['_merge'].eq('right_only').sum()),
            'changed_common':int(changed.sum()),'affected':int(changed.sum()+(m['_merge']!='both').sum()),
            'improved':int((common.net_c-common.net_b>.005).sum()),'worse':int((common.net_c-common.net_b<-.005).sum()),
            'old_mh_to_profit':int((orig_mh.net_c>0).sum()),'old_mh_reasons':orig_mh.reason_code_c.value_counts().to_dict(),
            'removed_winners':int(((m['_merge']=='right_only')&(m.net_b>0)).sum()),
            'winning_to_losing':int(((common.net_b>0)&(common.net_c<=0)).sum())}
    return counts,m

def uncertainty(eq,base_eq):
    daily=(eq.set_index('time').cash-base_eq.set_index('time').cash).resample('D').last().ffill()
    changes=daily.diff(); changes.iloc[0]=daily.iloc[0]
    a=changes.to_numpy(); rng=np.random.default_rng(20260908); n=len(a)
    starts=rng.integers(0,n,size=(2000,int(np.ceil(n/7))))
    indices=(starts[:,:,None]+np.arange(7))%n
    draws=a[indices.reshape(2000,-1)[:,:n]].sum(axis=1)
    centered=draws-a.sum()
    p=(1+int((centered>=a.sum()).sum()))/2001
    return {'delta':float(a.sum()),'block7_ci95':np.quantile(draws,[.025,.975]).tolist(),
            'one_sided_centered_block_p':float(p)}

class Study:
    def __init__(self):
        self.d=pd.read_csv(ROOT/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
        self.mark=pd.read_csv(HISTORY/'mark_1h_full.csv'); self.fund=pd.read_csv(HISTORY/'funding_full.csv')
        m=json.loads((HISTORY/'manifest.json').read_text())
        assert all(sha(HISTORY/k)==v for k,v in m['sha256'].items())
        assert sha(ROOT/'data/maxhold_review_20260908/candles.csv')==m['frozen_candles_sha256']
        self.fund['nominal']=pd.to_datetime(self.fund.fundingTime,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None).dt.round('h')
        assert self.fund.nominal.is_unique
        times=pd.to_datetime(self.mark.open_time,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
        assert times.astype('datetime64[ns]').equals(self.d.datetime.astype('datetime64[ns]'))
        self.engine=base.load_engine(); self.fn=build_simulator(self.engine)
        self.ind=self.engine.compute_indicators(self.d); self.ind.update(features(self.d)); self.cache={}

    def run(self,name='base',slip=0,historical=False,policy=None,cut=None,save=True):
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=dict(self.ind) if cut is None else {**self.engine.compute_indicators(d),**features(d)}
        if policy is None: configs=[dict(CONFIGS[name],name=name)]; pol=np.zeros(len(d),dtype=int)
        else: configs,pol=policy; pol=pol[:len(d)]
        ind.update(configs=configs,policy=pol)
        raw,terminal,events=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                                    margin_schedule=base.MARGIN_SCHEDULE if historical else None)
        trades=review.normalize(pd.DataFrame(raw))
        if cut is not None:return trades
        assert not any(terminal.values()),'期末未平倉，需延伸帳本支援後再評分'
        f,eq,ledger=account(trades,d,self.mark,self.fund)
        key=f'{name}_{"hist" if historical else "flat"}_{slip}'
        if save:
            f.to_csv(OUT/f'{key}_trades.csv',index=False); eq.to_csv(OUT/f'{key}_equity.csv',index=False)
            ledger.to_csv(OUT/f'{key}_funding.csv',index=False); pd.DataFrame(events).to_csv(OUT/f'{key}_events.csv',index=False)
        row={'name':name,'slip':slip,'historical':historical,'full':metrics(f,eq),
             'pre2026':metrics(f,eq,end='2026-01-01'),'2026':metrics(f,eq,start='2026-01-01'),
             'recent':metrics(f,eq,start='2026-06-01'),
             'funding_low':float(f.funding_low.sum()),'funding_high':float(f.funding_high.sum())}
        self.cache[(name,slip,historical)]=(f,eq,row)
        return f,eq,row

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    tracked=[base.ENGINE_PATH,ROOT/'strategy.py',ROOT/'executor.py',ROOT/'doc/strategy_optimization_plan_20260908.md',
             ROOT/'data/maxhold_review_20260908/candles.csv',HISTORY/'funding_full.csv',HISTORY/'mark_1h_full.csv']
    hashes={str(p):sha(p) for p in tracked}
    study=Study(); allrows=[]; tests=[]
    # 原引擎與研究副本在三種成本、兩種保證金下逐欄核對。
    for hist in [False,True]:
        for slip in [0,2,5]:
            f,eq,row=study.run(slip=slip,historical=hist)
            original=review.normalize(pd.DataFrame(study.engine.simulate_v14_detailed(study.ind,study.d.datetime.to_numpy(),
                realistic=True,slip_bps=slip,margin_schedule=base.MARGIN_SCHEDULE if hist else None)))
            pd.testing.assert_frame_equal(f[original.columns],original)
            tests.append(f'baseline parity hist={hist} slip={slip}')
            allrows.append(row)
    for name in list(CONFIGS)[1:]:
        for hist in [False,True]:
            for slip in [0,2,5]:
                f,eq,row=study.run(name,slip,hist); allrows.append(row)
        f,eq,row=study.cache[(name,0,False)]
        print(name,round(row['full']['net_pnl'],2),'MH',row['full']['mh'],'MDD',round(row['full']['mdd'],2),flush=True)
    # 所有規則做三個截斷點，不讀未來指標或結果。
    for name in CONFIGS:
        full=study.cache[(name,0,False)][0]
        for cut in [6000,11000,16000]:
            short=study.run(name,cut=cut,save=False)
            expected=full[full.exit_dt<=study.d.datetime.iloc[cut-1]+pd.Timedelta(hours=1)].reset_index(drop=True)
            pd.testing.assert_frame_equal(short,expected[short.columns])
        tests.append(name+' prefix 6000/11000/16000')
    bf,be,br=study.cache[('base',0,False)]
    analyses=[]
    for name in list(CONFIGS)[1:]:
        f,eq,row=study.cache[(name,0,False)]
        pair,m=paired(f,bf); m.to_csv(OUT/f'{name}_paired.csv',index=False)
        u=uncertainty(eq,be)
        cost_pass=all(study.cache[(name,s,False)][2][period]['net_pnl']>study.cache[('base',s,False)][2][period]['net_pnl']
                      for s in [0,2,5] for period in ['pre2026','2026'])
        risk_pass=all(study.cache[(name,s,False)][2]['full']['mdd']<=study.cache[('base',s,False)][2]['full']['mdd']*1.1 and
                      study.cache[(name,s,False)][2]['full']['worst30']>=min(0,study.cache[('base',s,False)][2]['full']['worst30'])*1.1 for s in [0,2,5])
        analyses.append({'name':name,'pair':pair,'bootstrap':u,'historical_cost_gate':cost_pass,'risk_gate':risk_pass})
    # Holm校正同批14個非基準規則；bootstrap僅為歷史不確定性估計。
    order=sorted(analyses,key=lambda x:x['bootstrap']['one_sided_centered_block_p']); prev=0.
    for rank,a in enumerate(order):
        prev=max(prev,min(1.,(len(order)-rank)*a['bootstrap']['one_sided_centered_block_p']))
        a['bootstrap']['holm_p']=prev
    # 每段選擇只用當時已出場且早於48h embargo的交易。
    wf=[]
    for family,names in FAMILIES.items():
        cfgs=[dict(CONFIGS[n],name=n) for n in ['base']+names]
        policy=np.zeros(len(study.d),dtype=int); decisions=[]
        for start,end in zip(FOLDS[:-1],FOLDS[1:]):
            cutoff=start-pd.Timedelta(hours=48)
            trainbase=bf[bf.exit_dt<cutoff]
            choices=[]
            for name in names:
                f=study.cache[(name,0,False)][0]; train=f[f.exit_dt<cutoff]
                p,_=paired(train,trainbase)
                choices.append({'name':name,'train_net':float(train.net.sum()),'affected':p['affected']})
            # 最低受影響數未在原計畫明定；此處保守用同計畫30筆門檻。
            eligible=[x for x in choices if x['affected']>=30 and x['train_net']>trainbase.net.sum()]
            winner=max(eligible,key=lambda x:x['train_net'])['name'] if len(trainbase)>=100 and eligible else 'base'
            times=study.d.datetime+pd.Timedelta(hours=1)
            policy[(times>=start)&(times<end)]=['base',*names].index(winner)
            decisions.append({'start':str(start),'end':str(end),'cutoff':str(cutoff),'train_n':len(trainbase),
                              'winner':winner,'choices':choices,'sample_valid':len(trainbase)>=100 and any(x['affected']>=30 for x in choices)})
        # 末段選擇延用至資料結尾；不在9/1重新挑參數。
        policy[(study.d.datetime+pd.Timedelta(hours=1))>=FOLDS[-1]]=policy[np.flatnonzero((study.d.datetime+pd.Timedelta(hours=1))<FOLDS[-1])[-1]]
        results=[]
        for slip in [0,2,5]:
            f,eq,row=study.run('wf_'+family,slip,policy=(cfgs,policy))
            bfe,bee,_=study.cache[('base',slip,False)]
            folds=[]
            for dec in decisions:
                cm=metrics(f,eq,dec['start'],dec['end']); bm=metrics(bfe,bee,dec['start'],dec['end'])
                mask=lambda x:x[x.exit_dt.between(pd.Timestamp(dec['start']),pd.Timestamp(dec['end']),inclusive='left')]
                diff,_=paired(mask(f),mask(bfe))
                folds.append({'start':dec['start'],'delta':cm['net_pnl']-bm['net_pnl'],'affected':diff['affected'],
                              'candidate':cm,'baseline':bm})
            results.append({'slip':slip,'folds':folds,'evaluation':metrics(f,eq,FOLDS[0],FOLDS[-1])})
        # 混合policy序列也做截斷檢查，以驗證跨期狀態沒有重置。
        full=study.cache[('wf_'+family,0,False)][0]
        partial=study.run('wf_'+family,policy=(cfgs,policy),cut=16000,save=False)
        expected=full[full.exit_dt<=study.d.datetime.iloc[15999]+pd.Timedelta(hours=1)].reset_index(drop=True)
        pd.testing.assert_frame_equal(partial,expected[partial.columns])
        tests.append('WF '+family+' continuous-state prefix')
        wf.append({'family':family,'decisions':decisions,'results':results})
        print('WF',family,[d['winner'] for d in decisions],flush=True)
    # 只有兩個參數家族個別完成門檻才測交互作用；本輪不救失敗參數。
    for a in analyses:
        family=next(k for k,v in FAMILIES.items() if a['name'] in v)
        w=next(x for x in wf if x['family']==family)
        foldpass=all(sum(f['delta']>1e-8 and d['sample_valid'] for f,d in zip(r['folds'],w['decisions']))>=4 and
                     sum(f['delta'] for f in r['folds'])-max(f['delta'] for f in r['folds'])>0 and
                     sum(f['affected'] for f in r['folds'])>=30 for r in w['results'])
        a['wf_gate']=foldpass
        a['status']='REJECTED' if not(a['historical_cost_gate'] and a['risk_gate']) else 'INCONCLUSIVE'
        if a['historical_cost_gate'] and a['risk_gate'] and foldpass and a['bootstrap']['holm_p']<.05:
            a['status']='SHADOW_REVIEW'
    combo_allowed=all(any(a['name'] in FAMILIES[fam] and a['status']=='SHADOW_REVIEW' for a in analyses) for fam in ['tp','mh'])
    assert not combo_allowed,'交互作用前需檢查鄰域，停止以免跳過人工研究稽核'
    # 僅對歷史成本／風險較好的候選做最終10bp壓力測試，明確不改挑選規則。
    stress=[]
    for name in ['base']+[a['name'] for a in analyses if a['historical_cost_gate'] and a['risk_gate']]:
        _,_,r=study.run(name,10); stress.append(r)
    assert all(sha(Path(p))==h for p,h in hashes.items())
    payload={'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
             'inputs_sha256':hashes,'script_sha256':sha(Path(__file__)),'configs':CONFIGS,'tests':tests,
             'runs':allrows,'analysis':analyses,'walk_forward':wf,'stress10':stress,
             'combination':'SKIPPED: independent families not both qualified','production_inputs_unchanged':True,
             'wf_training_affected_minimum':30}
    (OUT/'results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE',OUT,flush=True)

if __name__=='__main__':main()
