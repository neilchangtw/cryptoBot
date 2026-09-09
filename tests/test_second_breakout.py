"""再次突破的事件狀態與禁止事後挑選測試。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backtest/research'))
from second_breakout_20260909 import Episodes


class EpisodeTests(unittest.TestCase):
    def step(self,e,i,close,side='L',safe=True,signal=False,boundary=100.):
        return e.step(i,side,safe,signal,boundary,close,[] if safe else ['cooldown'])

    def seed(self,e,side='L'):
        if e.mode=='wait':self.step(e,10,101 if side=='L' else 99,side=side,signal=True)
        else:e.closed(10,side,5,{'kind':'normal','origin':5,'boundary':100.})

    def test_wait_needs_distinct_return_then_rebreak(self):
        e=Episodes('wait12');self.seed(e)
        self.assertIsNone(self.step(e,11,102.))
        self.assertIsNone(self.step(e,12,100.))
        r=self.step(e,13,101.)
        self.assertEqual(r['kind'],'wait');self.assertEqual(r['origin'],10)
        self.assertNotIn('L',e.pending)

    def test_boundary_is_locked_when_new_signals_arrive(self):
        e=Episodes('wait12');self.seed(e)
        self.step(e,11,100.,signal=True,boundary=90.)
        r=self.step(e,12,101.,boundary=102.)
        self.assertEqual(r['boundary'],100.)

    def test_equality_is_inside_short_is_symmetric(self):
        e=Episodes('wait12');self.seed(e,'S')
        self.step(e,11,100.,side='S')
        r=self.step(e,12,99.,side='S')
        self.assertEqual(r['kind'],'wait')

    def test_exact_deadline_can_fill(self):
        e=Episodes('wait6');self.seed(e);self.step(e,15,99.)
        self.assertIsNotNone(self.step(e,16,101.))

    def test_expiry_does_not_reseed_same_bar(self):
        e=Episodes('wait6');self.seed(e)
        self.assertIsNone(self.step(e,17,101.,signal=True))
        self.assertFalse(e.pending);self.assertEqual(e.events[-1]['event'],'expired')

    def test_first_blocked_rebreak_consumes_ticket(self):
        e=Episodes('re24');self.seed(e);self.step(e,11,99.)
        self.assertIsNone(self.step(e,12,101.,safe=False))
        self.assertFalse(e.pending);self.assertIsNone(self.step(e,13,102.))
        self.assertEqual(e.events[-1]['event'],'cross_blocked')

    def test_exit_bar_does_not_count_as_post_exit_return(self):
        e=Episodes('re24');self.seed(e);self.step(e,10,99.)
        self.assertIsNone(self.step(e,11,101.))
        self.assertFalse(e.pending['L']['inside'])

    def test_regular_eligible_signal_has_priority(self):
        e=Episodes('re24');self.seed(e);self.step(e,11,99.)
        r=self.step(e,12,101.,signal=True,boundary=100.5)
        self.assertEqual(r['kind'],'normal');self.assertEqual(r['origin'],12)
        self.assertFalse(e.pending)

    def test_retry_cannot_seed_another_retry(self):
        e=Episodes('re24');self.seed(e);self.step(e,11,99.)
        r=self.step(e,12,101.)
        e.closed(20,'L',12,r)
        self.assertFalse(e.pending)

    def test_outside_control_waits_for_safety_without_return(self):
        e=Episodes('outside24');self.seed(e)
        self.assertIsNone(self.step(e,11,101.,safe=False))
        self.assertIsNotNone(self.step(e,12,101.))

    def test_sides_have_independent_tickets(self):
        e=Episodes('wait12');self.seed(e);self.seed(e,'S')
        self.step(e,11,99.);self.step(e,12,101.)
        self.assertNotIn('L',e.pending);self.assertIn('S',e.pending)

    def test_future_events_do_not_rewrite_saved_history(self):
        e=Episodes('wait12');self.seed(e)
        before=dict(e.events[0]);self.step(e,11,99.)
        self.assertEqual(e.events[0],before)
        self.assertFalse(e.events[0]['inside'])


if __name__=='__main__':unittest.main()
