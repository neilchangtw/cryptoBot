"""
EMA50 改善：5 個方案（P1~P5）
基底：1h Swing突破 + FR過濾 + EMA50 trailing
"""
import requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
import time as _time, warnings
warnings.filterwarnings("ignore")

MARGIN=100;LEVERAGE=20;SAFENET_PCT=0.03;MAX_SAME=2

def fetch(interval,days=365):
    all_d=[];end=int(datetime.now().timestamp()*1000);cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":"BTCUSDT","interval":interval,"startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_d.extend(d);cur=d[-1][0]+1;_time.sleep(0.1)
        except:break
    if not all_d:return pd.DataFrame()
    df=pd.DataFrame(all_d,columns=["ot","open","high","low","close","volume","ct","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]:df[c]=pd.to_numeric(df[c])
    df["datetime"]=pd.to_datetime(df["ot"],unit="ms")+timedelta(hours=8)
    return df

def fetch_funding(days=365):
    all_fr=[];cur=int((datetime.now()-timedelta(days=days)).timestamp()*1000);end=int(datetime.now().timestamp()*1000)
    while cur<end:
        try:
            r=requests.get("https://fapi.binance.com/fapi/v1/fundingRate",params={"symbol":"BTCUSDT","startTime":cur,"limit":1000},timeout=10)
            d=r.json()
            if not d:break
            all_fr.extend(d);cur=d[-1]["fundingTime"]+1;_time.sleep(0.1)
        except:break
    if not all_fr:return pd.Series(dtype=float)
    fr=pd.DataFrame(all_fr);fr["dt"]=pd.to_datetime(fr["fundingTime"],unit="ms")+timedelta(hours=8)
    fr["rate"]=pd.to_numeric(fr["fundingRate"])
    return fr.set_index("dt")["rate"].resample("1h").ffill()

print("Fetching...", end=" ", flush=True)
raw=fetch("1h",365);fr_s=fetch_funding(365)
df=raw.copy()
tr=pd.DataFrame({"hl":df["high"]-df["low"],"hc":abs(df["high"]-df["close"].shift(1)),"lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
df["atr"]=tr.rolling(14).mean()
df["ema50"]=df["close"].ewm(span=50).mean()
df["vol_ma20"]=df["volume"].rolling(20).mean();df["vol_ratio"]=df["volume"]/df["vol_ma20"]
df["bb_mid"]=df["close"].rolling(20).mean();bbs=df["close"].rolling(20).std()
df["bb_width"]=(df["bb_mid"]+2*bbs-(df["bb_mid"]-2*bbs))/df["bb_mid"]*100
df["bb_width_pctile"]=df["bb_width"].rolling(100).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
w=5;sh=pd.Series(np.nan,index=df.index);sl=pd.Series(np.nan,index=df.index)
for i in range(w,len(df)-w):
    if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max():sh.iloc[i]=df["high"].iloc[i]
    if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min():sl.iloc[i]=df["low"].iloc[i]
df["swing_hi"]=sh.ffill();df["swing_lo"]=sl.ffill()
df=df.set_index("datetime");df["fr"]=fr_s.reindex(df.index,method="ffill")
df["fr_pctile"]=df["fr"].rolling(200).apply(lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
df=df.reset_index().dropna().reset_index(drop=True)
df["month"]=df["datetime"].dt.to_period("M")
print(f"{len(df)} bars")

def run(data, confirm_bars=0, bb_max=100, min_hold=0, vol_min=0):
    """
    confirm_bars: 突破後等幾根確認（0=不等）
    bb_max: BB 寬度百分位上限（100=不過濾）
    min_hold: 最低持倉根數（0=不限）
    vol_min: 最低成交量比（0=不過濾）
    """
    lpos=[];spos=[];trades=[]
    # 追蹤突破待確認
    pending_long=[];pending_short=[]

    for i in range(60,len(data)-1):
        row=data.iloc[i];nxt=data.iloc[i+1]
        hi=row["high"];lo=row["low"];close=row["close"]

        # 更新持倉
        nl=[]
        for p in lpos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(hi-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if lo<=p["entry"]*(1-SAFENET_PCT):
                ep=p["entry"]*(1-SAFENET_PCT)-(p["entry"]*(1-SAFENET_PCT)-lo)*0.25
                pnl=(ep-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            elif bars>=max(2,min_hold) and close<=row["ema50"]:
                pnl=(close-p["entry"])*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","side":"long","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:nl.append(p)
        lpos=nl

        ns=[]
        for p in spos:
            c=False;bars=i-p["ei"]
            mf=max(p.get("mf",0),(p["entry"]-lo)*(MARGIN*LEVERAGE)/p["entry"]);p["mf"]=mf
            if hi>=p["entry"]*(1+SAFENET_PCT):
                ep=p["entry"]*(1+SAFENET_PCT)+(hi-p["entry"]*(1+SAFENET_PCT))*0.25
                pnl=(p["entry"]-ep)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"SafeNet","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            elif bars>=max(2,min_hold) and close>=row["ema50"]:
                pnl=(p["entry"]-close)*(MARGIN*LEVERAGE)/p["entry"]-1.6
                trades.append({"pnl":pnl,"type":"Trail","side":"short","bars":bars,"mf":mf,"dt":row["datetime"]}); c=True
            if not c:ns.append(p)
        spos=ns

        # Swing 突破偵測
        if i>=1:
            l_break=close>data["swing_hi"].iloc[i-1] and data["close"].iloc[i-1]<=data["swing_hi"].iloc[i-1]
            s_break=close<data["swing_lo"].iloc[i-1] and data["close"].iloc[i-1]>=data["swing_lo"].iloc[i-1]
            # FR 過濾
            fr_p=row.get("fr_pctile",50)
            if not np.isnan(fr_p):
                if fr_p>80:l_break=False
                if fr_p<20:s_break=False
            # BB 寬度過濾
            if row["bb_width_pctile"]>=bb_max:
                l_break=False;s_break=False
            # 成交量過濾
            if vol_min>0 and row["vol_ratio"]<vol_min:
                l_break=False;s_break=False

            if confirm_bars==0:
                # 不等確認，直接進
                ep=nxt["open"]
                if l_break and len(lpos)<MAX_SAME:
                    lpos.append({"entry":ep,"ei":i,"mf":0})
                if s_break and len(spos)<MAX_SAME:
                    spos.append({"entry":ep,"ei":i,"mf":0})
            else:
                # 等確認
                if l_break:pending_long.append({"bar":i,"swing":data["swing_hi"].iloc[i-1],"count":0})
                if s_break:pending_short.append({"bar":i,"swing":data["swing_lo"].iloc[i-1],"count":0})

        # 處理待確認
        if confirm_bars>0:
            new_pending_l=[]
            for p in pending_long:
                p["count"]+=1
                if close>p["swing"]:  # 仍在突破位上方
                    if p["count"]>=confirm_bars:  # 確認完成
                        ep=nxt["open"]
                        if len(lpos)<MAX_SAME:
                            lpos.append({"entry":ep,"ei":i,"mf":0})
                    else:
                        new_pending_l.append(p)
                # else: 跌回去了，假突破，放棄
            pending_long=new_pending_l

            new_pending_s=[]
            for p in pending_short:
                p["count"]+=1
                if close<p["swing"]:
                    if p["count"]>=confirm_bars:
                        ep=nxt["open"]
                        if len(spos)<MAX_SAME:
                            spos.append({"entry":ep,"ei":i,"mf":0})
                    else:
                        new_pending_s.append(p)
                # else: 漲回去了
            pending_short=new_pending_s

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","type","side","bars","mf","dt"])

def calc(tdf):
    if len(tdf)<1:return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"win_avg":0,"loss_avg":0,"rr":0,"avg_bars":0}
    pnl=tdf["pnl"].sum();wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0];l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum();dd=(cum-cum.cummax()).min()
    wa=w["pnl"].mean() if len(w)>0 else 0;la=l["pnl"].mean() if len(l)>0 else 0
    rr=abs(wa/la) if la!=0 else 999
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),"dd":round(dd,2),
            "win_avg":round(wa,2),"loss_avg":round(la,2),"rr":round(rr,2),"avg_bars":round(tdf["bars"].mean(),1)}

configs = [
    # (名稱, confirm_bars, bb_max, min_hold, vol_min)
    ("0.base(現行)", 0, 100, 0, 0),

    # P1: 等確認
    ("P1a.確認2根", 2, 100, 0, 0),
    ("P1b.確認3根", 3, 100, 0, 0),
    ("P1c.確認1根", 1, 100, 0, 0),

    # P2: BB 寬度
    ("P2a.BB<70", 0, 70, 0, 0),
    ("P2b.BB<60", 0, 60, 0, 0),
    ("P2c.BB<80", 0, 80, 0, 0),

    # P3: 最低持倉
    ("P3a.min24h", 0, 100, 24, 0),
    ("P3b.min12h", 0, 100, 12, 0),
    ("P3c.min6h", 0, 100, 6, 0),

    # P4: 成交量
    ("P4a.Vol>1.5x", 0, 100, 0, 1.5),
    ("P4b.Vol>1.2x", 0, 100, 0, 1.2),

    # P5: 組合
    ("P5a.確認2+BB70", 2, 70, 0, 0),
    ("P5b.確認2+min12h", 2, 100, 12, 0),
    ("P5c.確認2+BB70+min12h", 2, 70, 12, 0),
    ("P5d.BB70+min12h", 0, 70, 12, 0),
    ("P5e.確認2+BB70+Vol1.2", 2, 70, 0, 1.2),
    ("P5f.確認3+BB70", 3, 70, 0, 0),
]

# 全樣本
print(f"\n{'='*110}")
print(f"全樣本（1 年，{len(configs)} 組合）")
print(f"{'='*110}")

print(f"\n  {'方案':<25s} {'N':>5s} {'PnL':>8s} {'WR':>6s} {'PF':>5s} {'DD':>8s} {'Bars':>5s} {'W均':>6s} {'L均':>6s} {'RR':>5s}")
print(f"  {'-'*80}")

all_r=[]
for name,cb,bb,mh,vm in configs:
    tdf=run(df,confirm_bars=cb,bb_max=bb,min_hold=mh,vol_min=vm)
    s=calc(tdf)
    if s["n"]<3:
        print(f"  {name:<25s} {s['n']:>5d} (too few)");continue
    all_r.append({"name":name,"cb":cb,"bb":bb,"mh":mh,"vm":vm,**s})
    print(f"  {name:<25s} {s['n']:>5d} ${s['pnl']:>+6,.0f} {s['wr']:>5.1f}% {s['pf']:>4.2f} ${s['dd']:>7,.0f} "
          f"{s['avg_bars']:>4.0f}h ${s['win_avg']:>+4.0f} ${s['loss_avg']:>+4.0f} {s['rr']:>4.1f}:1")

# WF + 滾動
print(f"\n{'='*110}")
print("Walk-Forward + 滾動")
print(f"{'='*110}")

all_r.sort(key=lambda x:x["pnl"],reverse=True)
split=int(len(df)*8/12);dev=df.iloc[:split].reset_index(drop=True);oos=df.iloc[split:].reset_index(drop=True)
months=df["month"].unique()

print(f"\n  {'方案':<25s} {'Full':>7s} {'OOS':>7s} {'OOS WR':>7s} {'OOS PF':>7s} {'OOS RR':>6s} {'滾動':>8s} {'折':>5s}")
print(f"  {'-'*75}")

for r in all_r:
    to=run(oos,r["cb"],r["bb"],r["mh"],r["vm"])
    so=calc(to)
    fp=[]
    for fold in range(len(months)-3):
        tm=months[fold+3];ft=df[df["month"]==tm].reset_index(drop=True)
        if len(ft)<20:continue
        t=run(ft,r["cb"],r["bb"],r["mh"],r["vm"])
        fp.append(t["pnl"].sum() if len(t)>0 else 0)
    prof=sum(1 for x in fp if x>0)
    print(f"  {r['name']:<25s} ${r['pnl']:>+5,.0f} ${so['pnl']:>+5,.0f} {so['wr']:>6.1f}% {so['pf']:>6.2f} "
          f"{so['rr']:>5.1f}:1 ${sum(fp):>+6,.0f} {prof}/{len(fp)}")

print("\nDone.")
