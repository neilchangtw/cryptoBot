import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch3_round3_20260912 as o

class MarkTests(unittest.TestCase):
    def setUp(self):
        self.e=SimpleNamespace(L_SN=.035,S_SN=.04)
        self.d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=3,freq='h'),'low':[90.,97.,90.],'high':[110.,103.,110.]})
        self.mark=pd.DataFrame({'low':[90.,96.5,90.],'high':[110.,104.,110.]})
        self.states=pd.DataFrame([{'bar':0,'lp_active':True,'sp_active':True,'lp_entry':100.,'sp_entry':100.,'lp_bar':0,'sp_bar':0},
            {'bar':1,'lp_active':False,'sp_active':False,'lp_entry':100.,'sp_entry':100.,'lp_bar':0,'sp_bar':0}])
    def test_only_after_entry_and_inclusive_bounds(self):
        x=o.potential(self.d,self.mark,self.states,self.e)
        self.assertEqual(x.bar.tolist(),[1,1]);self.assertEqual(x.side.tolist(),['L','S'])
        self.assertEqual(x.hourly_touch_class.tolist(),['mark_only','mark_only'])
    def test_both_touches_are_ambiguous(self):
        self.d.loc[1,'low']=96.;self.d.loc[1,'high']=105.
        x=o.potential(self.d,self.mark,self.states,self.e)
        self.assertEqual(x.hourly_touch_class.tolist(),['both_touch','both_touch'])
        self.assertTrue(x.exact_trigger_ts.isna().all())
    def test_physical_prefix_no_future_action(self):
        x=o.potential(self.d,self.mark,self.states,self.e)
        p=o.potential(self.d.iloc[:2],self.mark.iloc[:2],self.states.iloc[:1],self.e)
        pd.testing.assert_frame_equal(p,x)
        self.assertTrue(o.potential(self.d.iloc[:1],self.mark.iloc[:1],self.states.iloc[:1],self.e).empty)

if __name__=='__main__':unittest.main()
