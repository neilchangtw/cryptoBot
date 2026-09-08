"""研究引擎的因果進場、Wilder指標與funding帳本測例。"""
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest'/'research'))
import execute_optimization_plan_20260908 as research

class ResearchChecks(unittest.TestCase):
    def step(self,i,cfg,pending=None,safe=True,signal=True,hi=100,ci=99):
        return research.entry_step(i,cfg,pending,safe,signal,hi,ci,100,2,1.2,30,10,20)

    def test_retest_requires_later_bar_and_preserves_original_boundary(self):
        cfg={'mode':'retest','wait':3,'name':'retest3'}
        entered,pending,_,_=self.step(10,cfg)
        self.assertFalse(entered)
        self.assertEqual(100,pending['boundary'])
        # 次棒確認時仍用原事件規則，不能被新policy替換。
        entered,pending,selected,event=self.step(11,{},pending,signal=False)
        self.assertTrue(entered); self.assertIsNone(pending)
        self.assertEqual(cfg,selected); self.assertEqual('filled',event)

    def test_retest_invalidates_on_close_above_boundary(self):
        cfg={'mode':'retest','wait':3}; _,p,_,_=self.step(10,cfg)
        entered,p,_,event=self.step(11,cfg,p,hi=102,ci=101)
        self.assertFalse(entered); self.assertIsNone(p); self.assertEqual('invalidated',event)

    def test_retest_cannot_bypass_circuit_breaker(self):
        cfg={'mode':'retest','wait':3}; _,p,_,_=self.step(10,cfg)
        entered,p,_,event=self.step(11,cfg,p,safe=False)
        self.assertFalse(entered); self.assertIsNone(p); self.assertEqual('safety_rejected',event)

    def test_delay_waits_exact_duration_without_retest_condition(self):
        cfg={'mode':'delay','wait':3}; _,p,_,_=self.step(10,cfg)
        for i in [11,12]:
            entered,p,_,_=self.step(i,cfg,p,hi=102,ci=101)
            self.assertFalse(entered); self.assertIsNotNone(p)
        entered,p,_,_=self.step(13,cfg,p,signal=False,hi=102,ci=101)
        self.assertTrue(entered)

    def test_wilder_direction_seed_and_prefix(self):
        close=200-np.arange(80,dtype=float)
        d=pd.DataFrame({'high':close+1,'low':close-1,'close':close,'volume':np.ones(80)*10})
        f=research.features(d)
        self.assertTrue(np.isnan(f['adx'][26])); self.assertAlmostEqual(100,f['adx'][27])
        self.assertEqual(0,f['pdi'][-1]); self.assertGreater(f['mdi'][-1],0)
        p=research.features(d.iloc[:40])
        for key in f: np.testing.assert_allclose(f[key][:40],p[key],equal_nan=True)
        d.loc[30,'volume']=100
        self.assertAlmostEqual(10,research.features(d)['volume'][30])

    def test_funding_sign_boundary_and_mark_equity(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=3,freq='h')})
        mark=pd.DataFrame({'close':[100,90,110]})
        times=d.datetime+pd.Timedelta(hours=1)
        fund=pd.DataFrame({'nominal':times,'markPrice':[100]*3,'fundingRate':[.001]*3})
        def trade(side,reason='MH'):
            return pd.DataFrame([{'entry_bar':0,'exit_bar':2,'qty_exact':40.,'fee_exact':4.,'pnl':396.,
                'side':side,'entry_exact':100.,'entry_dt':times.iloc[0],'exit_dt':times.iloc[2],'reason_code':reason}])
        f,eq,_=research.account(trade('L'),d,mark,fund)
        self.assertAlmostEqual(-8,f.funding.iloc[0])
        self.assertAlmostEqual(388,eq.equity.iloc[-1])
        self.assertAlmostEqual(-406,eq.equity.iloc[1])
        f,eq,_=research.account(trade('S'),d,mark,fund)
        self.assertAlmostEqual(8,f.funding.iloc[0])
        f,eq,_=research.account(trade('L','SN'),d,mark,fund)
        self.assertAlmostEqual(-4,f.funding.iloc[0])
        self.assertAlmostEqual(396+f.funding.iloc[0],eq.cash.iloc[-1])

    def test_policy_switch_does_not_change_existing_position_tp(self):
        study=research.Study()
        baseline=study.run(cut=len(study.d),save=False)
        target=baseline[(baseline.side=='S')&(baseline.regime_code=='DOWN')].iloc[0]
        policy=np.zeros(len(study.d),dtype=int)
        policy[int(target.entry_bar)+1:]=1
        configs=[{'name':'base'},{'name':'tp35','tp':.035}]
        changed=study.run(policy=(configs,policy),cut=len(study.d),save=False)
        same=changed[(changed.side==target.side)&(changed.entry_dt==target.entry_dt)].iloc[0]
        self.assertEqual(target.exit_dt,same.exit_dt)
        self.assertEqual(target.pnl,same.pnl)
        self.assertEqual(.02,same.locked_tp)
        later=changed[(changed.side=='S')&(changed.regime_code=='DOWN')&(changed.entry_dt>target.entry_dt)]
        self.assertTrue(later.locked_tp.eq(.035).all())

if __name__=='__main__':unittest.main()
