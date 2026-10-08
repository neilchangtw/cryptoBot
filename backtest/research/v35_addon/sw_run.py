import sys,os; SP=sys.argv[1]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'swing.py'),encoding='utf-8').read())
F=load(SP); FR=load(SP,True); THR=97.5
c=pd.Series(F['close'],index=pd.to_datetime(F['dt'])); o=pd.Series(F['open'],index=c.index)
mret=(c.resample('ME').last()/o.resample('ME').first()-1); mret.index=mret.index.to_period('M')

def mdd_tr(t):
    x=t.sort_values('exit_dt').pnl.cumsum(); return (x.cummax()-x).max() if len(x) else 0
def bh_mdd(a,b,up):
    p=c[(c.index>=a)&(c.index<b)]; v=NOT*(p/p.iloc[0]) if up else NOT*(2-p/p.iloc[0]); return (v.cummax()-v).max()
def capture(t,a,b,up):
    m=mret[(mret.index>=a.to_period('M'))&(mret.index<b.to_period('M'))]; big=m[m>0.15] if up else m[m<-0.15]
    if len(big)==0: return np.nan
    tm=t.exit_dt.dt.to_period('M'); return t[tm.isin(big.index)].pnl.sum()/(NOT*big.abs().sum())

summary=[]
for side in 'LS':
    for fam,g in GRIDS.items():
        up=side=='L'; res={}
        for p in itertools.product(*g):
            e,x=signals(F,fam,side,p); t=win(run(F,side,e,x),IS0,OOS0); res[p]=(t.pnl.sum(),len(t))
        idx={p:tuple(g[k].index(p[k]) for k in range(2)) for p in res}
        nbr=lambda p:[q for q in res if q!=p and all(abs(idx[q][k]-idx[p][k])<=1 for k in range(2))]
        sc={p:np.median([res[q][0] for q in nbr(p)+[p]]) for p in res}; b=max(sc,key=sc.get)
        S=dict(name=f'{side}-{fam}',is_pos=int(sum(v[0]>0 for v in res.values())),n_cfg=len(res),center=b,IS=round(res[b][0]),nIS=res[b][1],nbr_med=round(sc[b]))
        if not(res[b][0]>0 and sc[b]>0):
            S['result']='NO CANDIDATE'; print(S); summary.append(S); continue
        e,x=signals(F,fam,side,b); t=run(F,side,e,x); IS,OOS=win(t,IS0,OOS0),win(t,OOS0,END); G={}
        S.update(OOS=round(OOS.pnl.sum()),nOOS=len(OOS),wrOOS=round((OOS.pnl>0).mean()*100,1) if len(OOS) else 0)
        G['G1']=res[b][0]>0; G['G2']=S['OOS']>0
        S['capIS']=round(capture(IS,IS0,OOS0,up),3); S['capOOS']=round(capture(OOS,OOS0,END,up),3)
        S['mddOOS']=round(mdd_tr(OOS)); S['bhmddOOS']=round(bh_mdd(OOS0,END,up))
        G['G3']=(not np.isnan(S['capOOS'])) and S['capOOS']>=0.25 and S['mddOOS']<S['bhmddOOS']
        S['nb']=[]
        for q in nbr(b):
            e2,x2=signals(F,fam,side,q); S['nb'].append(round(win(run(F,side,e2,x2),OOS0,END).pnl.sum()))
        G['G4']=S['OOS']>0 and all(v>=0.7*S['OOS'] for v in S['nb'])
        rng=np.random.default_rng(3); dec=F['dec']; ddt=pd.to_datetime(F['dt'][dec])
        ks=np.where((ddt>=OOS0)&(ddt<END))[0][:-30]
        holds=OOS.hold.values if len(OOS) else np.array([72]); nul=[]
        for r in range(200):
            pick=np.sort(rng.choice(ks,size=min(len(ks),max(1,len(OOS))*3),replace=False)); rd={int(k):int(rng.choice(holds)) for k in pick}
            nul.append(win(run(F,side,np.zeros(len(dec),bool),x,rand=rd),OOS0,END).iloc[:len(OOS)].pnl.sum())
        nul=np.array(nul); S['null_mean']=round(nul.mean()); S['pct']=round((nul<S['OOS']).mean()*100,1); G['G5']=S['pct']>=THR
        aI=res[b][0]/(OOS0-IS0).days; aO=S['OOS']/(END-OOS0).days
        S['fwd']=round((1-aO/aI)*100,1) if aI>0 else 999; S['bwd']=round((1-aI/aO)*100,1) if aO>0 else 999
        G['G6']=S['fwd']<50 and S['bwd']<50
        ed=pd.date_range(IS0,END,periods=7); S['wf']=[round(win(t,ed[i],ed[i+1]).pnl.sum()) for i in range(6)]
        tot=t[t.entry_dt>=IS0].pnl.sum(); S['full55']=round(tot)
        G['G7']=sum(v>0 for v in S['wf'])>=4 and min(S['wf'])>-0.25*tot
        e3,x3=signals(FR,fam,side,b); S['rev']=round(run(FR,side,e3,x3).pnl.sum()); G['G8']=S['rev']>0
        m=OOS.groupby(OOS.exit_dt.dt.to_period('M')).pnl.sum() if len(OOS) else pd.Series([0.0])
        S['wo_best']=round(S['OOS']-m.max()); G['G9']=S['wo_best']>0
        G['G10']=True; S['gates']={k:('P' if v else 'F') for k,v in G.items()}; S['pass']=int(sum(G.values()))
        print(S); summary.append(S)
json.dump(summary,open(SP+'/swing_summary.json','w'),default=str)
