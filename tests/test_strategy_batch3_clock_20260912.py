import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
import strategy_batch3_20260912 as b

def frame(decisions):return pd.DataFrame({'datetime':pd.to_datetime(decisions)-pd.Timedelta(hours=1)})

class ClockTests(unittest.TestCase):
    def test_dst_spring_and_fall(self):
        f=b.ny_clock(frame(['2026-03-06 22:00','2026-03-09 21:00','2026-10-30 21:00','2026-11-02 22:00']))
        self.assertTrue(f.blocked.all())
        self.assertTrue(f.ny_time.str.contains('09:00:00').all())
    def test_inclusive_start_exclusive_end_and_weekends(self):
        d=pd.DatetimeIndex(['2026-07-07 08:29','2026-07-07 08:30','2026-07-07 15:59','2026-07-07 16:00','2026-07-11 10:00']).tz_localize('America/New_York').tz_convert('Asia/Taipei').tz_localize(None)
        self.assertEqual(b.ny_clock(frame(d)).blocked.tolist(),[False,True,True,False,False])
    def test_decision_time_not_open(self):
        f=b.ny_clock(pd.DataFrame({'datetime':pd.to_datetime(['2026-07-07 20:00'])}))
        self.assertTrue(f.blocked.iloc[0]);self.assertIn('09:00',f.ny_time.iloc[0])
    def test_future_independent_and_missing_rejected(self):
        d=pd.DataFrame({'datetime':pd.date_range('2026-03-06',periods=150,freq='h')})
        pd.testing.assert_frame_equal(b.ny_clock(d.iloc[:60]),b.ny_clock(d).iloc[:60])
        d['close']=100
        pd.testing.assert_frame_equal(b.ny_clock(d),b.ny_clock(d.drop(columns='close')))
        with self.assertRaises(AssertionError):b.ny_clock(pd.DataFrame({'datetime':[pd.NaT]}))

if __name__=='__main__':unittest.main()
