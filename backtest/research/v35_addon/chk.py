import sys,pandas as pd,numpy as np
SP=sys.argv[1]; sys.argv=[sys.argv[0],SP]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'sleeve.py'),encoding='utf-8').read().split("if __name__=='__main__':")[0])
R=pd.read_pickle(SP+'/grid.pkl'); pd.set_option('display.width',250)
r=R[(R.side=='L')&(R.F==1)]
for k in ['s_pnl','s_OOS','s_pnl2y']:
    print(k); print(r.pivot_table(index=['N','G'],columns=['M','SL'],values=k).round(0).to_string())
print('L F=1 all:',len(r),'pnl>0',(r.s_pnl>0).sum(),'OOS>0',(r.s_OOS>0).sum(),'2y>0',(r.s_pnl2y>0).sum())
base=pd.read_pickle(SP+'/base_trades.pkl'); base['exit_dt']=pd.to_datetime(base.exit_dt)
for N,G,M,SL in [(48,75,48,0.06),(48,75,48,0.04),(48,50,48,0.06),(72,75,48,0.06),(120,75,48,0.06)]:
    t=sim('L',N,G,1,M,SL); t['exit_dt']=pd.to_datetime(t.exit_dt)
    y=t.groupby(t.exit_dt.dt.year).pnl.sum().round(0).to_dict(); yb=base.groupby(base.exit_dt.dt.year).pnl_usd.sum().round(0).to_dict()
    print((N,G,M,SL),'n',len(t),'avg hold h',round((t.xb-t.eb).mean(),1),'max loss',round(t.pnl.min()),'best',round(t.pnl.max()),'sleeve/yr',y)
print('base/yr',yb)
