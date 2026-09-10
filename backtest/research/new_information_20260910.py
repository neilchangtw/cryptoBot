"""Preregistered OI/premium study using the existing stateful V14 engine.

Only research files are written. Public inputs are acquired separately.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import zipfile
import numpy as np
import pandas as pd
import execute_optimization_plan_20260908 as cost
import intrahour_entry_20260908 as intra
import maxhold_review_20260908 as review

ROOT = cost.ROOT
OUT = ROOT / 'data/new_information_20260910'
DELIVERY = ROOT / 'doc/research_results/20260910_new_information'
PLAN = ROOT / 'doc/new_information_plan_20260910.md'
MAIN = {f: [f + '_' + s for s in ['both', 'long', 'short']] for f in ['oi', 'premium']}


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def archive_frames(kinds):
    manifest = json.loads((OUT / 'download_manifest.json').read_text())
    frames, audit = [], []
    for rec in manifest.values():
        if rec.get('status') != 200 or not any('/' + k + '/' in rec.get('path', '').replace('\\', '/') for k in kinds):
            continue
        path = ROOT / rec['path']
        assert cost.sha(path) == rec['sha256']
        checksum = path.with_suffix('.zip.CHECKSUM').read_text().split()[0]
        assert checksum == rec['sha256']
        with zipfile.ZipFile(path) as z:
            assert len(z.namelist()) == 1
            f = pd.read_csv(z.open(z.namelist()[0]))
        is_oi = 'create_time' in f
        ts = pd.to_datetime(f.create_time, utc=True) if is_oi else pd.to_datetime(f.open_time, unit='ms', utc=True)
        f['event_ts'] = ts.dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
        audit.append({'file': str(path.relative_to(ROOT)), 'rows': len(f),
                      'start_utc': str(ts.min()), 'end_utc': str(ts.max()),
                      'unsorted': not ts.is_monotonic_increasing,
                      'duplicates': int(ts.duplicated().sum()), 'last_modified': rec.get('last_modified')})
        frames.append(f)
    if not frames:
        raise RuntimeError('No validated archive files for ' + str(kinds))
    allf = pd.concat(frames, ignore_index=True)
    dup = allf[allf.event_ts.duplicated(keep=False)]
    if len(dup):
        numeric = [c for c in allf.columns if c not in ['event_ts', 'create_time']]
        assert dup.groupby('event_ts')[numeric].nunique(dropna=False).max().max() <= 1, 'Conflicting archive duplicates'
    identical_duplicates = int(allf.event_ts.duplicated().sum())
    allf = allf.drop_duplicates('event_ts').sort_values('event_ts').reset_index(drop=True)
    assert allf.event_ts.is_unique
    return allf, {'files': audit, 'identical_duplicates_removed': identical_duplicates}


def oi_features(d, raw, window_minutes=60, lag_minutes=15):
    """Exact timestamps only; require every intervening 5m sample, no filling."""
    decision = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    query = decision - pd.Timedelta(minutes=lag_minutes)
    series = raw.set_index('event_ts').sum_open_interest
    assert series.index.is_unique
    a = np.column_stack([series.reindex(query - pd.Timedelta(minutes=j)).to_numpy(float)
                         for j in range(0, window_minutes + 1, 5)])
    valid = (np.isfinite(a) & (a > 0)).all(axis=1)
    change = np.full(len(d), np.nan)
    change[valid] = a[valid, 0] / a[valid, -1] - 1
    feat = pd.DataFrame({'decision_ts': decision, 'event_ts': query,
                         'close_ts_bound': query + pd.Timedelta(minutes=5),
                         'assumed_available_ts': query + pd.Timedelta(minutes=10),
                         'valid': valid, 'delta_oi': change})
    assert (feat.assumed_available_ts <= feat.decision_ts).all()
    return feat


def premium_features(d, raw, window=24, lag_hours=1):
    """At t+1h decision use premium open t-1h, closed at t, plus earlier history."""
    decision = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    query = pd.DatetimeIndex(d.datetime - pd.Timedelta(hours=lag_hours))
    q = raw.set_index('event_ts')
    assert q.index.is_unique
    valid_raw = np.isfinite(q[['open', 'high', 'low', 'close']]).all(axis=1)
    valid_raw &= (q.high >= q[['open', 'close', 'low']].max(axis=1))
    valid_raw &= (q.low <= q[['open', 'close', 'high']].min(axis=1))
    values = q.close.where(valid_raw)
    a = np.column_stack([values.reindex(query - pd.Timedelta(hours=j)).to_numpy(float)
                         for j in range(window + 1)])
    valid = np.isfinite(a).all(axis=1)
    level = np.full(len(d), np.nan)
    change = np.full(len(d), np.nan)
    level[valid] = a[valid, 0] - np.median(a[valid, 1:], axis=1)
    change[valid] = a[valid, 0] - a[valid, 1]
    return pd.DataFrame({'decision_ts': decision, 'event_ts': query,
                         'close_ts_bound': query + pd.Timedelta(hours=1),
                         'assumed_available_ts': query + pd.Timedelta(hours=1, minutes=10),
                         'valid': valid, 'premium': a[:, 0], 'level': level, 'change': change})


def make_masks(family, feat):
    valid = feat.valid.to_numpy(bool)
    yes = np.ones(len(feat), bool)
    rules = ['base', family + '_quality'] + MAIN[family]
    if family == 'premium':
        rules += ['premium_level', 'premium_change']
    masks = {name: {} for name in rules}
    for side, sign in [('L', 1), ('S', -1)]:
        if family == 'oi':
            bad = feat.delta_oi.to_numpy() <= 0
        else:
            A = sign * feat.level.to_numpy() > 0
            B = sign * feat.change.to_numpy() > 0
            bad = A & B
        for name in rules:
            blocked = bad if (name.endswith('_both') or (name.endswith('_long') and side == 'L')
                             or (name.endswith('_short') and side == 'S')) else np.zeros(len(feat), bool)
            if name == 'premium_level':
                blocked = A
            elif name == 'premium_change':
                blocked = B
            masks[name][side] = yes.copy() if name == 'base' else valid & ~blocked
    return masks


def extra_metrics(f, eq, d, start=None, end=None):
    result = cost.metrics(f, eq, start, end)
    subset = f
    if start:
        subset = subset[subset.exit_dt >= start]
    if end:
        subset = subset[subset.exit_dt < end]
    wins = subset.loc[subset.net > 0, 'net']
    losses = subset.loc[subset.net <= 0, 'net']
    result.update(expectancy=float(subset.net.mean()) if len(subset) else None,
                  avg_win=float(wins.mean()) if len(wins) else None,
                  avg_loss=float(losses.mean()) if len(losses) else None,
                  mh_pct=100 * result['mh'] / len(subset) if len(subset) else None,
                  tail5_mean=float(subset.net.nsmallest(max(1, int(np.ceil(len(subset) * .05)))).mean()) if len(subset) else None)
    times = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    counts = np.zeros(len(d), int)
    for t in f.itertuples():
        counts[int(t.entry_bar):int(t.exit_bar)] += 1
    selected = np.ones(len(d), bool)
    if start:
        selected &= times >= pd.Timestamp(start)
    if end:
        selected &= times < pd.Timestamp(end)
    values = counts[selected]
    result.update(exposure_hours=int((values > 0).sum()),
                  exposure_pct=float((values > 0).mean() * 100),
                  position_hours=int(values.sum()), max_simultaneous=int(values.max()))
    return result


def block_uncertainty(eq, base, block=7, seed=20260910):
    delta = (eq.set_index('time').cash - base.set_index('time').cash).resample('D').last().ffill()
    a = delta.diff().to_numpy(copy=True)
    a[0] = delta.iloc[0]
    rng = np.random.default_rng(seed)
    n = len(a)
    starts = rng.integers(0, n, size=(2000, int(np.ceil(n / block))))
    indices = ((starts[:, :, None] + np.arange(block)) % n).reshape(2000, -1)[:, :n]
    draws = a[indices].sum(axis=1)
    return {'block_days': block, 'draws': 2000, 'delta': float(a.sum()),
            'ci95': np.quantile(draws, [.025, .975]).tolist(),
            'p': float((1 + ((draws - a.sum()) >= a.sum()).sum()) / 2001)}


def groups(frame):
    f = frame.sort_values('entry_dt').copy()
    if not len(f):
        f['cluster'] = pd.Series(dtype=int)
        return f
    f['cluster'] = f.entry_dt.diff().gt(pd.Timedelta(hours=24)).cumsum() + 1
    return f


class Study:
    def __init__(self, family):
        self.family = family
        self.source = cost.Study()
        self.d = self.source.d
        self.engine = self.source.engine
        self.ind = self.engine.compute_indicators(self.d)
        self.fn = intra.simulator(self.engine)
        self.cache = {}
        if family == 'oi':
            self.raw, self.audit = archive_frames(['metrics'])
            assert self.raw.symbol.eq('ETHUSDT').all()
            self.feat = oi_features(self.d, self.raw)
            interval = '5min'
        else:
            self.raw, self.audit = archive_frames(['premium', 'premium_daily'])
            assert (self.raw.close_time - self.raw.open_time == 3599999).all()
            self.feat = premium_features(self.d, self.raw)
            interval = '1h'
        expected = pd.date_range(self.raw.event_ts.min(), self.raw.event_ts.max(), freq=interval)
        missing = expected.difference(pd.DatetimeIndex(self.raw.event_ts))
        assert self.raw.event_ts.isin(expected).all(), 'Off-grid archive timestamp'
        self.audit.update(raw_rows=len(self.raw), raw_start=str(self.raw.event_ts.min()), raw_end=str(self.raw.event_ts.max()),
                          missing_raw=len(missing), missing_times=[str(t) for t in missing],
                          invalid_feature_hours=int((~self.feat.valid).sum()),
                          invalid_post_warmup=int((~self.feat.valid.iloc[self.engine.WARMUP:]).sum()),
                          post_warmup_hours=len(self.d) - self.engine.WARMUP,
                          availability='assumed_lag_not_historical_receipts',
                          archive_revision_risk=True)
        if family == 'oi':
            invalid = ~(np.isfinite(self.raw.sum_open_interest) & (self.raw.sum_open_interest > 0))
            self.audit.update(invalid_oi_rows=int(invalid.sum()),
                              invalid_oi_times=[str(t) for t in self.raw.loc[invalid, 'event_ts']])
        self.filters = make_masks(family, self.feat)
        self.feat.to_csv(OUT / f'{family}_features.csv', index=False)
        self.raw.to_csv(OUT / f'{family}_source_merged.csv', index=False)

    def run(self, name, slip=0, hist=False, cut=None, masks=None, save=True):
        d = self.d if cut is None else self.d.iloc[:cut]
        ind = self.ind if cut is None else self.engine.compute_indicators(d)
        filters = self.filters[name] if masks is None else masks
        events = []

        def gate(i, side):
            allowed = bool(filters[side][i])
            events.append({'bar': i, 'side': side, 'allowed': allowed})
            return allowed

        raw, active = self.fn(ind, d.datetime.to_numpy(), realistic=True, slip_bps=slip,
                              margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None, gate=gate)
        f = review.normalize(pd.DataFrame(raw))
        ev = pd.DataFrame(events)
        if cut is not None:
            return f, ev
        assert not any(active.values()), 'Terminal position: stop scoring until ledger is extended'
        f, eq, ledger = cost.account(f, d, self.source.mark, self.source.fund)
        row = {'name': name, 'slip': slip, 'historical': hist,
               'full': extra_metrics(f, eq, d),
               'early': extra_metrics(f, eq, d, end='2026-01-01'),
               'late': extra_metrics(f, eq, d, start='2026-01-01'),
               'recent': extra_metrics(f, eq, d, start='2026-06-01'),
               'side': {}, 'eligible_gates': len(ev), 'rejected_gates': int((~ev.allowed).sum()),
               'funding_low': float(f.funding_low.sum()), 'funding_high': float(f.funding_high.sum()),
               'terminal': active}
        for side in ['L', 'S']:
            sf = f[f.side == side]
            sf, se, _ = cost.account(sf, d, self.source.mark, self.source.fund)
            row['side'][side] = extra_metrics(sf, se, d)
        if save:
            stem = f'{name}_{"hist" if hist else "flat"}_{slip}'
            f.to_csv(OUT / f'{stem}_trades.csv', index=False)
            eq.to_csv(OUT / f'{stem}_equity.csv', index=False)
            ledger.to_csv(OUT / f'{stem}_funding.csv', index=False)
            ev['decision_ts'] = (d.datetime + pd.Timedelta(hours=1)).iloc[ev.bar].to_numpy()
            for column in self.feat.columns:
                if column != 'decision_ts':
                    ev[column] = self.feat[column].iloc[ev.bar].to_numpy()
            ev.to_csv(OUT / f'{stem}_events.csv', index=False)
            self.cache[name, slip, hist] = f, eq, row, ev[['bar', 'side', 'allowed']]
        return f, eq, row, ev[['bar', 'side', 'allowed']]

    def verify_base(self, slip, hist):
        f, eq, row, _ = self.cache['base', slip, hist]
        original = pd.DataFrame(self.engine.simulate_v14_detailed(
            self.ind, self.d.datetime.to_numpy(), realistic=True, slip_bps=slip,
            margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None))
        raw, _ = self.fn(self.ind, self.d.datetime.to_numpy(), realistic=True, slip_bps=slip,
                        margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)
        pd.testing.assert_frame_equal(pd.DataFrame(raw)[original.columns], original)
        old = pd.read_csv(ROOT / f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
        for column in ['side', 'entry_bar', 'exit_bar', 'reason_code']:
            assert f[column].equals(old[column])
        for column in ['pnl', 'funding', 'net']:
            np.testing.assert_allclose(f[column], old[column], atol=1e-8, rtol=0)
        return {'baseline_parity': [hist, slip], 'status': 'PASS'}

    def verify_prefix(self, cut):
        d = self.d.iloc[:cut]
        # Raw source genuinely truncated before the last decision timestamp.
        raw = self.raw[self.raw.event_ts < d.datetime.iloc[-1] + pd.Timedelta(hours=1)]
        feat = oi_features(d, raw) if self.family == 'oi' else premium_features(d, raw)
        pd.testing.assert_frame_equal(feat, self.feat.iloc[:cut])
        masks = make_masks(self.family, feat)
        for name in self.filters:
            for side in ['L', 'S']:
                np.testing.assert_array_equal(masks[name][side], self.filters[name][side][:cut])
            small, ev = self.run(name, cut=cut, masks=masks[name], save=False)
            full, _, _, full_ev = self.cache[name, 0, False]
            full = full[full.exit_bar < cut]
            pd.testing.assert_frame_equal(small.reset_index(drop=True), full[small.columns].reset_index(drop=True))
            pd.testing.assert_frame_equal(ev, full_ev[full_ev.bar < cut].reset_index(drop=True))
        return {'prefix_cut': cut, 'rules': len(self.filters), 'features_gates_trades': 'PASS'}

    def diagnostic(self, name):
        bf, be, br, _ = self.cache['base', 0, False]
        f, eq, row, _ = self.cache[name, 0, False]
        counts, paired = cost.paired(f, bf)
        paired.to_csv(OUT / f'{name}_paired.csv', index=False)
        removed = paired[paired._merge == 'right_only']
        added = paired[paired._merge == 'left_only']
        common = paired[paired._merge == 'both']
        parts = {'avoided_losing_net': float(-removed.loc[removed.net_b <= 0, 'net_b'].sum()),
                 'missed_winning_net': float(-removed.loc[removed.net_b > 0, 'net_b'].sum()),
                 'new_net': float(added.net_c.sum()), 'common_delta': float((common.net_c - common.net_b).sum())}
        delta = row['full']['net_pnl'] - br['full']['net_pnl']
        assert abs(sum(parts.values()) - delta) < 1e-7
        valid = self.feat.valid.to_numpy()
        direct = [valid[int(t.entry_bar)] and not self.filters[name][t.side][int(t.entry_bar)] for t in bf.itertuples()]
        affected = groups(bf[direct])
        affected.to_csv(OUT / f'{name}_original_affected.csv', index=False)
        clusters = affected.groupby('cluster').entry_dt.min() if len(affected) else pd.Series(dtype='datetime64[ns]')
        late_clusters = int((clusters >= '2026-01-01').sum())
        years = (self.d.datetime.iloc[-1] - self.d.datetime.iloc[0]).total_seconds() / (365.25 * 86400)
        sample = {'direct_original_events': len(affected), 'clusters24h': len(clusters),
                  'late_direct_events': int((affected.entry_dt >= '2026-01-01').sum()),
                  'late_clusters24h': late_clusters, 'annual_clusters': len(clusters) / years,
                  'years_for_30_new_clusters_at_historical_rate': 30 * years / len(clusters) if len(clusters) else None}
        hard, practical, sample_fails = [], [], []
        for slip in [0, 2, 5]:
            r = self.cache[name, slip, False][2]
            for control in ['base', self.family + '_quality']:
                b = self.cache[control, slip, False][2]
                for period in ['full', 'early', 'late']:
                    if r[period]['net_pnl'] < b[period]['net_pnl'] - 1e-7:
                        hard.append(f'{control}:slip{slip}:{period}:net_decreases')
                if r['full']['wr'] <= b['full']['wr']:
                    hard.append(f'{control}:slip{slip}:full:wr_not_improved')
                if r['full']['mdd'] > b['full']['mdd'] + 1e-7:
                    hard.append(f'{control}:slip{slip}:mdd_worse')
                if r['full']['worst30'] < b['full']['worst30'] - 1e-7:
                    hard.append(f'{control}:slip{slip}:worst30_worse')
        if delta < br['full']['net_pnl'] * .05:
            practical.append('net_gain_below_5pct')
        if row['full']['wr'] - br['full']['wr'] < 1:
            practical.append('wr_gain_below_1pp')
        if len(clusters) < 30:
            sample_fails.append('clusters_below_30')
        if late_clusters < 15:
            sample_fails.append('late_clusters_below_15')
        coverage_failed = self.audit['invalid_post_warmup'] / self.audit['post_warmup_hours'] > .01
        status = 'DATA_BLOCKED' if coverage_failed else 'REJECTED' if hard else 'INSUFFICIENT_SAMPLE' if sample_fails else 'SMALL_EFFECT' if practical else 'NEEDS_FURTHER_VALIDATION'
        increments = np.r_[(-removed.net_b).to_numpy(), added.net_c.to_numpy(), (common.net_c - common.net_b).to_numpy()]
        monthly = (eq.set_index('time').cash - be.set_index('time').cash).resample('ME').last()
        monthly_changes = monthly.diff()
        monthly_changes.iloc[0] = monthly.iloc[0]
        monthly_changes.to_csv(OUT / f'{name}_monthly_delta.csv')
        return {'status': status if name in MAIN[self.family] else 'DIAGNOSTIC_CONTROL',
                'pair': counts, 'decomposition': parts, 'sample': sample,
                'delta': delta, 'gain_pct': delta / br['full']['net_pnl'] * 100,
                'wr_delta_pp': row['full']['wr'] - br['full']['wr'],
                'uncertainty7': block_uncertainty(eq, be, 7), 'uncertainty30': block_uncertainty(eq, be, 30),
                'without_best_increment_month': float(delta - monthly_changes.max()),
                'without_best_pair_component': float(delta - increments.max()) if len(increments) else delta,
                'hard_failures': hard, 'practical_failures': practical, 'sample_failures': sample_fails,
                'quality_coverage_failed': coverage_failed,
                'heavy_validation': 'NOT_RUN_BASIC_GATES_FAILED' if hard or practical or sample_fails or coverage_failed else 'REQUIRED'}

    def random_controls(self, name, repetitions=100):
        """Match rejection counts within side/quarter and original/other technical events."""
        bf, be, br, _ = self.cache['base', 0, False]
        quality = self.filters[self.family + '_quality']
        candidate = self.filters[name]
        quarters = pd.DatetimeIndex(self.d.datetime + pd.Timedelta(hours=1)).to_period('Q').astype(str).to_numpy()
        core = {}
        original = {s: set(bf.loc[bf.side == s, 'entry_bar'].astype(int)) for s in ['L', 'S']}
        for s in ['L', 'S']:
            long = s == 'L'
            core[s] = (np.arange(len(self.d)) >= self.engine.WARMUP) & quality[s]
            core[s] &= self.ind['pctile_L' if long else 'pctile_S'] < (self.engine.L_GK_TH if long else self.engine.S_GK_TH)
            core[s] &= self.ind['brk_up' if long else 'brk_dn'] & ~self.ind['regime_block_l' if long else 'regime_block_s']
            core[s] &= ~np.isin(self.ind['hours'], list(self.engine.L_BLK_H if long else self.engine.S_BLK_H))
            core[s] &= ~np.isin(self.ind['dows'], list(self.engine.L_BLK_D if long else self.engine.S_BLK_D))
        strata = []
        for side in ['L', 'S']:
            for quarter in np.unique(quarters):
                universe = np.flatnonzero(core[side] & (quarters == quarter))
                for is_original in [True, False]:
                    indices = np.array([i for i in universe if (i in original[side]) == is_original], dtype=int)
                    k = int((~candidate[side][indices]).sum())
                    strata.append((side, indices, k))
        seed = 20260910 + int(hashlib.sha256(name.encode()).hexdigest()[:6], 16)
        rng = np.random.default_rng(seed)
        rows = []
        for repeat in range(repetitions):
            masks = {side: quality[side].copy() for side in ['L', 'S']}
            for side, indices, k in strata:
                if k:
                    masks[side][rng.choice(indices, k, replace=False)] = False
            # Exact matching is before state cascades; output trades need not match.
            for side in ['L', 'S']:
                bidx = np.array(sorted(original[side]), dtype=int)
                assert int((~masks[side][bidx]).sum()) == int((~candidate[side][bidx]).sum())
                assert int((~masks[side][core[side]]).sum()) == int((~candidate[side][core[side]]).sum())
            f, eq, row, _ = self.run(name, masks=masks, save=False)
            rows.append({'repeat': repeat, 'net': row['full']['net_pnl'], 'delta': row['full']['net_pnl'] - br['full']['net_pnl'],
                         'wr': row['full']['wr'], 'mdd': row['full']['mdd'], 'n': len(f)})
            if repeat % 25 == 24:
                print(json.dumps({'random': name, 'completed': repeat + 1}), flush=True)
        frame = pd.DataFrame(rows)
        frame.to_csv(OUT / f'{name}_matched_random.csv', index=False)
        actual = self.cache[name, 0, False][2]['full']['net_pnl'] - br['full']['net_pnl']
        return {'repetitions': repetitions, 'seed': seed, 'matching': 'side_quarter_original_vs_other_technical_events',
                'delta_quantiles_025_50_975': frame.delta.quantile([.025, .5, .975]).tolist(),
                'p_random_at_least_actual': float((1 + (frame.delta >= actual).sum()) / (repetitions + 1)),
                'conditional_diagnostic_not_deployable': True}


def registration(family):
    protected = [PLAN, ROOT / 'strategy.py', ROOT / 'executor.py', cost.base.ENGINE_PATH,
                 Path(cost.__file__), Path(intra.__file__), Path(review.__file__), Path(cost.base.__file__),
                 ROOT / 'data/maxhold_review_20260908/candles.csv',
                 cost.HISTORY / 'mark_1h_full.csv', cost.HISTORY / 'funding_full.csv',
                 ROOT / 'doc/strategy_research_prompt_20260910.md']
    hashes = {str(p.relative_to(ROOT)).replace('\\', '/'): cost.sha(p) for p in protected}
    info = {'registered_utc': datetime.now(timezone.utc).isoformat(), 'family': family,
            'main': MAIN[family], 'hashes': hashes, 'research_code_sha256': cost.sha(Path(__file__)),
            'download_manifest_sha256': cost.sha(OUT / 'download_manifest.json'),
            'comparison_is_new_holdout': False}
    path = OUT / f'{family}_registration.json'
    if path.exists():
        old = json.loads(path.read_text())
        assert old['hashes'] == hashes, 'Registered sources changed'
    else:
        dump(path, info)
    return hashes


def run_family(family):
    hashes = registration(family)
    s = Study(family)
    dump(OUT / f'{family}_data_audit.json', s.audit)
    rows, checks = [], []
    # Baseline/parity first, then diagnostic counts, before candidate PnL.
    s.run('base')
    checks.append(s.verify_base(0, False))
    baseline = s.cache['base', 0, False][0]
    label = baseline.copy()
    for col in s.feat:
        label[col] = s.feat[col].iloc[baseline.entry_bar.astype(int)].to_numpy()
    label.to_csv(OUT / f'{family}_baseline_labels.csv', index=False)
    counts = {}
    for name in MAIN[family]:
        direct = [s.feat.valid.iloc[int(t.entry_bar)] and not s.filters[name][t.side][int(t.entry_bar)] for t in baseline.itertuples()]
        counts[name] = {'direct_original_events': int(sum(direct)), 'clusters24h': int(groups(baseline[direct]).cluster.nunique())}
    print(json.dumps({'family': family, 'pre_pnl_counts': counts, 'invalid_hours': s.audit['invalid_feature_hours']}), flush=True)
    dump(OUT / f'{family}_pre_pnl_counts.json', counts)
    for hist in [False, True]:
        for slip in [0, 2, 5]:
            for name in s.filters:
                if (name, slip, hist) not in s.cache:
                    s.run(name, slip, hist)
                rows.append(s.cache[name, slip, hist][2])
                if name == 'base':
                    checks.append(s.verify_base(slip, hist))
            print(json.dumps({'family': family, 'hist': hist, 'slip': slip, 'complete': True}), flush=True)
    for cut in [10000, 16000]:
        checks.append(s.verify_prefix(cut))
    diagnostics = {name: s.diagnostic(name) for name in s.filters if name not in ['base', family + '_quality']}
    for name in MAIN[family]:
        diagnostics[name]['random_control'] = s.random_controls(name)
    # Basic failures stop family. If a survivor exists the root task must finish advanced validation.
    pending = [name for name in MAIN[family] if diagnostics[name]['heavy_validation'] == 'REQUIRED']
    qf, qe, qr, _ = s.cache[family + '_quality', 0, False]
    bf, be, br, _ = s.cache['base', 0, False]
    result = {'family': family, 'rows': rows, 'diagnostics': diagnostics, 'data_audit': s.audit,
              'checks': checks, 'hashes': hashes,
              'quality_control': {'net_delta': qr['full']['net_pnl'] - br['full']['net_pnl'], 'pairs': cost.paired(qf, bf)[0]},
              'scenario_count': len(rows), 'random_scenarios': 300, 'advanced_pending': pending,
              'implementation_sha256': cost.sha(Path(__file__)),
              'implementation_notes': 'Bootstrap numpy view uses copy=True; event count serialized as native int. Feature rules and preregistration unchanged.',
              'status': 'PENDING_ADVANCED' if pending else 'NO_PROMOTION',
              'scope': 'Historical archive replay with assumed availability lag; not true unseen or receipt-time-verified data.'}
    assert hashes == {p: cost.sha(ROOT / p) for p in hashes}
    dump(OUT / f'{family}_results.json', result)
    pd.DataFrame([{'name': r['name'], 'slip': r['slip'], 'historical': r['historical'], **r['full']} for r in rows]).to_csv(OUT / f'{family}_summary.csv', index=False)
    print(json.dumps({'family_complete': family, 'pending': pending,
                      'results': {n: {k: v for k, v in q.items() if k in ['status', 'delta', 'gain_pct', 'wr_delta_pp', 'sample']} for n, q in diagnostics.items()}}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('family', choices=['oi', 'premium'])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    DELIVERY.mkdir(parents=True, exist_ok=True)
    if args.family == 'premium':
        prior = json.loads((OUT / 'oi_results.json').read_text())
        assert not prior['advanced_pending'], 'Finish OI before premium'
    run_family(args.family)


if __name__ == '__main__':
    main()
