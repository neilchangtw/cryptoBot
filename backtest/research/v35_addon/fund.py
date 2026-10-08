import sys,pandas as pd,numpy as np,itertools,json
NOT=4000; COST=9.0; SLIP=0.0002; PEN=0.25; SL=0.05
IS0,OOS0,END=pd.Timestamp('2021-02-16'),pd.Timestamp('2024-10-08'),pd.Timestamp('2026-10-09')
def load(SP,rev=False):
    d=pd.read_csv(SP+'/ETH_1h_long.csv'); d['dt']=pd.to_datetime(d.datetime)
    f=pd.read_csv(SP+'/funding_long.csv',parse_dates=['t']); f['bt']=f.t+pd.Timedelta(hours=8)
    d=d.merge(f[['bt','rate']],left_on='dt',right_on='bt',how='left')
    pr=f.rate.rank().values # placeholder
    f['pct']=f.rate.rolling(271).apply(lambda s:(s[:-1]<s[-1]).mean()*100,raw=True)
    d=d.merge(f[['bt','pct']],on='bt',how='left')
    if rev:
        r=d.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=d.dt.values
        # funding & pct stay attached to their bars (reversed order), reversed meaning
        d=r
    return d
def feats(d): return dict(o=d.open.values,h=d.high.values,l=d.low.values,c=d.close.values,rate=d.rate.fillna(0).values,pct=d.pct.values,dt=d.dt.values)
def run(F,side,P,H,rand=None,i0=320):
    o,h,l,c=F['o'],F['h'],F['l'],F['c']; up=side=='L'; n=len(c); out=[]; pos=None; i=i0
    ent=(F['pct']<=100-P) if up else (F['pct']>=P); ent=np.nan_to_num(ent.astype(float))>0
    while i<n-1:
        if pos is None:
            go=ent[i] if rand is None else (i in rand)
            if go: pos=dict(ep=o[i+1]*(1+SLIP if up else 1-SLIP),eb=i+1,hold=(rand[i] if rand is not None else H),fund=0.0)
            i+=1; continue
        pos['fund']+=(-1 if up else 1)*F['rate'][i]*NOT
        ep=pos['ep']; st=ep*(1-SL) if up else ep*(1+SL)
        if up and l[i]<=st: px=min(st,o[i])-PEN*(min(st,o[i])-l[i]); out.append((pos['eb'],i,px/ep-1,pos['fund'])); pos=None; i+=1; continue
        if (not up) and h[i]>=st: px=max(st,o[i])+PEN*(h[i]-max(st,o[i])); out.append((pos['eb'],i,ep/px-1,pos['fund'])); pos=None; i+=1; continue
        if i-pos['eb']+1>=pos['hold']:
            px=o[i+1]*(1-SLIP if up else 1+SLIP); out.append((pos['eb'],i+1,(px/ep-1) if up else (ep/px-1),pos['fund'])); pos=None; i+=2; continue
        i+=1
    t=pd.DataFrame(out,columns=['eb','xb','ret','fund'])
    if len(t)==0: return pd.DataFrame(columns=['eb','xb','ret','fund','hold','pnl','entry_dt','exit_dt'])
    t['hold']=t.xb-t.eb+1; t['pnl']=t.ret*NOT-COST+t.fund
    t['entry_dt']=pd.to_datetime(F['dt'][t.eb]); t['exit_dt']=pd.to_datetime(F['dt'][t.xb]); return t
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
GRID=[(90,95,98),(24,48,96)]
