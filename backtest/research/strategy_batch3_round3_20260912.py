"""Bound possible mark-stop events; hourly OHLC cannot establish execution."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import strategy_batch3_20260912 as b

def potential(d,mark,states,engine):
    events=[]
    for st in states.to_dict('records'):
        i=int(st['bar'])+1
        if i>=len(d):continue
        for side,p in [('L','lp'),('S','sp')]:
            if not st[p+'_active']:continue
            ep=st[p+'_entry'];level=ep*(1-engine.L_SN) if side=='L' else ep*(1+engine.S_SN)
            mt=mark.low.iloc[i]<=level if side=='L' else mark.high.iloc[i]>=level
            ct=d.low.iloc[i]<=level if side=='L' else d.high.iloc[i]>=level
            if mt:events.append({'bar':i,'side':side,'root_entry_bar':st[p+'_bar'],
                'decision_ts':d.datetime.iloc[i]+pd.Timedelta(hours=1),'stop_level':level,
                'hourly_touch_class':'both_touch' if ct else 'mark_only','exact_trigger_ts':None})
    return pd.DataFrame(events,columns=['bar','side','root_entry_bar','decision_ts','stop_level','hourly_touch_class','exact_trigger_ts'])

def main():
    reg=b.register_round(3,[Path(__file__),b.ROOT/'doc/strategy_batch3_round3_20260912.md',
        b.ROOT/'tests/test_strategy_batch3_mark_20260912.py'],'O_add_mark_safenet')
    s=b.a.Study();d=s.d
    path=b.ROOT/'data/public_cost_history_20260908/mark_1h_full.csv'
    mark=pd.read_csv(path)
    times=pd.to_datetime(mark.open_time,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    pd.testing.assert_series_equal(times,d.datetime,check_names=False)
    assert len(mark)==17519 and times.is_unique and times.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert np.isfinite(mark[['open','high','low','close']].to_numpy()).all() and (mark.low>0).all()
    assert (mark.high>=mark[['open','close']].max(axis=1)).all() and (mark.low<=mark[['open','close']].min(axis=1)).all()
    states=pd.read_csv(b.old.OUT/'base_0bp_states.csv',keep_default_na=False)
    events=potential(d,mark,states,s.engine);counts=b.a.previous.previous.event_counts(events,'decision_ts')
    prefix=[]
    for cut in [6000,11000,16000]:
        pe=potential(d.iloc[:cut],mark.iloc[:cut],states[states.bar<cut],s.engine)
        pd.testing.assert_frame_equal(pe,events[events.bar<cut].reset_index(drop=True));prefix.append(cut)
    events.to_csv(b.DOC/'round3_potential_hours.csv',index=False)
    sources={'utc':b.now(),'mark_path':b.rel(path),'sha256':b.sha(path),'rows':len(mark),
        'quality':'aligned finite valid OHLC','schema':list(mark.columns),'bar_quality':'PASS',
        'schema_inventory_reused':'doc/research_results/20260912_bounded_strategy/round3_diagnostic.json',
        'readable_inventory_scope':'prior verified 2128 CSV / 79 schemas; no synchronized mark/contract tick+fills+received archive',
        'missing':['intrahour exact mark trigger sequence','contract execution price at mark trigger',
            'synchronized received/revision archive','duplicate close/cancel race execution evidence'],
        'not_assumed':['OHLC reveals first touch order','mark is executable price','25% contract penetration transfers to mark']}
    b.dump(b.DOC/'round3_sources.json',sources)
    result={'utc':b.now(),'round':3,'name':'O_add_mark_safenet','status':'DATA_LIMITED',
        'counts':None,'potential_hour_counts':counts,'touch_classes':events.hourly_touch_class.value_counts().to_dict(),
        'candidate_pnl_trials':0,'prefix':prefix,'exact_affected_event_count_known':False,
        'protected_verified':b.verify(reg['protected_sha256'])}
    path=b.DOC/'round3_diagnostic.json';assert not path.exists();b.dump(path,result)
    print(json.dumps({'status':result['status'],'potential_hour_counts':counts,'touch_classes':result['touch_classes'],'candidate_pnl_trials':0}))

if __name__=='__main__':main()
