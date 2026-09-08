"""第一階段：離線基準、已知 funding 現金流及逐時成交價淨值代理。"""
from pathlib import Path
import hashlib
import inspect
import json
import subprocess
import numpy as np
import pandas as pd
import price_candle_trade_analysis as base
import maxhold_review_20260908 as review

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'data'/'phase1_cost_risk_20260908'
CANDLES = ROOT/'data'/'maxhold_review_20260908'/'candles.csv'
FUNDING = ROOT/'data'/'ETHUSDT_funding.csv'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def drawdown(values):
    eq=np.r_[0.,np.asarray(values,dtype=float)]
    return float((np.maximum.accumulate(eq)-eq).max())

def funding_cash(side, qty, mark_proxy, rate):
    return (-1 if side=='L' else 1)*qty*mark_proxy*rate

def held_at(entry, exit_time, event):
    # 約定 funding 先於整點後的市價成交；同邊界實際次序另列敏感度。
    return entry < event <= exit_time

def equity_curve(trades, candles):
    price=candles.close.to_numpy()
    cash=np.zeros(len(candles)); unreal=np.zeros(len(candles))
    for t in trades.itertuples():
        a,b=int(t.entry_bar),int(t.exit_bar)
        fee=t.margin/200*4
        qty=t.margin*20/t.entry_price
        cash[a]-=fee/2
        cash[b]+=t.pnl+fee/2
        unreal[a:b]+=(1 if t.side=='L' else -1)*qty*(price[a:b]-t.entry_price)
    result=pd.DataFrame({'time':candles.datetime+pd.Timedelta(hours=1),
                         'cash_pnl':cash.cumsum(),'unrealized_proxy':unreal})
    result['equity_pnl_proxy']=result.cash_pnl+result.unrealized_proxy
    assert abs(result.cash_pnl.iloc[-1]-trades.pnl.sum())<1e-7
    return result

