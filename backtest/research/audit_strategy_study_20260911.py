"""Independent Decimal feature audit and pure production-indicator parity."""
import ast
from decimal import Decimal
import json
import numpy as np
import pandas as pd
import strategy_study_20260911 as research


def main():
    root=research.ROOT
    d=pd.read_csv(root/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime'])
    f=pd.read_csv(research.OUT/'features.csv',parse_dates=['event_ts','decision_ts'])
    raw=pd.read_csv(root/'data/new_information_20260910/oi_source_merged.csv',dtype=str)
    # Merged input itself is verified against the original ZIPs by the main study;
    # this separate path uses decimal strings instead of its numpy calculations.
    validated,_=research.prior.archive_frames(['metrics'])
    assert list(raw.columns)==list(validated.columns)
    for col in ['event_ts','sum_open_interest','sum_toptrader_long_short_ratio','count_long_short_ratio']:
        if col=='event_ts':
            assert pd.to_datetime(raw[col]).equals(validated[col])
        else:
            np.testing.assert_allclose(raw[col].astype(float),validated[col],rtol=0,atol=0)
    source={pd.Timestamp(t):row for t,row in zip(raw.event_ts,raw.to_dict('records'))}
    checked=0
    for r in f.itertuples():
        for label,col,minutes in [('oi','sum_open_interest',120),
                                 ('top','sum_toptrader_long_short_ratio',60),
                                 ('global','count_long_short_ratio',60)]:
            values=[Decimal(source.get(r.event_ts-pd.Timedelta(minutes=k),{}).get(col,'NaN'))
                    for k in range(0,minutes+1,5)]
            valid=all(v.is_finite() and v>0 for v in values)
            assert valid==getattr(r,label+'_valid')
            if valid:
                delta=values[0]/values[-1]-1
                calculated=getattr(r,label+'_delta')
                assert abs(float(delta)-calculated)<1e-12
                assert (delta<0)==(calculated<0) and (delta>0)==(calculated>0)
            checked+=1
    # Execute only literal constants and the pure indicator function from actual
    # strategy.py; imports, dotenv, API calls and account settings are excluded.
    tree=ast.parse((root/'strategy.py').read_text(encoding='utf-8-sig'))
    namespace={'np':np,'pd':pd}
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try: namespace[node.targets[0].id]=ast.literal_eval(node.value)
            except (ValueError,TypeError): pass
    indicator=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='compute_indicators')
    exec(compile(ast.Module(body=[indicator],type_ignores=[]),'<pure-current-strategy>','exec'),namespace)
    actual=namespace['compute_indicators'](d)
    engine=research.cost.base.load_engine()
    expected=engine.compute_indicators(d)
    keys={'gk_pctile':'pctile_L','gk_pctile_s':'pctile_S',
          'breakout_long':'brk_up','breakout_short':'brk_dn',
          'sma_slope':'slope','regime_block_l':'regime_block_l','regime_block_s':'regime_block_s'}
    for prod,key in keys.items():
        np.testing.assert_allclose(actual[prod].to_numpy()[310:],expected[key][310:],equal_nan=True,rtol=0,atol=1e-12)
    assert namespace['BRK_LOOK']==engine.L_BRK==engine.S_BRK==15
    constants={'L_GK_THRESH':'L_GK_TH','S_GK_THRESH':'S_GK_TH','L_MAX_HOLD':'L_MH',
               'S_MAX_HOLD':'S_MH','L_EXIT_CD':'L_CD','S_EXIT_CD':'S_CD',
               'L_TP_BY_REGIME':'_L_TP_BR','L_MH_BY_REGIME':'_L_MH_BR','S_MH_BY_REGIME':'_S_MH_BR'}
    for prod,key in constants.items():assert namespace[prod]==getattr(engine,key)
    events=pd.read_csv(research.OUT/'base_0bp_events.csv',parse_dates=['decision_ts'])
    selected=events[events.valid & events.X & events.Y]
    labels=research.event_counts(selected,'decision_ts')
    assert labels==json.loads((research.DOC/'pre_pnl_counts.json').read_text())['B']
    # All native bar-2 exits are excluded before the new decision point.
    trades=pd.read_csv(research.OUT/'base_0bp_trades.csv')
    pairs=selected.merge(trades,on=['side','entry_bar'],suffixes=('_event','_trade'),validate='one_to_one')
    assert (pairs.bar < pairs.exit_bar).all()
    result={'decimal_feature_checks':checked,'decimal_result':'PASS',
            'production_indicator_columns':list(keys),'production_indicators':'PASS',
            'production_constant_pairs':constants,'constant_parity':'PASS',
            'bar2_eligible_after_native_exits':'PASS','B_count_recheck':labels,
            'no_candidates_scored':True}
    research.dump(research.DOC/'independent_audit.json',result)
    print(json.dumps({'decimal_checks':checked,'production_parity':'PASS','event_count_audit':'PASS'}))


if __name__=='__main__':main()
