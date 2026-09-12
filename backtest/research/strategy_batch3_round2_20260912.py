"""Diagnose funding-debit budget actions, without candidate returns."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch3_20260912 as b

def debit_budget(d,funding):
    assert funding.time.notna().all() and np.isfinite(funding.cashflow).all()
    times=pd.DatetimeIndex(d.datetime)+pd.Timedelta(hours=1)
    # Counters in the engine are keyed by open-bar day/month. Match that exact
    # convention rather than silently changing the reset clock as another factor.
    opens=pd.DatetimeIndex(d.datetime)
    result=pd.DataFrame({'bar':np.arange(len(d)),'decision_ts':times,'daily_debit':0.,'L_month_debit':0.,'S_month_debit':0.})
    for f in funding.itertuples():
        assert f.side in ['L','S']
        if not f.included or f.cashflow>=0:continue
        available=times>f.time
        same_day=opens.normalize()==f.time.normalize()
        same_month=(opens.year==f.time.year)&(opens.month==f.time.month)
        result.loc[available&same_day,'daily_debit']+=f.cashflow
        result.loc[available&same_month,f.side+'_month_debit']+=f.cashflow
    return result

def affected(gates,states,budget,engine):
    # observe is after entry; entering does not debit these original PnL counters.
    # Therefore the saved counters equal the pre-entry counters at each gate.
    x=gates[['bar','side','decision_ts']].merge(states[['bar','d_pnl','l_m_pnl','s_m_pnl']],on='bar',validate='many_to_one')
    x=x.merge(budget.drop(columns='decision_ts'),on='bar',validate='many_to_one')
    side_pnl=np.where(x.side.eq('L'),x.l_m_pnl,x.s_m_pnl)
    side_debit=np.where(x.side.eq('L'),x.L_month_debit,x.S_month_debit)
    side_cap=np.where(x.side.eq('L'),engine.CB_L_MONTH,engine.CB_S_MONTH)
    x['blocked']=(x.d_pnl+x.daily_debit<=engine.CB_DAILY)|(side_pnl+side_debit<=side_cap)
    return x.loc[x.blocked].reset_index(drop=True)

def main():
    reg=b.register_round(2,[Path(__file__),b.ROOT/'doc/strategy_batch3_round2_20260912.md',
        b.ROOT/'tests/test_strategy_batch3_funding_20260912.py'],'N_funding_debit_budget')
    s=b.a.Study();fund=pd.read_csv(b.old.OUT/'base_0bp_funding.csv',parse_dates=['time'])
    gates=pd.read_csv(b.old.OUT/'base_0bp_gates.csv',parse_dates=['decision_ts'])
    states=pd.read_csv(b.old.OUT/'base_0bp_states.csv')
    budget=debit_budget(s.d,fund);events=affected(gates,states,budget,s.engine)
    counts=b.a.previous.previous.event_counts(events,'decision_ts')
    prefix=[]
    for cut in [6000,11000,16000]:
        d=s.d.iloc[:cut];pf=fund[fund.time<=d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        p=debit_budget(d,pf);pd.testing.assert_frame_equal(p,budget.iloc[:cut])
        ev=affected(gates[gates.bar<cut],states[states.bar<cut],p,s.engine)
        pd.testing.assert_frame_equal(ev,events[events.bar<cut].reset_index(drop=True));prefix.append(cut)
    budget.to_csv(b.DOC/'round2_debit_budget.csv',index=False);events.to_csv(b.DOC/'round2_provisional_events.csv',index=False)
    # No historical account receipt/revision archive; model arithmetic alone is
    # insufficient to admit a new funding-driven decision feature.
    sample=counts['clusters24h']>=30 and counts['late_clusters24h']>=15
    result={'utc':b.now(),'round':2,'name':'N_funding_debit_budget','status':'INSUFFICIENT_SAMPLE' if not sample else 'DATA_LIMITED',
        'additional_status':'DATA_LIMITED','counts_provisional':counts,'candidate_pnl_trials':0,'prefix':prefix,
        'historical_account_received_ts':None,'revision_status':'not archived',
        'model_quality':'PASS under inherited settlement accounting; original-state event diagnosis only',
        'protected_verified':b.verify(reg['protected_sha256'])}
    path=b.DOC/'round2_diagnostic.json';assert not path.exists();b.dump(path,result)
    print(json.dumps({'status':result['status'],'additional_status':'DATA_LIMITED','provisional_counts':counts,'candidate_pnl_trials':0}))

if __name__=='__main__':main()
