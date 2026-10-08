import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'pair.py'),encoding='utf-8').read())
d=load(SP); F=feats(d); sel={}; tot=0
for fam,g in GRIDS.items():
    res={}
    for p in itertools.product(*g):
        dd,exr=spec(fam,p,F); t=win(run(F,dd,exr),IS0,OOS0); res[p]=(t.pnl.sum(),len(t)); tot+=1
    idx={p:tuple(g[k].index(p[k]) for k in range(len(p))) for p in res}
    nb=lambda p:[q for q in res if all(abs(idx[q][k]-idx[p][k])<=1 for k in range(len(p)))]
    sc={p:np.median([res[q][0] for q in nb(p)]) for p in res}; best=max(sc,key=sc.get)
    print(fam,'IS>0:',sum(v[0]>0 for v in res.values()),'/',len(res),'center',best,'IS',round(res[best][0]),'n',res[best][1],'nbr-med',round(sc[best]))
    print('  all IS:',{k:round(v[0]) for k,v in res.items()})
    if res[best][0]>0 and sc[best]>0: sel[fam]=list(best)
print('tested',tot,'selected',sel); json.dump(sel,open(SP+'/p_sel.json','w'))
