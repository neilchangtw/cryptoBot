import inspect, numpy as np, pandas as pd, sys
import engine as E
df=pd.read_csv('data/eth1h.csv',parse_dates=['datetime']); dts=df['datetime'].values
src=inspect.getsource(E.simulate_v14_detailed)
for old,new in [("not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i]):","not np.isnan(pL[i]) and pL[i] < L_GK_TH and brk_up[i] and ('okL' not in ind or ind['okL'][i])):"),
                ("not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i]):","not np.isnan(pS[i]) and pS[i] < S_GK_TH and brk_dn[i] and ('okS' not in ind or ind['okS'][i])):")]:
    assert src.count(old)==1; src=src.replace(old,new)
BASE={k:getattr(E,k) for k in ['CB_CONSEC','CB_CONSEC_CD','CB_DAILY','CB_L_MONTH','CB_S_MONTH','L_BLK_H','L_BLK_D','S_BLK_H','S_BLK_D','R_TH_UP','R_TH_SIDE']}
P=[('P1',pd.Timestamp('2021-03-01'),pd.Timestamp('2024-07-01')),('P2',pd.Timestamp('2024-07-01'),pd.Timestamp('2026-05-01')),('P3',pd.Timestamp('2026-05-01'),pd.Timestamp('2030-01-01'))]
def vol_rank(w=30):
    h,l,c=(df[k].to_numpy(float) for k in('high','low','close')); pc=np.roll(c,1); pc[0]=np.nan
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    atrp=pd.Series(tr).rolling(14,min_periods=14).mean()/c*100
    v=atrp.shift(1).rolling(w*24,min_periods=w*24).median()
    return (v.rolling(730*24,min_periods=180*24).rank(pct=True)*100).to_numpy()
def run(slip=0,mask=None,start=None,**ov):
    ns=dict(E.__dict__); ns.update(BASE); ns.update(ov)
    exec(inspect.getsource(E.compute_indicators),ns); exec(src,ns)
    ind=ns['compute_indicators'](df)
    if mask: mask(ind,ns)
    t=pd.DataFrame(ns['simulate_v14_detailed'](ind,dts,realistic=True,slip_bps=slip))
    t['e']=pd.to_datetime(t.entry_dt); t=t[t.e>=(start or P[0][1])].sort_values('exit_dt')
    r={}
    for n,a,b in P:
        s=t[(t.e>=a)&(t.e<b)]; r[n]=round(s.pnl_usd.sum()); r[n+'n']=len(s)
    eq=t.pnl_usd.cumsum(); r['all']=round(t.pnl_usd.sum()); r['mdd']=round((eq.cummax()-eq).max()); r['n']=len(t); r['t']=t
    return r
def ct_mask(q):
    def f(ind,ns):
        rk=vol_rank(); sl=ind['slope']
        reg=np.array([ns['_classify_regime'](x) for x in sl])
        low=rk<q   # NaN -> False
        ind['okL']=~(low&(reg=='DOWN')); ind['okS']=~(low&np.isin(reg,['UP','MILD_UP']))
    return f
CASES={'BASE':{},
 'A_OFF':dict(CB_CONSEC=10**6),'A_N4CD12':dict(CB_CONSEC_CD=12),'A_N5CD24':dict(CB_CONSEC=5),'A_N3CD24':dict(CB_CONSEC=3),'A_N4CD48':dict(CB_CONSEC_CD=48),
 'B_noDaily':dict(CB_DAILY=-10**9),'B_noLmonth':dict(CB_L_MONTH=-10**9),'B_noSmonth':dict(CB_S_MONTH=-10**9),
 'C_noLhour':dict(L_BLK_H=set()),'C_noLdow':dict(L_BLK_D=set()),'C_noShour':dict(S_BLK_H=set()),'C_noSdow':dict(S_BLK_D=set()),
 'C_noLup':dict(R_TH_UP=10**6),'C_noSside':dict(R_TH_SIDE=0.0),
 'D_q30':dict(mask=ct_mask(30)),'D_q20':dict(mask=ct_mask(20)),'D_q40':dict(mask=ct_mask(40)),'D_q50':dict(mask=ct_mask(50))}
if __name__=='__main__':
    rows=[]
    for slip in (0,2):
        base=None
        for k,ov in CASES.items():
            r=run(slip,**ov); r.pop('t')
            if k=='BASE': base=r
            rows.append(dict(slip=slip,case=k,**r,dP1=r['P1']-base['P1'],dP2=r['P2']-base['P2'],dP3=r['P3']-base['P3'],dMDD=r['mdd']-base['mdd']))
    o=pd.DataFrame(rows); o.to_csv('v46_results.csv',index=False); print(o.to_string())
