import itertools, numpy as np, pandas as pd, sys
df=pd.read_csv('/home/user/v46/data/eth1h.csv',parse_dates=['datetime'])
o,h,l,c=(df[k].to_numpy(float) for k in('open','high','low','close')); dt=df.datetime.values; n=len(c)
cs=pd.Series(c); sma24=cs.rolling(24).mean().values; sd24=cs.rolling(24).std().values; z=(c-sma24)/sd24
SMA={T:(cs.rolling(T).mean().values if T else None) for T in (0,200,500,1200)}
NTL=4000; FEE=4
def sim(T,k,H,SL,slip_bps=2,end=None):
    slip=slip_bps/1e4; tr=[]; pos=False; last=-999
    N=n if end is None else int(np.searchsorted(dt,np.datetime64(end)))
    st=SMA[T]
    for i in range(1300,N):
        if pos:
            held=i-eb; stop=ep*(1-SL)
            if l[i]<=stop: xp=ep*(1-SL*1.25); why='SL'
            elif c[i]>=sma24[i]: xp=c[i]*(1-slip); why='MR'
            elif held>=H: xp=c[i]*(1-slip); why='MH'
            else: continue
            tr.append((dt[eb],dt[i],ep,xp,(xp/ep-1)*NTL-FEE,why)); pos=False; last=i; continue
        if i-last>=6 and z[i]<-k and (st is None or c[i]>st[i]):
            pos=True; eb=i; ep=c[i]*(1+slip)
    t=pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','why']); return t
GRID=list(itertools.product((0,200,500,1200),(1.5,2.0,2.5),(12,24,48),(0.03,0.05)))
def half(t): return t.groupby(t.e.dt.year.astype(str)+'H'+((t.e.dt.month>6)+1).astype(str)).pnl_usd.sum().round()
def wf(G,folds):
    out=[];picks=[]
    for a,b in zip(folds[:-1],folds[1:]):
        best=max(G,key=lambda k:G[k][G[k].x<a].pnl_usd.sum())
        t=G[best]; out.append(t[(t.e>=a)&(t.e<b)]); picks.append((str(a.date()),best))
    return pd.concat(out).sort_values('x'),picks
def stat(t):
    eq=t.pnl_usd.cumsum()
    return dict(n=len(t),pnl=round(t.pnl_usd.sum()),wr=round((t.pnl_usd>0).mean()*100),mdd=round((eq.cummax()-eq).max()),worst30=round(t.pnl_usd.rolling(30).sum().min()))
if __name__=='__main__':
    END=sys.argv[1]  # data cutoff
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]
    folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}
        w,picks=wf(G,folds); print('slip',slip,'WF',stat(w)); hw=half(w); print(hw.to_dict())
        if slip==2:
            print('picks',picks)
            a=np.array([G[g][(G[g].e>='2021-07-01')&(G[g].e<'2023-07-01')].pnl_usd.sum() for g in GRID]); b=np.array([G[g][(G[g].e>='2023-07-01')].pnl_usd.sum() for g in GRID])
            print('G2 both>0 %',round(((a>0)&(b>0)).mean()*100),'| A>0',round((a>0).mean()*100),'B>0',round((b>0).mean()*100),'medians',round(np.median(a)),round(np.median(b)))
            print('G3 halves>0',int((hw>0).sum()),'/',len(hw),'worst',hw.min()); print('G5',hw.reindex(['2022H2','2023H1','2024H1']).sum())
            tot=pd.Series({g:G[g][G[g].e>='2021-07-01'].pnl_usd.sum() for g in GRID}).round()
            print(tot.groupby(level=0).median().to_dict(),tot.groupby(level=1).median().to_dict(),tot.groupby(level=2).median().to_dict(),tot.groupby(level=3).median().to_dict())
            print('n trades median',np.median([len(G[g]) for g in GRID]))
