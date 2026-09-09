"""補固定回測區間的現貨 1h；公開資料、小量下載、完整驗證後才發布。"""
from pathlib import Path
import hashlib
import json
import time
import numpy as np
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/spot_futures_sync_20260909'
SOURCE=ROOT/'data/maxhold_review_20260908/candles.csv'
URL='https://api.binance.com/api/v3/klines'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def validate(raw,expected):
    assert raw and all(len(r)==12 for r in raw)
    opens=np.array([int(r[0]) for r in raw],dtype=np.int64)
    closes=np.array([int(r[6]) for r in raw],dtype=np.int64)
    assert (closes==opens+3599999).all(),'Invalid close timestamp'
    times=pd.to_datetime(opens,unit='ms',utc=True).tz_convert('Asia/Taipei').tz_localize(None)
    assert times.equals(pd.DatetimeIndex(expected)),'Missing, duplicate or shifted spot hours'
    assert closes.max()<pd.Timestamp.now(tz='UTC').value//1000000,'Unclosed candle'
    f=pd.DataFrame({'datetime':times,**{n:[float(r[i]) for r in raw] for n,i in
        [('open',1),('high',2),('low',3),('close',4),('volume',5),('taker_buy_volume',9)]}})
    assert np.isfinite(f.drop(columns='datetime').to_numpy()).all()
    assert (f[['open','high','low','close']]>0).all().all()
    assert (f.high>=f[['open','close','low']].max(axis=1)).all()
    assert (f.low<=f[['open','close','high']].min(axis=1)).all()
    assert ((f.volume>=0)&(f.taker_buy_volume>=0)&(f.taker_buy_volume<=f.volume)).all()
    return f

def main():
    OUT.mkdir(exist_ok=True);(OUT/'raw').mkdir(exist_ok=True)
    d=pd.read_csv(SOURCE,parse_dates=['datetime']);source_hash=sha(SOURCE)
    start=int(d.datetime.iloc[0].tz_localize('Asia/Taipei').tz_convert('UTC').value//1000000)
    end=int(d.datetime.iloc[-1].tz_localize('Asia/Taipei').tz_convert('UTC').value//1000000)+3599999
    allrows=[];requests_log=[];cursor=start;clock=time.perf_counter()
    with requests.Session() as session:
        while cursor<=end:
            params={'symbol':'ETHUSDT','interval':'1h','startTime':cursor,'endTime':end,'limit':1000}
            p=OUT/'raw'/f'{cursor}.json';cached=p.exists();t=time.perf_counter()
            if not cached:
                response=session.get(URL,params=params,timeout=(10,30))
                response.raise_for_status();rows=response.json()
                assert isinstance(rows,list) and 0<len(rows)<=1000
                p.write_bytes(response.content)
            else:rows=json.loads(p.read_text())
            assert rows and rows[0][0]==cursor
            assert all(rows[i][0]==cursor+i*3600000 for i in range(len(rows)))
            assert rows[-1][6]<=end
            requests_log.append({'params':params,'cached':cached,'bytes':p.stat().st_size,
                'seconds':time.perf_counter()-t,'sha256':sha(p),'file':str(p.relative_to(OUT))})
            allrows.extend(rows);cursor=int(rows[-1][0])+3600000
            print(json.dumps({'rows':len(allrows),'cached':cached}),flush=True)
    f=validate(allrows,d.datetime);assert sha(SOURCE)==source_hash
    f.to_csv(OUT/'spot_1h.csv',index=False)
    manifest={'url':URL,'source_sha256':source_hash,'csv_sha256':sha(OUT/'spot_1h.csv'),
        'rows':len(f),'first_open_taipei':str(f.datetime.iloc[0]),'last_open_taipei':str(f.datetime.iloc[-1]),
        'downloaded_utc':str(pd.Timestamp.now(tz='UTC')),'requests':requests_log,
        'wall_seconds':time.perf_counter()-clock,'download_bytes':sum(x['bytes'] for x in requests_log if not x['cached'])}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k!='requests'}),flush=True)

if __name__=='__main__':main()
