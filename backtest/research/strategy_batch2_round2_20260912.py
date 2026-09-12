"""Cost-aware extension: causal baseline transition count, no candidate payoff."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch2_20260912 as b

ROOT=b.ROOT;DOC=b.DOC

def extension_events(d,states):
    out=[];lookup=states.set_index('bar')
    for i in lookup.index:
        if i-1 not in lookup.index:continue
        prev=lookup.loc[i-1];cur=lookup.loc[i]
        for side,p,sign in [('L','lp',1),('S','sp',-1)]:
            if not prev[p+'_active'] or not cur[p+'_active']:continue
            if prev[p+'_bar']!=cur[p+'_bar'] or prev[p+'_ext'] or not cur[p+'_ext']:continue
            ep=cur[p+'_entry'];cpnl=sign*(d.close.iloc[i]/ep-1)
            assert cpnl>0 and cur[p+'_ntl']==4000
            if cpnl<=cur[p+'_fee']/cur[p+'_ntl']:
                out.append({'bar':int(i),'side':side,'root_entry_bar':int(cur[p+'_bar']),
                    'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1),
                    'observed_price_return':cpnl,'known_fee_fraction':cur[p+'_fee']/cur[p+'_ntl']})
    return pd.DataFrame(out,columns=['bar','side','root_entry_bar','decision_ts','observed_price_return','known_fee_fraction'])

def main():
    reg=b.register_round(2,[Path(__file__),ROOT/'doc/strategy_batch2_round2_20260912.md',
        ROOT/'tests/test_strategy_batch2_extension_20260912.py',DOC/'round1_diagnostic.json'],'K_net_positive_extension')
    d=pd.read_csv(ROOT/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
    states=pd.read_csv(b.old.OUT/'base_0bp_states.csv',keep_default_na=False)
    events=extension_events(d,states);events.to_csv(DOC/'round2_events_pre_pnl.csv',index=False)
    counts=b.old.a.previous.previous.event_counts(events,'decision_ts')
    prefixes=[]
    for cut in [6000,11000,16000]:
        short=extension_events(d.iloc[:cut],states[states.bar<cut])
        pd.testing.assert_frame_equal(short,events[events.bar<cut].reset_index(drop=True));prefixes.append(cut)
    result={'utc':b.now(),'round':2,'name':'K_net_positive_extension',
        'status':'READY' if counts['clusters24h']>=30 and counts['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE',
        'counts':counts,'prefix_cuts_passed':prefixes,'quality':'PASS with original closed-kline and known model fee assumptions',
        'candidate_pnl_trials':0,'source':'Original causal state transitions and contemporaneous close, no outcome fields',
        'protected_verified':b.verify(reg['protected_sha256'])}
    p=DOC/'round2_diagnostic.json';assert not p.exists();b.dump(p,result);print(json.dumps(result))

if __name__=='__main__':main()
