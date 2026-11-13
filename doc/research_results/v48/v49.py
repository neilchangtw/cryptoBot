import itertools, numpy as np, pandas as pd, sys
sys.path.insert(0,'/home/user/v46')
import engine as E, v48 as A, v48c as C
D=C.bars('4h')
def prep(d):
    o,h,l,c=(d[k].values for k in('open','high','low','close'))
    gk=0.5*np.log(h/l)**2-(2*np.log(2)-1)*np.log(c/o)**2; g=pd.Series(gk)
    ratio=(g.rolling(10).mean()/g.rolling(30).mean().replace(0,np.nan)).values
    sh=np.roll(ratio,1); sh[0]=np.nan
    return E.rolling_pctile(sh,100)
PCT=prep(D)
def sim(th,B,TP,MH,SL,slip_bps=2,end=None):
    d=D; N=len(d) if end is None else int(d.index.searchsorted(pd.Timestamp(end),side='right'))
    c=d.close.values; hi=d.high.values; t=d.index
    loB=d.close.shift(1).rolling(B).min().values
    slip=slip_bps/1e4; tr=[]; pos=False; last=-99
    for i in range(140,N):
        if pos:
            if hi[i]>=ep*(1+SL): xp=ep*(1+SL*1.25); why='SL'
            elif c[i]<=ep*(1-TP): xp=c[i]*(1+slip); why='TP'
            elif i-eb>=MH: xp=c[i]*(1+slip); why='MH'
            else: continue
            f=C.fund(t[eb],t[i])*A.NTL   # short receives +funding
            tr.append((t[eb],t[i],ep,xp,(1-xp/ep)*A.NTL-A.FEE+f,-f,why)); pos=False; last=i; continue
        if i-last>=2 and PCT[i]<th and c[i]<loB[i]: pos=True; eb=i; ep=c[i]*(1-slip)
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund','why'])
GRID=list(itertools.product((25,35,45),(10,15,20),(0.03,0.045,0.06),(6,10,14),(0.05,0.08)))
if __name__=='__main__':
    END=sys.argv[1]
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); w=C.report(G,GRID,folds,slip==2)
        if slip==2: print('G5b',A.half(w).reindex(['2021H2','2022H2','2023H1','2023H2']).sum()); w.to_pickle('v49_wf_dev.pkl')
