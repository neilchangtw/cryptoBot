import itertools, numpy as np, pandas as pd, sys
import v48 as A, v48c as C
c,l,n=A.c,A.l,A.n; T=pd.DatetimeIndex(A.dt)
# rate known at bar i close: funding with calc time <= bar open time
fk=C.FR.reindex(T,method='ffill').values
isset=np.isin(T,C.FR.index)   # bar opening at settlement time; enter at its close
def sim(th,H,SL,slip_bps=2,end=None):
    slip=slip_bps/1e4; N=n if end is None else int(np.searchsorted(A.dt,np.datetime64(end)))
    tr=[]; pos=False
    for i in range(300,N):
        if pos:
            if l[i]<=ep*(1-SL): xp=ep*(1-SL*1.25)
            elif i>=due:
                if fk[i]<=th: due=i+H; continue
                xp=c[i]*(1-slip)
            else: continue
            f=C.fund(T[eb]+pd.Timedelta(hours=1),T[i]+pd.Timedelta(hours=1))*A.NTL
            tr.append((T[eb],T[i],ep,xp,(xp/ep-1)*A.NTL-A.FEE-f,f)); pos=False; continue
        if isset[i] and fk[i]<=th: pos=True; eb=i; ep=c[i]*(1+slip); due=i+H
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund'])
GRID=list(itertools.product((0.0,-0.00005,-0.0001),(8,24,72),(0.05,0.08)))
if __name__=='__main__':
    END=sys.argv[1]
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); C.report(G,GRID,folds,slip==2)
