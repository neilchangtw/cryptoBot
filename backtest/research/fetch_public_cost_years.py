"""依固定回測窗口逐年抓公開funding與1h mark-price，保留原始回應及下載稽核。"""
import argparse
import gzip
import hashlib
import json
import time
from pathlib import Path
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data'/'public_cost_history_20260908'
WINDOWS={'year1':('2024-09-08 11:00','2025-09-08 11:00'),
         'year2':('2025-09-08 11:00','2026-09-08 10:00')}
COLS=['open_time','open','high','low','close','ignore_volume','close_time','ignore_quote',
      'ignore_trades','ignore_buy','ignore_buy_quote','ignore']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def stamp(s): return pd.Timestamp(s,tz='Asia/Taipei')
def ms(t): return int(t.timestamp()*1000)

def validate(fund,mark,start,end):
    for f,k in [(fund,'fundingTime'),(mark,'open_time')]:
        assert f[k].is_unique and f[k].is_monotonic_increasing
        assert f[k].between(ms(start),ms(end)-1).all()
    actual=pd.to_datetime(fund.fundingTime,unit='ms',utc=True).dt.tz_convert('Asia/Taipei')
    nominal=actual.dt.round('h')
    assert (actual-nominal).abs().max()<=pd.Timedelta(seconds=60)
    expected=pd.date_range(start.floor('D'),end,freq='8h',inclusive='left')
    expected=expected[(expected>=start)&(expected<end)]
    assert nominal.tolist()==expected.tolist(),'Funding完整性未通過，檢查結算間隔或缺口'
    assert pd.to_numeric(fund.markPrice,errors='raise').gt(0).all()
    assert pd.to_numeric(fund.fundingRate,errors='raise').notna().all()
    expected_mark=[ms(x) for x in pd.date_range(start,end,freq='h',inclusive='left')]
    assert mark.open_time.tolist()==expected_mark,'Mark-price K線缺漏或時間錯位'
    for c in ['open','high','low','close']:
        mark[c]=pd.to_numeric(mark[c],errors='raise'); assert mark[c].gt(0).all()
    assert (mark.high>=mark[['open','low','close']].max(axis=1)).all()
    assert (mark.low<=mark[['open','high','close']].min(axis=1)).all()
    assert (mark.close_time==mark.open_time+3_600_000-1).all()
    return {'funding_rows':len(fund),'mark_1h_rows':len(mark),'hourly_continuity':'PASS',
            'funding_8h_continuity':'PASS','funding_mark_price':'PASS','ohlc':'PASS'}

