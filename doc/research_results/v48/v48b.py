import itertools, numpy as np, pandas as pd, sys
import v48 as A
c,l,dt,n=A.c,A.l,A.dt,A.n
hr=pd.to_datetime(A.df.datetime).dt.hour.values; dw=pd.to_datetime(A.df.datetime).dt.dayofweek.values
sma500=A.SMA[500]
def sim(h,k,wk,T,slip_bps=2,end=None):
    slip=slip_bps/1e4; N=n if end is None else int(np.searchsorted(dt,np.datetime64(end)))
    idx=np.where((hr==h)&((dw<5) if wk else True)&((c>sma500) if T else True))[0]; idx=idx[(idx>=1300)&(idx+k<N)]
    tr=[]
    for i in idx:
        ep=c[i]*(1+slip); stop=ep*0.95; lo=l[i+1:i+k+1]; hit=np.where(lo<=stop)[0]
        if len(hit): j=i+1+hit[0]; xp=ep*(1-0.05*1.25)
        else: j=i+k; xp=c[j]*(1-slip)
        tr.append((dt[i],dt[j],ep,xp,(xp/ep-1)*A.NTL-A.FEE,''))
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','why'])
GRID=list(itertools.product(range(24),(4,8,12),(0,1),(0,1)))
if __name__=='__main__':
    END=sys.argv[1]
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}
        w,picks=A.wf(G,folds); print('slip',slip,'WF',A.stat(w)); hw=A.half(w); print(hw.to_dict())
        if slip==2:
            print('picks',picks)
            a=np.array([G[g][(G[g].e>='2021-07-01')&(G[g].e<'2023-07-01')].pnl_usd.sum() for g in GRID]); b=np.array([G[g][(G[g].e>='2023-07-01')].pnl_usd.sum() for g in GRID])
            print('G2 both>0 %',round(((a>0)&(b>0)).mean()*100),'| A>0',round((a>0).mean()*100),'B>0',round((b>0).mean()*100),'medians',round(np.median(a)),round(np.median(b)),'corr',round(np.corrcoef(a,b)[0,1],2))
            print('G3 halves>0',int((hw>0).sum()),'/',len(hw),'worst',hw.min()); print('G5',hw.reindex(['2022H2','2023H1','2024H1']).sum())
