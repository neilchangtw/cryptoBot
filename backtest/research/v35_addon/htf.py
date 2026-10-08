import sys,pandas as pd,numpy as np,itertools,json
NOT=4000; COST=9.0; SLIP=0.0002; FUND_H=NOT*0.0001/8; PEN=0.25; SL=0.05
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(SP,tf,rev=False):
    d=pd.read_csv(SP+'/ETH_1h_long.csv'); d['dt']=pd.to_datetime(d.datetime)
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=d.dt.values; d=r
    # resample on UTC boundaries: UTC+8 -> shift 8h
    d=d.set_index(d.dt-pd.Timedelta(hours=8))
    rule={'4h':'4h','1d':'1D'}[tf]
    a=d.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','dt':'first'}).dropna()
    a['hours']=d.resample(rule).size().reindex(a.index).values; a=a[a.hours>=(4 if tf=='4h' else 24)]
    return a.reset_index(drop=True), (4 if tf=='4h' else 24)
def feats(a,side):
    o,h,l,c=[a[x].values for x in ['open','high','low','close']]
    gk=0.5*np.log(h/l)**2-(2*np.log(2)-1)*np.log(c/o)**2; g=pd.Series(gk)
    s,L=(5,20) if side=='L' else (10,30)
    ratio=g.rolling(s).mean()/g.rolling(L).mean(); pc=ratio.shift(1).rolling(100).rank(pct=True).values*100
    cs=pd.Series(c); bu=c>cs.shift(1).rolling(15).max().values; bd=c<cs.shift(1).rolling(15).min().values
    return dict(o=o,h=h,l=l,c=c,pc=pc,bu=bu,bd=bd,dt=a.dt.values)
def run(F,side,th,H,TP,hpb,rand=None,i0=130):
    o,h,l,c=F['o'],F['h'],F['l'],F['c']; up=side=='L'; n=len(c); out=[]; pos=None; i=i0
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
        x=(age>=pos['hold']) if pos['hold'] is not None else (age>=H or (TP and mv>=TP))
        if x:
            px=o[i+1]*(1-SLIP if up else 1+SLIP); out.append((pos['eb'],i+1,(px/ep-1) if up else (ep/px-1))); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','ret'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','ret','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST-FUND_H*t.hold*hpb
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][np.minimum(t.xb,len(F['dt'])-1)]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
FAMS={'4hL':('4h','L'),'4hS':('4h','S'),'1dL':('1d','L'),'1dS':('1d','S')}
GRIDS={'4h':[(15,25,35),(6,12,24),(0.04,0.08,0)],'1d':[(15,25,35),(3,5,10),(0.08,0.15,0)]}
