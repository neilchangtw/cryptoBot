import os,sys,importlib.util,pandas as pd,numpy as np,itertools
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..'))
spec=importlib.util.spec_from_file_location('e',os.path.join(ROOT,'backtest','research','v14_export_trades.py')); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
SP=sys.argv[1]
df=pd.read_csv(SP+'/ETH_1h_long.csv'); dt=pd.to_datetime(df.datetime).values
I=e.compute_indicators(df); o,h,l,c=I['o'],I['h'],I['l'],I['c']; pl=np.nan_to_num(I['pctile_L'],nan=50); ps=np.nan_to_num(I['pctile_S'],nan=50); sl_=np.nan_to_num(I['slope'])
n=len(c); NOT=4000; COST=9.0; WARM=310
cs=pd.Series(c)
mx={N:cs.shift(1).rolling(N).max().values for N in (24,48,72,120)}
mn={N:cs.shift(1).rolling(N).min().values for N in (12,24,48,72,120)}
def sim(side,N,G,F,M,SL):
    out=[];pos=None
    up=(side=='L')
    brk=(c>mx[N]) if up else (c<mn[N])
    gk=pl if up else ps
    ext=mn[M] if up else cs.shift(1).rolling(M).max().values
    for i in range(WARM,n):
        if pos is not None:
            ep,eb=pos
            if up and l[i]<=ep*(1-SL): px=ep*(1-SL)-0.25*(ep*(1-SL)-l[i]); out.append((eb,i,'L',(px/ep-1))); pos=None; continue
            if (not up) and h[i]>=ep*(1+SL): px=ep*(1+SL)+0.25*(h[i]-ep*(1+SL)); out.append((eb,i,'S',(ep/px-1))); pos=None; continue
            if (up and c[i]<ext[i]) or ((not up) and c[i]>ext[i]):
                out.append((eb,i,side,(c[i]/ep-1) if up else (ep/c[i]-1))); pos=None
            continue
        if brk[i] and gk[i]>=G and (F==0 or (up and sl_[i]>0) or ((not up) and sl_[i]<0)):
            pos=(c[i],i)
    t=pd.DataFrame(out,columns=['eb','xb','side','ret'])
    t['pnl']=t.ret*NOT-COST; t['exit_dt']=dt[t.xb] if len(t) else []
    return t
if __name__=='__main__':
    base=pd.read_pickle(SP+'/base_trades.pkl')[['exit_dt','pnl_usd','side']].rename(columns={'pnl_usd':'pnl'})
    base['exit_dt']=pd.to_datetime(base.exit_dt)
    hc=pd.Series(c,index=pd.to_datetime(dt)); ho=pd.Series(o,index=pd.to_datetime(dt))
    mret=(hc.resample('ME').last()/ho.resample('ME').first()-1)
    mret.index=mret.index.to_period('M')
    up_m=set(mret[mret>0.15].index); dn_m=set(mret[mret<-0.15].index)
    def metrics(t):
        t=t.sort_values('exit_dt'); cum=t.pnl.cumsum(); m=t.exit_dt.dt.to_period('M')
        two=t[t.exit_dt>='2024-10-08']
        return dict(n=len(t),pnl=t.pnl.sum(),pnl2y=two.pnl.sum(),IS=t[t.exit_dt<'2024-01-01'].pnl.sum(),OOS=t[t.exit_dt>='2024-01-01'].pnl.sum(),
            mdd=(cum.cummax()-cum).max(),wr=(t.pnl>0).mean()*100,up=t[m.isin(up_m)].pnl.sum(),dn=t[m.isin(dn_m)].pnl.sum())
    B=metrics(base); print('BASE',{k:round(v,1) for k,v in B.items()}); print('upmonths',len(up_m),'dnmonths',len(dn_m))
    rows=[]
    for side,N,G,F,M,SL in itertools.product('LS',(24,48,72,120),(0,50,75),(0,1),(12,24,48),(0.04,0.06)):
        t=sim(side,N,G,F,M,SL); t['exit_dt']=pd.to_datetime(t.exit_dt)
        sm=metrics(t); comb=metrics(pd.concat([base,t[['exit_dt','pnl','side']]]))
        rows.append(dict(side=side,N=N,G=G,F=F,M=M,SL=SL,**{'s_'+k:v for k,v in sm.items()},**{'c_'+k:v for k,v in comb.items()}))
    R=pd.DataFrame(rows); R.to_pickle(SP+'/grid.pkl')
    pd.set_option('display.width',250)
    for side in 'LS':
        r=R[R.side==side].sort_values('s_pnl',ascending=False)
        print(side,'sleeve positive total:',(r.s_pnl>0).sum(),'/',len(r),' IS&OOS both>0:',((r.s_IS>0)&(r.s_OOS>0)).sum())
        print(r.head(12)[['N','G','F','M','SL','s_n','s_wr','s_pnl','s_IS','s_OOS','s_pnl2y','s_up','s_dn','s_mdd','c_pnl','c_mdd']].round(0).to_string())
