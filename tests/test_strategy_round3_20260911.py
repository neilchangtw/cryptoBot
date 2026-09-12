"""Hand-calculated temporal leakage, missingness, clustering and source-failure guards."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_round3_20260911 as r


class Tests(unittest.TestCase):
    def fixture(self):
        times=pd.date_range('2026-01-01',periods=60,freq='h')
        raw=pd.DataFrame({'event_ts':times,'close':100.})
        raw.loc[24,'close']=110.; raw.loc[25,'close']=111.
        d=pd.DataFrame({'datetime':times})
        return d,raw

    def test_exact_boundary_and_time(self):
        d,raw=self.fixture(); f=r.features(d,raw)
        # Decision at hour 26 uses source hour24, known at hour25:05, vs hour0.
        self.assertEqual(f.source_ts.iloc[25],pd.Timestamp('2026-01-02'))
        self.assertTrue(r.allowed(f)[25]); self.assertFalse(r.allowed(f)[26])
        self.assertEqual((f.decision_ts-f.assumed_available).iloc[25],pd.Timedelta(minutes=55))

    def test_missing_hour_cannot_be_bridged(self):
        d,raw=self.fixture(); raw=raw.drop(12)
        f=r.features(d,raw)
        self.assertFalse(f.valid.iloc[25]); self.assertFalse(r.allowed(f)[25])

    def test_future_change_and_physical_prefix(self):
        d,raw=self.fixture(); f=r.features(d,raw)
        past=raw[raw.event_ts<d.datetime.iloc[40]]
        pd.testing.assert_frame_equal(f.iloc[:40],r.features(d.iloc[:40],past))
        raw.loc[40:,'close']=9999.
        pd.testing.assert_frame_equal(f.iloc[:40],r.features(d,raw).iloc[:40])

    def test_cluster_is_chained_and_uses_group_head(self):
        times=pd.Series(pd.to_datetime(['2025-12-31 10:00','2026-01-01 10:00','2026-01-02 10:01']))
        self.assertEqual(r.counts(times),{'events':3,'clusters24h':2,'late_clusters24h':1})

    def test_bad_source_is_rejected(self):
        for rows in [[[0,1,2,0,1]],[[1,1,2,1,1]],[[0,1,2,1,1],[0,1,2,1,1]],[[0,3,2,1,1]]]:
            with self.assertRaises(ValueError): r.normalize_rows(rows)

    def test_saved_failure_never_requests_network(self):
        # This guard needs a recorded-failure condition, not filesystem ACL behavior.
        with patch.object(Path,'exists',return_value=True),patch.object(r.requests,'Session') as network:
            with self.assertRaisesRegex(RuntimeError,'recorded_source_failure_do_not_retry'):
                r.request_once(0,1,'forbidden_retry')
            network.assert_not_called()


if __name__=='__main__': unittest.main()
