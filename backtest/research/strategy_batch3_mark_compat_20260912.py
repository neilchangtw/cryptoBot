"""Additive pre-diagnostic timestamp-resolution compatibility, no data edits."""
import inspect
from pathlib import Path
import pandas as pd
import strategy_batch3_round3_20260912 as original
b=original.b

def assert_same_instants(left,right):
    pd.testing.assert_series_equal(left.astype('datetime64[ns]'),right.astype('datetime64[ns]'),check_names=False)

def main():
    path=b.DOC/'round3_implementation_revision.json'
    expected='pd.testing.assert_series_equal(times,d.datetime,check_names=False)'
    source=inspect.getsource(original.main);assert source.count(expected)==1
    revised=source.replace(expected,'assert_same_instants(times,d.datetime)')
    record={'utc':b.now(),'reason':'Initial diagnostic stopped before events: pandas milliseconds versus microseconds dtype only; normalize resolution while comparing every instant exactly.',
        'candidate_pnl_trials_before':0,'events_computed_before':False,'original_source_sha256':b.sha(Path(original.__file__)),
        'sha256':{b.rel(Path(__file__)):b.sha(Path(__file__)),
            'tests/test_strategy_batch3_mark_compat_20260912.py':b.sha(b.ROOT/'tests/test_strategy_batch3_mark_compat_20260912.py')},
        'only_runtime_change':{'before':expected,'after':'assert_same_instants(times,d.datetime)'},
        'original_registration_preserved':True,'formulas_thresholds_data_unchanged':True}
    if path.exists():b.verify(b.read(path)['sha256'])
    else:b.dump(path,record)
    scope={**original.__dict__,'assert_same_instants':assert_same_instants}
    exec(compile(revised,'<mark-timestamp-resolution-compat>','exec'),scope)
    scope['main']()

if __name__=='__main__':main()
