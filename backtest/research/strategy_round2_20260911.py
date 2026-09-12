"""Offline, preregistered spot-flow study; all writes confined to new round outputs."""
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import argparse
import inspect
import json
import subprocess
import numpy as np
import pandas as pd
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review
import new_information_20260910 as prior
import strategy_study_20260911 as previous
from fetch_spot_sync_20260909 import validate

ROOT = cost.ROOT
DOC = ROOT/'doc/research_results/20260911_strategy_round2'
OUT = ROOT/'data/strategy_round2_20260911'
PLAN = ROOT/'doc/strategy_research_plan_20260911_round2.md'
OLD = ROOT/'data/strategy_study_20260911'
SPOT = ROOT/'data/spot_futures_sync_20260909'
SOURCES = [
    'https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market',
    'https://github.com/binance/binance-public-data#updates',
]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=serial), encoding='utf-8')


def serial(x):
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, (pd.Timestamp, datetime, Path)): return str(x)
    raise TypeError(type(x).__name__)


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def rel(path): return path.relative_to(ROOT).as_posix()


def protected_hashes():
    paths = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'], cwd=ROOT, text=True).splitlines()
    paths += [rel(p) for folder in [OLD, SPOT, ROOT/'data/public_cost_history_20260908'] for p in folder.rglob('*') if p.is_file()]
    paths += ['data/maxhold_review_20260908/candles.csv', '.env', 'eth_state.json', 'eth_state_live.json']
    excluded = [rel(DOC)+'/', rel(OUT)+'/', rel(PLAN), rel(Path(__file__)), 'tests/test_strategy_round2_20260911.py',
                'doc/strategy_research_results_20260911_round2.md']
    return {p:cost.sha(ROOT/p) for p in sorted(set(paths)) if (ROOT/p).is_file() and not any(p.startswith(x) for x in excluded)}


def features(d, spot, lag=1):
    assert spot.datetime.is_unique
    decision = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    source_open = pd.DatetimeIndex(d.datetime - pd.Timedelta(hours=lag))
    s = spot.set_index('datetime').reindex(source_open)
    v, b = s.volume.to_numpy(float), s.taker_buy_volume.to_numpy(float)
    valid = np.isfinite(v) & np.isfinite(b) & (v > 0) & (b >= 0) & (b <= v)
    imbalance = np.divide(2*b-v, v, out=np.full(len(v), np.nan), where=valid)
    f = pd.DataFrame({'decision_ts':decision, 'source_open':source_open,
        'source_close':source_open+pd.Timedelta(hours=1),
        'assumed_available':source_open+pd.Timedelta(hours=1, minutes=5),
        'valid':valid, 'imbalance':imbalance})
    assert (f.assumed_available < f.decision_ts).all()
    return f


def allowed(feat, side, threshold=0., quality=False):
    sign = 1 if side == 'L' else -1
    return feat.valid.to_numpy() & (np.ones(len(feat),bool) if quality else sign*feat.imbalance.to_numpy() >= threshold)


STATE_KEYS = ('lp_active lp_entry lp_ntl lp_fee lp_bar lp_held lp_mfe lp_mae lp_reduced lp_ext lp_ext_bars lp_gk_pctile lp_regime '
              'sp_active sp_entry sp_ntl sp_fee sp_bar sp_held sp_mfe sp_mae sp_ext sp_ext_bars sp_gk_pctile sp_regime '
              'l_last_exit s_last_exit cur_month l_m_entries s_m_entries l_m_pnl s_m_pnl cur_day d_pnl consec consec_end').split()


def snapshot(scope): return {k:scope[k] for k in STATE_KEYS}


def simulator(engine):
    """Same gate/SafeNet adaptation as verified prior study, plus observable terminal state."""
    src = inspect.getsource(engine.simulate_v14_detailed)
    def patch(a,b):
        nonlocal src
        assert src.count(a)==1, a
        src = src.replace(a,b)
    patch('realistic=False, slip_bps=0.0, margin_schedule=None):',
          'realistic=False, slip_bps=0.0, margin_schedule=None, gate=None, observe=None):')
    patch('and brk_up[i]):', "and brk_up[i] and (gate is None or gate(i,'L'))):")
    patch('and brk_dn[i]):', "and brk_dn[i] and (gate is None or gate(i,'S'))):")
    for p,side,sign in [('lp','L',1),('sp','S',-1)]:
        expr = '(ex_price - ep)' if side=='L' else '(ep - ex_price)'
        a = f'            if ex_price > 0:\n                pnl_pct = {expr} / ep'
        patch(a, f"            if ex_reason == 'SN':\n                ex_price *= (1 - ({sign}) * slip)\n"+a)
        a = f"                    'margin': round({p}_ntl / 20.0, 2),"
        patch(a, a+f"\n                    'qty_exact':{p}_ntl/ep,'fee_exact':{p}_fee,'entry_exact':ep,")
    patch('    return trades', '        if observe is not None:\n            observe(i, _snapshot(locals()))\n\n    return trades, _snapshot(locals())')
    ns = dict(engine.__dict__, _snapshot=snapshot)
    exec(compile(src, '<research-round2>', 'exec'),ns)
    return ns['simulate_v14_detailed']


