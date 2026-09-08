"""只抓台灣時間2026年8月的公開funding及1h mark-price，量測時間與回應大小。"""
from pathlib import Path
import datetime as dt
import gzip
import hashlib
import json
import time
import requests
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]

def main():
    start=pd.Timestamp('2026-08-01',tz='Asia/Taipei')
    end=pd.Timestamp('2026-09-01',tz='Asia/Taipei')
    params=dict(symbol='ETHUSDT',startTime=int(start.timestamp()*1000),endTime=int(end.timestamp()*1000)-1,limit=1000)
    out=ROOT/'data'/'public_month_benchmark'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    summary={'start_taipei':str(start),'end_exclusive_taipei':str(end),'requests':[]}
    session=requests.Session()
    session.headers['Accept-Encoding']='identity'
    wall=time.perf_counter()
    for name,endpoint,extra in [('funding','fundingRate',{}),('mark_1h','markPriceKlines',{'interval':'1h'})]:
        before=time.perf_counter()
        try:
            with session.get('https://fapi.binance.com/fapi/v1/'+endpoint,params={**params,**extra},timeout=(10,30),stream=True) as r:
                r.raise_for_status()
                raw=r.raw.read(1_000_001,decode_content=False)
                assert len(raw)<=1_000_000,'單次回應超過1MB，停止'
                elapsed=time.perf_counter()-before
                encoding=r.headers.get('Content-Encoding','identity')
                body=gzip.decompress(raw) if encoding=='gzip' else raw
                assert encoding in ['gzip','identity'],'未支援的回應壓縮'
                rows=json.loads(body)
            assert isinstance(rows,list) and 0<len(rows)<1000,'分頁或資料異常，停止'
            (out/(name+'_raw.json')).write_bytes(body)
            if name=='funding':
                frame=pd.DataFrame(rows)
                timestamps=pd.to_numeric(frame.fundingTime)
                assert pd.to_numeric(frame.markPrice).gt(0).all()
                assert pd.to_numeric(frame.fundingRate).notna().all()
                nominal=pd.to_datetime(timestamps,unit='ms',utc=True).dt.round('h')
                expected=pd.date_range(start.tz_convert('UTC'),end.tz_convert('UTC'),freq='8h',inclusive='left')
                assert nominal.tolist()==expected.tolist(),'funding非預期8h序列，需檢查'
            else:
                frame=pd.DataFrame(rows,columns=['open_time','open','high','low','close','ignore_volume','close_time','ignore_quote','ignore_trades','ignore_buy','ignore_buy_quote','ignore'])
                timestamps=frame.open_time
                expected=pd.date_range(start,end,freq='h',inclusive='left')
                assert timestamps.tolist()==[int(x.timestamp()*1000) for x in expected],'mark K線缺漏'
                for col in ['open','high','low','close']:
                    frame[col]=pd.to_numeric(frame[col]); assert frame[col].gt(0).all()
                assert (frame.high>=frame[['open','close','low']].max(axis=1)).all()
                assert (frame.low<=frame[['open','close','high']].min(axis=1)).all()
                assert (frame.close_time<params['endTime']+1).all()
            assert timestamps.is_unique and timestamps.is_monotonic_increasing
            assert timestamps.between(params['startTime'],params['endTime']).all()
            frame.to_csv(out/(name+'.csv'),index=False)
            item={'name':name,'rows':len(frame),'request_seconds':elapsed,'body_bytes_on_wire':len(raw),
                  'decoded_json_bytes':len(body),'csv_bytes':(out/(name+'.csv')).stat().st_size,
                  'encoding':encoding,'sha256':hashlib.sha256(body).hexdigest(),'validation':'PASS'}
            summary['requests'].append(item)
            print(json.dumps(item),flush=True)
        except Exception as exc:
            summary['error']={'type':type(exc).__name__,'stage':name,'seconds':time.perf_counter()-before}
            (out/'benchmark.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
            print(json.dumps(summary['error']),flush=True)
            raise
    summary['download_validate_save_seconds']=time.perf_counter()-wall
    summary['total_body_bytes_on_wire']=sum(x['body_bytes_on_wire'] for x in summary['requests'])
    summary['total_csv_bytes']=sum(x['csv_bytes'] for x in summary['requests'])
    summary['note']='Body bytes exclude HTTP/TLS headers; single trial, no automatic retries; no full-history download.'
    (out/'benchmark.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    print(out)

if __name__=='__main__': main()
