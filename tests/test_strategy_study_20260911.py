"""Causal time alignment and exit boundaries, using hand-checkable fixtures."""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_study_20260911 as study


class ResearchTests(unittest.TestCase):
    def fixture(self):
        d=pd.DataFrame({'datetime':pd.to_datetime(['2026-01-01 12:00','2026-01-01 13:00'])})
        ts=pd.date_range('2026-01-01 10:00','2026-01-01 14:30',freq='5min')
        raw=pd.DataFrame({'event_ts':ts,'sum_open_interest':100.,
                          'sum_toptrader_long_short_ratio':2.,'count_long_short_ratio':3.})
        raw.loc[raw.event_ts==pd.Timestamp('2026-01-01 12:45'),'sum_open_interest']=90.
        return d,raw

    def test_decision_lag_and_exact_change(self):
        d,raw=self.fixture();f=study.features(d,raw)
        self.assertEqual(f.event_ts.iloc[0],pd.Timestamp('2026-01-01 12:45'))
        self.assertEqual(f.assumed_available.iloc[0],pd.Timestamp('2026-01-01 12:55'))
        self.assertAlmostEqual(f.oi_delta.iloc[0],-.1)

    def test_missing_intermediate_point_disables_condition(self):
        d,raw=self.fixture()
        raw=raw[raw.event_ts!=pd.Timestamp('2026-01-01 11:35')]
        f=study.features(d,raw)
        self.assertFalse(f.oi_valid.iloc[0])
        self.assertTrue(np.isnan(f.oi_delta.iloc[0]))
        self.assertFalse(study.exit_condition('oi_exit',2,-.01,False,-.1))

    def test_prefix_and_future_perturbation(self):
        d,raw=self.fixture();f=study.features(d,raw)
        small=study.features(d.iloc[:1],raw[raw.event_ts<pd.Timestamp('2026-01-01 13:00')])
        pd.testing.assert_frame_equal(f.iloc[:1],small)
        raw.loc[raw.event_ts>=pd.Timestamp('2026-01-01 13:00'),'sum_open_interest']=1e9
        pd.testing.assert_frame_equal(f.iloc[:1],study.features(d,raw).iloc[:1])

    def test_exit_strict_boundaries_and_controls(self):
        self.assertTrue(study.exit_condition('oi_exit',2,-.01,True,-.02))
        for bh,cpnl,delta in [(1,-.01,-.02),(3,-.01,-.02),(2,0,-.02),(2,-.01,0)]:
            self.assertFalse(study.exit_condition('oi_exit',bh,cpnl,True,delta))
        self.assertTrue(study.exit_condition('price_control',2,-.01,True,.02))
        self.assertTrue(study.exit_condition('oi_control',2,.01,True,-.02))
        self.assertFalse(study.exit_condition('base',2,-.01,True,-.02))

    def test_cluster_24_hour_boundary(self):
        f=pd.DataFrame({'time':pd.to_datetime(['2025-12-30','2025-12-31','2026-01-02'])})
        self.assertEqual(study.event_counts(f,'time'),{'events':3,'clusters24h':2,'late_clusters24h':1})


if __name__=='__main__':
    unittest.main()