def account_terminal(f, d, mark, fund, state):
    """Use proven closed-trade ledger, then carry actual open positions without invented exits."""
    f, eq, ledger = cost.account(f,d,mark,fund)
    cash = np.zeros(len(d)); floating = np.zeros(len(d)); funding = np.zeros(len(d))
    times = pd.DatetimeIndex(eq.time); terminal = []; rows = []
    for side,p,sign in [('L','lp',1),('S','sp',-1)]:
        if not state[p+'_active']: continue
        a = int(state[p+'_bar']); ep = state[p+'_entry']; q = state[p+'_ntl']/ep
        entry = times[a]; fee = state[p+'_fee']/2
        cash[a] -= fee
        floating[a:] += sign*q*(mark.close.to_numpy()[a:]-ep)
        total = 0.
        for t in fund[(fund.nominal>entry)&(fund.nominal<=times[-1])].itertuples():
            value = -sign*q*t.markPrice*t.fundingRate
            funding[times.get_loc(t.nominal)] += value; total += value
            rows.append({'trade_id':'OPEN_'+side,'time':t.nominal,'side':side,'cashflow':value,
                         'possible_cashflow':value,'boundary':False,'rate':t.fundingRate,'markPrice':t.markPrice,'included':True})
        terminal.append({'side':side,'entry_bar':a,'entry_dt':entry,'entry_exact':ep,'qty_exact':q,
                         'entry_fee':fee,'funding':total,'unrealized_mark':float(sign*q*(mark.close.iloc[-1]-ep))})
    eq['trading_cash'] += cash.cumsum(); eq['funding_cash'] += funding.cumsum()
    eq['unrealized_mark'] += floating
    eq['cash'] = eq.trading_cash+eq.funding_cash; eq['equity'] = eq.cash+eq.unrealized_mark
    ledger = pd.concat([ledger,pd.DataFrame(rows)],ignore_index=True)
    expected = f.net.sum()+sum(t['funding']-t['entry_fee']+t['unrealized_mark'] for t in terminal)
    assert abs(expected-eq.equity.iloc[-1])<1e-7
    return f,eq,ledger,terminal


