import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case06_20260912 as u

def frame(n=400):return pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=n,freq='h'),'close':100+np.arange(n)*.1})

class RegimeExitTests(unittest.TestCase):
    def test_slope_matches_lagged_locked_formula(self):
        f=u.features(frame())
        expected=(124.95-114.95)/114.95
        self.assertAlmostEqual(f.slope.iloc[350],expected)
        self.assertTrue(f.block_L.iloc[350]);self.assertFalse(f.block_S.iloc[350])
    def test_no_current_or_future_price_use(self):
        d=frame();f=u.features(d);d.loc[350:,'close']=9999
        pd.testing.assert_frame_equal(u.features(d).iloc[:351],f.iloc[:351])
        pd.testing.assert_frame_equal(u.features(d.iloc[:351]),u.features(d).iloc[:351])
    def test_confirmation_never_uses_pre_entry_bars(self):
        d=frame();self.assertTrue(u.regime_exit(d)(351,'L',350))
        self.assertFalse(u.regime_exit(d,2)(351,'L',350));self.assertTrue(u.regime_exit(d,2)(352,'L',350))
    def test_sideways_short_and_invalid_input(self):
        d=frame();d.close=100
        self.assertTrue(u.regime_exit(d)(351,'S',350));self.assertFalse(u.regime_exit(d)(351,'L',350))
        d.loc[320,'close']=np.nan
        with self.assertRaises(AssertionError):u.regime_exit(d)

if __name__=='__main__':unittest.main()
