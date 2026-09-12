"""Direct GK event boundary and preserved-session tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from test_session_ablation_20260912 import Tests as Fixture
import gk_ablation_20260912 as r


class Tests(unittest.TestCase):
    def fixture(self):
        e,i,s=Fixture().fixture();e.L_BLK_H=e.S_BLK_H={0,1,2,12};i['hours'][0]=9;i['pctile_L'][0]=25.
        return e,i,s

    def test_original_boundary_counts_direct_event(self):
        e,i,s=self.fixture();self.assertEqual(r.direct_opportunities(i,s,e).side.tolist(),['L'])
        i['pctile_L'][0]=24.999;self.assertTrue(r.direct_opportunities(i,s,e).empty)

    def test_other_gates_must_still_pass(self):
        e,i,s=self.fixture();i['hours'][0]=12;self.assertTrue(r.direct_opportunities(i,s,e).empty)
        i['hours'][0]=9;i['regime_block_l'][0]=True;self.assertTrue(r.direct_opportunities(i,s,e).empty)
        i['regime_block_l'][0]=False;s.loc[0,'lp_active']=True;self.assertTrue(r.direct_opportunities(i,s,e).empty)


if __name__=='__main__':unittest.main()
