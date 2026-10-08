import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'fund.py'),encoding='utf-8').read())
d=load(SP); F=feats(d); sel={}
for side in 'LS':
    res={p:(lambda t:(t.pnl.sum(),len(t)))(win(run(F,side,*p),IS0,OOS0)) for p in itertools.product(*GRID)}
    idx={p:tuple(GRID[k].index(p[k]) for k in range(2)) for p in res}
    sc={p:np.median([res[q][0] for q in res if all(abs(idx[q][k]-idx[p][k])<=1 for k in range(2))]) for p in res}; b=max(sc,key=sc.get)
    print('F'+side,{k:(round(v[0]),v[1]) for k,v in res.items()}); print('  center',b,'IS',round(res[b][0]),'nbr-med',round(sc[b]))
    if res[b][0]>0 and sc[b]>0: sel['F'+side]=list(b)
print('tested 18 selected',sel); json.dump(sel,open(SP+'/f_sel.json','w'))
