import sys,os; SP=sys.argv[1]
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'swing.py'),encoding='utf-8').read())
F=load(SP); h,l=F['high'],F['low']
e,x=signals(F,'T','S',(55,2)); t=run(F,'S',e,x); t=t[t.entry_dt>=IS0]
ep=F['open'][t.eb.values]
t['MAE%']=[ (h[a:b+1].max()/p-1)*100 for a,b,p in zip(t.eb,t.xb,ep)]
t['MFE%']=[ (1-l[a:b+1].min()/p)*100 for a,b,p in zip(t.eb,t.xb,ep)]
t['days']=(t.hold/24).round(1); t['ret%']=(t.ret*100).round(2)
pd.set_option('display.width',200)
print(t[['entry_dt','exit_dt','days','ret%','MAE%','MFE%','pnl']].round(1).to_string())
print('n',len(t),'total',round(t.pnl.sum()),'maxMAE',round(t['MAE%'].max(),1))
# sensitivity: N 45..70, m 1.5..3, and slippage 10bp
for N in (40,45,50,55,60,65,70):
    row=[]
    for m in (1.5,2,2.5,3):
        e2,x2=signals(F,'T','S',(N,m)); tt=run(F,'S',e2,x2); row.append((round(win(tt,IS0,OOS0).pnl.sum()),round(win(tt,OOS0,END).pnl.sum())))
    print(N,row)
