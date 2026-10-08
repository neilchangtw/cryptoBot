import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'m30.py'),encoding='utf-8').read())
d=load(SP); sel={}
for side in 'LS':
    F=feats(d,side); res={p:(lambda t:(t.pnl.sum(),len(t)))(win(run(F,side,*p),IS0,OOS0)) for p in itertools.product(*GRID)}
    idx={p:tuple(GRID[k].index(p[k]) for k in range(3)) for p in res}
    sc={p:np.median([res[q][0] for q in res if all(abs(idx[q][k]-idx[p][k])<=1 for k in range(3))]) for p in res}; b=max(sc,key=sc.get)
    print('M'+side,'IS>0',sum(v[0]>0 for v in res.values()),'/27 center',b,'IS',round(res[b][0]),'n',res[b][1],'nbr-med',round(sc[b]),' best raw',max(round(v[0]) for v in res.values()))
    if res[b][0]>0 and sc[b]>0: sel['M'+side]=list(b)
print('tested 54 selected',sel); json.dump(sel,open(SP+'/m_sel.json','w'))
