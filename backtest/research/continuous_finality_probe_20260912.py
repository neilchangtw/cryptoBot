"""Bounded source qualification for S; deliberately no market data or PnL imports."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'doc/research_results/20260912_continuous_strategy/finality_probe'
BASE='https://ethereum-beacon-api.publicnode.com'
DATES=['2024-09-07','2025-09-01','2026-09-06']

def main():
    OUT.mkdir(exist_ok=True)
    prior=[p for p in OUT.glob('attempt_*.json') if re.fullmatch(r'attempt_\d+\.json',p.name)]
    if any(json.loads(p.read_text(encoding='utf-8')).get('error_type')=='HTTPError' for p in prior):
        raise SystemExit('Recorded HTTP limit: do not retry or change provider under this contract')
    attempt=len(prior)+1
    rec={'utc':datetime.now(timezone.utc).isoformat(),'candidate_pnl_trials':0,'bytes':0,'requests':[],
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'prereg_sha256':hashlib.sha256((ROOT/'doc/continuous_finality_probe_20260912.md').read_bytes()).hexdigest()}
    def get(path):
        assert len(rec['requests'])<4
        target=OUT/f'attempt_{attempt}_response_{len(rec["requests"])+1}.json'
        if target.exists():
            body=target.read_bytes()
            rec['requests'].append({'url':BASE+path,'bytes':len(body),'cached_after_parser_failure':True,
                'file':str(target.relative_to(ROOT)),'sha256':hashlib.sha256(body).hexdigest()})
            return json.loads(body)
        with requests.get(BASE+path,timeout=15,stream=True) as response:
            body=b''
            for chunk in response.iter_content(4096):
                body+=chunk
                assert rec['bytes']+len(body)<=32000,'response cap'
            rec['bytes']+=len(body)
            target.write_bytes(body)
            rec['requests'].append({'url':response.url,'status':response.status_code,'bytes':len(body),
                'received_utc':datetime.now(timezone.utc).isoformat(),'file':str(target.relative_to(ROOT)),
                'sha256':hashlib.sha256(body).hexdigest()})
            response.raise_for_status()
            return json.loads(body)
    try:
        genesis=int(get('/eth/v1/beacon/genesis')['data']['genesis_time'])
        rec['genesis_time']=genesis
        for date in DATES:
            target=datetime.fromisoformat(date).replace(tzinfo=timezone.utc)+timedelta(hours=23)
            slot=(int(target.timestamp())-genesis)//12
            response=get(f'/eth/v1/beacon/states/{slot}/finality_checkpoints')
            assert response.get('execution_optimistic') is False
            value=response['data']['finalized']
            assert int(value['epoch'])>0 and len(value['root'])==66
        rec['status']='SOURCE_SAMPLE_READY_NEEDS_EXECUTION_HASH_LINK'
    except requests.exceptions.RequestException as exc:
        rec['status']='DATA_LIMITED';rec['error_type']=type(exc).__name__
    except (AssertionError,ValueError,KeyError) as exc:
        rec['status']='DATA_LIMITED';rec['error_type']=type(exc).__name__;rec['reason']=str(exc)
    (OUT/f'attempt_{attempt}.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:rec.get(k) for k in ['status','bytes','error_type','candidate_pnl_trials']}))
    if rec.get('error_type')=='ProxyError':raise SystemExit(2)

if __name__=='__main__':main()
