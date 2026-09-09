"""試抓三天、一個月或三個月公開5m行情，核對既有1h；不自動續抓。"""
from pathlib import Path
import argparse
import hashlib
import json
import time
import numpy as np
import pandas as pd
import requests

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser()
    periods=parser.add_mutually_exclusive_group()
    periods.add_argument('--month',action='store_true',help='固定回測起點起一個月')
    periods.add_argument('--three-months',action='store_true',help='固定回測起點起三個月')
    parser.add_argument('--reuse-existing',action='store_true',help='重用既有試抓原始檔，只補缺少時間')
    args=parser.parse_args()
    start=pd.Timestamp('2024-09-08 11:00',tz='Asia/Taipei')
    months=3 if args.three_months else (1 if args.month else 0)
    end=start+pd.DateOffset(months=months) if months else start+pd.Timedelta(days=3)
    expected=pd.date_range(start,end,freq='5min',inclusive='left')
    folder='eth_5m_three_month_benchmark' if args.three_months else ('eth_5m_month_benchmark' if args.month else 'eth_5m_three_day_benchmark')
    out=ROOT/'data'/folder/pd.Timestamp.now(tz='UTC').strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True,exist_ok=False)
    summary={'symbol':'ETHUSDT','interval':'5m','start':str(start),'end_exclusive':str(end),
             'expected_rows':len(expected),'requests':0,'automatic_retries':0,'pages':[]}
    wall=time.perf_counter()
    try:
        cached={}; sources=[]
        expected_ms=[int(t.timestamp()*1000) for t in expected]
        expected_set=set(expected_ms)
        if args.reuse_existing:
            for cache_folder in ['eth_5m_three_day_benchmark','eth_5m_month_benchmark','eth_5m_three_month_benchmark']:
                for path in sorted((ROOT/'data'/cache_folder).glob('*/raw*.json')):
                    raw_cache=path.read_bytes(); saved=json.loads(raw_cache)
                    assert isinstance(saved,list)
                    used=0
                    for row in saved:
                        assert len(row)==12 and row[6]==row[0]+299999
                        if row[0] not in expected_set:continue
                        if row[0] in cached:
                            assert cached[row[0]]==row,'Conflicting cached candle'
                        else:cached[row[0]]=row
                        used+=1
                    if used:sources.append({'path':str(path.relative_to(ROOT)),'rows_in_range':used,
                                             'sha256':hashlib.sha256(raw_cache).hexdigest()})
        missing=expected[[t not in cached for t in expected_ms]]
        summary.update(reused_rows=len(cached),downloaded_rows=0,reused_sources=sources)
        # 不讓分頁跨越已快取時段，避免API重複傳送。
        pages=[]; batch=[]
        for t in missing:
            if batch and (len(batch)==1000 or t-batch[-1]!=pd.Timedelta(minutes=5)):
                pages.append(batch); batch=[]
            batch.append(t)
        if batch:pages.append(batch)
        total_bytes=0; request_seconds=0.
        with requests.Session() as session:
            for page_times in pages:
                before=time.perf_counter()
                with session.get('https://fapi.binance.com/fapi/v1/klines',
                                 params={'symbol':'ETHUSDT','interval':'5m','startTime':int(page_times[0].timestamp()*1000),
                                         'endTime':int((page_times[-1]+pd.Timedelta(minutes=5)).timestamp()*1000)-1,'limit':1000},
                                 headers={'Accept-Encoding':'identity'},timeout=(10,30),stream=True) as r:
                    r.raise_for_status()
                    raw=r.raw.read(1_000_001,decode_content=False)
                    elapsed=time.perf_counter()-before
                    assert len(raw)<=1_000_000,'Response exceeds 1MB cap'
                    assert r.headers.get('Content-Encoding','identity')=='identity','Unexpected compression'
                    page=json.loads(raw)
                assert isinstance(page,list) and len(page)==len(page_times)
                assert [x[0] for x in page]==[int(t.timestamp()*1000) for t in page_times]
                number=len(summary['pages'])+1
                (out/f'raw_{number:02d}.json').write_bytes(raw)
                item={'page':number,'rows':len(page),'seconds':elapsed,'body_bytes':len(raw),
                      'sha256':hashlib.sha256(raw).hexdigest()}
                summary['pages'].append(item); summary['requests']=number
                for row in page:
                    assert row[0] not in cached
                    cached[row[0]]=row
                summary['downloaded_rows']+=len(page)
                total_bytes+=len(raw); request_seconds+=elapsed
                print(json.dumps(item),flush=True)
        summary['request_seconds']=request_seconds
        rows=[cached[t] for t in expected_ms]
        cols=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_buy_volume','taker_buy_quote','ignore']
        d=pd.DataFrame(rows,columns=cols)
        assert len(d)==len(expected)
        assert d.open_time.tolist()==[int(t.timestamp()*1000) for t in expected]
        assert (d.close_time==d.open_time+299999).all()
        for col in ['open','high','low','close','volume','taker_buy_volume']:
            d[col]=pd.to_numeric(d[col])
            assert np.isfinite(d[col]).all()
        assert (d[['open','high','low','close']]>0).all().all()
        assert (d.high>=d[['open','close','low']].max(axis=1)).all()
        assert (d.low<=d[['open','close','high']].min(axis=1)).all()
        assert (d.volume>=0).all() and d.taker_buy_volume.between(0,d.volume).all()
        d['datetime']=pd.to_datetime(d.open_time,unit='ms',utc=True).dt.tz_convert('Asia/Taipei').dt.tz_localize(None)
        slim=d[['open','high','low','close','volume','taker_buy_volume','datetime']]
        slim.to_csv(out/'ETHUSDT_5m.csv',index=False)
        agg=slim.set_index('datetime').resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','taker_buy_volume':'sum'})
        baseline=pd.read_csv(ROOT/'data/maxhold_review_20260908/candles.csv',parse_dates=['datetime']).set_index('datetime')
        ref=baseline.reindex(agg.index)
        assert len(agg)==len(expected)//12 and ref.notna().all().all()
        diff=(agg-ref[agg.columns]).abs().max()
        summary['max_abs_1h_difference']=diff.to_dict()
        assert np.allclose(agg.to_numpy(),ref[agg.columns].to_numpy(),rtol=0,atol=1e-7),'1h aggregate mismatch'
        summary.update(rows=len(d),body_bytes=total_bytes,csv_bytes=(out/'ETHUSDT_5m.csv').stat().st_size,
                       csv_sha256=hashlib.sha256((out/'ETHUSDT_5m.csv').read_bytes()).hexdigest(),validation='PASS',matched_1h_bars=len(agg),
                       download_validate_save_seconds=time.perf_counter()-wall,
                       note='Body bytes exclude headers/TLS. Single historical trial; no full-history download.')
    except Exception as exc:
        summary['error_type']=type(exc).__name__
        summary['elapsed_seconds']=time.perf_counter()-wall
        raise
    finally:
        (out/'benchmark.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary),flush=True)
        print(out,flush=True)


if __name__=='__main__':main()
