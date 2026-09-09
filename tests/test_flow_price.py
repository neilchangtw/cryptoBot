"""棒內買賣占比加權、時間分界與交互條件。"""
import json
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from flow_price_20260909 import features,RULES,Study,OUT,harness

class FlowPriceTests(unittest.TestCase):
    def sample(self):
        d=pd.DataFrame({'datetime':pd.date_range('2024-01-01',periods=40,freq='h')})
        s=pd.DataFrame({'datetime':pd.date_range(d.datetime.iloc[0],periods=480,freq='5min'),
                        'close':100.,'volume':10.,'taker_buy_volume':5.})
        return d,s,np.ones(40,bool)

    def test_registered_interaction_cases(self):
        cases=json.loads((Path(__file__).parent/'fixtures/flow_price_cases.json').read_text())
        for case in cases:
            d,s,q=self.sample();a=20*12
            s.loc[a:a+8,'taker_buy_volume']=10*case['early_buy_fraction']
            s.loc[a+9:a+11,'taker_buy_volume']=10*case['late_buy_fraction']
            s.loc[a+8,'close']=case['close45'];s.loc[a+11,'close']=case['close60']
            f,m=features(d,s,q);r=f[case['side']].iloc[20]
            self.assertEqual(bool(r.strong),case['strong'],case['name'])
            self.assertEqual(bool(r.weak),case['weak'],case['name'])
            self.assertEqual(not bool(m['pair'][case['side']][20]),case['blocked'],case['name'])

    def test_volume_weighted_not_mean_ratios(self):
        d,s,q=self.sample();a=240
        s.loc[a:a+8,'volume']=[100]+[1]*8;s.loc[a:a+8,'taker_buy_volume']=[0]+[1]*8
        f,_=features(d,s,q)
        self.assertAlmostEqual(f['L'].F45.iloc[20],2*8/108-1)
        self.assertLess(f['L'].F45.iloc[20],0)

    def test_fifteen_minute_price_starts_at_ninth_close(self):
        d,s,q=self.sample();s.loc[248,'close']=110.;s.loc[249,'close']=10.;s.loc[251,'close']=100.
        f,_=features(d,s,q)
        self.assertAlmostEqual(f['L'].R15.iloc[20],100/110-1)
        self.assertAlmostEqual(f['S'].R15.iloc[20],1-100/110)

    def test_zero_volume_and_quality_are_unknown_not_good(self):
        d,s,q=self.sample();s.loc[249:251,['volume','taker_buy_volume']]=0
        f,m=features(d,s,q);self.assertFalse(f['L'].valid.iloc[20])
        self.assertTrue(m['base']['L'][20])
        for name in RULES[1:]:self.assertFalse(m[name]['L'][20])
        d,s,q=self.sample();q[20]=False
        f,_=features(d,s,q)
        self.assertFalse(f['L'].valid.iloc[20:36].any());self.assertTrue(f['L'].valid.iloc[36])

    def test_no_future_dependency(self):
        d,s,q=self.sample();f,m=features(d,s,q)
        s.loc[21*12:,'close']=1e9;s.loc[21*12:,'taker_buy_volume']=10
        ff,mm=features(d,s,q)
        for side in ['L','S']:
            pd.testing.assert_frame_equal(f[side].iloc[:21],ff[side].iloc[:21])
            for name in RULES:np.testing.assert_array_equal(m[name][side][:21],mm[name][side][:21])

    def test_missing_shifted_and_invalid_volume_rejected(self):
        d,s,q=self.sample()
        with self.assertRaises(AssertionError):features(d,s.iloc[1:],q)
        s.loc[20,'datetime']+=pd.Timedelta(minutes=1)
        with self.assertRaises(AssertionError):features(d,s,q)
        d,s,q=self.sample();s.loc[20,'taker_buy_volume']=11
        with self.assertRaises(AssertionError):features(d,s,q)

    def test_opposite_side_not_subject_to_directional_filter(self):
        d,s,q=self.sample();s.loc[249:251,'taker_buy_volume']=7
        f,m=features(d,s,q)
        self.assertFalse(m['pair_long']['L'][20]);self.assertTrue(m['pair_short']['L'][20])
        self.assertTrue(m['pair_long']['S'][20])

    def test_reused_harness_has_isolated_output_binding(self):
        self.assertEqual(Study.run.__globals__['OUT'],OUT)
        self.assertNotEqual(harness.Study.run.__globals__['OUT'],OUT)

if __name__=='__main__':unittest.main()
