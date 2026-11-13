import itertools, numpy as np, pandas as pd, sys
import v48 as A, v48c as C
c,l,n=A.c,A.l,A.n; T=pd.DatetimeIndex(A.dt)
m=pd.read_csv('metrics_1h.csv',index_col=0,parse_dates=True)
# bar i closes at T[i]+1h; use metric known at T[i] (1h earlier than close) => lag 1h
ls=m.count_long_short_ratio.reindex(T).values
RK={W:pd.Series(ls).rolling(W,min_periods=W//2).rank(pct=True).values for W in (360,720,1440)}
def sim(W,q,H,SL,slip_bps=2,end=None,start='2021-12-01'):
    slip=slip_bps/1e4; N=n if end is None else int(np.searchsorted(A.dt,np.datetime64(end))); i0=int(np.searchsorted(A.dt,np.datetime64(start)))
    r=RK[W]; tr=[]; pos=False; last=-99
    for i in range(i0,N):
        if pos:
            if l[i]<=ep*(1-SL): xp=ep*(1-SL*1.25); why='SL'
            elif i>=due:
                if r[i]<q: due=i+H; continue
                xp=c[i]*(1-slip); why='T'
            else: continue
            f=C.fund(T[eb]+pd.Timedelta(hours=1),T[i]+pd.Timedelta(hours=1))*A.NTL
            tr.append((T[eb],T[i],ep,xp,(xp/ep-1)*A.NTL-A.FEE-f,f,why)); pos=False; last=i; continue
        if i-last>=6 and r[i]<q: pos=True; eb=i; ep=c[i]*(1+slip); due=i+H
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund','why'])
GRID=list(itertools.product((360,720,1440),(0.05,0.10,0.20),(24,72),(0.05,0.08)))
def report(G,folds,full=True,split='2024-01-01'):
    w,picks=A.wf(G,folds); print('WF',A.stat(w),'funding',round(w.fund.sum())); hw=A.half(w); print(hw.to_dict())
    if full:
        print('picks',picks)
        a=np.array([G[g][(G[g].e<split)].pnl_usd.sum() for g in GRID]); b=np.array([G[g][(G[g].e>=split)].pnl_usd.sum() for g in GRID])
        print('G2 both>0 %',round(((a>0)&(b>0)).mean()*100),'| A>0',round((a>0).mean()*100),'B>0',round((b>0).mean()*100),'medians',round(np.median(a)),round(np.median(b)))
        print('G3 halves>0',int((hw>0).sum()),'/',len(hw),'worst',hw.min()); print('G5',hw.reindex(['2022H2','2023H1','2024H1']).sum())
        tot=pd.Series({g:G[g].pnl_usd.sum() for g in GRID}).round(); print([tot.groupby(level=i).median().to_dict() for i in range(4)]); print('n median',np.median([len(G[g]) for g in GRID]))
    return w
if __name__=='__main__':
    END=sys.argv[1]
    folds=[pd.Timestamp(f'{y}-{m_:02d}-01') for y in range(2022,2028) for m_ in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); w=report(G,folds,slip==2)
        if slip==2: w.to_pickle('v51_wf_%s.pkl'%END)