class Study:
    def __init__(self):
        self.source = cost.Study()
        self.d = self.source.d; self.engine = self.source.engine
        self.ind = self.engine.compute_indicators(self.d)
        self.spot = pd.read_csv(SPOT/'spot_1h.csv',parse_dates=['datetime'])
        self.feat = features(self.d,self.spot)
        self.future_feat = features(self.d,self.d)
        self.fn = simulator(self.engine); self.cache = {}

    def source_audit(self):
        m = read(SPOT/'manifest.json'); raw = []; hashes={}
        assert cost.sha(SPOT/'spot_1h.csv')==m['csv_sha256']
        for r in m['requests']:
            p = SPOT/r['file']; assert cost.sha(p)==r['sha256']; hashes[rel(p)]=r['sha256']; raw.extend(read(p))
        restored = validate(raw,self.d.datetime)
        pd.testing.assert_frame_equal(restored,self.spot,check_dtype=False,atol=1e-12,rtol=1e-12)
        checks=0
        for i in range(1,len(self.d)):
            v,b = Decimal(raw[i-1][5]),Decimal(raw[i-1][9]); f=self.feat.iloc[i]
            assert v>0 and 0<=b<=v
            signed = 2*b-v
            assert np.sign(f.imbalance)==int(signed>0)-int(signed<0)
            assert abs(f.imbalance-float(signed/v))<1e-12; checks+=1
        self.feat.to_csv(OUT/'features.csv',index=False)
        return {'raw_files':len(hashes),'raw_rows':len(raw),'sha256':hashes,'restored_all_columns':'PASS',
            'decimal_checks':checks,'decimal':'PASS','download_bytes':0,'network_requests':0,
            'official_sources':SOURCES,'raw_fields':{'volume':5,'taker_buy_volume':9,'open_ms':0,'close_ms':6},
            'source_close_max_taipei':str(self.spot.datetime.iloc[-1]+pd.Timedelta(hours=1)),
            'historical_received_ts':'unavailable','revision_status':'unverified; historical API snapshot',
            'proxy_retry':False,'longer_common_history_checked':False}

    def run(self,name='base',slip=0,cut=None,save=True,lag=1,threshold=0.):
        d = self.d if cut is None else self.d.iloc[:cut]
        ind = self.ind if cut is None else self.engine.compute_indicators(d)
        # Physically remove source rows at/after decision; feature uses strictly earlier hour.
        source = self.d if name=='futures_control' else self.spot
        if cut is not None: source=source[source.datetime<d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        feat = features(d,source,lag=lag)
        masks = {side:np.ones(len(d),bool) if name=='base' else allowed(feat,side,threshold,name=='quality') for side in ['L','S']}
        events=[]; states=[]
        def gate(i,side):
            yes=bool(masks[side][i]); events.append({'bar':i,'side':side,'allowed':yes,'decision_ts':feat.decision_ts.iloc[i]}); return yes
        def observe(i,state): states.append({'bar':i,**state})
        raw,state = self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,gate=gate,observe=observe)
        f = review.normalize(pd.DataFrame(raw)); ev=pd.DataFrame(events); st=pd.DataFrame(states)
        fund = self.source.fund[self.source.fund.nominal<=d.datetime.iloc[-1]+pd.Timedelta(hours=1)]
        f,eq,ledger,terminal = account_terminal(f,d,self.source.mark.iloc[:len(d)],fund,state)
        row={'name':name,'slip':slip,'terminal_positions':terminal,'end_state':state,'eligible_gates':len(ev),
             'rejected_gates':int((~ev.allowed).sum()),'fee_total_closed':float(f.fee_exact.sum())}
        periods = [('full',None,None),('early',None,'2026-01-01'),('late','2026-01-01',None),('recent','2026-06-01',None)] if cut is None else []
        for label,start,end in periods:
            m=prior.extra_metrics(f,eq,d,start,end)
            part=f
            if start: part=part[part.exit_dt>=start]
            if end: part=part[part.exit_dt<end]
            m['loss_total']=float(-part.loc[part.net<0,'net'].sum()); row[label]=m
        row['terminal_net']=float(eq.equity.iloc[-1]-f.net.sum())
        row['max_simultaneous']=int((st.lp_active.astype(int)+st.sp_active.astype(int)).max())
        assert row['max_simultaneous']<=2
        if save:
            for suffix,frame in [('trades',f),('equity',eq),('funding',ledger),('gates',ev),('states',st)]:
                frame.to_csv(OUT/f'{name}_{slip}bp_{suffix}.csv',index=False)
            self.cache[name,slip]=f,eq,row,ev,st
        return f,eq,row,ev,st

    def parity(self):
        checks=[]
        for slip in [0,2,5]:
            f,eq,row,ev,st=self.run(slip=slip)
            for suffix,current,dates in [('trades',f,['entry_dt','exit_dt']),('equity',eq,['time'])]:
                old=pd.read_csv(OLD/f'base_{slip}bp_{suffix}.csv',parse_dates=dates)
                pd.testing.assert_frame_equal(current[old.columns],old,check_dtype=False,atol=1e-8,rtol=0)
            expected=next(r for r in read(ROOT/'doc/research_results/20260911_strategy_study/results.json')['runs'] if r['name']=='base' and r['slip']==slip)
            for k in ['net_pnl','wr','pf','mdd','worst30','loss_total']:
                assert abs(row['full'][k]-expected['full'][k])<1e-8, (slip,k)
            checks.append({'slip':slip,'stored_all_trade_columns':'PASS','stored_equity_all_columns':'PASS','metrics':'PASS'})
        return checks

    def prefix(self,name):
        f,eq,row,ev,st=self.cache[name,0]
        # Include an actual open trade, not only flat checkpoints.
        trade=f.iloc[2]; cuts=sorted(set([int(trade.entry_bar)+2,10000,16000]))
        results=[]
        for cut in cuts:
            cf,ce,cr,cv,cs=self.run(name,cut=cut,save=False)
            pd.testing.assert_frame_equal(f[f.exit_bar<cut].reset_index(drop=True),cf.reset_index(drop=True),check_dtype=False,atol=1e-8,rtol=0)
            pd.testing.assert_frame_equal(ev[ev.bar<cut].reset_index(drop=True),cv.reset_index(drop=True),check_dtype=False)
            pd.testing.assert_frame_equal(st[st.bar<cut].reset_index(drop=True),cs.reset_index(drop=True),check_dtype=False)
            # Closed trades book the original round-trip fee only as paid halves. Open-position ledger must match prefix.
            pd.testing.assert_frame_equal(eq.iloc[:cut].reset_index(drop=True),ce.reset_index(drop=True),check_dtype=False,atol=.011,rtol=0)
            past=features(self.d.iloc[:cut],self.spot[self.spot.datetime<self.d.datetime.iloc[cut-1]+pd.Timedelta(hours=1)])
            pd.testing.assert_frame_equal(self.feat.iloc[:cut],past)
            results.append({'name':name,'cut':cut,'features_gates_closed_trades_all_states':'PASS','equity_prefix':'PASS',
                            'terminal_positions':cr['terminal_positions'],'equity_rounding_tolerance':.011})
        return results


