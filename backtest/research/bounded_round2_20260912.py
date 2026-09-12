"""Round H, pre-return opportunity audit only until 30/15 clusters pass."""
import json
from pathlib import Path
import pandas as pd
import bounded_strategy_20260912 as b

ROOT=b.ROOT;DOC=b.DOC
PLAN=ROOT/'doc/strategy_bounded_round2_20260912.md'

def direct(gates,states):
    assert gates.allowed.eq(True).all()
    assert not gates.groupby('bar').side.nunique().gt(1).any(), 'Opposite breakout signals cannot coincide'
    st=states.set_index('bar');out=[]
    for r in gates.itertuples():
        s=st.loc[r.bar];other='sp' if r.side=='L' else 'lp'
        if bool(s[other+'_active']):
            assert s[other+'_bar']<r.bar
            out.append({'bar':r.bar,'side':r.side,'decision_ts':r.decision_ts,
                        'other_entry_bar':int(s[other+'_bar']),'other_held':int(s[other+'_held'])})
    return pd.DataFrame(out,columns=['bar','side','decision_ts','other_entry_bar','other_held'])

def main():
    reg=b.register();g=b.read(DOC/'round1_diagnostic.json')
    assert g['status'] in ['INSUFFICIENT_SAMPLE','DATA_LIMITED','REJECTED']
    sources=[PLAN,Path(__file__),ROOT/'tests/test_bounded_diagnostics_20260912.py',DOC/'round1_diagnostic.json']
    path=DOC/'round2_registration.json'
    inputs={b.rel(p):b.sha(p) for p in sources}
    if path.exists(): b.verify(b.read(path)['sha256'])
    else:b.dump(path,{'utc':b.now(),'round':2,'main':'H_no_opposite_overlap','sha256':inputs,'candidate_pnl_trials_before':0})
    gates=pd.read_csv(b.OUT/'base_0bp_gates.csv',parse_dates=['decision_ts'])
    states=pd.read_csv(b.OUT/'base_0bp_states.csv',usecols=['bar','lp_active','sp_active','lp_bar','sp_bar','lp_held','sp_held'])
    events=direct(gates,states);events.to_csv(DOC/'round2_events_pre_pnl.csv',index=False)
    counts=b.a.previous.previous.event_counts(events,'decision_ts')
    for cut in [6000,11000,16000]:
        p=direct(gates[gates.bar<cut],states[states.bar<cut])
        pd.testing.assert_frame_equal(p,events[events.bar<cut].reset_index(drop=True))
    result={'utc':b.now(),'round':2,'name':'H_no_opposite_overlap','counts':counts,
            'status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
            'source':'Original base eligible gates and post-exit states; no payoff columns',
            'prefix_cuts_passed':[6000,11000,16000],'candidate_pnl_trials':0,'protected_verified':b.verify(reg['protected_sha256'])}
    out=DOC/'round2_diagnostic.json';assert not out.exists();b.dump(out,result);print(json.dumps(result))

if __name__=='__main__':main()
