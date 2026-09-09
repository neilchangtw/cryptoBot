"""子K時間切分與突破條件的獨立小樣本。"""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from intrahour_entry_20260908 import masks, simulator, cost


class IntrahourTests(unittest.TestCase):
    def sample(self):
        t=pd.date_range('2024-01-01',periods=40,freq='h')
        d=pd.DataFrame({'datetime':t,'close':100.})
        sub=pd.DataFrame({'datetime':pd.date_range(t[0],periods=480,freq='5min'),'close':100.})
        return d,sub,np.ones(40,bool)

    def test_30m_close_cannot_see_intermediate_rejection(self):
        d,sub,q=self.sample();i=20
        sub.loc[i*12:i*12+11,'close']=[99]*6+[101,101,101,99,101,101]
        d.loc[i,'close']=101
        m=masks(d,sub,q)
        self.assertFalse(m['m5_w30']['L'][i])
        self.assertTrue(m['m15_w30']['L'][i])
        self.assertTrue(m['m30_w30']['L'][i])
        self.assertFalse(m['m30_w60']['L'][i])

    def test_short_requires_strictly_below_prior_boundary(self):
        d,sub,q=self.sample();i=20
        sub.loc[i*12:i*12+11,'close']=99;d.loc[i,'close']=99
        m=masks(d,sub,q)
        self.assertTrue(m['m5_w60']['S'][i])
        self.assertFalse(m['m5_w60']['L'][i])
        sub.loc[i*12+11,'close']=100;d.loc[i,'close']=100
        self.assertFalse(masks(d,sub,q)['m30_w30']['S'][i])

    def test_quality_propagates_through_prior_15_hours(self):
        d,sub,q=self.sample();q[17:19]=False
        valid=masks(d,sub,q)['quality']['L']
        self.assertTrue(valid[16]);self.assertFalse(valid[17:34].any());self.assertTrue(valid[34])

    def test_missing_or_shifted_subbar_is_rejected(self):
        d,sub,q=self.sample()
        with self.assertRaises(AssertionError):masks(d,sub.iloc[1:],q)
        sub.loc[10,'datetime']+=pd.Timedelta(minutes=1)
        with self.assertRaises(AssertionError):masks(d,sub,q)

    def test_rejected_first_entry_does_not_create_cooldown_or_cap(self):
        root=Path(__file__).resolve().parents[1]
        d=pd.read_csv(root/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
        engine=cost.base.load_engine();ind=engine.compute_indicators(d)
        original=engine.simulate_v14_detailed(ind,d.datetime.to_numpy(),realistic=True)
        first=min(t['entry_bar'] for t in original)
        expected=engine.simulate_v14_detailed(ind,d.datetime.to_numpy(),start_bar=first+1,realistic=True)
        calls=[]
        def gate(i,side):
            calls.append((i,side));return i>first
        actual,_=simulator(engine)(ind,d.datetime.to_numpy(),realistic=True,gate=gate)
        self.assertTrue(any(i==first for i,_ in calls))
        pd.testing.assert_frame_equal(pd.DataFrame(actual)[pd.DataFrame(expected).columns],pd.DataFrame(expected))


if __name__=='__main__':unittest.main()
