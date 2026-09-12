import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from strategy_batch3_mark_compat_20260912 import assert_same_instants

class ResolutionTests(unittest.TestCase):
    def test_resolution_is_not_a_time_shift(self):
        t=pd.Series(pd.date_range('2026-01-01',periods=3,freq='h'))
        assert_same_instants(t.astype('datetime64[ms]'),t.astype('datetime64[us]'))
        with self.assertRaises(AssertionError):assert_same_instants(t,t+pd.Timedelta(milliseconds=1))

if __name__=='__main__':unittest.main()
