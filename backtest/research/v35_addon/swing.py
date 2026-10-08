import sys,pandas as pd,numpy as np,itertools,json
NOT=4000; COST=9.0; SLIP=0.0002; FUND_H=NOT*0.0001/8; PEN=0.25; HARD=0.10
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')

def load(SP,rev=False):
    d=pd.read_csv(SP+'/ETH_1h_long.csv'); d['dt']=pd.to_datetime(d.datetime)
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=d.dt.values; d=r
    F={k:d[k].values for k in ['open','high','low','close']}; F['dt']=d.dt.values
    hr=pd.to_datetime(d.dt).dt.hour.values
    # daily bar = 24 x 1h ending at the bar whose open hour is 07 (UTC+8); decision at that bar's close
    dec=np.where(hr==7)[0]; dec=dec[dec>=23]; F['dec']=dec
    c=F['close']; h=F['high']; l=F['low']
    dc=c[dec]; dh=np.array([h[i-23:i+1].max() for i in dec]); dl=np.array([l[i-23:i+1].min() for i in dec])
    pc=np.r_[np.nan,dc[:-1]]; tr=np.maximum(dh-dl,np.maximum(abs(dh-pc),abs(dl-pc)))
    F['dc']=dc; F['atr']=pd.Series(tr).rolling(20).mean().values; F['dcs']=pd.Series(dc)
    f4=np.where(hr%4==3)[0]; F['c4']=pd.Series(c[f4]); F['pos4']={int(i):k for k,i in enumerate(f4)}
    return F

def signals(F,fam,side,p):
    dcs=F['dcs']; dc=F['dc']; up=side=='L'
    if fam=='T':
        N,m=p
        ent=(dc>dcs.shift(1).rolling(N).max().values) if up else (dc<dcs.shift(1).rolling(N).min().values)
        return ent,('trail',m)
    D,Fl=p
    ed=dcs.ewm(span=D,adjust=False).mean().values; e4=F['c4'].ewm(span=Fl,adjust=False).mean().values
    e4d=np.array([e4[F['pos4'][int(i)]] for i in F['dec']]); c4d=F['close'][F['dec']]
    cond=(dc>ed)&(c4d>e4d) if up else (dc<ed)&(c4d<e4d)
    ex=(dc<ed) if up else (dc>ed)
    return cond,('ma',ex)

def run(F,side,ent,exr,rand=None,k0=120):
    o,h,l,c=F['open'],F['high'],F['low'],F['close']; dec=F['dec']; atr=F['atr']; dc=F['dc']; up=side=='L'; s=1 if up else -1
    out=[]; n=len(c); k=k0
    while k<len(dec)-1:
        go=ent[k] if rand is None else (k in rand)
        if not go or np.isnan(atr[k]): k+=1; continue
        i0=dec[k]+1; ep=o[i0]*(1+SLIP*s); best=dc[k]
        stop=ep*(1-HARD) if up else ep*(1+HARD)
        if exr[0]=='trail' and rand is None:
            stop=max(stop,dc[k]-exr[1]*atr[k]) if up else min(stop,dc[k]+exr[1]*atr[k])
        hold_h=rand[k] if rand is not None else None
        i=i0; kk=k; done=False
        while i<n-1:
            if up and l[i]<=stop:
                px=min(stop,o[i])-PEN*(min(stop,o[i])-l[i]); out.append((i0,i,s,px/ep-1)); done=True; break
            if (not up) and h[i]>=stop:
                px=max(stop,o[i])+PEN*(h[i]-max(stop,o[i])); out.append((i0,i,s,ep/px-1)); done=True; break
            if hold_h is not None and i-i0+1>=hold_h:
                px=o[i+1]*(1-SLIP*s); out.append((i0,i+1,s,(px/ep-1)*s)); done=True; break
            if kk+1<len(dec) and i==dec[kk+1]:
                kk+=1
                if hold_h is None:
                    if exr[0]=='trail':
                        best=max(best,dc[kk]) if up else min(best,dc[kk])
                        stop=max(stop,best-exr[1]*atr[kk]) if up else min(stop,best+exr[1]*atr[kk])
                    elif exr[1][kk]:
                        px=o[i+1]*(1-SLIP*s); out.append((i0,i+1,s,(px/ep-1)*s)); done=True; break
            i+=1
        if not done: break
        k=int(np.searchsorted(dec,out[-1][1]))
    t=pd.DataFrame(out,columns=['eb','xb','s','ret'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','s','ret','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST-FUND_H*t.hold
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t

def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
GRIDS={'T':[(20,40,55),(2,3,4)],'M':[(20,50,100),(20,50,100)]}
