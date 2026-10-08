import os,sys,importlib.util,pandas as pd,numpy as np,pickle
ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','..')); sys.path.insert(0,ROOT)
spec=importlib.util.spec_from_file_location('e',os.path.join(ROOT,'backtest','research','v14_export_trades.py')); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
SP=sys.argv[1]
df=pd.read_csv(SP+'/ETH_1h_long.csv'); ind=e.compute_indicators(df)
tr=e.simulate_v14_detailed(ind,df['datetime'].values,realistic=True,slip_bps=0,margin_schedule=None,extra_cost=5.0)
t=pd.DataFrame(tr); print(t.columns.tolist()); print(len(t),round(t.pnl_usd.sum(),1),round((t.pnl_usd>0).mean()*100,1))
t.to_pickle(SP+'/base_trades.pkl'); 
tr0=e.simulate_v14_detailed(ind,df['datetime'].values,realistic=True,slip_bps=0,margin_schedule=None,extra_cost=0.0)
print('extra0',len(tr0),round(pd.DataFrame(tr0).pnl_usd.sum(),1))
