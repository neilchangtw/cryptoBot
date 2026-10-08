import sys,pandas as pd,numpy as np,itertools,json
SP=sys.argv[1]
NOT=4000; COST=9.0; SLIP=0.0002; FUND_H=NOT*0.0001/8; PEN=0.25
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(rev=False):
    d=pd.read_csv(SP+'/ETH_1h_long.csv'); d['dt']=pd.to_datetime(d.datetime)
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=d.dt.values; d=r
    return d
def feats(d):
    o,h,l,c=[d[x].values for x in ['open','high','low','close']]
    tr=np.maximum(h-l,np.maximum(abs(h-np.r_[c[0],c[:-1]]),abs(l-np.r_[c[0],c[:-1]])))
    atr=pd.Series(tr).rolling(24).mean().values
    return dict(o=o,h=h,l=l,c=c,atr=atr,hour=d.dt.dt.hour.values,dt=d.dt.values,cs=pd.Series(c))
# ---------- signal + exit definitions ----------
def spec(fam,p,F):
    c,h,l,o,atr,cs=F['c'],F['h'],F['l'],F['o'],F['atr'],F['cs']
    if fam in('DonL','DonS'):
        N,M=p; up=fam=='DonL'
        ent=(c>cs.shift(1).rolling(N).max().values) if up else (c<cs.shift(1).rolling(N).min().values)
        ex =(cs.shift(1).rolling(M).min().values) if up else (cs.shift(1).rolling(M).max().values)
        return up,ent,('chan',ex),0.05
    if fam in('VXL','VXS'):
        k,m=p; up=fam=='VXL'; rng=h-l; pa=np.r_[np.nan,atr[:-1]]
        pos=(c-l)/np.where(rng>0,rng,np.nan)
        ent=(rng>k*pa)&((pos>=0.8)&(c>o) if up else (pos<=0.2)&(c<o))
        return up,ent,('chand',m),0.05
    if fam in('TSL','TSS'):
        (Ld,)=p; up=fam=='TSL'; chk=F['hour']==8
        r=c/cs.shift(24*Ld).values-1
        sig=(r>0) if up else (r<0)
        ent=chk&sig
        return up,ent,('flip',chk,sig),0.10
def run(F,up,ent,exr,SL,rand=None,i0=320):
    o,h,l,c,atr=F['o'],F['h'],F['l'],F['c'],F['atr']; n=len(c); out=[]; pos=None; pend_exit=False
    i=i0
    while i<n-1:
        if pos is None:
            go=ent[i] if rand is None else (i in rand)
            if go and not np.isnan(c[i]):
                ep=o[i+1]*(1+SLIP if up else 1-SLIP); pos=dict(ep=ep,eb=i+1,hi=c[i],lo=c[i],hold=rand.get(i) if rand is not None else None)
            i+=1; continue
        # in position at bar i (entered at open of eb)
        ep=pos['ep']; st=ep*(1-SL) if up else ep*(1+SL)
        if up and l[i]<=st: px=min(st,o[i])-PEN*(min(st,o[i])-l[i]); out.append((pos['eb'],i,px/ep-1)); pos=None; i+=1; continue
        if (not up) and h[i]>=st: px=max(st,o[i])+PEN*(h[i]-max(st,o[i])); out.append((pos['eb'],i,ep/px-1)); pos=None; i+=1; continue
        pos['hi']=max(pos['hi'],c[i]); pos['lo']=min(pos['lo'],c[i])
        if pos['hold'] is not None: x=(i-pos['eb']+1)>=pos['hold']
        elif exr[0]=='chan': x=(c[i]<exr[1][i]) if up else (c[i]>exr[1][i])
        elif exr[0]=='chand': x=(c[i]<pos['hi']-exr[1]*atr[i]) if up else (c[i]>pos['lo']+exr[1]*atr[i])
        else: x=exr[1][i] and not exr[2][i]
        if x:
            px=o[i+1]*(1-SLIP if up else 1+SLIP); out.append((pos['eb'],i+1,(px/ep-1) if up else (ep/px-1))); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','ret'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','ret','pnl','entry_dt','exit_dt','hold'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST-FUND_H*t.hold
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
GRIDS={'DonL':[(48,72,120,168,240),(12,24,48,72)],'DonS':[(48,72,120,168,240),(12,24,48,72)],
       'VXL':[(2.0,2.5,3.0,3.5),(2,3,4,5)],'VXS':[(2.0,2.5,3.0,3.5),(2,3,4,5)],
       'TSL':[(7,14,21,30,45,60,90)],'TSS':[(7,14,21,30,45,60,90)]}
