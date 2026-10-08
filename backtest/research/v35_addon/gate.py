import sys,pandas as pd,numpy as np
SP=sys.argv[1]
df=pd.read_csv(SP+'/ETH_1h_long.csv'); df['dt']=pd.to_datetime(df.datetime)
COST=9.0; NOT=4000
def sim(d,N=120,M=12,SL=0.04,rand_entries=None):
    o,h,l,c=[d[x].values for x in ['open','high','low','close']]; dt=d.dt.values
    cs=pd.Series(c); mx=cs.shift(1).rolling(N).max().values; mn=cs.shift(1).rolling(M).min().values
    out=[];pos=None;n=len(c)
    for i in range(max(N,M)+1,n):
        if pos is not None:
            ep,eb=pos; st=ep*(1-SL)
            if l[i]<=st: px=st-0.25*(st-l[i]); out.append((eb,i,px/ep-1)); pos=None; continue
            if c[i]<mn[i]: out.append((eb,i,c[i]/ep-1)); pos=None
            continue
        go = (c[i]>mx[i]) if rand_entries is None else (i in rand_entries)
        if go: pos=(c[i],i)
    t=pd.DataFrame(out,columns=['eb','xb','ret']); t['pnl']=t.ret*NOT-COST
    t['exit_dt']=pd.to_datetime(dt[t.xb]) if len(t) else pd.Series(dtype='datetime64[ns]'); t['entry_dt']=pd.to_datetime(dt[t.eb]) if len(t) else None
    return t
S0,MID,E0=pd.Timestamp('2024-10-08'),pd.Timestamp('2025-10-08'),pd.Timestamp('2026-10-09')
def win(t,a,b): return t[(t.entry_dt>=a)&(t.entry_dt<b)]
def mdd(p): c=p.cumsum(); return (c.cummax()-c).max()
t=sim(df); W=win(t,S0,E0); IS=win(t,S0,MID); OOS=win(t,MID,E0)
res={}
print('cand 2y n',len(W),'pnl',round(W.pnl.sum()),'wr',round((W.pnl>0).mean()*100,1),'PF',round(W.pnl[W.pnl>0].sum()/-W.pnl[W.pnl<0].sum(),2),'mdd',round(mdd(W.sort_values('exit_dt').pnl)))
print('G1 IS',round(IS.pnl.sum()),len(IS),' G2 OOS',round(OOS.pnl.sum()),len(OOS))
base=pd.read_pickle(SP+'/base_trades.pkl'); base['entry_dt']=pd.to_datetime(base.entry_dt); base['exit_dt']=pd.to_datetime(base.exit_dt); base['pnl']=base.pnl_usd
for nm,a,b in [('IS',S0,MID),('OOS',MID,E0),('2y',S0,E0)]:
    bb=win(base,a,b); cc=pd.concat([bb[['exit_dt','pnl']],win(t,a,b)[['exit_dt','pnl']]]).sort_values('exit_dt')
    print('G3',nm,'base',round(bb.pnl.sum()),'comb',round(cc.pnl.sum()),'base mdd',round(mdd(bb.sort_values('exit_dt').pnl)),'comb mdd',round(mdd(cc.pnl)))
print('G4 neighborhood OOS (cand OOS',round(OOS.pnl.sum()),')')
for N in (96,120,144):
    print(N,[ (M,round(win(sim(df,N,M),MID,E0).pnl.sum()),round(win(sim(df,N,M),S0,MID).pnl.sum())) for M in (9,12,15)])
# G5 random entries
rng=np.random.default_rng(42); idx=np.where((df.dt>=S0)&(df.dt<E0))[0]
rnd=[]
for k in range(100):
    ent=set(rng.choice(idx,size=len(W)*3,replace=False))
    r=win(sim(df,rand_entries=ent),S0,E0)
    r=r.iloc[:len(W)] if len(r)>len(W) else r
    rnd.append(r.pnl.sum())
rnd=np.array(rnd); print('G5 random mean',round(rnd.mean()),'p95',round(np.percentile(rnd,95)),'cand pctile',round((rnd<W.pnl.sum()).mean()*100))
# G6
isp,oop=IS.pnl.sum(),OOS.pnl.sum(); print('G6 fwd degr %',round((1-oop/isp)*100,1),'bwd degr %',round((1-isp/oop)*100,1))
# G7 6 folds
edges=pd.date_range(S0,E0,periods=7); print('G7',[round(win(t,edges[i],edges[i+1]).pnl.sum()) for i in range(6)])
# G8 reverse
r=df.iloc[::-1].reset_index(drop=True).copy(); r[['open','close']]=r[['close','open']].values; r['dt']=df.dt.values
tr=sim(r); print('G8 reversed full-5.5y',round(tr.pnl.sum()),' last-2y-equivalent',round(tr[tr.entry_dt<pd.Timestamp('2021-02-16')+(E0-S0)].pnl.sum()), 'orig 5.5y',round(t.pnl.sum()))
# G9
m=W.groupby(W.exit_dt.dt.to_period('M')).pnl.sum(); print('G9 best',m.idxmax(),round(m.max()),'without best',round(W.pnl.sum()-m.max()),'without top2',round(W.pnl.sum()-m.nlargest(2).sum()),'top4 share',round(m.nlargest(4).sum()))
