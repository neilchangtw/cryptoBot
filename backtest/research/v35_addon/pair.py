import sys,pandas as pd,numpy as np,itertools,json
LEG=2000; COST=9.0; SLIP=0.0002; FUND_H=4000*0.0001/8; SLUSD=300
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(SP,rev=False):
    e=pd.read_csv(SP+'/ETH_1h_long.csv'); b=pd.read_csv(SP+'/BTC_1h_long.csv')
    d=e.merge(b,on='datetime',suffixes=('_e','_b')); d['dt']=pd.to_datetime(d.datetime)
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy()
        for s in ('_e','_b'): r[['open'+s,'close'+s]]=r[['close'+s,'open'+s]].values
        r['dt']=d.dt.values; d=r
    return d
def feats(d):
    F=dict(oe=d.open_e.values,ce=d.close_e.values,ob=d.open_b.values,cb=d.close_b.values,dt=d.dt.values)
    x=np.log(F['ce']/F['cb']); F['x']=x; F['xs']=pd.Series(x)
    r=F['xs'].diff(); F['r']=r
    return F
def spec(fam,p,F):
    xs=F['xs']; x=F['x']
    if fam=='MR':
        W,k=p; m=xs.rolling(W).mean().values; s=xs.rolling(W).std().values; z=(x-m)/s
        d=np.where(z>k,-1,np.where(z<-k,1,0)); return d,('zc',z,W)
    if fam=='BO':
        N,M=p; d=np.where(x>xs.shift(1).rolling(N).max().values,1,np.where(x<xs.shift(1).rolling(N).min().values,-1,0))
        return d,('chan',xs.shift(1).rolling(M).min().values,xs.shift(1).rolling(M).max().values)
    if fam=='GKR':
        th,H=p; v=F['r']**2; ratio=v.rolling(5).mean()/v.rolling(20).mean()
        pc=ratio.shift(1).rolling(100).rank(pct=True).values*100
        d=np.where(pc<th,np.where(x>xs.shift(1).rolling(15).max().values,1,np.where(x<xs.shift(1).rolling(15).min().values,-1,0)),0)
        return d,('fix',H)
def run(F,d,exr,rand=None,i0=400):
    oe,ce,ob,cb=F['oe'],F['ce'],F['ob'],F['cb']; n=len(ce); out=[]; pos=None; i=i0
    while i<n-1:
        if pos is None:
            if rand is None: s=d[i]
            else: s=rand.get(i,(0,0))[0]
            if s!=0 and not np.isnan(ce[i]):
                pos=dict(s=s,eb=i+1,pe=oe[i+1]*(1+SLIP*s),pb=ob[i+1]*(1-SLIP*s),hold=(rand.get(i)[1] if rand is not None else None))
            i+=1; continue
        s=pos['s']; mk=s*(LEG*(ce[i]/pos['pe']-1)-LEG*(cb[i]/pos['pb']-1)); age=i-pos['eb']+1
        if pos['hold'] is not None: x=age>=pos['hold']
        elif exr[0]=='zc': x=(np.sign(exr[1][i])==s) or np.isnan(exr[1][i]) or (exr[1][i]*(-s)<=0) or age>=exr[2]
        elif exr[0]=='chan': x=(F['x'][i]<exr[1][i]) if s==1 else (F['x'][i]>exr[2][i])
        else: x=age>=exr[1]
        x = x or mk<=-SLUSD
        if x:
            px_e=oe[i+1]*(1-SLIP*s); px_b=ob[i+1]*(1+SLIP*s)
            g=s*(LEG*(px_e/pos['pe']-1)-LEG*(px_b/pos['pb']-1)); out.append((pos['eb'],i+1,s,g)); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','s','g'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','s','g','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.g-COST-FUND_H*t.hold
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
GRIDS={'MR':[(24,48,96,168,336),(1.5,2.0,2.5,3.0)],'BO':[(24,48,72,120,168,240),(12,24,48,72)],'GKR':[(15,25,35,50),(6,12,24,48)]}
