import sys,pandas as pd,numpy as np
SP=sys.argv[1]; sys.argv=[sys.argv[0],SP]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'sleeve.py'),encoding='utf-8').read().split("if __name__=='__main__':")[0])
R=pd.read_pickle(SP+'/grid.pkl')
for side,N,G,F,M,SL in [('L',48,75,1,48,0.06),('L',48,50,1,48,0.06),('S',120,50,0,48,0.06),('S',120,75,0,48,0.06)]:
    t=sim(side,N,G,F,M,SL); t['exit_dt']=pd.to_datetime(t.exit_dt)
    x=t[t.exit_dt>='2026-01-01']; print(side,N,G,F,M,SL,'2026 by month',x.groupby(x.exit_dt.dt.month).pnl.sum().round(0).to_dict())
r=R[R.side=='S']; print('S all 2y>0',(r.s_pnl2y>0).sum(),'/',len(r),' OOS>0',(r.s_OOS>0).sum())
# combined 2y for every config vs base 2y
print('combined 2y >= base 2y : L',(R[R.side=="L"].c_pnl2y>=6409.4).sum(),'/144  S',(R[R.side=="S"].c_pnl2y>=6409.4).sum(),'/144')
