"""Round 3: bounded DVOL feasibility/diagnostics. No production writes, no retries."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import subprocess
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'doc/research_results/20260911_strategy_round3'
OUT = ROOT / 'data/strategy_round3_20260911'
PREV = ROOT / 'doc/research_results/20260911_strategy_round2'
PLAN = ROOT / 'doc/strategy_research_plan_20260911_round3.md'
API = 'https://www.deribit.com/api/v2/public/get_volatility_index_data'
MAX_BYTES = 50_000_000
MAX_REQUESTS = 40


def now(): return datetime.now(timezone.utc).isoformat()
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def rel(p): return p.relative_to(ROOT).as_posix()
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def dump(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding='utf-8')


def verify_map(mapping):
    bad = [p for p, h in mapping.items() if not (ROOT/p).is_file() or sha(ROOT/p) != h]
    if bad: raise AssertionError({'hash_mismatches': bad})
    return len(mapping)


def initial_audit():
    original = read(PREV/'registration.json')
    revised = read(PREV/'implementation_revision.json')
    diffs = [p for p,h in original['sha256'].items() if sha(ROOT/p) != h]
    assert diffs == ['backtest/research/strategy_round2_20260911.py']
    counts = {'prior_protected':verify_map(original['protected_sha256']),
              'effective_registration':verify_map(revised['sha256'])}
    manifest = read(PREV/'artifact_manifest.json')['files']
    counts['prior_artifacts'] = verify_map({r['path']:r['sha256'] for r in manifest})
    return {'checked_utc':now(), 'checks':counts, 'explained_original_differences':diffs,
            'reuse':'Prior no-op, raw reconstruction, prefix and independent ledger audits reused by hash; no old replay.',
            'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'git_status':subprocess.check_output(['git','status','--porcelain=v1'],cwd=ROOT,text=True),
            'prior_evidence_sha256':{rel(p):sha(p) for p in [PREV/'registration.json',PREV/'implementation_revision.json',
                PREV/'artifact_manifest.json',PREV/'results.json',PREV/'independent_output_audit.json']}}


def protected():
    paths = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    old = read(PREV/'registration.json')['protected_sha256']
    paths += list(old)
    paths += [x['path'] for x in read(PREV/'artifact_manifest.json')['files']]
    paths += [rel(p) for p in (ROOT/'data_live').rglob('*') if p.is_file()]
    excluded = ['doc/research_results/20260911_strategy_round3/','data/strategy_round3_20260911/',
                'doc/strategy_research_plan_20260911_round3.md','doc/strategy_research_results_20260911_round3.md',
                'backtest/research/strategy_round3_20260911.py','tests/test_strategy_round3_20260911.py']
    return {p:sha(ROOT/p) for p in sorted(set(paths)) if (ROOT/p).is_file() and not any(p.startswith(e) for e in excluded)}


def register():
    DOC.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    frozen = {rel(p):sha(p) for p in [PLAN,Path(__file__),ROOT/'tests/test_strategy_round3_20260911.py',
        ROOT/'doc/research_results/20260910_new_information/history_index.json']}
    if (DOC/'registration.json').exists():
        old = read(DOC/'registration.json'); assert frozen == old['sha256']; verify_map(old['protected_sha256']); return
    audit = initial_audit(); dump(DOC/'initial_hash_audit.json',audit)
    dump(DOC/'registration.json',{'registered_utc':now(),'sha256':frozen,'protected_sha256':protected(),
        'main':'D_dvol_shock','main_threshold':0.10,'neighborhoods':[0.05,0.15],
        'candidate_pnl_trials_before_registration':0,'historical_unseen':False,
        'max_download_bytes':MAX_BYTES,'prior_AB_pnl_trials':0,'prior_20260910_main_trials':6,'prior_C_main_trials':1})


def normalize_rows(rows):
    if not rows: raise ValueError('empty_data')
    if any(not isinstance(r,list) or len(r)!=5 for r in rows): raise ValueError('invalid_OHLC_schema')
    f = pd.DataFrame(rows,columns=['timestamp','open','high','low','close']).apply(pd.to_numeric,errors='raise')
    if not np.isfinite(f.to_numpy()).all(): raise ValueError('nonfinite_data')
    if f.timestamp.duplicated().any(): raise ValueError('duplicate_timestamp')
    if not f.timestamp.mod(3_600_000).eq(0).all(): raise ValueError('off_hour_timestamp')
    if not f[['open','high','low','close']].gt(0).all().all(): raise ValueError('nonpositive_index')
    if not ((f.high>=f[['open','close','low']].max(axis=1)) & (f.low<=f[['open','close','high']].min(axis=1))).all():
        raise ValueError('inconsistent_OHLC')
    f['event_ts'] = pd.to_datetime(f.timestamp,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    return f.sort_values('timestamp').reset_index(drop=True)


def features(d, raw, lag=2):
    decision = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    q = decision - pd.Timedelta(hours=lag)
    s = raw.set_index('event_ts').close
    assert s.index.is_unique
    a = np.column_stack([s.reindex(q-pd.Timedelta(hours=h)).to_numpy(float) for h in range(25)])
    valid = (np.isfinite(a)&(a>0)).all(axis=1)
    shock = np.divide(a[:,0],a[:,-1],out=np.full(len(a),np.nan),where=valid)-1
    f = pd.DataFrame({'decision_ts':decision,'source_ts':q,'source_close_bound':q+pd.Timedelta(hours=1),
        'assumed_available':q+pd.Timedelta(hours=1,minutes=5),'valid':valid,'iv_now':a[:,0],
        'iv_previous_day':a[:,-1],'shock':shock})
    assert (f.assumed_available < f.decision_ts).all()
    return f


def allowed(f, threshold=.10):
    # Tiny tolerance gives the intended boundary for exact decimal source quotes.
    return f.valid.to_numpy() & (f.shock.to_numpy() <= threshold+1e-12)


def counts(times):
    times = times.sort_values().reset_index(drop=True)
    if times.empty: return {'events':0,'clusters24h':0,'late_clusters24h':0}
    group = times.diff().gt(pd.Timedelta(hours=24)).cumsum()
    heads = times.groupby(group).min()
    return {'events':len(times),'clusters24h':len(heads),'late_clusters24h':int((heads>=pd.Timestamp('2026-01-01')).sum())}


def quality(f, entries):
    invalid = {}
    for name,a,b in [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None)]:
        p = f.iloc[310:]
        if a: p=p[p.decision_ts>=a]
        if b: p=p[p.decision_ts<b]
        invalid[name] = float((~p.valid).mean()) if len(p) else 1.
    missing_entries = int((~f.valid.iloc[entries.entry_bar.astype(int)]).sum())
    return {'invalid_fraction':invalid,'invalid_baseline_entries':missing_entries,
            'passed':max(invalid.values())<=.01 and missing_entries==0}


def request_once(start, end, label, resolution='3600'):
    """New requests only, ordinary environment proxy, streamed hard limit, no fallback route."""
    if (DOC/'source_failure.json').exists(): raise RuntimeError('recorded_source_failure_do_not_retry')
    log = read(DOC/'source_requests.json') if (DOC/'source_requests.json').exists() else []
    path = OUT/'raw'/f'{label}.json'
    prior = next((r for r in log if r['label']==label),None)
    if prior:
        assert prior.get('sha256') and sha(path)==prior['sha256']; return read(path)
    assert len(log)<MAX_REQUESTS
    used = sum(r.get('bytes',0) for r in log)
    rec = {'label':label,'url':API,'params':{'currency':'ETH','resolution':resolution,
        'start_timestamp':int(start),'end_timestamp':int(end)},'requested_utc':now(),'bytes':0}
    payload = bytearray()
    try:
        with requests.Session() as session:
            assert session.trust_env  # Do not disable the configured proxy or other network controls.
            session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
            with session.get(API,params=rec['params'],timeout=(10,20),stream=True,allow_redirects=False) as response:
                rec['http_status']=response.status_code
                response.raise_for_status()
                if response.status_code!=200: raise ValueError('unexpected_http_status')
                for part in response.iter_content(8192):
                    payload.extend(part); rec['bytes']=len(payload)
                    if len(payload)>1_000_000 or used+len(payload)>MAX_BYTES: raise ValueError('download_budget_exceeded')
        result=json.loads(payload)
        if 'error' in result or not isinstance(result.get('result',{}).get('data'),list): raise ValueError('api_error_or_schema')
        path.parent.mkdir(exist_ok=True); path.write_bytes(payload)
        rec['sha256']=sha(path); rec['received_utc']=now(); rec['rows']=len(result['result']['data'])
        log.append(rec); dump(DOC/'source_requests.json',log)
        return result
    except (requests.RequestException,ValueError) as error:
        rec.update(error_type=type(error).__name__, received_utc=now(), stopped=True,
                   winerror10061='10061' in str(error), retry_count=0)
        # Keep partial evidence without logging potentially credential-bearing proxy URLs.
        if payload:
            path.parent.mkdir(exist_ok=True); path.with_suffix('.partial').write_bytes(payload)
            rec['partial_sha256']=sha(path.with_suffix('.partial'))
        log.append(rec); dump(DOC/'source_requests.json',log); dump(DOC/'source_failure.json',rec)
        raise RuntimeError('source_check_failed_no_retry') from None


def probe():
    register()
    if (DOC/'source_failure.json').exists(): return {'status':'DATA_CONSTRAINED','reason':'recorded_source_failure_do_not_retry'}
    d=pd.read_csv(ROOT/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
    # First q-24h = first candle open -25h; add one hour conservative margin.
    start=d.datetime.iloc[0]-pd.Timedelta(hours=26)
    last=d.datetime.iloc[-1]-pd.Timedelta(hours=1)
    def ms(t): return int(t.tz_localize('Asia/Taipei').timestamp()*1000)
    samples=[]
    try:
        for label,t in [('oldest',start),('middle',pd.Timestamp('2025-09-08')),('latest',last-pd.Timedelta(hours=23))]:
            a,b=ms(t),ms(t+pd.Timedelta(hours=24))-1
            response=request_once(a,b,'probe_'+label)
            f=normalize_rows(response['result']['data'])
            expected=set(range(a,b+1,3_600_000))
            if set(f.timestamp.astype(int))!=expected: raise ValueError('probe_missing_or_outside_requested_hours')
            samples.append({'label':label,'start':str(f.event_ts.min()),'end':str(f.event_ts.max()),'rows':len(f)})
    except (RuntimeError,ValueError) as e:
        result={'status':'DATA_CONSTRAINED','reason':str(e),'samples':samples,'candidate_pnl_trials':0,
                'common_coverage_verified':False,'event_counts':None}
        dump(DOC/'source_status.json',result); return result
    # A successful small probe is not yet authorization to score incomplete history.
    result={'status':'PROBES_PASS_REQUIRES_FULL_DATA_AUDIT','samples':samples,'candidate_pnl_trials':0,
            'common_coverage_verified':False,'estimated_full_rows':int((last-start)/pd.Timedelta(hours=1))+1,
            'next_required':'Verify 60s/3600s timestamp convention, then bounded common-coverage retrieval and diagnostics.'}
    dump(DOC/'source_status.json',result); return result


def diagnose():
    register()
    if not (OUT/'dvol_1h.csv').exists():
        reason=read(DOC/'source_status.json') if (DOC/'source_status.json').exists() else {'reason':'no_local_DVOL_data'}
        result={'status':'DATA_CONSTRAINED','source_status':reason,'candidate_pnl_trials':0,'event_counts':None}
    else:
        # Reconstructed source must pass audit before externally supplied data can enter diagnostics.
        source=read(DOC/'full_source_audit.json'); assert source['passed'] and source['csv_sha256']==sha(OUT/'dvol_1h.csv')
        d=pd.read_csv(ROOT/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
        raw=pd.read_csv(OUT/'dvol_1h.csv',parse_dates=['event_ts']); f=features(d,raw)
        entries=pd.read_csv(ROOT/'data/strategy_round2_20260911/base_0bp_trades.csv',
            usecols=['side','entry_bar','entry_dt'],parse_dates=['entry_dt'])
        q=quality(f,entries); f.to_csv(OUT/'features.csv',index=False)
        result={'status':'DATA_CONSTRAINED','quality':q,'candidate_pnl_trials':0,'event_counts':None}
        if q['passed']:
            ev=entries.loc[~allowed(f)[entries.entry_bar.astype(int)]].copy()
            ev.to_csv(OUT/'direct_events_pre_pnl.csv',index=False); n=counts(ev.entry_dt)
            result.update(event_counts=n,status='READY_FOR_STATEFUL_VALIDATION' if n['clusters24h']>=30 and n['late_clusters24h']>=15 else 'INSUFFICIENT_SAMPLE')
    result['recorded_utc']=now(); dump(DOC/'diagnostic_status.json',result); return result


def audit_saved():
    reg=read(DOC/'registration.json')
    out={'registered_files':verify_map(reg['sha256']),'preserved_files':verify_map(reg['protected_sha256'])}
    if (DOC/'artifact_manifest.json').exists():
        out['artifacts']=verify_map({r['path']:r['sha256'] for r in read(DOC/'artifact_manifest.json')['files']})
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--phase',choices=['register','probe','diagnose','audit'],required=True)
    args=parser.parse_args()
    if args.phase=='register': register(); print('REGISTERED')
    elif args.phase=='probe': print(json.dumps(probe(),ensure_ascii=False))
    elif args.phase=='diagnose': print(json.dumps(diagnose(),ensure_ascii=False))
    else: print(json.dumps(audit_saved()))