def download(year):
    start,end=map(stamp,WINDOWS[year]); folder=OUT/year
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'manifest.json').exists():
        saved=json.loads((folder/'manifest.json').read_text())
        assert all(sha(folder/n)==h for n,h in saved['sha256'].items())
        print(json.dumps({'status':'cached_verified','year':year,'manifest':saved}),flush=True); return
    session=requests.Session(); session.headers['Accept-Encoding']='identity'
    log=[]; frames={}; started=time.perf_counter()
    for name,endpoint,key in [('funding','fundingRate','fundingTime'),('mark_1h','markPriceKlines','open_time')]:
        cursor=ms(start); rows=[]; page=0
        while cursor<ms(end):
            page+=1
            assert page<=15,'超過預估分頁數，停止檢查'
            upper=min(ms(end)-1,cursor+1000*3_600_000-1) if name=='mark_1h' else ms(end)-1
            params={'symbol':'ETHUSDT','startTime':cursor,'endTime':upper,'limit':1000}
            if name=='mark_1h': params['interval']='1h'
            begin=time.perf_counter()
            with session.get('https://fapi.binance.com/fapi/v1/'+endpoint,params=params,timeout=(10,30),stream=True) as r:
                r.raise_for_status()
                raw=r.raw.read(1_000_001,decode_content=False)
                assert len(raw)<=1_000_000,'單頁超過1MB，停止'
                elapsed=time.perf_counter()-begin
                encoding=r.headers.get('Content-Encoding','identity')
                assert encoding in ['identity','gzip']
                body=gzip.decompress(raw) if encoding=='gzip' else raw
            batch=json.loads(body); assert isinstance(batch,list)
            rawfile=folder/f'{name}_page{page:02}.json'; rawfile.write_bytes(body)
            log.append({'dataset':name,'page':page,'rows':len(batch),'seconds':elapsed,
                        'body_bytes':len(raw),'params':params,'raw_sha256':sha(rawfile)})
            (folder/'progress.json').write_text(json.dumps(log,indent=2),encoding='utf-8')
            if not batch: break
            rows.extend(batch)
            last=int(batch[-1]['fundingTime'] if name=='funding' else batch[-1][0])
            nxt=last+1 if name=='funding' else last+3_600_000
            assert nxt>cursor
            cursor=nxt
            print(f'{year} {name} page={page} rows={len(batch)} time={elapsed:.3f}s bytes={len(raw)}',flush=True)
            if name=='funding' and len(batch)<1000: break
            if cursor<ms(end): time.sleep(.15)
        frames[name]=pd.DataFrame(rows) if name=='funding' else pd.DataFrame(rows,columns=COLS)
    checks=validate(frames['funding'],frames['mark_1h'],start,end)
    for name,frame in frames.items(): frame.to_csv(folder/f'{name}.csv',index=False)
    # 以匯出後重讀資料再次核對。
    validate(pd.read_csv(folder/'funding.csv'),pd.read_csv(folder/'mark_1h.csv'),start,end)
    summary={'year':year,'start_taipei':str(start),'end_exclusive_taipei':str(end),
             'checks':checks,'request_count':len(log),'request_seconds':sum(x['seconds'] for x in log),
             'download_validate_save_seconds':time.perf_counter()-started,
             'response_body_bytes':sum(x['body_bytes'] for x in log),
             'csv_bytes':sum((folder/f'{n}.csv').stat().st_size for n in frames),
             'sha256':{f'{n}.csv':sha(folder/f'{n}.csv') for n in frames},
             'notes':'HTTP body only, excludes headers/TLS; pauses included in total; no automatic retries'}
    (folder/'manifest.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)

def merge():
    manifests=[]
    for y in WINDOWS:
        p=OUT/y; m=json.loads((p/'manifest.json').read_text())
        assert all(sha(p/n)==h for n,h in m['sha256'].items()); manifests.append(m)
    f=pd.concat([pd.read_csv(OUT/y/'funding.csv') for y in WINDOWS],ignore_index=True)
    k=pd.concat([pd.read_csv(OUT/y/'mark_1h.csv') for y in WINDOWS],ignore_index=True)
    checks=validate(f,k,stamp(WINDOWS['year1'][0]),stamp(WINDOWS['year2'][1]))
    candles=ROOT/'data'/'maxhold_review_20260908'/'candles.csv'
    candle_dt=pd.to_datetime(pd.read_csv(candles).datetime).dt.tz_localize('Asia/Taipei')
    assert k.open_time.tolist()==[ms(x) for x in candle_dt],'與固定回測窗口不一致'
    old=pd.read_csv(ROOT/'data'/'ETHUSDT_funding.csv')
    old['nominal']=pd.to_datetime(old.funding_time_ms,unit='ms',utc=True).dt.round('h')
    f['nominal']=pd.to_datetime(f.fundingTime,unit='ms',utc=True).dt.round('h')
    common=old.merge(f,on='nominal')
    assert len(common)==1987
    assert (common.rate-common.fundingRate).abs().max()<1e-12
    f=f.drop(columns='nominal')
    f.to_csv(OUT/'funding_full.csv',index=False); k.to_csv(OUT/'mark_1h_full.csv',index=False)
    validate(pd.read_csv(OUT/'funding_full.csv'),pd.read_csv(OUT/'mark_1h_full.csv'),stamp(WINDOWS['year1'][0]),stamp(WINDOWS['year2'][1]))
    summary={'checks':checks,'old_funding_overlap_rows':len(common),'old_funding_rates_match':True,
             'years':manifests,'total_response_body_bytes':sum(m['response_body_bytes'] for m in manifests),
             'sum_download_validate_save_seconds':sum(m['download_validate_save_seconds'] for m in manifests),
             'frozen_candles_sha256':sha(candles),'sha256':{n:sha(OUT/n) for n in ['funding_full.csv','mark_1h_full.csv']}}
    (OUT/'manifest.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('part',choices=['year1','year2','merge'])
    args=parser.parse_args()
    if args.part=='merge': merge()
    else: download(args.part)
