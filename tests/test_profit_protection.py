"""子棒出場的時間、優先序與未來資訊測試。"""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from profit_protection_20260909 import simulator, expand, cost


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.engine=cost.base.load_engine();self.fn=simulator(self.engine)
        self.a=self.engine.WARMUP*12+11;n=self.a+180
        self.ind={k:np.full(n,100.) for k in ['o','h','l','c','sub_h','sub_l']}
        self.ind.update({k:np.zeros(n,dtype=bool) for k in ['brk_up','brk_dn','regime_block_l','regime_block_s']})
        self.ind.update({k:np.full(n,v) for k,v in [('pctile_L',10.),('pctile_S',10.),('hours',10),('dows',1),('months',202501),('days',20250101),('slope',0.),('valid',True)]})
        self.ind['brk_up'][self.a]=True
        self.times=pd.date_range('2025-01-01',periods=n,freq='5min').to_numpy()

    def run_rule(self,family='both',minute=5):
        return self.fn(self.ind,self.times,realistic=True,family=family,minute=minute)[0]

    def test_first_post_entry_bar_and_tp_precedes_mfe(self):
        j=self.a+1;self.ind['h'][j]=104.;self.ind['sub_h'][j]=104.;self.ind['c'][j]=102.
        t=self.run_rule()[0]
        self.assertEqual(t['exit_bar'],j);self.assertEqual(t['exit_reason'],'TP')
        self.assertEqual(t['exit_price'],102.)

    def test_safenet_precedes_profit_on_same_subbar(self):
        j=self.a+1;self.ind['h'][j]=104.;self.ind['l'][j]=95.
        self.ind['sub_h'][j]=104.;self.ind['sub_l'][j]=95.
        t=self.run_rule()[0]
        self.assertEqual(t['exit_reason'],'SN');self.assertAlmostEqual(t['exit_price'],96.125,places=2)

    def test_mfe_uses_observed_high_and_can_trigger_first_hour(self):
        j=self.a+1;self.ind['h'][j]=101.2;self.ind['sub_h'][j]=101.2;self.ind['c'][j]=100.3
        t=self.run_rule('protect')[0]
        self.assertEqual(t['exit_bar'],j);self.assertEqual(t['exit_reason'],'MFE')

    def test_15m_waits_until_third_completed_subbar(self):
        j=self.a+1
        self.ind['h'][j:j+3]=104.;self.ind['sub_h'][j]=104.;self.ind['c'][j:j+3]=102.
        t=self.run_rule('tp',15)[0]
        self.assertEqual(t['exit_bar'],j+2)

    def test_be_only_after_hourly_extension_activation(self):
        self.ind['brk_up'][self.a]=False;self.ind['brk_dn'][self.a]=True
        self.ind['slope'][:]=.02
        self.ind['c'][self.a+1:]=99.5
        self.ind['h'][self.a+1:]=100.;self.ind['l'][self.a+1:]=99.5
        t=self.run_rule('protect')[0]
        self.assertEqual(t['exit_bar'],self.a+10*12+1)
        self.assertEqual(t['exit_reason'],'BE')

    def test_quality_failure_disables_intermediate_exits(self):
        j=self.a+1;self.ind['valid'][j:j+12]=False
        self.ind['h'][j:j+12]=104.;self.ind['c'][j:j+12]=102.
        t=self.run_rule()[0]
        self.assertEqual(t['exit_bar'],self.a+12)

    def test_non_hourly_entry_signals_are_ignored(self):
        self.ind['brk_up'][self.a]=False;self.ind['brk_up'][self.a+1]=True
        self.assertEqual(self.run_rule(),[])

    def test_unseen_subbar_does_not_change_earlier_execution(self):
        j=self.a+1;self.ind['h'][j]=104.;self.ind['c'][j]=102.
        before=self.run_rule()[0]
        for k in ['h','l','c','sub_h','sub_l']:self.ind[k][j+1:]*=1.5
        after=self.run_rule()[0]
        self.assertEqual(before,after)

    def test_expansion_does_not_leak_hour_high_before_close(self):
        d=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=330,freq='h'),'open':100.,'high':100.,'low':100.,'close':100.})
        sub=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=330*12,freq='5min'),'open':100.,'high':100.,'low':100.,'close':100.})
        d.loc[320,'high']=110.;sub.loc[320*12+10,'high']=110.
        ind,_=expand(d,sub,np.ones(330,bool),self.engine)
        self.assertEqual(ind['h'][320*12+9],100.)
        self.assertEqual(ind['h'][320*12+10],110.)


if __name__=='__main__':unittest.main()
