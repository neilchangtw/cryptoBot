"""Audit local execution evidence; never manufacture passive limit fills."""
import csv
import json
from pathlib import Path
import bounded_strategy_20260912 as b

ROOT=b.ROOT;DOC=b.DOC
PLAN=ROOT/'doc/strategy_bounded_round3_20260912.md'

def main():
    reg=b.register()
    assert b.read(DOC/'round2_diagnostic.json')['status']=='INSUFFICIENT_SAMPLE'
    inputs={b.rel(p):b.sha(p) for p in [PLAN,Path(__file__),DOC/'round2_diagnostic.json']}
    rp=DOC/'round3_registration.json'
    if rp.exists():b.verify(b.read(rp)['sha256'])
    else:b.dump(rp,{'utc':b.now(),'round':3,'main':'I_passive_5s','sha256':inputs,'candidate_pnl_trials_before':0})
    inventory=[];execution_names=[];groups={}
    roots=[ROOT/n for n in ['data','data_live','logs','cache'] if (ROOT/n).is_dir()]
    keywords=['orderbook','order_book','bookticker','aggtrade','fills','execution_report','latency','depth','commission']
    for root in roots:
        for p in root.rglob('*'):
            if not p.is_file():continue
            if any(x in p.name.lower() for x in keywords):execution_names.append({'path':b.rel(p),'bytes':p.stat().st_size})
            if p.suffix.lower()!='.csv':continue
            with p.open(encoding='utf-8-sig',errors='replace') as f:
                header=f.readline(16384).strip()
            columns=next(csv.reader([header]),[])
            low=[x.lower() for x in columns]
            has_receive=any('receiv' in x for x in low)
            has_book=any(x in ['bid','ask','best_bid','best_ask','bids','asks'] for x in low)
            has_order=any(x in ['order_sent_ts','order_sent','ack_ts','cancel_ack_ts','fill_ts'] for x in low)
            item={'path':b.rel(p),'bytes':p.stat().st_size,'columns':columns,'has_receive':has_receive,'has_book':has_book,'has_order_timing':has_order}
            inventory.append(item)
            key=tuple(columns);groups.setdefault(key,[]).append(b.rel(p))
    b.dump(DOC/'round3_local_schema_inventory.json',{'utc':b.now(),'roots':[b.rel(p) for p in roots],
        'csv_files':len(inventory),'csv_schemas':len(groups),'files':inventory,'execution_named_files':execution_names,
        'method':'Read CSV header only; filename inventory for JSON/raw; no credentials or account reads; not an internet availability claim.'})
    found=[x for x in inventory if x['has_receive'] and (x['has_book'] or x['has_order_timing'])]
    # Discovery is not proof of complete sequence, queue reconstruction, or received timestamps.
    status='REQUIRES_SCHEMA_REVIEW' if found else 'DATA_LIMITED'
    result={'utc':b.now(),'round':3,'name':'I_passive_5s','status':status,'candidate_pnl_trials':0,'counts':None,
        'csv_files_checked':len(inventory),'schemas_checked':len(groups),'plausible_execution_sources':found,
        'missing':['historical received timestamps with continuous bid/ask depth sequence','initial queue and subsequent trade/queue depletion',
                   'signal/order/ack/cancel/fill chronology and partial-fill reconciliation'],
        'reason':'Available kline, funding, market metrics and research ledgers cannot establish passive queue fill or historical latency.',
        'network_requests':0,'download_bytes':0,'protected_verified':b.verify(reg['protected_sha256'])}
    p=DOC/'round3_diagnostic.json';assert not p.exists();b.dump(p,result);print(json.dumps({k:v for k,v in result.items() if k!='missing'}))

if __name__=='__main__':main()
