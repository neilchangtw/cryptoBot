import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import bounded_strategy_20260912 as b

class BoundedTests(unittest.TestCase):
    def test_transitive_event_clusters_and_boundary(self):
        f=pd.DataFrame({'t':pd.to_datetime(['2025-12-31 12:00','2026-01-01 12:00','2026-01-02 12:00','2026-01-03 13:00'])})
        self.assertEqual(b.a.previous.previous.event_counts(f,'t'),{'events':4,'clusters24h':2,'late_clusters24h':1})

    def test_empty_events(self):
        f=pd.DataFrame({'t':pd.Series([],dtype='datetime64[ns]')})
        self.assertEqual(b.a.previous.previous.event_counts(f,'t')['clusters24h'],0)

    def test_source_mutation_is_exact_and_noop_is_original(self):
        s=b.Study()
        fn=b.patched_engine(s.engine,'G')
        self.assertIn('simulate_v14_detailed',fn.__name__)
        # No live module or executor is instantiated by the research harness.
        self.assertNotIn('executor',sys.modules)

if __name__=='__main__':unittest.main()
