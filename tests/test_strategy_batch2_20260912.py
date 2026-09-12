import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch2_20260912 as b

class LeadTests(unittest.TestCase):
    def data(self):
        rng=np.random.default_rng(20260912);x=rng.normal(0,.001,400)
        dt=pd.date_range('2025-01-01',periods=len(x),freq='h')
        s=pd.DataFrame({'datetime':dt,'close':100*np.exp(np.cumsum(x))})
        f=pd.DataFrame({'datetime':dt,'close':100*np.exp(np.cumsum(np.r_[0,x[:-1]]))})
        return f,s

    def test_known_spot_lead_and_timestamp(self):
        d,s=self.data();f=b.lead_features(d,s)
        self.assertGreater(f.score.iloc[-1],.5)
        self.assertTrue((f.assumed_available<f.decision_ts).all())
        self.assertEqual(f.decision_ts.iloc[-1]-f.source_close.iloc[-1],pd.Timedelta(hours=1))

    def test_physical_prefix_and_future_perturbation(self):
        d,s=self.data();f=b.lead_features(d,s)
        pd.testing.assert_frame_equal(f.iloc[:300],b.lead_features(d.iloc[:300],s.iloc[:300]))
        s.loc[300:,'close']*=1.2
        pd.testing.assert_frame_equal(f.iloc[:300],b.lead_features(d,s).iloc[:300])

    def test_missing_or_zero_variance_not_filled(self):
        d,s=self.data();s['close']=100.
        self.assertFalse(b.lead_features(d,s).valid.any())
        d,s=self.data();s.loc[250,'close']=np.nan
        self.assertFalse(b.lead_features(d,s).valid.iloc[251])

    def test_no_same_hour_return(self):
        d,s=self.data();f=b.lead_features(d,s)
        d.loc[299,'close']*=1.1;s.loc[299,'close']*=.9
        g=b.lead_features(d,s)
        self.assertEqual(f.score.iloc[299],g.score.iloc[299])

if __name__=='__main__':unittest.main()
