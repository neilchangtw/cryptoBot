"""Bounded preregistered single-agent research. No production writes or orders."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import inspect
import json
import subprocess
import sys
import numpy as np
import pandas as pd
import requests
import execute_optimization_plan_20260908 as cost
import new_information_20260910 as prior
import maxhold_review_20260908 as review

ROOT = cost.ROOT
OUT = ROOT / 'data/strategy_study_20260911'
DOC = ROOT / 'doc/research_results/20260911_strategy_study'
PLAN = ROOT / 'doc/strategy_research_plan_20260911.md'
SOURCE_URL = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
dump = prior.dump


def features(d, raw, lag=15):
    decision = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    query = decision - pd.Timedelta(minutes=lag)
    source = raw.set_index('event_ts')
    assert source.index.is_unique
    f = pd.DataFrame({'decision_ts': decision, 'event_ts': query,
                      'close_bound': query + pd.Timedelta(minutes=5),
                      'assumed_available': query + pd.Timedelta(minutes=10)})
    assert (f.assumed_available <= f.decision_ts).all()
    for label, column, minutes in [('oi', 'sum_open_interest', 120),
                                   ('top', 'sum_toptrader_long_short_ratio', 60),
                                   ('global', 'count_long_short_ratio', 60)]:
        a = np.column_stack([source[column].reindex(query - pd.Timedelta(minutes=k)).to_numpy(float)
                             for k in range(0, minutes + 1, 5)])
        valid = (np.isfinite(a) & (a > 0)).all(axis=1)
        f[label + '_valid'] = valid
        f[label + '_now'] = a[:, 0]
        f[label + '_before'] = a[:, -1]
        f[label + '_delta'] = np.where(valid, a[:, 0] / np.where(a[:, -1] > 0, a[:, -1], np.nan) - 1, np.nan)
    return f


def exit_condition(rule, bh, cpnl, valid, delta):
    if bh != 2 or not valid:
        return False
    if rule == 'oi_exit':
        return cpnl < 0 and delta < 0
    if rule == 'price_control':
        return cpnl < 0
    if rule == 'oi_control':
        return delta < 0
    return False


def simulator(engine):
    src = inspect.getsource(engine.simulate_v14_detailed)
    def patch(old, new):
        nonlocal src
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None):',
          'realistic=False, slip_bps=0.0, margin_schedule=None, gate=None, hook=None, sn_extra=False):')
    patch('and brk_up[i]):', "and brk_up[i] and (gate is None or gate(i,'L'))):")
    patch('and brk_dn[i]):', "and brk_dn[i] and (gate is None or gate(i,'S'))):")
    for pos, side, sign in [('lp', 'L', 1), ('sp', 'S', -1)]:
        anchor = f"            if ex_price > 0:\n                pnl_pct = {'(ex_price - ep)' if side == 'L' else '(ep - ex_price)'} / ep"
        inserted = (f"            if ex_price == 0 and hook is not None and hook(i, '{side}', {pos}_bar, bh, ep, ci):\n"
                    f"                ex_price = {'l_mkt' if side == 'L' else 's_mkt'}\n"
                    "                ex_reason = 'OIX'\n"
                    "            if ex_reason == 'SN' and sn_extra:\n"
                    f"                ex_price *= (1 - ({sign}) * slip)\n")
        patch(anchor, inserted + anchor)
        anchor = f"                    'margin': round({pos}_ntl / 20.0, 2),"
        patch(anchor, anchor + f"\n                    'qty_exact':{pos}_ntl/ep,'fee_exact':{pos}_fee,'entry_exact':ep,")
    patch('    return trades', "    return trades, {'L':lp_active,'S':sp_active}")
    ns = dict(engine.__dict__)
    exec(compile(src, '<research-only-20260911>', 'exec'), ns)
    return ns['simulate_v14_detailed']


def event_counts(frame, time_col):
    times = frame[time_col].sort_values().reset_index(drop=True)
    cluster = times.diff().gt(pd.Timedelta(hours=24)).cumsum()
    heads = times.groupby(cluster).min()
    return {'events': len(times), 'clusters24h': len(heads),
            'late_clusters24h': int((heads >= pd.Timestamp('2026-01-01')).sum())}


def inspect_mapping(raw):
    """Tiny anonymous public-source checks, no credentials, no retries."""
    records = []
    start = int(pd.Timestamp('2026-09-08', tz='UTC').timestamp() * 1000)
    for endpoint, column in [('topLongShortPositionRatio', 'sum_toptrader_long_short_ratio'),
                             ('globalLongShortAccountRatio', 'count_long_short_ratio')]:
        url = 'https://fapi.binance.com/futures/data/' + endpoint
        rec = {'url': url, 'params': {'symbol':'ETHUSDT', 'period':'5m', 'startTime':start, 'limit':3},
               'requested_utc': datetime.now(timezone.utc).isoformat()}
        try:
            response = requests.get(url, params=rec['params'], timeout=15)
            rec.update(status=response.status_code, bytes=len(response.content))
            assert len(response.content) < 20000
            (OUT / (endpoint + '_sample.json')).write_bytes(response.content)
            if response.status_code != 200:
                records.append(rec)
                break
            rows = response.json()
            assert isinstance(rows, list) and len(rows) == 3
            ts = pd.to_datetime([r['timestamp'] for r in rows], unit='ms', utc=True).tz_convert('Asia/Taipei').tz_localize(None)
            archive = raw.set_index('event_ts')[column].reindex(ts).to_numpy(float)
            errors = np.abs(archive - np.array([float(r['longShortRatio']) for r in rows]))
            rec.update(matched_times=int(np.isfinite(archive).sum()), max_abs_difference=float(errors.max()),
                       mapping_pass=bool(np.isfinite(errors).all() and (errors <= .0002).all()))
            records.append(rec)
            if not rec['mapping_pass']:
                break
        except (requests.RequestException, ValueError, AssertionError, KeyError) as e:
            rec.update(error_type=type(e).__name__)
            records.append(rec)
            break
    return {'checks': records, 'mapped': len(records) == 2 and all(r.get('mapping_pass', False) for r in records),
            'download_bytes': sum(r.get('bytes', 0) for r in records),
            'historical_received_ts': 'unavailable', 'archive_revision_risk': True,
            'official_definition': SOURCE_URL,
            'official_top_endpoint_key_required': True}


def registration():
    tracked = [PLAN, Path(__file__), ROOT/'strategy.py', ROOT/'executor.py', cost.base.ENGINE_PATH,
               ROOT/'doc/live_data_spec.md', ROOT/'AGENTS.md',
               ROOT/'data/maxhold_review_20260908/candles.csv',
               cost.HISTORY/'funding_full.csv', cost.HISTORY/'mark_1h_full.csv',
               prior.OUT/'download_manifest.json',
               ROOT/'backtest/research/execute_optimization_plan_20260908.py',
               ROOT/'backtest/research/new_information_20260910.py']
    hashes = {str(p.relative_to(ROOT)).replace('\\','/'):cost.sha(p) for p in tracked}
    index = json.loads((prior.DELIVERY/'history_index.json').read_text(encoding='utf-8'))
    records = index['records']
    old_paths = {r['path'] for r in records}
    additions = [str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'doc').glob('*.md')
                 if str(p.relative_to(ROOT)).replace('\\','/') not in old_paths]
    changed = [r['path'] for r in records if (ROOT/r['path']).exists() and cost.sha(ROOT/r['path']) != r['sha256']]
    result = {'registered_utc': datetime.now(timezone.utc).isoformat(), 'sha256':hashes,
              'git_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT,text=True).strip(),
              'git_status': subprocess.check_output(['git','status','--short'], cwd=ROOT,text=True),
              'history_index_records':len(records), 'changed_indexed_files':changed,
              'additional_current_docs':additions,
              'prior_main_trials':6, 'this_round_max_main_trials':2, 'historical_unseen_data':False}
    target = DOC/'registration.json'
    if target.exists():
        old = json.loads(target.read_text(encoding='utf-8'))
        # Implementation fixes may be recorded separately; locked plan and inputs never change.
        for p,h in old['sha256'].items():
            if p != str(Path(__file__).relative_to(ROOT)).replace('\\','/'):
                assert cost.sha(ROOT/p) == h, p
        dump(DOC/'implementation_run.json', result)
    else:
        dump(target, result)
    return hashes


class Study:
    def __init__(self):
        self.source = cost.Study()
        self.d, self.engine = self.source.d, self.source.engine
        self.ind = self.engine.compute_indicators(self.d)
        self.raw, self.raw_audit = prior.archive_frames(['metrics'])
        assert self.raw.symbol.eq('ETHUSDT').all()
        self.feat = features(self.d, self.raw)
        self.fn = simulator(self.engine)
        self.cache = {}

    def run(self, rule='base', slip=0, cut=None, sn_extra=True, save=True):
        d = self.d if cut is None else self.d.iloc[:cut]
        ind = self.ind if cut is None else self.engine.compute_indicators(d)
        feat = self.feat if cut is None else features(d, self.raw[self.raw.event_ts < d.datetime.iloc[-1]+pd.Timedelta(hours=1)])
        events = []
        gates = []
        valid, change = feat.oi_valid.to_numpy(), feat.oi_delta.to_numpy()
        def hook(i, side, entry, bh, ep, ci):
            if bh != 2:
                return False
            cpnl = (1 if side == 'L' else -1)*(ci/ep-1)
            allowed = exit_condition(rule, bh, cpnl, valid[i], change[i])
            events.append({'bar':i,'side':side,'entry_bar':entry,'decision_ts':feat.decision_ts.iloc[i],
                           'cpnl':cpnl,'valid':bool(valid[i]),'oi_delta':change[i],
                           'X':cpnl<0,'Y':bool(change[i]<0),'trigger':allowed})
            return allowed
        def gate(i, side):
            allowed = True
            if rule.startswith('distribution'):
                sign = 1 if side == 'L' else -1
                row = feat.iloc[i]
                allowed = bool(row.top_valid and row.global_valid)
                if rule == 'distribution':
                    allowed &= not (sign*row.top_delta < 0 and sign*row.global_delta > 0)
            gates.append({'bar':i,'side':side,'allowed':allowed})
            return allowed
        raw, active = self.fn(ind, d.datetime.to_numpy(), realistic=True, slip_bps=slip,
                              gate=gate, hook=hook, sn_extra=sn_extra)
        f = review.normalize(pd.DataFrame(raw))
        ev = pd.DataFrame(events)
        ge = pd.DataFrame(gates)
        if cut is not None:
            return f, ev, ge, active
        if any(active.values()):
            raise RuntimeError('Terminal position requires ledger extension before scoring: '+str(active))
        f, eq, ledger = cost.account(f, d, self.source.mark, self.source.fund)
        row = {'name':rule,'slip':slip,'terminal':active}
        for label, start, end in [('full',None,None),('early',None,'2026-01-01'),
                                  ('late','2026-01-01',None),('recent','2026-06-01',None)]:
            m = prior.extra_metrics(f,eq,d,start,end)
            part = f
            if start: part=part[part.exit_dt>=start]
            if end: part=part[part.exit_dt<end]
            m['loss_total'] = float(-part.loc[part.net<0,'net'].sum())
            row[label] = m
        row['fee_total'] = float(f.fee_exact.sum())
        row['funding_bounds'] = [float(f.funding_low.sum()),float(f.funding_high.sum())]
        if save:
            stem = f'{rule}_{slip}bp'
            for suffix, frame in [('trades',f),('equity',eq),('funding',ledger),('events',ev),('gates',ge)]:
                frame.to_csv(OUT/f'{stem}_{suffix}.csv',index=False)
            self.cache[rule,slip] = f,eq,row,ev,ge
        return f,eq,row,ev,ge

    def parity(self):
        checks = []
        for slip in [0,2,5]:
            original = pd.DataFrame(self.engine.simulate_v14_detailed(self.ind,self.d.datetime.to_numpy(), realistic=True,slip_bps=slip))
            raw, _ = self.fn(self.ind,self.d.datetime.to_numpy(),realistic=True,slip_bps=slip)
            pd.testing.assert_frame_equal(pd.DataFrame(raw)[original.columns],original)
            f,_,r,_,_ = self.run(slip=slip,sn_extra=False,save=False)
            old = pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_flat_{slip}_trades.csv')
            for col in ['side','entry_bar','exit_bar','reason_code']:
                assert f[col].equals(old[col]), col
            for col in ['pnl','funding','net']:
                np.testing.assert_allclose(f[col],old[col],atol=1e-8,rtol=0)
            self.run(slip=slip)
            checks.append({'slip':slip,'legacy_noop_all_trade_fields':'PASS','legacy_net':r['full']['net_pnl'],
                           'all_fills_stress_net':self.cache['base',slip][2]['full']['net_pnl']})
        return checks


def economic_verdict(study, rule):
    fails = []
    for slip in [0,2,5]:
        c = study.cache[rule,slip][2]
        b = study.cache['base',slip][2]
        for period in ['early','late']:
            if c[period]['net_pnl'] < b[period]['net_pnl']-1e-7:
                fails.append(f'{slip}bp:{period}:net_decreases')
        for key, ok in [('net_gain_5pct',c['full']['net_pnl']>=b['full']['net_pnl']*1.05),
                        ('loss_total',c['full']['loss_total']<b['full']['loss_total']),
                        ('mark_mdd',c['full']['mdd']<=b['full']['mdd']+1e-7),
                        ('worst30',c['full']['worst30']>=b['full']['worst30']-1e-7)]:
            if not ok: fails.append(f'{slip}bp:{key}')
    return fails


def decomposition(study, rule):
    f,eq,_,_,_ = study.cache[rule,0]
    bf,be,_,_,_ = study.cache['base',0]
    counts,p = cost.paired(f,bf)
    p.to_csv(OUT/f'{rule}_paired.csv',index=False)
    removed,added,common = [p[p._merge==label] for label in ['right_only','left_only','both']]
    parts = {'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),
             'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),
             'new_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum())}
    delta = float(f.net.sum()-bf.net.sum())
    assert abs(sum(parts.values())-delta)<1e-7
    return {'pair_counts':counts,'parts':parts,'total_delta':delta,
            'trading_delta':float(f.pnl.sum()-bf.pnl.sum()),
            'funding_delta':float(f.funding.sum()-bf.funding.sum()),'terminal_delta':0}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    DOC.mkdir(parents=True,exist_ok=True)
    hashes = registration()
    s = Study()
    parity = s.parity()
    s.feat.to_csv(OUT/'features.csv',index=False)
    mapping = inspect_mapping(s.raw)
    dump(DOC/'sources.json',mapping)
    bf,_,_,ev,_ = s.cache['base',0]
    valid_a = s.feat.top_valid & s.feat.global_valid
    affected_a = []
    for t in bf.itertuples():
        r=s.feat.iloc[t.entry_bar];sign=1 if t.side=='L' else -1
        affected_a.append(bool(valid_a.iloc[t.entry_bar] and sign*r.top_delta<0 and sign*r.global_delta>0))
    a_counts=event_counts(bf[affected_a],'entry_dt')
    b_events=ev[ev.valid & ev.X & ev.Y]
    b_events.to_csv(OUT/'oi_exit_original_events.csv',index=False)
    b_counts=event_counts(b_events,'decision_ts')
    quality = {'a_invalid_fraction':float((~valid_a.iloc[s.engine.WARMUP:]).mean()),
               'b_invalid_eligible_fraction':float((~ev.valid).mean()),
               'source_rows':len(s.raw),'original_eligible_bar2':len(ev)}
    pre = {'A':a_counts,'B':b_counts,'quality':quality}
    dump(DOC/'pre_pnl_counts.json',pre)
    print(json.dumps({'phase':'baseline_and_pre_pnl','base':s.cache['base',0][2]['full'],
                      'counts':pre,'A_mapped':mapping['mapped']},ensure_ascii=False),flush=True)
    statuses = {}
    for rule, counts, data_ok in [('distribution',a_counts,mapping['mapped'] and quality['a_invalid_fraction']<=.01),
                                  ('oi_exit',b_counts,quality['b_invalid_eligible_fraction']<=.01)]:
        if not data_ok:
            statuses[rule]={'status':'DATA_LIMITED','reason':'mapping_or_quality_failed'}
            continue
        if counts['clusters24h']<30 or counts['late_clusters24h']<15:
            statuses[rule]={'status':'INSUFFICIENT_SAMPLE','reason':'pre_pnl_event_threshold_failed'}
            continue
        controls = ['distribution_quality'] if rule=='distribution' else ['price_control','oi_control']
        for name in [rule]+controls:
            for slip in [0,2,5]:
                s.run(name,slip)
        failed = economic_verdict(s,rule)
        statuses[rule]={'status':'REJECTED' if failed else 'HISTORICAL_CANDIDATE_PENDING',
                        'failures':failed,'decomposition':decomposition(s,rule),
                        'additional_validation':'NOT_RUN_BASIC_GATES_FAILED' if failed else 'REQUIRED'}
        print(json.dumps({'phase':'candidate','name':rule,'net':s.cache[rule,0][2]['full']['net_pnl'],
                          'status':statuses[rule]['status']},ensure_ascii=False),flush=True)
    prefix=[]
    for cut in [10000,16000]:
        shortened=features(s.d.iloc[:cut],s.raw[s.raw.event_ts<s.d.datetime.iloc[cut-1]+pd.Timedelta(hours=1)])
        pd.testing.assert_frame_equal(shortened,s.feat.iloc[:cut])
        for rule,slip in list(s.cache):
            if slip != 0:continue
            f,events,gates,terminal=s.run(rule,cut=cut,save=False)
            full,_,_,full_events,full_gates=s.cache[rule,0]
            pd.testing.assert_frame_equal(f,full.loc[full.exit_bar<cut,f.columns].reset_index(drop=True))
            pd.testing.assert_frame_equal(events,full_events[full_events.bar<cut].reset_index(drop=True))
            pd.testing.assert_frame_equal(gates,full_gates[full_gates.bar<cut].reset_index(drop=True))
            prefix.append({'cut':cut,'rule':rule,'status':'PASS','unclosed_at_cut':terminal})
    result={'data':{'start_open_taipei':str(s.d.datetime.iloc[0]),'end_open_taipei':str(s.d.datetime.iloc[-1]),
                    'last_close_taipei':str(s.feat.decision_ts.iloc[-1]),'bars':len(s.d),'warmup':s.engine.WARMUP},
            'parity':parity,'counts':pre,'statuses':statuses,'prefix':prefix,
            'runs':[value[2] for value in s.cache.values()],
            'changed_protected_files':[p for p,h in hashes.items() if cost.sha(ROOT/p)!=h]}
    assert not result['changed_protected_files']
    dump(DOC/'results.json',result)
    pd.DataFrame([{'rule':r['name'],'slip_bp':r['slip'],**r['full']} for r in result['runs']]).to_csv(DOC/'summary.csv',index=False)
    print(json.dumps({'done':str(DOC),'statuses':statuses},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
