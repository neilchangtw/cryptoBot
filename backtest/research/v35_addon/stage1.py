import sys; SP=sys.argv[1]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'strict.py'),encoding='utf-8').read())
d=load(); F=feats(d); sel={}; tot=0
for fam,g in GRIDS.items():
    res={}
    for p in itertools.product(*g):
        up,ent,exr,SL=spec(fam,p,F); t=win(run(F,up,ent,exr,SL),IS0,OOS0); res[p]=(t.pnl.sum(),len(t)); tot+=1
    idx={p:tuple(g[k].index(p[k]) for k in range(len(p))) for p in res}
    def nb(p): return [q for q in res if all(abs(idx[q][k]-idx[p][k])<=1 for k in range(len(p)))]
    score={p:np.median([res[q][0] for q in nb(p)]) for p in res}
    best=max(score,key=score.get)
    print(fam,'IS>0:',sum(v[0]>0 for v in res.values()),'/',len(res),' center',best,'IS',round(res[best][0]),'n',res[best][1],'nbr-median',round(score[best]))
    if res[best][0]>0 and score[best]>0: sel[fam]=list(best)
print('configs tested',tot); json.dump(sel,open(SP+'/sel.json','w')); print('selected',sel)
