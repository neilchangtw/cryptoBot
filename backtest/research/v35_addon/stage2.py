import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'strict.py'),encoding='utf-8').read())
sel=json.load(open(SP+'/sel.json'))
d=load(); F=feats(d); R=load(True); FR=feats(R)
base=pd.read_pickle(SP+'/base_trades.pkl'); base['entry_dt']=pd.to_datetime(base.entry_dt); base['exit_dt']=pd.to_datetime(base.exit_dt); base['pnl']=base.pnl_usd
def mdd(t):
    c=t.sort_values('exit_dt').pnl.cumsum(); return (c.cummax()-c).max() if len(c) else 0
rng=np.random.default_rng(7)
out={}
for fam,p in sel.items():
    p=tuple(p); g=GRIDS[fam]; up,ent,exr,SL=spec(fam,p,F); t=run(F,up,ent,exr,SL)
    IS,OOS=win(t,IS0,OOS0),win(t,OOS0,END); r={}
    r['IS']=IS.pnl.sum(); r['OOS']=OOS.pnl.sum(); r['nOOS']=len(OOS); r['wrOOS']=(OOS.pnl>0).mean()*100
    G={}
    G['G1']=r['IS']>0; G['G2']=r['OOS']>0
    bI,bO=win(base,IS0,OOS0),win(base,OOS0,END)
    cI=pd.concat([bI[['exit_dt','pnl']],IS[['exit_dt','pnl']]]); cO=pd.concat([bO[['exit_dt','pnl']],OOS[['exit_dt','pnl']]])
    r['mddO_base']=mdd(bO); r['mddO_comb']=mdd(cO); r['mddI_base']=mdd(bI); r['mddI_comb']=mdd(cI)
    G['G3']=(r['IS']>0) and (r['OOS']>0) and r['mddO_comb']<=1.25*r['mddO_base']
    # G4 neighbours OOS
    ix=[g[k].index(p[k]) for k in range(len(p))]; nbv=[]
    for q in itertools.product(*g):
        qi=[g[k].index(q[k]) for k in range(len(q))]
        if q!=p and all(abs(qi[k]-ix[k])<=1 for k in range(len(q))):
            u,e,x,s=spec(fam,q,F); nbv.append(win(run(F,u,e,x,s),OOS0,END).pnl.sum())
    r['nbOOS']=[round(v) for v in nbv]
    G['G4']=r['OOS']>0 and all(v>=0.7*r['OOS'] for v in nbv)
    # G5 null: random entries in OOS, holds bootstrapped from candidate holds
    oi=np.where((F['dt']>=np.datetime64(OOS0))&(F['dt']<np.datetime64(END)))[0][:-200]
    holds=OOS.hold.values if len(OOS) else np.array([24]); nul=[]
    for k in range(200):
        bars=np.sort(rng.choice(oi,size=len(OOS)*4,replace=False)); rd={int(b):int(rng.choice(holds)) for b in bars}
        rt=win(run(F,up,np.zeros(len(F['c']),bool),exr,SL,rand=rd),OOS0,END).iloc[:len(OOS)]; nul.append(rt.pnl.sum())
    nul=np.array(nul); r['null_mean']=nul.mean(); r['null_p95']=np.percentile(nul,95); r['pct']=(nul<r['OOS']).mean()*100
    G['G5']=r['pct']>=95
    dI=(OOS0-IS0).days; dO=(END-OOS0).days; aI=r['IS']/dI; aO=r['OOS']/dO
    r['fwd']=(1-aO/aI)*100 if aI>0 else 999; r['bwd']=(1-aI/aO)*100 if aO>0 else 999
    G['G6']=r['fwd']<50 and r['bwd']<50
    edges=pd.date_range(IS0,END,periods=7); r['wf']=[round(win(t,edges[i],edges[i+1]).pnl.sum()) for i in range(6)]
    G['G7']=sum(v>0 for v in r['wf'])>=5
    u,e,x,s=spec(fam,p,FR); r['rev']=run(FR,u,e,x,s).pnl.sum(); G['G8']=r['rev']>0
    m=OOS.groupby(OOS.exit_dt.dt.to_period('M')).pnl.sum() if len(OOS) else pd.Series([0.0])
    r['oos_wo_best']=r['OOS']-m.max(); G['G9']=r['oos_wo_best']>0
    G['G10']=len(p)<=3
    r['pass']=sum(G.values())
    print(f"\n== {fam} {p}  PASS {r['pass']}/10")
    print({k:('PASS' if v else 'FAIL') for k,v in G.items()})
    print({k:(round(v,1) if isinstance(v,(float,np.floating)) else v) for k,v in r.items()})
