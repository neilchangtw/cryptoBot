"""Hand-computable temporal and terminal-ledger invariants, not profitability fixtures."""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_round2_20260911 as r


class Tests(unittest.TestCase):
    def fixture(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-01-01 10:00',periods=5,freq='h')})
        s=d.assign(volume=100.,taker_buy_volume=[40.,50.,60.,90.,10.])
        return d,s

    def test_known_hour_and_strict_zero(self):
        d,s=self.fixture(); f=r.features(d,s)
        self.assertFalse(f.valid.iloc[0]); self.assertAlmostEqual(f.imbalance.iloc[1],-.2)
        self.assertEqual(f.source_close.iloc[1],pd.Timestamp('2026-01-01 11:00'))
        self.assertEqual(f.decision_ts.iloc[1],pd.Timestamp('2026-01-01 12:00'))
        self.assertEqual(r.allowed(f,'L').tolist(),[False,False,True,True,True])
        self.assertEqual(r.allowed(f,'S').tolist(),[False,True,True,False,False])

    def test_missing_and_bad_volume_no_fill(self):
        d,s=self.fixture(); s=s.drop(index=1); s.loc[2,'volume']=0.; s.loc[3,'taker_buy_volume']=101.
        f=r.features(d,s)
        self.assertEqual(f.valid.tolist(),[False,True,False,False,False])
        self.assertTrue(f.imbalance.iloc[2:].isna().all())

    def test_truncate_and_perturb_future(self):
        d,s=self.fixture(); f=r.features(d,s)
        pd.testing.assert_frame_equal(f.iloc[:3],r.features(d.iloc[:3],s.iloc[:3]))
        s.loc[2:,'taker_buy_volume']=1e8
        pd.testing.assert_frame_equal(f.iloc[:3],r.features(d,s).iloc[:3])

    def test_terminal_fee_funding_and_mark(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=4,freq='h')})
        mark=pd.DataFrame({'close':[100.,101.,103.,102.]})
        fund=pd.DataFrame({'nominal':pd.to_datetime(['2026-01-01 03:00']), 'markPrice':[103.], 'fundingRate':[.01]})
        # Empty schema of completed trades, without inventing a close for the active position.
        f=pd.DataFrame({k:pd.Series(dtype=t) for k,t in {'entry_bar':int,'exit_bar':int,'qty_exact':float,'fee_exact':float,
            'pnl':float,'side':str,'entry_dt':'datetime64[ns]','exit_dt':'datetime64[ns]','reason_code':str,'entry_exact':float}.items()})
        state={'lp_active':True,'lp_bar':1,'lp_entry':101.,'lp_ntl':4040.,'lp_fee':4.,'sp_active':False}
        _,eq,ledger,terminal=r.account_terminal(f,d,mark,fund,state)
        self.assertEqual(len(terminal),1); self.assertEqual(len(f),0)
        np.testing.assert_allclose(eq.equity,[0.,-2.,36.8,-3.2],atol=1e-9)
        self.assertAlmostEqual(ledger.cashflow.sum(),-41.2)

    def test_no_promote_small_gain_or_larger_drawdown(self):
        b={'full':{'net_pnl':100.,'loss_total':40.,'mdd':20.,'worst30':-10.},'early':{'net_pnl':50.},'late':{'net_pnl':50.}}
        c={'full':{'net_pnl':104.,'loss_total':30.,'mdd':21.,'worst30':-10.},'early':{'net_pnl':52.},'late':{'net_pnl':52.}}
        self.assertEqual(r.failures(c,b),['net_gain_5pct','mark_mdd_not_worse'])


if __name__=='__main__': unittest.main()