def preregister():
    DOC.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
    old=read(ROOT/'doc/research_results/20260911_strategy_study/registration.json')
    assert all(cost.sha(ROOT/p)==h for p,h in old['sha256'].items())
    protected=protected_hashes()
    inputs={p:cost.sha(ROOT/p) for p in old['sha256']}
    for p in [PLAN,Path(__file__),ROOT/'tests/test_strategy_round2_20260911.py',SPOT/'manifest.json',SPOT/'spot_1h.csv']:
        inputs[rel(p)]=cost.sha(p)
    reg={'registered_utc':datetime.now(timezone.utc).isoformat(),'sha256':inputs,'protected_sha256':protected,
         'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'prior_main_pnl_trials_20260910':6,'prior_AB_pnl_trials':0,'this_round_max_main':1,
         'this_round_max_neighborhoods':2,'historical_unseen':False,
         'dedup_files':['doc/spot_futures_sync_results_20260909.md','doc/flow_price_results_20260909.md','doc/v13_research.md',
             'doc/v16_research.md','doc/new_information_results_20260910.md','doc/strategy_research_results_20260911.md']}
    path=DOC/'registration.json'
    if path.exists():
        existing=read(path)
        if existing['sha256']!=inputs:
            revision=read(DOC/'implementation_revision.json')
            assert revision['sha256']==inputs,'Implementation/input changed: record separately before continuing'
            assert all(inputs[p]==h for p,h in existing['sha256'].items() if p not in [rel(Path(__file__)), 'tests/test_strategy_round2_20260911.py'])
    else: dump(path,reg)
    return protected


def decomposition(study,name,slip):
    f,eq,row,_,_=study.cache[name,slip]; bf,be,br,_,_=study.cache['base',slip]
    counts,p=cost.paired(f,bf); p.to_csv(OUT/f'{name}_{slip}bp_paired.csv',index=False)
    removed=p[p._merge=='right_only']; added=p[p._merge=='left_only']; common=p[p._merge=='both']
    parts={'avoided_losses':float(-removed.loc[removed.net_b<0,'net_b'].sum()),
           'missed_winners':float(-removed.loc[removed.net_b>0,'net_b'].sum()),
           'added_net':float(added.net_c.sum()),'common_delta':float((common.net_c-common.net_b).sum()),
           'terminal_delta':row['terminal_net']-br['terminal_net']}
    delta=float(eq.equity.iloc[-1]-be.equity.iloc[-1]); assert abs(sum(parts.values())-delta)<1e-7
    return {'counts':counts,'parts':parts,'net_delta':delta,'trading_delta':float(f.pnl.sum()-bf.pnl.sum()),
            'funding_delta':float(f.funding.sum()-bf.funding.sum()),'closed_fee_delta':float(f.fee_exact.sum()-bf.fee_exact.sum())}


