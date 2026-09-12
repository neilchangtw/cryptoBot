import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch3_round2_20260912 as n

class FundingTests(unittest.TestCase):
    def test_received_lag_day_reset_credits_and_sides(self):
        d=pd.DataFrame({'datetime':pd.to_datetime(['2026-01-31 22:00','2026-01-31 23:00','2026-02-01 00:00','2026-02-01 01:00'])})
        f=pd.DataFrame({'time':pd.to_datetime(['2026-01-31 23:00','2026-02-01 00:00','2026-02-01 00:00']),
            'side':['L','S','L'],'cashflow':[-2.,-3.,100.],'included':[True,True,True]})
        b=n.debit_budget(d,f)
        self.assertEqual(b.daily_debit.tolist(),[0.,-2.,-3.,-3.])
        self.assertEqual(b.L_month_debit.tolist(),[0.,-2.,0.,0.])
        self.assertEqual(b.S_month_debit.tolist(),[0.,0.,-3.,-3.])
    def test_boundary_and_positive_credit_never_reopen(self):
        d=pd.DataFrame({'datetime':pd.to_datetime(['2026-01-01 01:00'])})
        f=pd.DataFrame({'time':pd.to_datetime(['2026-01-01 00:00']),'side':['L'],'cashflow':[-1.],'included':[True]})
        budget=n.debit_budget(d,f)
        gates=pd.DataFrame({'bar':[0,0],'side':['L','S'],'decision_ts':pd.to_datetime(['2026-01-01 02:00']*2)})
        states=pd.DataFrame({'bar':[0],'d_pnl':[-198.],'l_m_pnl':[-74.],'s_m_pnl':[-149.]})
        engine=SimpleNamespace(CB_DAILY=-200,CB_L_MONTH=-75,CB_S_MONTH=-150)
        self.assertEqual(n.affected(gates,states,budget,engine).side.tolist(),['L'])
    def test_no_future_settlement_and_not_included(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=6,freq='h')})
        f=pd.DataFrame({'time':pd.to_datetime(['2026-01-01 01:00','2026-01-01 05:00']),'side':['L','S'],'cashflow':[-3.,-5.],'included':[False,True]})
        b=n.debit_budget(d,f)
        self.assertEqual(b.daily_debit.tolist(),[0.,0.,0.,0.,0.,-5.])
        pd.testing.assert_frame_equal(n.debit_budget(d.iloc[:4],f.iloc[:1]),b.iloc[:4])

if __name__=='__main__':unittest.main()
