import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch2_round3_20260912 as l

class QuoteTests(unittest.TestCase):
    def test_symmetric_log_deviation(self):
        valid,block=l.parity_block([1.,np.exp(.002),np.exp(-.002)])
        self.assertEqual(valid.tolist(),[True,True,True]);self.assertEqual(block.tolist(),[False,True,True])

    def test_missing_never_becomes_parity(self):
        valid,block=l.parity_block([np.nan,0.,-1.,np.inf])
        self.assertFalse(valid.any());self.assertFalse(block.any())

if __name__=='__main__':unittest.main()
