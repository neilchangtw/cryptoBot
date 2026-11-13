import sys, pickle, pandas as pd, numpy as np
END=sys.argv[1]; res=pickle.load(open(f'v52_grid_{END}.pkl','rb'))
folds=[pd.Timestamp(f'{y}-{m:02d}-01') for y in range(2021,2028) for m in (1,7)][1:]; folds=[f for f in folds if f<=pd.Timestamp(END)]
def half(t): return t.groupby(t.e.dt.year.astype(str)+'H'+((t.e.dt.month>6)+1).astype(str)).pnl_usd.sum().round()
def st(t):
    eq=t.pnl_usd.cumsum(); return dict(n=len(t),pnl=round(t.pnl_usd.sum()),wr=round((t.pnl_usd>0).mean()*100),mdd=round((eq.cummax()-eq).max()))
G5={'S':['2021H2','2022H2','2023H1','2023H2'],'L':['2023H2','2024H1','2024H2']}
for slip in (2,4):
    for s in 'SL':
        G={k:t for sl,sd,k,t in res if sl==slip and sd==s}
        out=[];picks=[]
        for a,b in zip(folds[:-1],folds[1:]):
            best=max(G,key=lambda k:G[k][G[k].x<a].pnl_usd.sum()); t=G[best]; out.append(t[(t.e>=a)&(t.e<b)]); picks.append((str(a.date()),best))
        w=pd.concat(out).sort_values('x'); hw=half(w)
        print('slip',slip,s,'WF',st(w)); print('  ',hw.to_dict())
        if slip==2:
            a=np.array([G[k][(G[k].e>='2021-07-01')&(G[k].e<'2023-07-01')].pnl_usd.sum() for k in G]); b=np.array([G[k][(G[k].e>='2023-07-01')&(G[k].e<'2026-01-01')].pnl_usd.sum() for k in G])
            print('   G2 both>0 %',round(((a>0)&(b>0)).mean()*100),'A>0',round((a>0).mean()*100),'B>0',round((b>0).mean()*100),'medians',round(np.median(a)),round(np.median(b)))
            print('   G3',int((hw>0).sum()),'/',len(hw),'worst',hw.min(),'| G5',hw.reindex(G5[s]).sum()); print('   picks',picks)
            w.to_pickle(f'v52_wf_{s}_{END}.pkl')
