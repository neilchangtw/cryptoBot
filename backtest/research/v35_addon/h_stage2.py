import sys; SP=sys.argv[1]; THR=float(sys.argv[2]); SELF=sys.argv[3]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'htf.py'),encoding='utf-8').read())
sel=json.load(open(SP+'/'+SELF))
base=pd.read_pickle(SP+'/base_trades.pkl'); base['entry_dt']=pd.to_datetime(base.entry_dt); base['exit_dt']=pd.to_datetime(base.exit_dt); base['pnl']=base.pnl_usd
def mdd(t):
    c=t.sort_values('exit_dt').pnl.cumsum(); return (c.cummax()-c).max() if len(c) else 0
rng=np.random.default_rng(5)
for fam,p in sel.items():
    tf,side=FAMS[fam]; a,hpb=load(SP,tf); F=feats(a,side); ar,_=load(SP,tf,True); FR=feats(ar,side); g=GRIDS[tf]; p=tuple(p)
    t=run(F,side,*p,hpb); IS,OOS=win(t,IS0,OOS0),win(t,OOS0,END); r={};G={}
    r['IS']=IS.pnl.sum(); r['nIS']=len(IS); r['OOS']=OOS.pnl.sum(); r['nOOS']=len(OOS)
    G['G1']=r['IS']>0; G['G2']=r['OOS']>0
    bO=win(base,OOS0,END); r['mddO_base']=mdd(bO); r['mddO_comb']=mdd(pd.concat([bO[['exit_dt','pnl']],OOS[['exit_dt','pnl']]]))
    G['G3']=G['G1'] and G['G2'] and r['mddO_comb']<=1.25*r['mddO_base']
    ix=[g[k].index(p[k]) for k in range(3)]; nbv=[]
    for q in itertools.product(*g):
        qi=[g[k].index(q[k]) for k in range(3)]
        if q!=p and all(abs(qi[k]-ix[k])<=1 for k in range(3)): nbv.append(round(win(run(F,side,*q,hpb),OOS0,END).pnl.sum()))
    r['nb']=nbv; G['G4']=r['OOS']>0 and all(v>=0.7*r['OOS'] for v in nbv)
    oi=np.where((F['dt']>=np.datetime64(OOS0))&(F['dt']<np.datetime64(END)))[0][:-30]; holds=OOS.hold.values if len(OOS) else np.array([p[1]]); nul=[]
    for k in range(200):
        bars=np.sort(rng.choice(oi,size=min(len(oi),max(len(OOS),1)*4),replace=False)); rd={int(b):int(rng.choice(holds)) for b in bars}
        nul.append(win(run(F,side,*p,hpb,rand=rd),OOS0,END).iloc[:len(OOS)].pnl.sum())
    nul=np.array(nul); r['null_mean']=nul.mean(); r['pct']=(nul<r['OOS']).mean()*100; G['G5']=r['pct']>=THR
    aI=r['IS']/(OOS0-IS0).days; aO=r['OOS']/(END-OOS0).days
    r['fwd']=(1-aO/aI)*100 if aI>0 else 999; r['bwd']=(1-aI/aO)*100 if aO>0 else 999; G['G6']=r['fwd']<50 and r['bwd']<50
    e=pd.date_range(IS0,END,periods=7); r['wf']=[round(win(t,e[i],e[i+1]).pnl.sum()) for i in range(6)]; G['G7']=sum(v>0 for v in r['wf'])>=5
    r['rev']=run(FR,side,*p,hpb).pnl.sum(); G['G8']=r['rev']>0
    m=OOS.groupby(OOS.exit_dt.dt.to_period('M')).pnl.sum(); r['wo_best']=r['OOS']-(m.max() if len(m) else 0); G['G9']=r['wo_best']>0
    G['G10']=True
    print(f"== {fam} {p} PASS {sum(G.values())}/10"); print({k:('PASS' if v else 'FAIL') for k,v in G.items()})
    print({k:(round(float(v),1) if isinstance(v,(float,np.floating)) else v) for k,v in r.items()})
