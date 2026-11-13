import sys, itertools, pickle, pandas as pd, numpy as np
sys.path.insert(0,'/home/user/v46')
from multiprocessing import Pool
import v46
END=sys.argv[1] if len(sys.argv)>1 else '2026-01-01'
_d=pd.read_csv('/home/user/v52/btc1h.csv',parse_dates=['datetime']); _d=_d[_d.datetime<END].reset_index(drop=True)
v46.df=_d; v46.dts=_d['datetime'].values
LG=[dict(L_GK_TH=g,L_BRK=b,L_TP=tp,L_MH=mh) for g,b,tp,mh in itertools.product((15,25,35),(10,15,20),(0.015,0.025,0.035),(4,6,8))]
SG=[dict(S_GK_TH=g,S_BRK=b,S_TP=tp,S_MH=mh) for g,b,tp,mh in itertools.product((25,35,45),(10,15,20),(0.01,0.015,0.02),(8,10,12))]
def job(a):
    side,ov,slip=a; t=v46.run(slip,start=pd.Timestamp('2020-09-01'),**ov)['t']; t=t[t.side==side][['side','e','exit_dt','pnl_usd','exit_reason']].copy(); t['x']=pd.to_datetime(t.exit_dt)
    return slip,side,tuple(ov.values()),t
if __name__=='__main__':
    with Pool(4) as p: res=p.map(job,[('L',o,s) for o in LG for s in (2,4)]+[('S',o,s) for o in SG for s in (2,4)])
    pickle.dump(res,open(f'v52_grid_{END}.pkl','wb')); print(len(res))