def failures(candidate,base):
    c,b=candidate['full'],base['full']; checks={
        'net_gain_5pct':c['net_pnl']>=1.05*b['net_pnl'], 'loss_total_decreases':c['loss_total']<b['loss_total'],
        'mark_mdd_not_worse':c['mdd']<=b['mdd']+1e-7,'worst30_not_worse':c['worst30']>=b['worst30']-1e-7,
        **{p+'_net_not_worse':candidate[p]['net_pnl']>=base[p]['net_pnl']-1e-7 for p in ['early','late']}}
    return [k for k,v in checks.items() if not v]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--phase',choices=['diagnose','evaluate'],default='diagnose'); args=parser.parse_args()
    protected=preregister(); study=Study(); sources=study.source_audit(); dump(DOC/'sources.json',sources)
    f=pd.read_csv(OLD/'base_0bp_trades.csv',usecols=['side','entry_bar','entry_dt'],parse_dates=['entry_dt'])
    mask={s:allowed(study.feat,s) for s in ['L','S']}
    f['valid']=[study.feat.valid.iloc[i] for i in f.entry_bar]
    f['imbalance']=[study.feat.imbalance.iloc[i] for i in f.entry_bar]
    f['blocked']=[not mask[s][i] for s,i in zip(f.side,f.entry_bar)]
    direct=f[f.blocked & f.valid].copy(); counts=previous.event_counts(direct,'entry_dt')
    counts.update(invalid_fraction=float((~study.feat.valid.iloc[310:]).mean()),invalid_original=float((~f.valid).mean()),
                  counted_before_candidate_pnl=True,recorded_utc=datetime.now(timezone.utc).isoformat())
    # Immutable diagnostic record retains initial timestamp on a repeated execution.
    if (DOC/'pre_pnl_counts.json').exists():
        old=read(DOC/'pre_pnl_counts.json'); assert {k:v for k,v in old.items() if k!='recorded_utc'}=={k:v for k,v in counts.items() if k!='recorded_utc'}
        counts=old
    else: dump(DOC/'pre_pnl_counts.json',counts)
    f.to_csv(OUT/'original_event_diagnostics.csv',index=False); direct.to_csv(OUT/'direct_events.csv',index=False)
    status='READY'
    if counts['invalid_fraction']>.01 or counts['invalid_original']>.01: status='DATA_LIMITED'
    elif counts['clusters24h']<30 or counts['late_clusters24h']<15: status='INSUFFICIENT_SAMPLE'
    if args.phase=='diagnose':
        dump(DOC/'diagnostic_status.json',{'status':status,'counts':counts})
        print(json.dumps({'phase':'diagnose','status':status,'counts':counts})); return
    result={'counts':counts,'status':status,'sources':sources,'candidate_pnl_trials':0,'runs':[], 'prefix':[]}
    if status=='READY':
        result['noop']=study.parity()
        result['prefix']+=study.prefix('base')
        # All three predetermined costs, plus fixed diagnostic controls; no expanding family search.
        for name,slips in [('spot_prior',[0,2,5]),('quality',[0]),('futures_control',[0])]:
            for slip in slips: study.run(name,slip)
        result['candidate_pnl_trials']=1
        result['prefix']+=study.prefix('spot_prior')
        result['failed_gates']={str(s):failures(study.cache['spot_prior',s][2],study.cache['base',s][2]) for s in [0,2,5]}
        result['attribution']={str(s):decomposition(study,'spot_prior',s) for s in [0,2,5]}
        bad=[g for v in result['failed_gates'].values() for g in v]
        result['status']='BASIC_PASS_PENDING_ADVANCED' if not bad else ('EFFECT_TOO_SMALL' if set(bad)=={'net_gain_5pct'} else 'REJECTED')
        result['runs']=[v[2] for v in study.cache.values()]
        result['unperformed']=['neighborhoods','additional_delay','continuous_state_WF','block_bootstrap','random_controls','concentration_gates','prospective']
        result['skip_reason']='Basic economic gate failed; do not expand search' if bad else 'Complete preregistered advanced checks before concluding'
    else:
        result['skip_reason']='Pre-PnL quality/event gate failed'
    changed=[p for p,h in protected.items() if cost.sha(ROOT/p)!=h]; assert not changed,changed
    result['protected_files_checked']=len(protected); result['changed_protected_files']=changed
    dump(DOC/'results.json',result)
    pd.DataFrame([{'name':r['name'],'slip':r['slip'],**r['full']} for r in result['runs']]).to_csv(DOC/'summary.csv',index=False)
    print(json.dumps({'status':result['status'],'counts':counts,'failed_gates':result.get('failed_gates'),
        'rows':[{'name':r['name'],'slip':r['slip'],**{k:r['full'][k] for k in ['n','net_pnl','loss_total','mdd','worst30']}} for r in result['runs']]}))


if __name__=='__main__': main()
