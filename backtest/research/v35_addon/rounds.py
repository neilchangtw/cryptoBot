import sys; SP=sys.argv[1]; which=sys.argv[2]
exec(open(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)),'gen.py'),encoding='utf-8').read())
def s6(F,p):
    N,H=p; cb,ce=F['cbs'],F['cs']
    bu=F['close_b']>cb.shift(1).rolling(N).max().values; bd=F['close_b']<cb.shift(1).rolling(N).min().values
    eu=F['close']>ce.shift(1).rolling(N).max().values; ed=F['close']<ce.shift(1).rolling(N).min().values
    return np.where(bu&~eu,1,np.where(bd&~ed,-1,0)),H,0,0.03,None,None
def s7(F,p):
    k,H=p; c,o,v=F['close'],F['open'],pd.Series(F['volume'])
    r=pd.Series(c/o-1); z=(r/r.shift(1).rolling(168).std()).values; vs=(v/v.shift(1).rolling(24).mean()).values>2
    return np.where((z<-k)&vs,1,np.where((z>k)&vs,-1,0)),H,0,0.05,None,None
def s8(F,p):
    W,k=p; c=F['cs']; m=c.rolling(W).mean(); s=c.rolling(W).std(); z=((c-m)/s).values
    side=np.abs(F['slope'])<0.01
    return np.where(side&(z<-k),1,np.where(side&(z>k),-1,0)),24,0,0.03,z>=0,z<=0
def s9(F,p):
    P,h=p; hr=F['hour']; fp=F['fpct']
    pre=np.isin((hr+h+1)%24,[0,8,16])  # signal bar open hour = settlement-h-1 -> fill at settlement-h, exit at settlement
    return np.where(pre&(fp>=P),-1,np.where(pre&(fp<=100-P),1,0)),h,0,0.03,None,None
def s10(F,p):
    st,L=p; hr=F['hour']; sig=((hr+1)%24==st)
    return np.where(sig&(F['slope']>0),1,np.where(sig&(F['slope']<0),-1,0)),L,0,0.03,None,None
R={'6':('R6 BTC-lead',[(6,12,24,48),(3,6,12,24)],s6,99.17),
   '7':('R7 capitulation reversal',[(2.5,3.0,3.5,4.0),(6,12,24,48)],s7,99.29),
   '8':('R8 SIDE mean reversion',[(24,48,96,168),(1.5,2.0,2.5,3.0)],s8,99.375),
   '9':('R9 funding-settlement timing',[(70,80,90,95),(1,2,3,4)],s9,99.44),
   '10':('R10 trend time-of-day',[tuple(range(24)),(2,4,6)],s10,99.5)}
nm,G,s,thr=R[which]; out=research(SP,nm,G,s,thr,seed=int(which))
json.dump(out,open(SP+f'/round{which}.json','w'),default=str)
