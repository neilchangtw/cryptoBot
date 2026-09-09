"""驗證同步訊號只使用當時已完成的兩市場資料。"""
import sys
import json
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from spot_futures_sync_20260909 import masks
from fetch_spot_sync_20260909 import validate

class SpotSyncTests(unittest.TestCase):
    def sample(self):
        d=pd.DataFrame({'datetime':pd.date_range('2024-01-01',periods=40,freq='h'),'close':200.})
        spot=d.copy();spot.close=100.
        return d,spot

    def test_own_boundary_strict_and_warmup(self):
        d,s=self.sample();s.loc[15,'close']=101.;s.loc[16,'close']=99.
        m=masks(d,s)
        self.assertFalse(m['both']['L'][:15].any());self.assertFalse(m['both']['S'][:15].any())
        self.assertTrue(m['both']['L'][15]);self.assertTrue(m['both']['S'][16])
        self.assertFalse(m['both']['L'][17]);self.assertFalse(m['both']['S'][17])
        s.loc[17,'close']=101.;self.assertFalse(masks(d,s)['both']['L'][17])
        s.loc[17,'close']=99.;self.assertFalse(masks(d,s)['both']['S'][17])

    def test_side_only_gate(self):
        d,s=self.sample();m=masks(d,s)
        self.assertTrue(m['long']['S'].all());self.assertTrue(m['short']['L'].all())
        self.assertFalse(m['long']['L'].any());self.assertFalse(m['short']['S'].any())

    def test_future_changes_cannot_rewrite_past(self):
        d,s=self.sample();s.loc[20,'close']=102.;old=masks(d,s)
        s.loc[21:,'close']=1e8;new=masks(d,s)
        for name in old:
            for side in ['L','S']:np.testing.assert_array_equal(old[name][side][:21],new[name][side][:21])

    def test_shift_missing_duplicate_rejected(self):
        d,s=self.sample()
        with self.assertRaises(AssertionError):masks(d,s.iloc[1:])
        s.datetime+=pd.Timedelta(hours=1)
        with self.assertRaises(AssertionError):masks(d,s)
        d,s=self.sample();s.loc[20,'datetime']=s.loc[19,'datetime']
        with self.assertRaises(AssertionError):masks(d,s)

    def test_rest_timestamp_and_ohlc_validation(self):
        start=int(pd.Timestamp('2024-01-01',tz='UTC').value//1000000)
        raw=[[start,'100','102','99','101','8',start+3599999,'800',3,'4','400','0']]
        expected=pd.date_range('2024-01-01 08:00',periods=1,freq='h')
        self.assertEqual(validate(raw,expected).close.iloc[0],101)
        raw[0][6]+=1
        with self.assertRaises(AssertionError):validate(raw,expected)
        raw[0][6]-=1;raw[0][2]='100'
        with self.assertRaises(AssertionError):validate(raw,expected)

    def test_boundary_excludes_current_includes_exact_previous_15(self):
        d,s=self.sample();s.loc[0,'close']=1000.;s.loc[16,'close']=101.
        m=masks(d,s)
        self.assertTrue(m['both']['L'][16])
        s.loc[1,'close']=1000.;self.assertFalse(masks(d,s)['both']['L'][16])

    def test_archived_near_boundary_market_cases(self):
        cases=json.loads((Path(__file__).parent/'fixtures/spot_sync_boundary_cases.json').read_text())
        self.assertEqual(len(cases),3)
        for case in cases:
            s=pd.DataFrame({'datetime':pd.date_range(end=pd.Timestamp(case['entry_time_taipei'])-pd.Timedelta(hours=1),periods=16,freq='h'),
                            'close':[float(x) for x in case['spot_closes']]})
            d=s.copy();d.close*=1.001
            self.assertEqual(bool(masks(d,s)['both'][case['side']][-1]),case['sync'])

if __name__=='__main__':unittest.main()
