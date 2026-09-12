"""Local quote-currency source discovery, no invented parity series."""
import json
import re
import subprocess
from pathlib import Path
import strategy_batch2_20260912 as b

ROOT=b.ROOT;DOC=b.DOC

def parity_block(price,threshold=.001):
    import numpy as np
    x=np.asarray(price,float);valid=np.isfinite(x)&(x>0)
    score=np.full(len(x),np.nan);score[valid]=np.abs(np.log(x[valid]))
    return valid,valid&(score>threshold)

def main():
    reg=b.register_round(3,[Path(__file__),ROOT/'doc/strategy_batch2_round3_20260912.md',
        ROOT/'tests/test_strategy_batch2_quote_20260912.py',DOC/'round2_diagnostic.json'],'L_quote_parity_deviation')
    old_inventory=b.read(b.old.DOC/'round3_local_schema_inventory.json')
    pat=re.compile(r'USDC.?USDT|USDT.?USDC|USDT.?USD(?!T)|USDC.?USD(?!T)|stablecoin|depeg',re.I)
    prior=[x for x in old_inventory['files'] if pat.search(x['path']) or any(pat.search(c) for c in x['columns'])]
    # Targeted fresh filename inventory complements the frozen header audit.
    scan=subprocess.run(['rg','--files','--hidden','--no-ignore','data'],cwd=ROOT,text=True,capture_output=True)
    candidates=[p for p in scan.stdout.splitlines() if pat.search(p)]
    evidence={'utc':b.now(),'prior_inventory_sha256':b.sha(b.old.DOC/'round3_local_schema_inventory.json'),
        'prior_readable_csv_files':old_inventory['csv_files'],'prior_schemas':old_inventory['csv_schemas'],
        'prior_schema_or_path_matches':prior,'fresh_filename_count':len(scan.stdout.splitlines()),
        'fresh_named_candidates':candidates,'scan_return_code':scan.returncode,'scan_stderr':scan.stderr.strip(),
        'scope':'Known readable source inventory and fresh data filenames; inaccessible prior test temp is not claimed inspected',
        'network_requests':0,'download_bytes':0}
    b.dump(DOC/'round3_sources.json',evidence)
    found=bool(prior or candidates)
    result={'utc':b.now(),'round':3,'name':'L_quote_parity_deviation','status':'SOURCE_REVIEW_REQUIRED' if found else 'DATA_LIMITED',
        'counts':None,'candidate_pnl_trials':0,'matching_sources':len(prior)+len(candidates),
        'missing':['USDCUSDT hourly prices covering frozen ETH interval','historical receive/revision evidence'],
        'reason':'No matching quote-pair source found in readable local inventory; cannot assume p=1 or count missing data as no event',
        'protected_verified':b.verify(reg['protected_sha256'])}
    p=DOC/'round3_diagnostic.json';assert not p.exists();b.dump(p,result);print(json.dumps(result))

if __name__=='__main__':main()
