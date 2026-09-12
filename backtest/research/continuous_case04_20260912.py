"""S: capped public block samples; no candidate return computation."""
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
import requests
import continuous_strategy_20260912 as c

NUMBER=4;ID='S_onchain_basefee';NAME='鏈上需求資訊'
PREREG='doc/continuous_round04_onchain_20260912.md';TEST='tests/test_continuous_case04_20260912.py'
NEIGHBORS=[5,10]
DATES=['2024-09-07','2025-09-01','2026-09-06']
BASE='https://eth.blockscout.com'
LIMIT=100_000

def validate_pair(block,following,reference):
    before=pd.Timestamp(block['timestamp']);after=pd.Timestamp(following['timestamp']);target=pd.Timestamp(reference)
    assert before.tzinfo is not None and after.tzinfo is not None and target.tzinfo is not None
    assert before<=target<after
    assert int(following['height'])==int(block['height'])+1
    for value in [block,following]:
        assert re.fullmatch(r'0x[0-9a-fA-F]{64}',value['hash'])
        assert int(value['base_fee_per_gas'])>0 and int(value['gas_limit'])>0
        assert 0<=int(value['gas_used'])<=int(value['gas_limit'])
    return {'bracket':'PASS','reference':str(target),'block_timestamp':str(before),'next_timestamp':str(after),
        'block_height':int(block['height']),'base_fee_per_gas':int(block['base_fee_per_gas'])}

def historical_available(block,deadline):
    # Only explicit historically scoped evidence may pass. A current finality flag
    # is deliberately not accepted as a historical receipt or checkpoint proof.
    finalized=block.get('historical_finalized_at');available=block.get('historical_available_at')
    if not finalized or not available or not block.get('historical_evidence_uri'):return False
    a,z=pd.Timestamp(available),pd.Timestamp(finalized);deadline=pd.Timestamp(deadline)
    return a.tzinfo is not None and z.tzinfo is not None and a<=deadline and z<=deadline

def sample():
    case=__import__(__name__);reg,out=c.register(case)
    assert not (out/'diagnostic.json').exists()
    rawdir=out/'samples';rawdir.mkdir(exist_ok=True)
    attempt=len(list(out.glob('sample_attempt_*.json')))+1
    record={'utc':c.now(),'attempt':attempt,'requests':[],'total_response_bytes':0,'candidate_pnl_trials':0}
    session=requests.Session();session.headers['User-Agent']='CryptoBot-local-research/1.0'
    def get(url,filename,params=None):
        path=rawdir/filename
        if path.exists():
            body=path.read_bytes();return json.loads(body)
        with session.get(url,params=params,timeout=15,stream=True) as response:
            body=b''
            for chunk in response.iter_content(8192):
                body+=chunk
                assert record['total_response_bytes']+len(body)<=LIMIT,'Response cap reached'
            record['total_response_bytes']+=len(body)
            record['requests'].append({'url':response.url,'status':response.status_code,'received_utc':c.now(),'bytes':len(body),'file':c.rel(path)})
            response.raise_for_status()
            value=json.loads(body)
            path.write_bytes(body);record['requests'][-1]['sha256']=c.sha(path)
            return value
    checks=[]
    try:
        for date in DATES:
            reference=pd.Timestamp(date,tz='UTC')
            lookup=get(BASE+'/api/',f'{date}_lookup.json',{'module':'block','action':'getblocknobytime','timestamp':int(reference.timestamp()),'closest':'before'})
            assert lookup.get('status')=='1', 'Lookup source did not confirm historical block'
            height=int(lookup['result']['blockNumber'])
            block=get(BASE+f'/api/v2/blocks/{height}',f'{date}_block.json')
            following=get(BASE+f'/api/v2/blocks/{height+1}',f'{date}_next.json')
            checked=validate_pair(block,following,reference)
            checked['historical_timing_verified']=historical_available(block,reference+pd.Timedelta(days=1))
            checked['observed_schema']=sorted(block)
            checks.append(checked)
    except requests.exceptions.RequestException as exc:
        record['error_type']=type(exc).__name__;record['status']='NETWORK_OR_HTTP_LIMITED'
        c.dump(out/f'sample_attempt_{attempt}.json',record)
        print(json.dumps({'status':record['status'],'error_type':record['error_type'],'download_bytes':record['total_response_bytes']}))
        raise SystemExit(2)
    except (AssertionError,KeyError,ValueError) as exc:
        record['error_type']=type(exc).__name__;record['reason']=str(exc);record['status']='DATA_LIMITED'
    else:
        record['status']='SOURCE_SAMPLE_READY' if all(x['historical_timing_verified'] for x in checks) else 'DATA_LIMITED'
    record['sample_checks']=checks
    c.dump(out/f'sample_attempt_{attempt}.json',record)
    result={'utc':c.now(),'id':ID,'status':record['status'],'counts':None,'candidate_pnl_trials':0,
        'sample_checks':checks,'download_bytes_this_attempt':record['total_response_bytes'],
        'reason':'Current canonical block metadata does not establish historical finality/availability; no full-period download or payoff test.' if record['status']=='DATA_LIMITED' else 'Further full-period data contract needed',
        'protected_verified':c.verify(reg['protected_sha256'])}
    c.dump(out/'diagnostic.json',result)
    print(json.dumps({'id':ID,'status':result['status'],'samples_checked':len(checks),'response_bytes':record['total_response_bytes'],'candidate_pnl_trials':0}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['sample'],required=True);p.parse_args();sample()
