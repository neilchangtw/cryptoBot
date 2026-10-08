import sys,pandas as pd,numpy as np,itertools,json
NOT=4000; COST=9.0; SLIP=0.0002; FUND_H=NOT*0.0001/8; PEN=0.25
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(SP,rev=False):
    e=pd.read_csv(SP+'/ETH_1h_long.csv'); b=pd.read_csv(SP+'/BTC_1h_long.csv')[['datetime','open','high','low','close']]
    d=e.merge(b,on='datetime',suffixes=('','_b')); d['dt']=pd.to_datetime(d.datetime)
    f=pd.read_csv(SP+'/funding_long.csv',parse_dates=['t']); f['dt']=f.t+pd.Timedelta(hours=8)
    f['fpct']=f.rate.rolling(271).apply(lambda s:(s[:-1]<s[-1]).mean()*100,raw=True)
    d=d.merge(f[['dt','fpct']],on='dt',how='left'); d['fpct']=d.fpct.ffill()
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy()
        for s in ('','_b'): r[['open'+s,'close'+s]]=r[['close'+s,'open'+s]].values
        r['dt']=d.dt.values; d=r
    F={k:d[k].values for k in ['open','high','low','close','volume','close_b','fpct']}
    F['dt']=d.dt.values; F['hour']=pd.to_datetime(d.dt).dt.hour.values
    c=pd.Series(F['close']); sma=c.rolling(200).mean(); F['slope']=((sma-sma.shift(100))/sma.shift(100)).shift(1).values
    F['cs']=c; F['cbs']=pd.Series(F['close_b'])
    return F
def run(F,d,H,TP,SL,exL=None,exS=None,rand=None,i0=400):
    o,h,l,c=F['open'],F['high'],F['low'],F['close']; n=len(c); out=[]; pos=None; i=i0
    while i<n-1:
        if pos is None:
            s=d[i] if rand is None else rand.get(i,(0,0))[0]
            if s!=0 and not np.isnan(c[i]):
                pos=dict(s=s,ep=o[i+1]*(1+SLIP*s),eb=i+1,hold=(rand[i][1] if rand is not None else H))
            i+=1; continue
        s=pos['s']; up=s==1; ep=pos['ep']; st=ep*(1-SL) if up else ep*(1+SL)
        if up and l[i]<=st: px=min(st,o[i])-PEN*(min(st,o[i])-l[i]); out.append((pos['eb'],i,s,px/ep-1)); pos=None; i+=1; continue
        if (not up) and h[i]>=st: px=max(st,o[i])+PEN*(h[i]-max(st,o[i])); out.append((pos['eb'],i,s,ep/px-1)); pos=None; i+=1; continue
        age=i-pos['eb']+1; mv=(c[i]/ep-1)*s
        x=age>=pos['hold'] or (TP and mv>=TP and rand is None)
        if rand is None and exL is not None: x=x or (exL[i] if up else exS[i])
        if x: px=o[i+1]*(1-SLIP*s); out.append((pos['eb'],i+1,s,(px/ep-1)*s)); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','s','ret'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','s','ret','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST-FUND_H*t.hold
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
def mdd(t):
    c=t.sort_values('exit_dt').pnl.cumsum(); return (c.cummax()-c).max() if len(c) else 0
def research(SP,name,GRID,sig,THR,seed=1):
    F=load(SP); FR=load(SP,True)
    base=pd.read_pickle(SP+'/base_trades.pkl'); base['entry_dt']=pd.to_datetime(base.entry_dt); base['exit_dt']=pd.to_datetime(base.exit_dt); base['pnl']=base.pnl_usd
    K=len(GRID); res={}
    for p in itertools.product(*GRID):
        t=win(run(F,*sig(F,p)),IS0,OOS0); res[p]=(t.pnl.sum(),len(t))
    idx={p:tuple(GRID[k].index(p[k]) for k in range(K)) for p in res}
    nbr=lambda p:[q for q in res if q!=p and all(abs(idx[q][k]-idx[p][k])<=1 for k in range(K))]
    sc={p:np.median([res[q][0] for q in nbr(p)+[p]]) for p in res}; b=max(sc,key=sc.get)
    summ=dict(round=name,tested=len(res),is_pos=int(sum(v[0]>0 for v in res.values())),center=b,IS=round(res[b][0]),nIS=res[b][1],nbr_med=round(sc[b]))
    print(summ)
    if not(res[b][0]>0 and sc[b]>0): print('NO CANDIDATE'); return summ
    t=run(F,*sig(F,b)); IS,OOS=win(t,IS0,OOS0),win(t,OOS0,END); r={};G={}
    r['OOS']=OOS.pnl.sum(); r['nOOS']=len(OOS); r['wrOOS']=(OOS.pnl>0).mean()*100 if len(OOS) else 0
    G['G1']=res[b][0]>0; G['G2']=r['OOS']>0
    bO=win(base,OOS0,END); r['mdd_base']=mdd(bO); r['mdd_comb']=mdd(pd.concat([bO[['exit_dt','pnl']],OOS[['exit_dt','pnl']]]))
    G['G3']=G['G1'] and G['G2'] and r['mdd_comb']<=1.25*r['mdd_base']
    r['nb']=[round(win(run(F,*sig(F,q)),OOS0,END).pnl.sum()) for q in nbr(b)]
    G['G4']=r['OOS']>0 and all(v>=0.7*r['OOS'] for v in r['nb'])
    rng=np.random.default_rng(seed); oi=np.where((F['dt']>=np.datetime64(OOS0))&(F['dt']<np.datetime64(END)))[0][:-200]
    holds=OOS.hold.values if len(OOS) else np.array([12]); sides=OOS.s.values if len(OOS) else np.array([1]); nul=[]
    if len(OOS):
        for k in range(200):
            bars=np.sort(rng.choice(oi,size=len(OOS)*4,replace=False)); rd={int(x):(int(rng.choice(sides)),int(rng.choice(holds))) for x in bars}
            nul.append(win(run(F,np.zeros(len(F['close'])),0,0,sig(F,b)[3],rand=rd),OOS0,END).iloc[:len(OOS)].pnl.sum())
    nul=np.array(nul) if nul else np.array([0.0]); r['null_mean']=nul.mean(); r['pct']=(nul<r['OOS']).mean()*100; G['G5']=r['pct']>=THR
    aI=res[b][0]/(OOS0-IS0).days; aO=r['OOS']/(END-OOS0).days
    r['fwd']=(1-aO/aI)*100 if aI>0 else 999; r['bwd']=(1-aI/aO)*100 if aO>0 else 999; G['G6']=r['fwd']<50 and r['bwd']<50
    e=pd.date_range(IS0,END,periods=7); r['wf']=[round(win(t,e[i],e[i+1]).pnl.sum()) for i in range(6)]; G['G7']=sum(v>0 for v in r['wf'])>=5
    r['rev']=run(FR,*sig(FR,b)).pnl.sum(); G['G8']=r['rev']>0
    m=OOS.groupby(OOS.exit_dt.dt.to_period('M')).pnl.sum() if len(OOS) else pd.Series([0.0]); r['wo_best']=r['OOS']-m.max(); G['G9']=r['wo_best']>0
    G['G10']=K<=3
    summ.update({k:(round(float(v),1) if isinstance(v,(float,np.floating)) else v) for k,v in r.items()}); summ['gates']={k:('P' if v else 'F') for k,v in G.items()}; summ['pass']=int(sum(G.values()))
    print(summ); return summ
