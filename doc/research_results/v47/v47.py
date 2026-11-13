import itertools, pandas as pd, numpy as np, pickle
from multiprocessing import Pool
import v46
LG=[dict(L_GK_TH=g,L_BRK=b,L_TP=tp,L_MH=mh) for g,b,tp,mh in itertools.product((15,25,35),(10,15,20),(0.025,0.035,0.045),(4,6,8))]
SG=[dict(S_GK_TH=g,S_BRK=b,S_TP=tp,S_MH=mh) for g,b,tp,mh in itertools.product((25,35,45),(10,15,20),(0.015,0.02,0.03),(8,10,12))]
def job(a):
    side,ov=a; t=v46.run(2,start=pd.Timestamp('2020-09-01'),**ov)['t']; t=t[t.side==side][['side','e','exit_dt','pnl_usd','exit_reason']].copy(); t['x']=pd.to_datetime(t.exit_dt)
    return side,tuple(ov.values()),t
if __name__=='__main__':
    with Pool(4) as p: res=p.map(job,[('L',o) for o in LG]+[('S',o) for o in SG])
    pickle.dump(res,open('v47_grid.pkl','wb')); print(len(res))
