import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch2_round2_20260912 as k

class ExtensionTests(unittest.TestCase):
    def fixture(self,close=100.05,side='L'):
        d=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=2,freq='h'),'close':[100.,close]})
        p='lp' if side=='L' else 'sp'
        rows=[]
        for i in [0,1]:
            r={'bar':i}
            for prefix in ['lp','sp']:
                r.update({prefix+'_active':prefix==p,prefix+'_bar':0,prefix+'_ext':i==1,
                    prefix+'_entry':100.,prefix+'_ntl':4000.,prefix+'_fee':4.})
            rows.append(r)
        return d,pd.DataFrame(rows)

    def test_small_gross_profit_is_affected(self):
        d,s=self.fixture();self.assertEqual(len(k.extension_events(d,s)),1)

    def test_profit_exceeding_cost_is_unchanged(self):
        d,s=self.fixture(100.2);self.assertTrue(k.extension_events(d,s).empty)

    def test_short_direction(self):
        d,s=self.fixture(99.95,'S');self.assertEqual(len(k.extension_events(d,s)),1)

    def test_already_extended_or_changed_root_is_not_event(self):
        d,s=self.fixture();s.loc[0,'lp_ext']=True;self.assertTrue(k.extension_events(d,s).empty)
        d,s=self.fixture();s.loc[1,'lp_bar']=1;self.assertTrue(k.extension_events(d,s).empty)

    def test_physical_prefix_excludes_transition(self):
        d,s=self.fixture();self.assertTrue(k.extension_events(d.iloc[:1],s.iloc[:1]).empty)

if __name__=='__main__':unittest.main()
