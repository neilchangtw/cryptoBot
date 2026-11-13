import itertools, numpy as np, pandas as pd, sys
sys.path.insert(0,'/home/user/v46')
import engine as E, v48 as A, v48c as C
ind=E.compute_indicators(A.df); c,l,n=A.c,A.l,A.n; T=pd.DatetimeIndex(A.dt); dw=ind['dows']; pS=ind['pctile_S']
sc=pd.Series(c).shift(1)
LO={B:sc.rolling(B).min().values for B in (10,15,20)}
def sim(B,th,H,TP,slip_bps=2,end=None,days=(5,6)):
    slip=slip_bps/1e4; N=n if end is None else int(np.searchsorted(A.dt,np.datetime64(end)))
    SL=0.04; tr=[]; pos=False; last=-99; wk=np.isin(dw,days)
    for i in range(300,N):
        if pos:
            if l[i]<=ep*(1-SL): xp=ep*(1-SL*1.25); why='SL'
            elif c[i]>=ep*(1+TP): xp=c[i]*(1-slip); why='TP'
            elif i-eb>=H: xp=c[i]*(1-slip); why='MH'
            else: continue
            f=C.fund(T[eb]+pd.Timedelta(hours=1),T[i]+pd.Timedelta(hours=1))*A.NTL
            tr.append((T[eb],T[i],ep,xp,(xp/ep-1)*A.NTL-A.FEE-f,f,why)); pos=False; last=i; continue
        if wk[i] and i-last>=3 and c[i]<LO[B][i] and (th>=100 or pS[i]<th): pos=True; eb=i; ep=c[i]*(1+slip)
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund','why'])
GRID=list(itertools.product((10,15,20),(35,100),(6,10,16),(0.015,0.025)))
if __name__=='__main__':
    END=sys.argv[1]
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); w=C.report(G,GRID,folds,slip==2)
        if slip==2: w.to_pickle('v50_wf_%s.pkl'%END)
