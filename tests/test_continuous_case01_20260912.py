import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case01_20260912 as c

def frame(values):return pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=len(values),freq='h'),'close':values})

class ExtremeAgeTests(unittest.TestCase):
    def test_age_direction_and_exclude_current(self):
        f=c.features(frame(list(range(1,17))))
        self.assertEqual(f.age_L.iloc[15],1);self.assertEqual(f.age_S.iloc[15],15)
        self.assertTrue(f.allow_L.iloc[15]);self.assertFalse(f.allow_S.iloc[15])
    def test_ties_are_most_recent_and_boundary(self):
        x=[1.]*15;x[0]=2.;x[8]=2.
        f=c.features(frame(x+[999.]))
        self.assertEqual(f.age_L.iloc[-1],7);self.assertTrue(f.allow_L.iloc[-1])
        self.assertFalse(c.features(frame(x+[999.]),6).allow_L.iloc[-1])
    def test_future_and_current_perturbation_invariance(self):
        x=frame(np.sin(np.arange(70))+10);f=c.features(x)
        changed=x.copy();changed.loc[40:,'close']=1000
        pd.testing.assert_frame_equal(c.features(changed).iloc[:41],f.iloc[:41])
        pd.testing.assert_frame_equal(c.features(x.iloc[:41]),f.iloc[:41])
    def test_missing_prior_never_backfilled(self):
        x=frame(np.arange(40,dtype=float));x.loc[20,'close']=np.nan
        f=c.features(x)
        self.assertTrue(f.valid.iloc[20]);self.assertFalse(f.valid.iloc[21:36].any());self.assertTrue(f.valid.iloc[36])

if __name__=='__main__':unittest.main()
