import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case02_20260912 as q

def frame(v):return pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=len(v),freq='h'),'volume':v})

class VolumeClockTests(unittest.TestCase):
    def test_constant_volume_equals_original_clock(self):
        due=q.clock_function(frame([100.]*100))
        for mh in [5,6,7,8,10]:
            for held in range(1,25):self.assertEqual(due(30+held,'L',30,held,mh),held>=mh)
    def test_min_half_and_double_cap(self):
        v=np.full(100,100.);v[31:]=1000.;due=q.clock_function(frame(v))
        self.assertFalse(due(32,'L',30,2,6));self.assertTrue(due(33,'L',30,3,6))
        v[31:]=0.;due=q.clock_function(frame(v))
        self.assertFalse(due(41,'L',30,11,6));self.assertTrue(due(42,'L',30,12,6))
    def test_entry_volume_excluded_and_reference_frozen(self):
        v=np.full(100,100.);v[30]=100000.;due=q.clock_function(frame(v))
        self.assertFalse(due(33,'S',30,3,6));self.assertTrue(due(36,'S',30,6,6))
    def test_prefix_and_future_volume_not_used(self):
        d=frame(np.arange(100,dtype=float)+20);one=q.clock_function(d);two=q.clock_function(d.iloc[:50])
        for i in range(31,50):self.assertEqual(one(i,'L',30,i-30,6),two(i,'L',30,i-30,6))
        pd.testing.assert_frame_equal(q.features(d.iloc[:50]),q.features(d).iloc[:50])

if __name__=='__main__':unittest.main()
