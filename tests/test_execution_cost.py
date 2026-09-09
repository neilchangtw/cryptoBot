"""費用對成交損益、排程與熔斷的影響。"""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from execution_cost_20260909 import intra,cost,price_deviation


class CostTests(unittest.TestCase):
    def setUp(self):
        self.engine=cost.base.load_engine();self.fn=intra.simulator(self.engine)
        n=350
        self.ind={k:np.full(n,100.) for k in ['o','h','l','c']}
        self.ind.update({k:np.zeros(n,dtype=bool) for k in ['brk_up','brk_dn','regime_block_l','regime_block_s']})
        self.ind.update({k:np.full(n,v) for k,v in [('pctile_L',10.),('pctile_S',10.),('hours',10),('dows',1),('months',202501),('days',20250101),('slope',0.)]})
        self.times=pd.date_range('2025-01-01',periods=n,freq='h').to_numpy()
        self.ind['brk_up'][310]=True;self.ind['h'][311]=104.;self.ind['c'][311]=100.5

    def run_fee(self,fee,schedule=None):
        self.fn.__globals__['FEE']=fee
        return self.fn(self.ind,self.times,realistic=True,margin_schedule=schedule)[0]

    def test_fee_changes_net_not_fill_price(self):
        a=self.run_fee(4)[0];b=self.run_fee(3)[0]
        self.assertEqual(a['entry_price'],b['entry_price']);self.assertEqual(a['exit_price'],b['exit_price'])
        self.assertEqual(b['pnl_usd']-a['pnl_usd'],1.)

    def test_original_engine_constant_is_not_modified(self):
        self.run_fee(0)
        self.assertEqual(self.engine.FEE,4)

    def test_margin_schedule_scales_cost(self):
        a=self.run_fee(4,[('2000-01-01',400)])[0]
        b=self.run_fee(3,[('2000-01-01',400)])[0]
        self.assertEqual(b['pnl_usd']-a['pnl_usd'],2.)
        self.assertEqual(b['fee_exact'],6.)

    def test_cost_reduction_reopens_monthly_gate(self):
        self.ind['c'][311]=100.;self.ind['brk_up'][320]=True;self.ind['h'][321]=104.
        self.fn.__globals__['CB_L_MONTH']=-3.
        self.assertEqual(len(self.run_fee(4)),1)
        self.assertEqual(len(self.run_fee(2)),2)

    def test_zero_fee_still_records_price_loss(self):
        self.ind['c'][311]=99.5
        t=self.run_fee(0)[0]
        self.assertEqual(t['pnl_usd'],-20.)

    def test_signed_price_deviation_for_both_sides(self):
        bt=pd.DataFrame({'side':['L','S'],'entry_dt':pd.to_datetime(['2025-01-01','2025-01-02']),
                         'exit_dt':pd.to_datetime(['2025-01-01 01:00','2025-01-02 01:00']),
                         'hold':[1,1],'reason_code':['TP','TP'],'regime_code':['UP','UP'],
                         'entry_price':[100.,100.],'exit_price':[100.,100.],'pnl':[0.,0.]})
        live=bt.copy();live['entry_price']=[101.,99.];live['exit_price']=[99.,101.]
        f=price_deviation(live,bt)
        np.testing.assert_allclose(f.entry_adverse_bp,[100.,100.])
        np.testing.assert_allclose(f.exit_adverse_bp,[100.,100.])


if __name__=='__main__':unittest.main()
