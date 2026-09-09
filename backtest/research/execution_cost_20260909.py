"""成本可改善幅度、完整狀態敏感度與附件成交偏差。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import intrahour_entry_20260908 as intra
import execute_optimization_plan_20260908 as cost
import maxhold_review_20260908 as review

ROOT=intra.ROOT
OUT=ROOT/'data/execution_cost_20260909'
FEES=[4.,3.6,3.5,3.,2.,0.]
SOURCE=Path('C:/Users/neil.chang/Desktop/Neil_Settout/n.其他/crypto')


def price_deviation(live,backtest):
    f=live.merge(backtest,on=['side','entry_dt'],suffixes=('_live','_bt'),validate='one_to_one')
    assert len(f)==len(live)
    for a,b in [('exit_dt','exit_dt'),('hold','hold'),('reason_code','reason_code'),('regime_code','regime_code')]:
        assert f[a+'_live'].equals(f[b+'_bt'])
    sign=f.side.map({'L':1.,'S':-1.})
    f['entry_adverse_bp']=sign*(f.entry_price_live/f.entry_price_bt-1)*10000
    f['exit_adverse_bp']=sign*(1-f.exit_price_live/f.exit_price_bt)*10000
    f['sum_adverse_bp']=f.entry_adverse_bp+f.exit_adverse_bp
    f['net_difference']=f.pnl_live-f.pnl_bt
    return f


class Study:
    def __init__(self):
        self.source=cost.Study();self.d=self.source.d;self.engine=self.source.engine
        self.fn=intra.simulator(self.engine);self.ind=self.engine.compute_indicators(self.d);self.cache={}

    def run(self,fee=4.,slip=0,hist=False,cut=None):
        self.fn.__globals__['FEE']=fee
        d=self.d if cut is None else self.d.iloc[:cut]
        ind=self.ind if cut is None else self.engine.compute_indicators(d)
        raw,active=self.fn(ind,d.datetime.to_numpy(),realistic=True,slip_bps=slip,
                           margin_schedule=cost.base.MARGIN_SCHEDULE if hist else None)
        f=review.normalize(pd.DataFrame(raw))
        if cut is not None:return f
        assert not any(active.values())
        f,eq,ledger=cost.account(f,self.d,self.source.mark,self.source.fund)
        stem=f'fee{fee:g}_{"hist" if hist else "flat"}_{slip}'
        f.to_csv(OUT/f'{stem}_trades.csv',index=False);eq.to_csv(OUT/f'{stem}_equity.csv',index=False)
        ledger.to_csv(OUT/f'{stem}_funding.csv',index=False)
        row={'fee':fee,'slip':slip,'historical':hist,'full':cost.metrics(f,eq),
             'early':cost.metrics(f,eq,end='2026-01-01'),'late':cost.metrics(f,eq,start='2026-01-01'),
             'model_fee_total':float(f.fee_exact.sum())}
        self.cache[fee,slip,hist]=(f,eq,row)
        return f,eq,row


def main():
    OUT.mkdir(exist_ok=True)
    paths=[Path(__file__),ROOT/'doc/execution_cost_plan_20260909.md',ROOT/'strategy.py',ROOT/'executor.py',ROOT/'binance_trade.py',
           cost.base.ENGINE_PATH,ROOT/'data/maxhold_review_20260908/candles.csv',SOURCE/'實戰ALL.txt',SOURCE/'回測ALL.txt']
    hashes={str(p):cost.sha(p) for p in paths}
    (OUT/'registration.json').write_text(json.dumps({'fees':FEES,'hashes':hashes},indent=2,ensure_ascii=False),encoding='utf-8')
    s=Study();rows=[];checks=[];pairs=[]
    for hist in [False,True]:
        for slip in [0,2,5]:
            for fee in FEES:
                f,eq,row=s.run(fee,slip,hist);rows.append(row)
                if fee==4:
                    old=pd.read_csv(ROOT/f'data/optimization_execution_20260908/base_{"hist" if hist else "flat"}_{slip}_trades.csv')
                    assert f[['side','entry_bar','exit_bar','reason_code']].equals(old[['side','entry_bar','exit_bar','reason_code']])
                    assert np.allclose(f.net,old.net,atol=1e-8,rtol=0)
                    checks.append({'parity':[hist,slip],'status':'PASS'})
                else:
                    counts,paired=cost.paired(f,s.cache[4.,slip,hist][0])
                    pairs.append({'fee':fee,'slip':slip,'historical':hist,**counts})
                    paired.to_csv(OUT/f'fee{fee:g}_{"hist" if hist else "flat"}_{slip}_paired.csv',index=False)
            print(json.dumps({'complete':[hist,slip]}),flush=True)
    for cut in [10000,16000]:
        for fee in FEES:
            f=s.run(fee,cut=cut);full=s.cache[fee,0,False][0];full=full[full.exit_bar<cut]
            pd.testing.assert_frame_equal(f.reset_index(drop=True),full[f.columns].reset_index(drop=True))
        checks.append({'prefix':cut,'fees':6,'status':'PASS'})
    # 模型 $4 完全移除只對原有交易清單構成這個算術上限。
    f=s.cache[4.,0,False][0];static=[]
    for fee in FEES:
        net=f.net+4.-fee
        static.append({'fee':fee,'net':float(net.sum()),'wr':float((net>0).mean()*100),
                       'flipped':int(((f.net<=0)&(net>0)).sum()),'mh_flipped':int(((f.reason_code=='MH')&(net>0)).sum())})
    thresholds=f.loc[f.net<=0,['side','entry_dt','reason_code','net']].copy()
    thresholds['saving_must_exceed']=-thresholds.net
    thresholds.sort_values('saving_must_exceed').to_csv(OUT/'break_even_savings.csv',index=False)
    live=cost.base.parse_trade_text(SOURCE/'實戰ALL.txt','live')
    bt=cost.base.parse_trade_text(SOURCE/'回測ALL.txt','backtest')
    deviations=price_deviation(live,bt);deviations.to_csv(OUT/'live_price_deviations.csv',index=False)
    biggest=int(deviations.entry_adverse_bp.abs().idxmax());less=deviations.drop(index=biggest)
    live_metrics={'n':len(deviations),'live_pnl':float(live.pnl.sum()),'backtest_pnl':float(deviations.pnl_bt.sum()),
                  'live_wr':float((live.pnl>0).mean()*100),'backtest_wr':float((deviations.pnl_bt>0).mean()*100),
                  'net_difference':float(deviations.net_difference.sum()),'largest_entry_deviation_trade':int(deviations.loc[biggest,'num_live'])}
    for col in ['entry_adverse_bp','exit_adverse_bp','sum_adverse_bp']:
        live_metrics[col]={'mean':float(deviations[col].mean()),'median':float(deviations[col].median()),
                           'mean_absolute':float(deviations[col].abs().mean()),'adverse_n':int((deviations[col]>0).sum()),
                           'mean_without_largest_entry':float(less[col].mean())}
    result={'hashes':hashes,'rows':rows,'static':static,'paired':pairs,'live':live_metrics,'checks':checks,
            'actual_commission_available':False,'deployable_execution_candidate':False,'status':'CONDITIONAL_SENSITIVITY_ONLY'}
    assert hashes=={str(p):cost.sha(p) for p in paths}
    (OUT/'results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    pd.DataFrame([{'fee':r['fee'],'slip':r['slip'],'historical':r['historical'],**r['full']} for r in rows]).to_csv(OUT/'summary.csv',index=False)
    print(json.dumps({'static':static,'live':live_metrics,'flat0':[r for r in rows if not r['historical'] and r['slip']==0]}),flush=True)


if __name__=='__main__':main()
