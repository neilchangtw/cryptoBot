import itertools, numpy as np, pandas as pd, sys
import v48 as A
d1=A.df.set_index('datetime')
fr=pd.read_csv('funding.csv'); fr['t']=pd.to_datetime(fr.calc_time,unit='ms').dt.round('h')+pd.Timedelta(hours=8)
FR=fr.set_index('t').last_funding_rate.sort_index(); FRC=FR.cumsum()
def fund(a,b):  # sum of rates in (a,b]
    i=FRC.index.searchsorted(a,side='right'); j=FRC.index.searchsorted(b,side='right')
    return (FRC.iloc[j-1] if j>0 else 0)-(FRC.iloc[i-1] if i>0 else 0)
def bars(tf='4h'):
    d=d1.resample(tf,label='right',closed='left').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
    return d   # index = bar close time
def sim(d,N,M,SL,K,slip_bps=2,end=None,side=1):
    if end is not None: d=d[d.index<=pd.Timestamp(end)]
    c=d.close.values; lo=d.low.values; t=d.index
    hiN=d.close.shift(1).rolling(N).max().values; loM=d.close.shift(1).rolling(M).min().values
    slip=slip_bps/1e4; tr=[]; pos=False
    for i in range(100,len(c)):
        if pos:
            if lo[i]<=ep*(1-SL): xp=ep*(1-SL*1.25)
            elif c[i]<loM[i] or (K and i-eb>=K): xp=c[i]*(1-slip)
            else: continue
            f=fund(t[eb],t[i])*A.NTL
            tr.append((t[eb],t[i],ep,xp,(xp/ep-1)*A.NTL-A.FEE-f,f)); pos=False; continue
        if c[i]>hiN[i]: pos=True; eb=i; ep=c[i]*(1+slip)
    return pd.DataFrame(tr,columns=['e','x','ep','xp','pnl_usd','fund'])
GRID=list(itertools.product((30,55,90),(10,20,30),(0.05,0.08),(42,0)))
def report(G,GRID,folds,full=True):
    w,picks=A.wf(G,folds); print('WF',A.stat(w),'funding paid',round(w.fund.sum())); hw=A.half(w); print(hw.to_dict())
    if full:
        print('picks',picks)
        a=np.array([G[g][(G[g].e>='2021-07-01')&(G[g].e<'2023-07-01')].pnl_usd.sum() for g in GRID]); b=np.array([G[g][(G[g].e>='2023-07-01')].pnl_usd.sum() for g in GRID])
        print('G2 both>0 %',round(((a>0)&(b>0)).mean()*100),'| A>0',round((a>0).mean()*100),'B>0',round((b>0).mean()*100),'medians',round(np.median(a)),round(np.median(b)))
        print('G3 halves>0',int((hw>0).sum()),'/',len(hw),'worst',hw.min()); print('G5',hw.reindex(['2022H2','2023H1','2024H1']).sum())
        tot=pd.Series({g:G[g][G[g].e>='2021-07-01'].pnl_usd.sum() for g in GRID}).round()
        print([tot.groupby(level=i).median().to_dict() for i in range(tot.index.nlevels)]); print('n trades median',np.median([len(G[g]) for g in GRID]))
    return w
if __name__=='__main__':
    END=sys.argv[1]; d=bars('4h')
    folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
    for slip in (2,4):
        G={g:sim(d,*g,slip_bps=slip,end=END) for g in GRID}; print('slip',slip); report(G,GRID,folds,slip==2)
