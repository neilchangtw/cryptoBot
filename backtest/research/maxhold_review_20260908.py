"""附件交易核對與因果出場敏感度；僅寫入本機研究輸出。"""
from pathlib import Path
import copy
import hashlib
import json
import numpy as np
import pandas as pd
import requests
import price_candle_trade_analysis as base
import dynamic_tp_regime_analysis as dynamic

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data' / 'maxhold_review_20260908'
SOURCE = Path('C:/Users/neil.chang/Desktop/Neil_Settout/n.其他/crypto')

def metrics(f):
    f = f.sort_values('exit_dt', kind='stable')
    p = f.pnl.astype(float)
    eq = np.r_[0., p.cumsum().to_numpy()]
    loss = -p[p < 0].sum()
    return dict(n=len(f), pnl=float(p.sum()), wr=float((p > 0).mean()*100),
                pf=float(p[p > 0].sum()/loss) if loss else None,
                mdd=float((np.maximum.accumulate(eq)-eq).max()),
                mh=int(f.reason_code.eq('MH').sum()),
                mh_pnl=float(f.loc[f.reason_code.eq('MH'), 'pnl'].sum()))

def normalize(f):
    f = f.rename(columns={'pnl_usd':'pnl','exit_reason':'reason_code','entry_regime':'regime_code'}).copy()
    for col in ['entry_dt','exit_dt']:
        f[col] = pd.to_datetime(f[col]) + pd.Timedelta(hours=1)
    return f

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bt = base.parse_trade_text(SOURCE / '回測ALL.txt', 'backtest')
    live = base.parse_trade_text(SOURCE / '實戰ALL.txt', 'live')
    data_path = OUT / 'candles.csv'
    if not data_path.exists():
        d = pd.read_csv(base.DATA_PATH, parse_dates=['datetime'])
        start = int((d.datetime.max().tz_localize('Asia/Taipei').tz_convert('UTC') + pd.Timedelta(hours=1)).timestamp()*1000)
        end = int(pd.Timestamp('2026-09-08 10:00', tz='Asia/Taipei').timestamp()*1000)-1
        r = requests.get('https://fapi.binance.com/fapi/v1/klines', params=dict(symbol='ETHUSDT', interval='1h', startTime=start, endTime=end, limit=1500), timeout=20)
        r.raise_for_status()
        rows = r.json()
        fresh = pd.DataFrame([dict(open=float(x[1]), high=float(x[2]), low=float(x[3]), close=float(x[4]), volume=float(x[5]), taker_buy_volume=float(x[9]), datetime=pd.to_datetime(x[0],unit='ms')+pd.Timedelta(hours=8)) for x in rows if x[6] <= end])
        d = pd.concat([d, fresh], ignore_index=True).drop_duplicates('datetime').sort_values('datetime')
        d = d[d.datetime.between('2024-09-08 11:00', '2026-09-08 09:00')].reset_index(drop=True)
        assert len(d) == 17519 and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
        d.to_csv(data_path,index=False)
    d = pd.read_csv(data_path, parse_dates=['datetime'])
    engine = base.load_engine()
    ind = engine.compute_indicators(d)
    dates = d.datetime.to_numpy()
    keys = ['L_MH','L_CMH_MH','S_MH','_L_MH_BR','_S_MH_BR','L_TP','S_TP','_L_TP_BR']
    original = {k:copy.deepcopy(getattr(engine,k)) for k in keys}
    def reset():
        for k,v in original.items(): setattr(engine,k,copy.deepcopy(v))
    def simulate(slip=0, flat=False, down_tp=None, cut=None):
        use_ind = ind if cut is None else engine.compute_indicators(d.iloc[:cut])
        use_dates = dates if cut is None else dates[:cut]
        opts = dict(realistic=True,slip_bps=slip,margin_schedule=None if flat else base.MARGIN_SCHEDULE)
        if down_tp is None:
            raw = engine.simulate_v14_detailed(use_ind,use_dates,**opts)
        else:
            fn = dynamic.build_dynamic_simulator(engine)
            tp = np.array([down_tp if engine._classify_regime(s)=='DOWN' else .02 for s in use_ind['slope']])
            raw = fn(use_ind,use_dates,s_tp_by_bar=tp,**opts)
        return normalize(pd.DataFrame(raw))
    baseline = simulate()
    pd.testing.assert_frame_equal(baseline,simulate(down_tp=.02))
    baseline.to_csv(OUT/'baseline.csv',index=False)
    compare = bt.merge(baseline,on=['side','entry_dt'],suffixes=('_text','_engine'),how='outer',indicator=True)
    assert len(compare)==len(bt)==269 and compare['_merge'].eq('both').all()
    for col in ['exit_dt','reason_code','regime_code']:
        assert compare[col+'_text'].eq(compare[col+'_engine']).all(), col
    assert (compare.pnl_text-compare.pnl_engine).abs().max() <= .0051
    matched = live.merge(bt,on=['side','entry_dt'],suffixes=('_live','_bt'),how='outer',indicator=True)
    recent_bt = bt[bt.entry_dt >= '2026-06-01']
    common = matched[matched['_merge'].eq('both')].copy()
    common['pnl_delta'] = common.pnl_live-common.pnl_bt
    common.to_csv(OUT/'live_parity.csv',index=False)
    report = dict(data=dict(start=str(d.datetime.min()),end=str(d.datetime.max()),bars=len(d)),
        sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [SOURCE/'回測ALL.txt',SOURCE/'實戰ALL.txt',base.ENGINE_PATH,data_path]},
        backtest=metrics(bt),live=metrics(live),recent_backtest=metrics(recent_bt),
        parity=dict(matched=len(common), live=len(live), recent_backtest=len(recent_bt),
            exit_matches=int(common.exit_dt_live.eq(common.exit_dt_bt).sum()),
            reason_matches=int(common.reason_code_live.eq(common.reason_code_bt).sum()),
            hold_matches=int(common.hold_live.eq(common.hold_bt).sum()),
            regime_matches=int(common.regime_code_live.eq(common.regime_code_bt).sum()),
            live_minus_backtest=float(common.pnl_delta.sum())),
        groups={},candidates=[])
    for label,f in [('backtest',bt),('live',live),('recent_backtest',recent_bt)]:
        report['groups'][label] = {group:[dict(key=str(k),**metrics(g)) for k,g in f.groupby(cols)] for group,cols in [('side',['side']),('exit',['reason_code']),('side_regime',['side','regime_code'])]}
    variants=[('baseline',0,0,None,None,None)]
    for side in ['L','S']:
        for diff in [-2,-1,1,2,3,4,5]:
            variants.append((f'{side}{diff:+d}h',diff if side=='L' else 0,diff if side=='S' else 0,None,None,None))
    for tp in [.01,.015,.0175,.025,.03]: variants.append((f'S_TP_{tp}',0,0,None,tp,None))
    for tp in [.02,.025,.03]: variants.append((f'L_TP_{tp}',0,0,tp,None,None))
    for tp in [.025,.0275,.03,.0325,.035]: variants.append((f'S_DOWN_TP_{tp}',0,0,None,None,tp))
    for name,ld,sd,lt,st,dt in variants:
        reset()
        engine.L_MH += ld; engine.L_CMH_MH += ld; engine.S_MH += sd
        engine._L_MH_BR={k:v+ld for k,v in original['_L_MH_BR'].items()}
        engine._S_MH_BR={k:v+sd for k,v in original['_S_MH_BR'].items()}
        if lt is not None: engine.L_TP=lt; engine._L_TP_BR={'DOWN':lt+.005}
        if st is not None: engine.S_TP=st
        f=simulate(down_tp=dt)
        f.to_csv(OUT/(name+'.csv'),index=False)
        periods={'full':f,'pre2026':f[f.exit_dt<'2026-01-01'],'2026':f[f.exit_dt>='2026-01-01'],'recent':f[f.exit_dt>='2026-06-01']}
        row=dict(name=name,**{k:metrics(v) for k,v in periods.items()})
        row['flat']=metrics(simulate(flat=True,down_tp=dt))
        row['slip2']=metrics(simulate(slip=2,down_tp=dt))
        row['slip5']=metrics(simulate(slip=5,down_tp=dt))
        row['quarters']={str(k):metrics(g) for k,g in f.groupby(f.exit_dt.dt.to_period('Q'))}
        if name in ['baseline','S+4h','S_DOWN_TP_0.03']:
            row['prefix_checks']=[]
            for cut in [6000,11000,16000]:
                partial=simulate(down_tp=dt,cut=cut)
                expected=f[f.exit_dt <= d.datetime.iloc[cut-1]+pd.Timedelta(hours=1)].reset_index(drop=True)
                pd.testing.assert_frame_equal(partial.reset_index(drop=True),expected)
                row['prefix_checks'].append(dict(bars=cut,trades=len(partial),passed=True))
            paired=f.merge(baseline,on=['side','entry_dt'],suffixes=('_candidate','_base'),how='outer',indicator=True)
            both=paired[paired['_merge'].eq('both')].copy()
            delta=both.pnl_candidate-both.pnl_base
            old_mh=both[both.reason_code_base.eq('MH')]
            row['paired']=dict(common=len(both),candidate_only=int(paired['_merge'].eq('left_only').sum()),baseline_only=int(paired['_merge'].eq('right_only').sum()),
                better=int((delta>.005).sum()),worse=int((delta<-.005).sum()),
                old_mh_new_reasons=old_mh.reason_code_candidate.value_counts().to_dict(),
                old_mh_new_net_wins=int((old_mh.pnl_candidate>0).sum()),
                changed_20260904=int(((both.exit_dt_candidate>='2026-09-04 16:00')&(delta.abs()>.005)).sum()))
            row['total_hold_hours']=int(f.bars_held.sum())
        report['candidates'].append(row)
        print(name,round(row['full']['pnl'],2),row['full']['mh'],flush=True)
    reset()
    report['live_wr_interval']=base.wilson_interval(int(live.win.sum()),len(live))
    report['live_mh_interval']=base.wilson_interval(int(live.reason_code.eq('MH').sum()),len(live))
    (OUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE',OUT)

if __name__=='__main__': main()
