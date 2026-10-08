import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'htf.py'),encoding='utf-8').read())
sel={}; tot=0
for fam,(tf,side) in FAMS.items():
    a,hpb=load(SP,tf); F=feats(a,side); g=GRIDS[tf]; res={}
    for p in itertools.product(*g):
        t=win(run(F,side,*p,hpb),IS0,OOS0); res[p]=(t.pnl.sum(),len(t)); tot+=1
    idx={p:tuple(g[k].index(p[k]) for k in range(3)) for p in res}
    nb=lambda p:[q for q in res if all(abs(idx[q][k]-idx[p][k])<=1 for k in range(3))]
    sc={p:np.median([res[q][0] for q in nb(p)]) for p in res}; best=max(sc,key=sc.get)
    print(fam,'IS>0',sum(v[0]>0 for v in res.values()),'/',len(res),'center',best,'IS',round(res[best][0]),'n',res[best][1],'nbr-med',round(sc[best]))
    if res[best][0]>0 and sc[best]>0: sel[fam]=list(best)
print('tested',tot,'selected',sel); json.dump(sel,open(SP+'/h_sel.json','w'))
