import sys,pandas as pd,numpy as np,itertools
SP=sys.argv[1]; sys.argv=[sys.argv[0],SP]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'sleeve.py'),encoding='utf-8').read().split("if __name__=='__main__':")[0])
base=pd.read_pickle(SP+'/base_trades.pkl'); base['exit_dt']=pd.to_datetime(base.exit_dt)
b2=base[base.exit_dt>='2024-10-08'].sort_values('exit_dt'); bc=b2.pnl_usd.cumsum(); bmdd=(bc.cummax()-bc).max()
print('base 2y',round(b2.pnl_usd.sum()),'mdd',round(bmdd),'A(24/10-25/09)',round(b2[b2.exit_dt<'2025-10-01'].pnl_usd.sum()),'B(25/10-26/10)',round(b2[b2.exit_dt>='2025-10-01'].pnl_usd.sum()))
rows=[]
for side,N,G,F,M,SL in itertools.product('LS',(24,48,72,120),(0,50,75),(0,1),(12,24,48),(0.04,0.06)):
    t=sim(side,N,G,F,M,SL); t['exit_dt']=pd.to_datetime(t.exit_dt); t=t[t.exit_dt>='2024-10-08']
    A=t[t.exit_dt<'2025-10-01'].pnl.sum(); B=t[t.exit_dt>='2025-10-01'].pnl.sum()
    cm=pd.concat([b2[['exit_dt','pnl_usd']].rename(columns={'pnl_usd':'pnl'}),t[['exit_dt','pnl']]]).sort_values('exit_dt').pnl.cumsum()
    y26=t[t.exit_dt>='2026-01-01'].pnl.sum(); aug=t[(t.exit_dt>='2026-08-01')&(t.exit_dt<'2026-10-01')].pnl.sum()
    rows.append(dict(side=side,N=N,G=G,F=F,M=M,SL=SL,n=len(t),wr=(t.pnl>0).mean()*100,p2y=t.pnl.sum(),A=A,B=B,y26=y26,augsep=aug,cmdd=(cm.cummax()-cm).max()))
R=pd.DataFrame(rows); pd.set_option('display.width',250)
for s in 'LS':
    r=R[R.side==s]
    print(s,'2y>0',(r.p2y>0).sum(),'A>0&B>0',((r.A>0)&(r.B>0)).sum(),'/144')
    print(r[(r.A>0)&(r.B>0)].sort_values('p2y',ascending=False).head(15).round(0).to_string())
R.to_pickle(SP+'/grid2y.pkl')
