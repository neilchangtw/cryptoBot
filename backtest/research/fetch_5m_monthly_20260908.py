"""按月補齊固定研究窗口；重用原始頁、保存差異，不修改1h基準。"""
from pathlib import Path
import hashlib
import json
import time
import os
import numpy as np
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/eth_5m_monthly_20260908'
COLS=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_volume','taker_buy_quote','ignore']
VALUES=['open','high','low','close','volume','taker_buy_volume']


def save_json(p,data):
    temp=p.with_suffix('.tmp')
    temp.write_text(json.dumps(data,indent=2),encoding='utf-8');temp.replace(p)


def frame(rows):
    d=pd.DataFrame(rows,columns=COLS)
    assert d.open_time.is_unique and d.open_time.is_monotonic_increasing
    assert (d.close_time==d.open_time+299999).all()
    for c in VALUES:d[c]=pd.to_numeric(d[c])
    assert np.isfinite(d[VALUES]).all().all()
    assert (d[['open','high','low','close']]>0).all().all()
    assert (d.high>=d[['open','low','close']].max(axis=1)).all()
    assert (d.low<=d[['open','high','close']].min(axis=1)).all()
    assert (d.volume>=0).all() and d.taker_buy_volume.between(0,d.volume).all()
    d['datetime']=pd.to_datetime(d.open_time,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
    return d[['datetime']+VALUES].set_index('datetime')


def main():
    OUT.mkdir(exist_ok=True);(OUT/'pages').mkdir(exist_ok=True)
    lock=OUT/'download.lock'; fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    os.close(fd)
    try:run()
    finally:lock.unlink()


def run():
    baseline_path=ROOT/'data/maxhold_review_20260908/candles.csv'
    source_hash=hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    base=pd.read_csv(baseline_path,parse_dates=['datetime']).set_index('datetime')
    start=base.index.min().tz_localize('Asia/Taipei'); end=(base.index.max()+pd.Timedelta(hours=1)).tz_localize('Asia/Taipei')
    assert len(base)==17519 and start==pd.Timestamp('2024-09-08 11:00',tz='Asia/Taipei')
    expected=pd.date_range(start,end,freq='5min',inclusive='left')
    stamps=[int(t.timestamp()*1000) for t in expected]; allowed=set(stamps)
    cached={}; sources=[]
    paths=[]
    for folder in ['eth_5m_three_day_benchmark','eth_5m_month_benchmark','eth_5m_three_month_benchmark']:
        paths.extend(sorted((ROOT/'data'/folder).glob('*/raw*.json')))
    paths.extend(sorted((OUT/'pages').glob('*.json')))
    for path in paths:
        raw=path.read_bytes(); rows=json.loads(raw); assert isinstance(rows,list)
        for r in rows:
            assert len(r)==12 and r[6]==r[0]+299999
            if r[0] not in allowed:continue
            if r[0] in cached:assert cached[r[0]]==r,'Conflicting raw data; manual audit required'
            cached[r[0]]=r
        sources.append({'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest()})
    manifest={'start':str(start),'end_exclusive':str(end),'expected_rows':len(stamps),'reused_rows':len(cached),
              'downloaded_rows':0,'request_seconds':0.,'body_bytes':0,'requests':0,'baseline_sha256':source_hash,
              'source_files':sources,'batches':[],'status':'RUNNING','batch_months':1,'automatic_retries':0,
              'alignment_policy':'Keep all raw rows, mark mismatched 1h invalid; never replace frozen 1h or impute features.'}
    save_json(OUT/'manifest.json',manifest)
    allframes=[]; diffs=[]; wall=time.perf_counter()
    try:
        with requests.Session() as session:
            a=start
            while a<end:
                z=min(a+pd.DateOffset(months=1),end); target=pd.date_range(a,z,freq='5min',inclusive='left')
                wanted=[int(t.timestamp()*1000) for t in target]; missing=[t for t in wanted if t not in cached]
                batch={'start':str(a),'end_exclusive':str(z),'rows':len(wanted),'reused':len(wanted)-len(missing),
                       'downloaded':0,'requests':0,'seconds':0.,'bytes':0}
                chunks=[];chunk=[]
                for t in missing:
                    if chunk and (len(chunk)==1000 or t-chunk[-1]!=300000):chunks.append(chunk);chunk=[]
                    chunk.append(t)
                if chunk:chunks.append(chunk)
                for part in chunks:
                    before=time.perf_counter()
                    with session.get('https://fapi.binance.com/fapi/v1/klines',params={
                        'symbol':'ETHUSDT','interval':'5m','startTime':part[0],'endTime':part[-1]+299999,'limit':1000},
                        headers={'Accept-Encoding':'identity'},timeout=(10,30),stream=True) as r:
                        r.raise_for_status();raw=r.raw.read(1_000_001,decode_content=False)
                        assert len(raw)<=1000000 and r.headers.get('Content-Encoding','identity')=='identity'
                    elapsed=time.perf_counter()-before;rows=json.loads(raw)
                    assert [r[0] for r in rows]==part
                    frame(rows)
                    path=OUT/'pages'/f'{part[0]}.json'
                    with path.open('xb') as output:output.write(raw)
                    for row in rows:assert row[0] not in cached;cached[row[0]]=row
                    item={'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest()}
                    manifest['source_files'].append(item)
                    batch['downloaded']+=len(rows);batch['requests']+=1;batch['seconds']+=elapsed;batch['bytes']+=len(raw)
                    manifest['downloaded_rows']+=len(rows);manifest['requests']+=1
                    manifest['request_seconds']+=elapsed;manifest['body_bytes']+=len(raw)
                    save_json(OUT/'manifest.json',manifest)
                d=frame([cached[t] for t in wanted]); assert len(d)==len(target)
                assert d.index.equals(target.tz_localize(None))
                agg=d.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','taker_buy_volume':'sum'})
                ref=base.reindex(agg.index)[VALUES]; assert ref.notna().all().all()
                delta=(agg-ref).abs(); invalid=delta.max(axis=1)>1e-7
                batch['mismatched_1h']=int(invalid.sum());batch['status']='FLAGGED' if invalid.any() else 'PASS'
                for dt in agg.index[invalid]:
                    diffs.append({'datetime':str(dt),**{'5m_'+k:float(agg.loc[dt,k]) for k in VALUES},
                                  **{'1h_'+k:float(ref.loc[dt,k]) for k in VALUES}})
                d.to_csv(OUT/f'ETHUSDT_5m_{a.strftime("%Y%m%d")}_{z.strftime("%Y%m%d")}.csv')
                batch['max_abs_difference']=delta.max().to_dict();manifest['batches'].append(batch)
                allframes.append(d);save_json(OUT/'manifest.json',manifest)
                print(json.dumps(batch),flush=True);a=z
        combined=pd.concat(allframes);assert len(combined)==len(expected) and combined.index.is_unique
        combined.to_csv(OUT/'ETHUSDT_5m_full.csv')
        quality=pd.DataFrame({'datetime':base.index,'valid_5m_alignment':True})
        bad_times=pd.to_datetime([x['datetime'] for x in diffs])
        quality.loc[quality.datetime.isin(bad_times),'valid_5m_alignment']=False
        quality.to_csv(OUT/'hour_quality.csv',index=False)
        pd.DataFrame(diffs).to_csv(OUT/'alignment_discrepancies.csv',index=False)
        assert hashlib.sha256(baseline_path.read_bytes()).hexdigest()==source_hash
        manifest.update(status='COMPLETE_WITH_FLAGS' if diffs else 'COMPLETE',rows=len(combined),
                        mismatched_1h=len(diffs),matched_1h=len(base)-len(diffs),
                        total_seconds=time.perf_counter()-wall,csv_bytes=(OUT/'ETHUSDT_5m_full.csv').stat().st_size,
                        csv_sha256=hashlib.sha256((OUT/'ETHUSDT_5m_full.csv').read_bytes()).hexdigest())
    except Exception as exc:
        manifest.update(status='STOPPED',error_type=type(exc).__name__)
        raise
    finally:save_json(OUT/'manifest.json',manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k not in ['source_files','batches']}),flush=True)


if __name__=='__main__':main()
