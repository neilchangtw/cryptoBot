"""突破失效條件的小樣本與原出場優先檢查。"""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from breakout_failure_20260908 import Failure,simulator,cost


class FailureTests(unittest.TestCase):
    def callback(self,prices,required=2):
        n=len(prices)
        return Failure({'L':np.full(n,100.),'S':np.full(n,100.)},np.ones(n,bool),np.ones(n,bool),prices,required)

    def test_entry_bar_not_used_and_equality_is_inside(self):
        f=self.callback([99,100,99])
        self.assertFalse(f(0,'L',0));self.assertFalse(f(1,'L',0));self.assertTrue(f(2,'L',0))

    def test_reclaim_and_gap_reset_confirmation(self):
        f=self.callback([101,99,101,99,99])
        self.assertFalse(f(1,'L',0));self.assertFalse(f(2,'L',0));self.assertFalse(f(3,'L',0));self.assertTrue(f(4,'L',0))
        f=self.callback([101,99,99,99]);f(1,'L',0)
        self.assertFalse(f(3,'L',0))

    def test_invalid_bar_resets_and_entry_boundary_is_locked(self):
        f=self.callback([101,99,99,99,99]);f.vb[2]=False;f.b['L'][3]=98
        self.assertFalse(f(1,'L',0));self.assertFalse(f(2,'L',0));self.assertFalse(f(3,'L',0));self.assertTrue(f(4,'L',0))

    def test_short_and_positions_have_separate_counts(self):
        f=self.callback([99,101,101])
        self.assertFalse(f(1,'S',0));self.assertFalse(f(1,'L',0));self.assertTrue(f(2,'S',0))

    def test_original_exit_bars_do_not_call_new_exit(self):
        root=Path(__file__).resolve().parents[1]
        d=pd.read_csv(root/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
        engine=cost.base.load_engine();ind=engine.compute_indicators(d)
        original=engine.simulate_v14_detailed(ind,d.datetime.to_numpy(),realistic=True)
        calls=[]
        def observe(i,side,entry):calls.append((side,entry,i));return False
        raw,_=simulator(engine)(ind,d.datetime.to_numpy(),realistic=True,failure=observe)
        pd.testing.assert_frame_equal(pd.DataFrame(raw)[pd.DataFrame(original).columns],pd.DataFrame(original))
        self.assertTrue(calls)
        for t in original:self.assertNotIn((t['side'],t['entry_bar'],t['exit_bar']),calls)


if __name__=='__main__':unittest.main()
