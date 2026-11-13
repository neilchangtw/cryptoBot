import pickle, pandas as pd, numpy as np
res=pickle.load(open('v47_grid.pkl','rb'))
G={'L':{},'S':{}}
for s,k,t in res: G[s][k]=t
LIVE={'L':(25,15,0.035,6),'S':(35,15,0.02,10)}
folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2027) for m in (1,7)][1:]+[pd.Timestamp('2027-01-01')]
def wf(mode):
    out=[];picks=[]
    for a,b in zip(folds[:-1],folds[1:]):
        for s in 'LS':
            best=None
            for k,t in G[s].items():
                tr=t[t.x<a]
                if mode=='roll24': tr=tr[tr.e>=a-pd.DateOffset(months=24)]
                v=tr.pnl_usd.sum()
                if best is None or v>best[0]: best=(v,k)
            t=G[s][best[1]]; out.append(t[(t.e>=a)&(t.e<b)]); picks.append((a.date(),s,best[1],round(best[0])))
    return pd.concat(out).sort_values('x'),picks
def stat(t):
    eq=t.pnl_usd.cumsum(); r30=t.pnl_usd.rolling(30).sum().min()
    return dict(n=len(t),pnl=round(t.pnl_usd.sum()),mdd=round((eq.cummax()-eq).max()),worst30=round(r30),wr=round((t.pnl_usd>0).mean()*100))
def half(t): return t.groupby(t.e.dt.year.astype(str)+'H'+((t.e.dt.month>6)+1).astype(str)).pnl_usd.sum().round()
fx=pd.concat([G[s][LIVE[s]] for s in 'LS']); fx=fx[fx.e>=folds[0]].sort_values('x')
e,pe=wf('expand'); r,pr=wf('roll24')
print('fixed',stat(fx)); print('WF expand',stat(e)); print('WF roll24',stat(r))
print(pd.DataFrame({'fixed':half(fx),'expand':half(e),'roll24':half(r)}).to_string())
for s in 'LS':
    print(s,'fixed',stat(fx[fx.side==s]),'exp',stat(e[e.side==s]),'roll',stat(r[r.side==s]))
print('picks expand'); [print(p) for p in pe]
print('picks roll'); [print(p) for p in pr]
# distribution of all grid configs, full period from 2021-07, and by era
for s in 'LS':
    tot={k:t[t.e>=folds[0]].pnl_usd.sum() for k,t in G[s].items()}
    p1={k:t[(t.e>=folds[0])&(t.e<'2024-07-01')].pnl_usd.sum() for k,t in G[s].items()}
    p2={k:t[(t.e>='2024-07-01')].pnl_usd.sum() for k,t in G[s].items()}
    v=np.array(list(tot.values())); a=np.array(list(p1.values())); b=np.array(list(p2.values()))
    print(s,'grid total: median',round(np.median(v)),'min',round(v.min()),'max',round(v.max()),'%>0',round((v>0).mean()*100),'| live',round(tot[LIVE[s]]),'rank',int((v>tot[LIVE[s]]).sum())+1)
    print('  P1(21-07~24-06) median',round(np.median(a)),'%>0',round((a>0).mean()*100),'live',round(p1[LIVE[s]]),'| P2 median',round(np.median(b)),'%>0',round((b>0).mean()*100),'live',round(p2[LIVE[s]]),'| corr P1-P2 across configs',round(np.corrcoef(a,b)[0,1],2))
