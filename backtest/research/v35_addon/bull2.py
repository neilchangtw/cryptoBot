import sys,os; SP=sys.argv[1]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'swing.py'),encoding='utf-8').read())
THR=98.3
def load2(SP,rev=False):
    F=load(SP,rev)
    b=pd.read_csv(SP+'/BTC_1h_long.csv'); b['dt']=pd.to_datetime(b.datetime)
    e=pd.read_csv(SP+'/ETH_1h_long.csv'); e['dt']=pd.to_datetime(e.datetime)
    bc=e[['dt']].merge(b[['dt','close','open']],on='dt',how='left').ffill()
    if rev:
        r=bc.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; bc=r
    F['bdc']=pd.Series(bc.close.values[F['dec']])
    return F
def sig2(F,fam,p):
    dcs=F['dcs']; dc=F['dc']; atr=F['atr']
    if fam=='P':
        D,k=p; ema=dcs.ewm(span=D,adjust=False).mean().values; hi=dcs.rolling(20).max().values
        return (dc>ema)&((hi-dc)>=k*atr),('trail',3)
    N,E=p; be=F['bdc'].ewm(span=E,adjust=False).mean().values
    return (dc>dcs.shift(1).rolling(N).max().values)&(F['bdc'].values>be),('trail',3)
G2={'P':[(50,100,200),(1,2,3)],'B':[(10,20,40),(20,50,100)]}
F=load2(SP); FR=load2(SP,True)
c=pd.Series(F['close'],index=pd.to_datetime(F['dt'])); o=pd.Series(F['open'],index=c.index)
mret=(c.resample('ME').last()/o.resample('ME').first()-1); mret.index=mret.index.to_period('M')
def mdd_tr(t):
    x=t.sort_values('exit_dt').pnl.cumsum(); return (x.cummax()-x).max() if len(x) else 0
for fam,g in G2.items():
    res={}
    for p in itertools.product(*g):
        e,x=sig2(F,fam,p); t=win(run(F,'L',e,x),IS0,OOS0); res[p]=(t.pnl.sum(),len(t))
    idx={p:tuple(g[k].index(p[k]) for k in range(2)) for p in res}
    nbr=lambda p:[q for q in res if q!=p and all(abs(idx[q][k]-idx[p][k])<=1 for k in range(2))]
    sc={p:np.median([res[q][0] for q in nbr(p)+[p]]) for p in res}; b=max(sc,key=sc.get)
    S=dict(name='L-'+fam,is_pos=int(sum(v[0]>0 for v in res.values())),center=b,IS=round(res[b][0]),nIS=res[b][1],nbr_med=round(sc[b]))
    if not(res[b][0]>0 and sc[b]>0): print(S,'NO CANDIDATE'); continue
    e,x=sig2(F,fam,b); t=run(F,'L',e,x); OOS=win(t,OOS0,END); G={}
    S.update(OOS=round(OOS.pnl.sum()),nOOS=len(OOS),wr=round((OOS.pnl>0).mean()*100,1))
    G['G1']=True; G['G2']=S['OOS']>0
    m=mret[(mret.index>=OOS0.to_period('M'))]; big=m[m>0.15]
    S['capOOS']=round(OOS[OOS.exit_dt.dt.to_period('M').isin(big.index)].pnl.sum()/(NOT*big.sum()),3)
    p_=c[c.index>=OOS0]; v=NOT*p_/p_.iloc[0]; S['bhmdd']=round((v.cummax()-v).max()); S['mdd']=round(mdd_tr(OOS))
    G['G3']=S['capOOS']>=0.25 and S['mdd']<S['bhmdd']
    S['nb']=[round(win(run(F,'L',*sig2(F,fam,q)),OOS0,END).pnl.sum()) for q in nbr(b)]
    G['G4']=G['G2'] and all(v_>=0.7*S['OOS'] for v_ in S['nb'])
    rng=np.random.default_rng(9); dec=F['dec']; ddt=pd.to_datetime(F['dt'][dec]); ks=np.where((ddt>=OOS0)&(ddt<END))[0][:-30]
    holds=OOS.hold.values if len(OOS) else np.array([72]); nul=[]
    for r in range(200):
        pick=np.sort(rng.choice(ks,size=min(len(ks),max(1,len(OOS))*3),replace=False)); rd={int(k):int(rng.choice(holds)) for k in pick}
        nul.append(win(run(F,'L',np.zeros(len(dec),bool),x,rand=rd),OOS0,END).iloc[:len(OOS)].pnl.sum())
    nul=np.array(nul); S['pct']=round((nul<S['OOS']).mean()*100,1); S['null_mean']=round(nul.mean()); G['G5']=S['pct']>=THR
    aI=res[b][0]/(OOS0-IS0).days; aO=S['OOS']/(END-OOS0).days
    S['fwd']=round((1-aO/aI)*100,1); S['bwd']=round((1-aI/aO)*100,1) if aO>0 else 999; G['G6']=S['fwd']<50 and S['bwd']<50
    ed=pd.date_range(IS0,END,periods=7); S['wf']=[round(win(t,ed[i],ed[i+1]).pnl.sum()) for i in range(6)]
    tot=t[t.entry_dt>=IS0].pnl.sum(); S['full']=round(tot); G['G7']=sum(v_>0 for v_ in S['wf'])>=4 and min(S['wf'])>-0.25*tot
    S['rev']=round(run(FR,'L',*sig2(FR,fam,b)).pnl.sum()); G['G8']=S['rev']>0
    mm=OOS.groupby(OOS.exit_dt.dt.to_period('M')).pnl.sum(); S['wo_best']=round(S['OOS']-mm.max()); G['G9']=S['wo_best']>0
    G['G10']=True; S['gates']=''.join('P' if v_ else 'F' for v_ in G.values()); S['pass']=int(sum(G.values()))
    print(S)
