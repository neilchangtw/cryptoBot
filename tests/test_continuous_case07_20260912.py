import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case07_20260912 as v

class EntropyRepairTests(unittest.TestCase):
    def test_same_extendable_pairs_last_template_counts(self):
        # [0,1,0] matches the last [0,1,0]; it must be counted.
        value,a,b=v.entropy([0,1,0,1,0],r_factor=0)
        self.assertEqual((a,b),(1,1));self.assertEqual(value,0.)
        self.assertTrue(np.isnan(v.legacy_function()(np.array([0.,1.,0.,1.,0.]))))
    def test_infinite_and_undefined_are_different(self):
        value,a,b=v.entropy([0,0,1,0,0,2],r_factor=0)
        self.assertEqual((a,b),(0,1));self.assertTrue(np.isinf(value))
        self.assertTrue(np.isnan(v.entropy([0,1,2,3,4],r_factor=0)[0]))
    def test_constant_sequence_and_scaling(self):
        self.assertEqual(v.entropy([5.]*20)[0],0)
        x=np.array([0,1,0,1,0,1,1,0,1,0.])
        self.assertEqual(v.entropy(x)[1:],v.entropy(x*1e-3+100)[1:])
        legacy=v.legacy_function();self.assertAlmostEqual(legacy(x),legacy(x*1e-3+100))
    def test_no_current_or_future_information(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=180,freq='h'),'close':np.exp(np.sin(np.arange(180))*.02+5)})
        f=v.features(d);d.loc[150:,'close']=10000
        pd.testing.assert_frame_equal(v.features(d).iloc[:150],f.iloc[:150])
        pd.testing.assert_frame_equal(v.features(d.iloc[:150]),f.iloc[:150])

if __name__=='__main__':unittest.main()
