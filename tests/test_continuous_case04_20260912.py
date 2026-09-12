import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import continuous_case04_20260912 as s

def block(height,time):return {'height':height,'timestamp':time,'hash':'0x'+'a'*64,'base_fee_per_gas':'123','gas_used':'20','gas_limit':'100'}

class OnchainSourceTests(unittest.TestCase):
    def test_reference_must_be_bracketed(self):
        a=block(1,'2026-01-01T00:00:00Z');b=block(2,'2026-01-01T00:00:12Z')
        self.assertEqual(s.validate_pair(a,b,'2026-01-01T00:00:01Z')['bracket'],'PASS')
        with self.assertRaises(AssertionError):s.validate_pair(a,b,'2026-01-01T01:00:00Z')
    def test_current_finality_does_not_prove_history(self):
        x=block(1,'2026-01-01T00:00:00Z');x['finalized']=True
        self.assertFalse(s.historical_available(x,'2026-01-02T00:00:00Z'))
    def test_historical_receipt_must_precede_decision(self):
        x={'historical_finalized_at':'2026-01-01T01:00:00Z','historical_available_at':'2026-01-02T01:00:00Z','historical_evidence_uri':'fixture://proof'}
        self.assertFalse(s.historical_available(x,'2026-01-02T00:00:00Z'))
        self.assertTrue(s.historical_available(x,'2026-01-02T02:00:00Z'))
    def test_invalid_chain_fields_rejected(self):
        a=block(1,'2026-01-01T00:00:00Z');b=block(2,'2026-01-01T00:00:12Z');a['gas_used']='101'
        with self.assertRaises(AssertionError):s.validate_pair(a,b,'2026-01-01T00:00:01Z')

if __name__=='__main__':unittest.main()
