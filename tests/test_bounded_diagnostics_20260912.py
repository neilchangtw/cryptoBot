import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import bounded_strategy_20260912 as b
import bounded_round2_20260912 as h

class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.engine=b.Study().engine

    def scenario(self,side='S',close=99.,high=100.,low=97.,held=1,ext=False):
        d=pd.DataFrame({'high':[100.,high],'low':[100.,low],'close':[100.,close],
            'datetime':pd.date_range('2025-01-01',periods=2,freq='h')})
        state={'bar':0,'lp_active':side=='L','sp_active':side=='S','lp_entry':100.,'sp_entry':100.,
            'lp_bar':0,'sp_bar':0,'lp_held':held,'sp_held':held,'lp_mfe':0.,'sp_mfe':0.,
            'lp_regime':'SIDE','sp_regime':'DOWN','lp_ext':ext,'sp_ext':ext,'lp_ext_bars':0,'sp_ext_bars':0,'lp_reduced':False}
        return d,pd.DataFrame([state])

    def test_short_touch_unconfirmed_changes_action(self):
        d,s=self.scenario();self.assertEqual(len(b.direct_tp_events(d,s,self.engine)),1)

    def test_close_equality_is_confirmed(self):
        d,s=self.scenario(close=98.);self.assertTrue(b.direct_tp_events(d,s,self.engine).empty)

    def test_safenet_has_priority(self):
        d,s=self.scenario(high=105.);self.assertTrue(b.direct_tp_events(d,s,self.engine).empty)

    def test_original_mh_same_close_is_not_event(self):
        d,s=self.scenario(close=100.5,high=101.,held=9);self.assertTrue(b.direct_tp_events(d,s,self.engine).empty)

    def test_original_l_mfe_same_close_is_not_event(self):
        d,s=self.scenario(side='L',close=101.,high=104.,low=100.);self.assertTrue(b.direct_tp_events(d,s,self.engine).empty)

    def test_l_near_target_continues(self):
        d,s=self.scenario(side='L',close=103.4,high=104.,low=100.);self.assertEqual(len(b.direct_tp_events(d,s,self.engine)),1)

    def test_opposite_gate_uses_current_active_after_exits(self):
        g=pd.DataFrame({'bar':[5,8],'side':['L','S'],'allowed':[True,True],
            'decision_ts':pd.to_datetime(['2025-01-01 05:00','2025-01-01 08:00'])})
        st=pd.DataFrame({'bar':[5,8],'sp_active':[True,True],'lp_active':[True,False],
            'lp_bar':[5,5],'sp_bar':[2,8],'sp_held':[3,0],'lp_held':[0,3]})
        events=h.direct(g,st);self.assertEqual(events.bar.tolist(),[5]);self.assertEqual(events.other_entry_bar.tolist(),[2])

if __name__=='__main__':unittest.main()
