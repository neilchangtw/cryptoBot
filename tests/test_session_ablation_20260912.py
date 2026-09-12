"""Specific ablation semantics and engine-isolation invariants; no market outcome fixtures."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import session_ablation_20260912 as r


class Tests(unittest.TestCase):
    def fixture(self):
        e=SimpleNamespace(CB_DAILY=-200,CB_L_MONTH=-75,CB_S_MONTH=-150,L_CD=6,S_CD=8,L_CAP=20,S_CAP=20,
                          L_BLK_D={5,6},S_BLK_D={0,5,6},L_GK_TH=25,S_GK_TH=35)
        ind={'hours':np.array([12]),'dows':np.array([1]),'regime_block_l':np.array([False]),
             'regime_block_s':np.array([False]),'pctile_L':np.array([24.]),'pctile_S':np.array([34.]),
             'brk_up':np.array([True]),'brk_dn':np.array([False])}
        st=pd.DataFrame([dict(bar=0,lp_active=False,sp_active=False,d_pnl=0,l_m_pnl=0,s_m_pnl=0,
             consec_end=-1,l_last_exit=-6,s_last_exit=-8,l_m_entries=0,s_m_entries=0)])
        return e,ind,st

    def test_only_removed_hour_and_exact_cooldown(self):
        e,i,s=self.fixture(); self.assertEqual(r.direct_opportunities(i,s,e).side.tolist(),['L'])
        s.loc[0,'l_last_exit']=-5; self.assertTrue(r.direct_opportunities(i,s,e).empty)
        s.loc[0,'l_last_exit']=-6; i['hours'][0]=13; self.assertTrue(r.direct_opportunities(i,s,e).empty)

    def test_weekday_caps_risk_and_gk_still_apply(self):
        e,i,s=self.fixture()
        for col,value in [('lp_active',True),('d_pnl',-200),('l_m_pnl',-75),('l_m_entries',20),('consec_end',1)]:
            f=s.copy();f.loc[0,col]=value;self.assertTrue(r.direct_opportunities(i,f,e).empty)
        i['dows'][0]=5;self.assertTrue(r.direct_opportunities(i,s,e).empty)
        i['dows'][0]=1;i['pctile_L'][0]=25.;self.assertTrue(r.direct_opportunities(i,s,e).empty)

    def test_namespace_isolation(self):
        ns1={'L_BLK_H':set(r.HOURS),'S_BLK_H':set(r.HOURS)}; ns2=dict(ns1)
        exec('def f(): pass',ns1); exec('def f(): pass',ns2)
        r.apply_hour_mode(ns1['f'],'no_hour')
        self.assertEqual(ns1['L_BLK_H'],set());self.assertEqual(ns2['L_BLK_H'],r.HOURS)
        r.apply_hour_mode(ns1['f'],'base');self.assertEqual(ns1['L_BLK_H'],r.HOURS)


if __name__=='__main__':unittest.main()