def self_checks():
    assert funding_cash('L',2,100,.001)==-.2
    assert funding_cash('S',2,100,.001)==.2
    assert funding_cash('L',2,100,-.001)==.2
    assert not held_at(8,10,8) and held_at(8,10,10) and not held_at(8,10,11)
    assert drawdown([-5,-2,3,-4])==7
    d=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=3,freq='h'),'close':[100,90,110]})
    t=pd.DataFrame([dict(entry_bar=0,exit_bar=2,margin=200,entry_price=100,pnl=396,side='L')])
    eq=equity_curve(t,d)
    assert eq.equity_pnl_proxy.tolist()==[-2.,-402.,396.]
    assert drawdown(eq.equity_pnl_proxy)==402
    return ['funding sign including negative rates','settlement boundary convention','drawdown includes initial zero',
            'entry/exit fee ledger and floating-loss fixture']

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    checks=self_checks()
    candles=pd.read_csv(CANDLES,parse_dates=['datetime'])
    assert len(candles)==17519 and candles.datetime.is_unique
    assert candles.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    frozen=json.loads((CANDLES.parent/'results.json').read_text(encoding='utf-8'))
    assert sha(CANDLES)==frozen['sources']['candles.csv']
    files=[CANDLES,FUNDING,base.ENGINE_PATH,ROOT/'strategy.py',ROOT/'executor.py',Path(__file__).resolve(),
           ROOT/'doc'/'strategy_optimization_plan_20260908.md']
    hashes={str(p.relative_to(ROOT)):sha(p) for p in files}
    git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    fund=pd.read_csv(FUNDING)
    raw=pd.to_datetime(fund.funding_time_ms,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    nominal=raw.dt.round('h')
    assert (raw-nominal).abs().max() <= pd.Timedelta(seconds=60)
    fund['event_time']=nominal
    fund['raw_event_time']=raw
    assert fund.event_time.is_unique and fund.event_time.is_monotonic_increasing
    assert fund.rate.notna().all()
    gaps=fund.event_time.diff().dropna()
    assert gaps.eq(pd.Timedelta(hours=8)).all(), '既有資金費不連續，需縮小有效覆蓋範圍'
    rate_end=fund.event_time.max()
    rate_start=fund.event_time.min()
    price_by_time=pd.Series(candles.close.to_numpy(),index=candles.datetime+pd.Timedelta(hours=1))
    eligible=fund[fund.event_time.between(price_by_time.index.min(),price_by_time.index.max())].copy()
    eligible['settlement_price_proxy']=eligible.event_time.map(price_by_time)
    assert eligible.settlement_price_proxy.notna().all()
    eligible.to_csv(OUT/'funding_inventory.csv',index=False)
    engine=base.load_engine()
    # 只在記憶體副本取回期末倉位，避免遺漏未平倉曝險。
    src=inspect.getsource(engine.simulate_v14_detailed)
    assert src.count('    return trades')==1
    src=src.replace('    return trades',"    return trades, {'L_active': bool(lp_active), 'S_active': bool(sp_active)}")
    ns=dict(engine.__dict__); exec(src,ns)
    run=ns['simulate_v14_detailed']
    ind=engine.compute_indicators(candles)
    result={'git_head':git_head,'sha256':hashes,'self_checks':checks,
            'funding_inventory':{'rows':len(fund),'first_taipei':str(rate_start),'last_taipei':str(rate_end),
             'interval_hours_counts':{str(k.total_seconds()/3600):int(v) for k,v in gaps.value_counts().items()},
             'has_mark_price':False,'funding_rows_in_window':len(eligible),
             'missing_tail_from':str(rate_end),'required_through':str(price_by_time.index.max()),
             'estimated_missing_rows_if_8h':int((price_by_time.index.max()-rate_end)/pd.Timedelta(hours=8))},
            'runs':[]}
    original_bt=base.parse_trade_text(review.SOURCE/'回測ALL.txt','backtest')
    live=base.parse_trade_text(review.SOURCE/'實戰ALL.txt','live')
    for schedule in ['flat200','historical']:
        for slip in [0,2,5]:
            rows,terminal=run(ind,candles.datetime.to_numpy(),realistic=True,slip_bps=slip,
                             margin_schedule=None if schedule=='flat200' else base.MARGIN_SCHEDULE)
            assert not any(terminal.values()), '期末持倉需另行估值，停止輸出不完整淨值'
            trades=review.normalize(pd.DataFrame(rows))
            label=f'{schedule}_slip{slip}'
            if schedule=='historical' and slip==0:
                cmp=original_bt.merge(trades,on=['side','entry_dt'],suffixes=('_text','_engine'),how='outer',indicator=True)
                assert len(cmp)==269 and cmp['_merge'].eq('both').all()
                for col in ['exit_dt','reason_code','regime_code']:
                    assert cmp[col+'_text'].eq(cmp[col+'_engine']).all()
                assert cmp.hold.eq(cmp.bars_held).all()
                for col in ['pnl','entry_price','exit_price']:
                    assert (cmp[col+'_text']-cmp[col+'_engine']).abs().max()<=.0051
                pair=live.merge(original_bt,on=['side','entry_dt'],suffixes=('_live','_bt'))
                assert len(pair)==35
                for col in ['exit_dt','reason_code','hold','regime_code']:
                    assert pair[col+'_live'].eq(pair[col+'_bt']).all()
                result['parity']={'backtest':269,'live':35,'live_minus_backtest':float((pair.pnl_live-pair.pnl_bt).sum())}
            eq=equity_curve(trades,candles)
            ledger=[]; per_trade=[]
            for i,t in enumerate(trades.itertuples()):
                candidates=eligible[eligible.event_time.between(t.entry_dt,t.exit_dt)]
                total=0.; low=0.; high=0.
                for f in candidates.itertuples():
                    amount=funding_cash(t.side,t.margin*20/t.entry_price,f.settlement_price_proxy,f.rate)
                    include=held_at(t.entry_dt,t.exit_dt,f.event_time)
                    ambiguous=(f.event_time==t.entry_dt or f.event_time==t.exit_dt)
                    # SN 在bar內停損，1h資料不知道精確時間，出場邊界視為不確定。
                    cash=amount if include else 0.
                    total+=cash; low+=min(0.,amount) if ambiguous else cash
                    high+=max(0.,amount) if ambiguous else cash
                    ledger.append(dict(trade_id=i,side=t.side,event_time=f.event_time,rate=f.rate,
                                       price_proxy=f.settlement_price_proxy,cashflow=cash,
                                       included=include,boundary_ambiguous=ambiguous,possible_cashflow=amount))
                covered=t.entry_dt>=rate_start and t.exit_dt<=rate_end
                per_trade.append(dict(funding_covered=covered,known_funding_proxy=total,
                                      known_funding_low=low,known_funding_high=high,
                                      pnl_after_funding_proxy=t.pnl+total if covered else None))
            trades=pd.concat([trades,pd.DataFrame(per_trade)],axis=1)
            ld=pd.DataFrame(ledger)
            bytime=ld.groupby('event_time').cashflow.sum()
            eq['known_funding_proxy']=eq.time.map(bytime).fillna(0).cumsum()
            eq['funding_coverage_complete']=eq.time.between(rate_start,rate_end)
            eq['equity_after_funding_proxy']=(eq.equity_pnl_proxy+eq.known_funding_proxy).where(eq.funding_coverage_complete)
            complete=trades[trades.funding_covered]
            end_window=eq[eq.funding_coverage_complete]
            assert abs(ld.cashflow.sum()-trades.known_funding_proxy.sum())<1e-8
            assert eq.loc[~eq.funding_coverage_complete,'equity_after_funding_proxy'].isna().all()
            item=dict(name=label,terminal=terminal,baseline=review.metrics(trades),
                      hourly_trade_price_mdd_proxy=drawdown(eq.equity_pnl_proxy),
                      full_period_funding_adjusted_pnl=None,
                      known_funding_proxy=float(ld.cashflow.sum()),
                      known_funding_boundary_low=float(trades.known_funding_low.sum()),
                      known_funding_boundary_high=float(trades.known_funding_high.sum()),
                      funding_covered_trades=len(complete),funding_uncovered_trades=len(trades)-len(complete),
                      covered_closed_pnl=float(complete.pnl.sum()),
                      covered_closed_pnl_after_funding_proxy=float(complete.pnl_after_funding_proxy.sum()),
                      covered_hourly_mdd_proxy=drawdown(end_window.equity_pnl_proxy),
                      covered_hourly_mdd_after_funding_proxy=drawdown(end_window.equity_after_funding_proxy),
                      included_settlements=int(ld.included.sum()),boundary_events=int(ld.boundary_ambiguous.sum()))
            result['runs'].append(item)
            trades.to_csv(OUT/f'{label}_trades.csv',index=False)
            ld.to_csv(OUT/f'{label}_funding_ledger.csv',index=False)
            eq.to_csv(OUT/f'{label}_equity.csv',index=False)
            print(json.dumps(item,ensure_ascii=False),flush=True)
    # 所有既有輸入保持位元一致。
    assert all(sha(ROOT/p)==h for p,h in hashes.items())
    result['input_hashes_unchanged']=True
    result['attachment_hashes']={p.name:sha(p) for p in [review.SOURCE/'回測ALL.txt',review.SOURCE/'實戰ALL.txt']}
    result['cost_semantics']={'funding_used_in_signal':False,'funding_used_in_circuit_breakers':False,
        'reason':'沿用現行策略以交易出場PnL觸發熔斷；funding只進入研究帳本，改熔斷另需完整狀態測試',
        'entry_exit_fee_split':'4 USD per 200U trade, split half at entry and exit; preserves existing all-in fee model',
        'funding_boundary':'entry < settlement <= exit, and alternative boundary inclusion range',
        'price_proxy':'hourly traded close, not mark price; reported entry prices rounded to cents',
        'equity_scope':'strategy PnL equity, excludes account deposits, withdrawals, other positions and liquidation'}
    manifest={'status':'REGISTERED_NOT_RUN','reference':'doc/strategy_optimization_plan_20260908.md',
        'frozen_candles_sha256':sha(CANDLES),'P0':'current baseline',
        'S_DOWN_TP':[.025,.03,.035],'S_MH_extra_hours':[2,3,4],
        'conditional_combination':{'S_DOWN_TP':.03,'S_MH_extra_hours':3,'requires':'both families pass'},
        'S_volume_ratio20':[1.,1.5],'S_ADX14_threshold':[20,25],
        'S_retest_wait_hours':[1,3],'S_retest_ATR14_tolerance':.1,'S_delay_control_hours':[1,3],
        'primary_margin':200,'leverage':20,'extra_slip_bps':[0,2,5],
        'max_first_batch_configurations':16}
    (OUT/'candidate_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
