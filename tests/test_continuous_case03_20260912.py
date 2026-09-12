import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case03_20260912 as r

def frame(close):return pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=len(close),freq='h'),'close':close})

class RenewalTests(unittest.TestCase):
    def test_no_fresh_breakout_preserves_original_mh(self):
        due=r.renewal_clock(frame([100.]*80))
        for h in range(1,14):self.assertEqual(due(30+h,'L',30,h,6),h>=6)
    def test_fresh_breakout_resets_but_hard_cap_holds(self):
        d=frame(np.arange(80)+100.);due=r.renewal_clock(d)
        self.assertFalse(due(36,'L',30,6,6));self.assertTrue(due(42,'L',30,12,6))
        self.assertTrue(due(36,'S',30,6,6))
    def test_direction_and_tie_does_not_renew(self):
        d=frame([100.]*40);d.loc[31,'close']=101.;d.loc[32,'close']=101.
        f=r.features(d);self.assertTrue(f.renew_L.iloc[31]);self.assertFalse(f.renew_L.iloc[32])
    def test_physical_prefix(self):
        d=frame(np.sin(np.arange(100))+100.);a=r.renewal_clock(d);b=r.renewal_clock(d.iloc[:50])
        for i in range(31,50):self.assertEqual(a(i,'S',30,i-30,8),b(i,'S',30,i-30,8))
        pd.testing.assert_frame_equal(r.features(d.iloc[:50]),r.features(d).iloc[:50])

if __name__=='__main__':unittest.main()
