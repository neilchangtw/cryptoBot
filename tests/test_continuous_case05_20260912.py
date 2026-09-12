import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case05_20260912 as t

def frame(values,volume=None):
    return pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=len(values),freq='h'),'close':values,'volume':[1.]*len(values) if volume is None else volume})

class AnchoredCloseTests(unittest.TestCase):
    def test_direction_confirmation_and_minimum(self):
        f=t.anchored_exit(frame([100,110,109,108,120]))
        self.assertFalse(f(2,'L',0));self.assertTrue(f(3,'L',0));self.assertFalse(f(3,'S',0));self.assertFalse(f(4,'L',0))
    def test_excludes_entry_bar_and_flat_is_not_adverse(self):
        d=frame([10000,110,109,108],[10000,1,1,1]);a=t.anchored_exit(d)
        d.loc[0,['close','volume']]=[1,1]
        self.assertEqual(a(3,'L',0),t.anchored_exit(d)(3,'L',0))
        f=t.anchored_exit(frame([10]*4));self.assertFalse(f(3,'L',0));self.assertFalse(f(3,'S',0))
    def test_volume_weighting_changes_reference(self):
        d=frame([100,110,90,95],[1,10,1,1]);self.assertTrue(t.anchored_exit(d)(3,'L',0))
        d.volume=[1,1,10,1];self.assertFalse(t.anchored_exit(d)(3,'L',0))
    def test_future_and_prefix_invariance(self):
        d=frame([100,110,109,108,107,106]);f=t.anchored_exit(d)
        short=t.anchored_exit(d.iloc[:4]);self.assertEqual(f(3,'L',0),short(3,'L',0))
        d.loc[4:,'close']=999;self.assertEqual(t.anchored_exit(d)(3,'L',0),short(3,'L',0))
    def test_invalid_volume_is_rejected(self):
        for value in [0,-1,float('nan')]:
            d=frame([10]*4);d.loc[2,'volume']=value
            with self.assertRaises(AssertionError):t.anchored_exit(d)

if __name__=='__main__':unittest.main()
