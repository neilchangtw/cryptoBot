"""Boundary/missing-data/causality tests for the new-information research only."""
from pathlib import Path
import json
import sys
import unittest
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backtest/research'))
import new_information_20260910 as study


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads((ROOT / 'tests/fixtures/new_information_small.json').read_text())
        self.oi = pd.DataFrame({'event_ts': pd.date_range(self.fx['oi_start'], periods=7, freq='5min'),
                                'sum_open_interest': self.fx['oi_values']})
        self.prem = pd.DataFrame({'event_ts': pd.date_range(self.fx['premium_start'], periods=7, freq='1h')})
        for col in ['open', 'high', 'low', 'close']:
            self.prem[col] = self.fx['premium_values']
        self.d = pd.DataFrame({'datetime': [pd.Timestamp(self.fx['oi_decision_open'])]})

    def test_oi_uses_exact_lagged_endpoints_and_not_later_values(self):
        f = study.oi_features(self.d, self.oi, window_minutes=10)
        self.assertTrue(f.valid.iloc[0])
        self.assertAlmostEqual(f.delta_oi.iloc[0], self.fx['expected_oi_change_10min'])
        self.assertEqual(f.event_ts.iloc[0], pd.Timestamp('2026-01-01 00:45'))
        self.assertLessEqual(f.assumed_available_ts.iloc[0], f.decision_ts.iloc[0])
        altered = self.oi.copy()
        altered.loc[altered.event_ts > f.event_ts.iloc[0], 'sum_open_interest'] = -100000
        pd.testing.assert_frame_equal(f, study.oi_features(self.d, altered, window_minutes=10))

    def test_missing_intermediate_sample_cannot_be_forward_filled(self):
        raw = self.oi[self.oi.event_ts != pd.Timestamp('2026-01-01 00:40')]
        f = study.oi_features(self.d, raw, window_minutes=10)
        self.assertFalse(f.valid.iloc[0])
        self.assertTrue(np.isnan(f.delta_oi.iloc[0]))
        for name, sides in study.make_masks('oi', f).items():
            if name != 'base':
                self.assertFalse(sides['L'][0])
                self.assertFalse(sides['S'][0])

    def test_flat_oi_rejected_and_single_side_scoped(self):
        raw = self.oi.copy()
        raw.sum_open_interest = 100
        f = study.oi_features(self.d, raw, window_minutes=10)
        masks = study.make_masks('oi', f)
        self.assertFalse(masks['oi_both']['L'][0])
        self.assertFalse(masks['oi_both']['S'][0])
        self.assertTrue(masks['oi_long']['S'][0])
        self.assertTrue(masks['oi_short']['L'][0])

    def test_premium_uses_prior_bar_and_excludes_it_from_median(self):
        f = study.premium_features(self.d, self.prem, window=3)
        self.assertTrue(f.valid.iloc[0])
        self.assertAlmostEqual(f.level.iloc[0], self.fx['expected_premium_level_3h'])
        self.assertAlmostEqual(f.change.iloc[0], self.fx['expected_premium_change'])
        self.assertEqual(f.event_ts.iloc[0], pd.Timestamp('2025-12-31 23:00'))
        self.assertLessEqual(f.assumed_available_ts.iloc[0], f.decision_ts.iloc[0])
        altered = self.prem.copy()
        altered.loc[altered.event_ts >= pd.Timestamp('2026-01-01'), ['open', 'high', 'low', 'close']] = -999
        pd.testing.assert_frame_equal(f, study.premium_features(self.d, altered, window=3))
        masks = study.make_masks('premium', f)
        self.assertFalse(masks['premium_both']['L'][0])
        self.assertTrue(masks['premium_both']['S'][0])

    def test_premium_equalities_pass_and_invalid_ohlc_is_missing(self):
        raw = self.prem.copy()
        raw[['open', 'high', 'low', 'close']] = -.001
        f = study.premium_features(self.d, raw, window=3)
        for side in ['L', 'S']:
            self.assertTrue(study.make_masks('premium', f)['premium_both'][side][0])
        raw.loc[raw.event_ts == pd.Timestamp('2025-12-31 22:00'), 'high'] = -.002
        self.assertFalse(study.premium_features(self.d, raw, window=3).valid.iloc[0])

    def test_cross_side_same_episode_is_not_two_independent_samples(self):
        f = pd.DataFrame({'entry_dt': pd.to_datetime(['2026-01-01 00:00', '2026-01-01 12:00', '2026-01-03 00:00']),
                          'side': ['L', 'S', 'L']})
        self.assertEqual(study.groups(f).cluster.tolist(), [1, 1, 2])

    def test_bootstrap_preserves_constant_zero_difference(self):
        eq = pd.DataFrame({'time': pd.date_range('2026-01-01', periods=240, freq='1h'), 'cash': np.arange(240.)})
        result = study.block_uncertainty(eq, eq)
        self.assertEqual(result['delta'], 0)
        self.assertEqual(result['ci95'], [0, 0])
        self.assertEqual(result['p'], 1)


if __name__ == '__main__':
    unittest.main()
