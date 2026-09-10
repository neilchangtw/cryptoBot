"""Independent Decimal checks, saved-ledger conservation and source protection."""
from pathlib import Path
from decimal import Decimal
import json
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/new_information_20260910'


def D(value):
    return Decimal(str(value))


def run(family):
    result = json.loads((OUT / f'{family}_results.json').read_text())
    raw = pd.read_csv(OUT / f'{family}_source_merged.csv', dtype=str)
    raw['event_ts'] = pd.to_datetime(raw.event_ts)
    raw = raw.set_index('event_ts')
    features = pd.read_csv(OUT / f'{family}_features.csv', parse_dates=['decision_ts', 'event_ts', 'assumed_available_ts'])
    checked = 0
    for row in features.itertuples():
        if family == 'oi':
            stamps = [row.event_ts - pd.Timedelta(minutes=j) for j in range(0, 61, 5)]
            exists = all(ts in raw.index for ts in stamps)
            values = [D(raw.loc[ts, 'sum_open_interest']) for ts in stamps] if exists else []
            valid = exists and all(v.is_finite() and v > 0 for v in values)
            assert bool(row.valid) == valid
            if valid:
                expected = values[0] / values[-1] - 1
                assert abs(D(row.delta_oi) - expected) < D('1e-12')
                assert (row.delta_oi <= 0) == (expected <= 0)
        else:
            stamps = [row.event_ts - pd.Timedelta(hours=j) for j in range(25)]
            exists = all(ts in raw.index for ts in stamps)
            values = [D(raw.loc[ts, 'close']) for ts in stamps] if exists else []
            valid = exists and all(v.is_finite() for v in values)
            if valid:
                for ts in stamps:
                    op, hi, lo, cl = [D(raw.loc[ts, c]) for c in ['open', 'high', 'low', 'close']]
                    valid &= hi >= max(op, lo, cl) and lo <= min(op, hi, cl)
            assert bool(row.valid) == valid
            if valid:
                sorted_previous = sorted(values[1:])
                median = (sorted_previous[11] + sorted_previous[12]) / 2
                assert abs(D(row.level) - (values[0] - median)) < D('1e-12')
                assert abs(D(row.change) - (values[0] - values[1])) < D('1e-12')
                for actual, expected in [(row.level, values[0] - median), (row.change, values[0] - values[1])]:
                    assert (actual > 0) == (expected > 0), (row.event_ts, actual, expected)
                    assert (actual < 0) == (expected < 0), (row.event_ts, actual, expected)
        assert row.assumed_available_ts <= row.decision_ts
        checked += 1
    scenarios = []
    for r in result['rows']:
        stem = f'{r["name"]}_{"hist" if r["historical"] else "flat"}_{r["slip"]}'
        f = pd.read_csv(OUT / f'{stem}_trades.csv')
        eq = pd.read_csv(OUT / f'{stem}_equity.csv')
        ledger = pd.read_csv(OUT / f'{stem}_funding.csv')
        ev = pd.read_csv(OUT / f'{stem}_events.csv')
        if r['name'] == 'base':
            expected_allowed = np.ones(len(ev), bool)
        else:
            sign = np.where(ev.side.eq('L'), 1, -1)
            if family == 'oi':
                blocked = ev.delta_oi.le(0).to_numpy(copy=True)
            else:
                level = sign * ev.level.to_numpy() > 0
                change = sign * ev.change.to_numpy() > 0
                blocked = level if r['name'].endswith('_level') else change if r['name'].endswith('_change') else level & change
            if r['name'].endswith('_quality'):
                blocked = np.zeros(len(ev), bool)
            elif r['name'].endswith('_long'):
                blocked &= ev.side.eq('L').to_numpy()
            elif r['name'].endswith('_short'):
                blocked &= ev.side.eq('S').to_numpy()
            expected_allowed = ev.valid.to_numpy(bool) & ~blocked
        np.testing.assert_array_equal(ev.allowed, expected_allowed)
        assert not any(r['terminal'].values())
        assert len(f) == int(ev.allowed.sum()), 'Allowed gates must equal opened/closed trades with flat terminal'
        assert abs(eq.cash.iloc[-1] - f.net.sum()) < 1e-7
        assert abs(eq.equity.iloc[-1] - f.net.sum()) < 1e-7
        assert abs(ledger.cashflow.sum() - f.funding.sum()) < 1e-7
        np.testing.assert_allclose(f.net, f.pnl + f.funding, atol=1e-8, rtol=0)
        for t in f.itertuples():
            assert t.exit_bar > t.entry_bar
            assert t.bars_held == t.exit_bar - t.entry_bar
            assert abs(t.qty_exact * t.entry_exact - t.margin * 20) < 1e-7
        for side in ['L', 'S']:
            rows = f[f.side == side].sort_values('entry_bar')
            if len(rows) > 1:
                assert (rows.entry_bar.to_numpy()[1:] - rows.exit_bar.to_numpy()[:-1] >= (6 if side == 'L' else 8)).all()
        scenarios.append(stem)
    report = {'family': family, 'decimal_all_feature_hours': checked,
              'independent_saved_scenarios': len(scenarios),
              'checked': ['decimal_features', 'assumed_available_before_decision', 'saved_gate_rules', 'gate_trade_conservation',
                          'cash_equity_trade_funding_conservation', 'position_size', 'hold_bars', 'same_side_cooldown'],
              'status': 'PASS', 'limitation': 'Does not establish historical receipt times or immutability of public archives.'}
    (OUT / f'{family}_independent_audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    run(sys.argv[1])
