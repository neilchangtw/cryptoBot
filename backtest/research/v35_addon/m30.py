import sys,pandas as pd,numpy as np,itertools,json
NOT=4000; COST=9.0; SLIP=0.0002; FUND_B=NOT*0.0001/16; PEN=0.25
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(SP,rev=False):
    d=pd.read_csv(SP+'/ETH_30m_long.csv'); d['dt']=pd.to_datetime(d.datetime)
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=d.dt.values; d=r
    return d
def feats(a,side):
    o,h,l,c=[a[x].values for x in ['open','high','low','close']]
    g=pd.Series(0.5*np.log(h/l)**2-(2*np.log(2)-1)*np.log(c/o)**2); s,L=(5,20) if side=='L' else (10,30)
    pc=(g.rolling(s).mean()/g.rolling(L).mean()).shift(1).rolling(100).rank(pct=True).values*100
    cs=pd.Series(c); return dict(o=o,h=h,l=l,c=c,pc=pc,bu=c>cs.shift(1).rolling(30).max().values,bd=c<cs.shift(1).rolling(30).min().values,dt=a.dt.values)
def run(F,side,th,TP,MH,rand=None,i0=200):
    o,h,l,c=F['o'],F['h'],F['l'],F['c']; up=side=='L'; SL=0.035 if up else 0.04; n=len(c); out=[]; pos=None; i=i0
    ent=(F['pc']<th)&(F['bu'] if up else F['bd'])
    while i<n-1:
        if pos is None:
            go=ent[i] if rand is None else (i in rand)
            if go: pos=dict(ep=o[i+1]*(1+SLIP if up else 1-SLIP),eb=i+1,hold=(rand[i] if rand is not None else None))
            i+=1; continue
        ep=pos['ep']; st=ep*(1-SL) if up else ep*(1+SL)
        if up and l[i]<=st: px=min(st,o[i])-PEN*(min(st,o[i])-l[i]); out.append((pos['eb'],i,px/ep-1)); pos=None; i+=1; continue
        if (not up) and h[i]>=st: px=max(st,o[i])+PEN*(h[i]-max(st,o[i])); out.append((pos['eb'],i,ep/px-1)); pos=None; i+=1; continue
        age=i-pos['eb']+1; mv=(c[i]/ep-1) if up else (ep/c[i]-1)
        x=(age>=pos['hold']) if pos['hold'] is not None else (age>=MH or mv>=TP)
        if x: px=o[i+1]*(1-SLIP if up else 1+SLIP); out.append((pos['eb'],i+1,(px/ep-1) if up else (ep/px-1))); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','ret'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','ret','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST-FUND_B*t.hold
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
GRID=[(15,25,35),(0.015,0.025,0.035),(6,12,24)]
