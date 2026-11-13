import itertools, numpy as np, pandas as pd, sys
import v48 as A, v48c as C
def sim(d,R,N,M,SL,K,slip_bps=2,end=None):
    if end is not None: d=d[d.index<=pd.Timestamp(end)]
    c=d.close.values; lo=d.low.values; t=d.index
    hiN=d.close.shift(1).rolling(N).max().values; loM=d.close.shift(1).rolling(M).min().values; sm=d.close.rolling(R).mean().values
    slip=slip_bps/1e4; tr=[]; pos=False
    for i in range(100,len(c)):
        if pos:
            if lo[i]<=ep*(1-SL): xp=ep*(1-SL*1.25)
            elif c[i]<loM[i] or (K and i-eb>=K): xp=c[i]*(1-slip)
            else: continue
            f=C.fund(t[eb],t[i])*A.NTL
            tr.append((t[eb],t[i],ep,xp,(xp/ep-1)*A.NTL-A.FEE-f,f)); pos=False; continue
        if c[i]>hiN[i] and c[i]>sm[i]: pos=True; eb=i; ep=c[i]*(1+slip)
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund'])
GRID=list(itertools.product((600,1200),(30,55,90),(10,20,30),(0.05,0.08),(42,0)))
if __name__=='__main__':
    END=sys.argv[1]; d=C.bars('4h')
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(d,*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); C.report(G,GRID,folds,slip==2)
