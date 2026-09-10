"""Index historical research and preserve pre-existing workspace status."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'doc/research_results/20260910_new_information'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    paths = []
    for p in (ROOT / 'doc').rglob('*'):
        if not p.is_file() or OUT in p.parents:
            continue
        name = p.name
        v = re.match(r'v(\d+)', name)
        if (p.suffix == '.md' and ((v and 9 <= int(v[1]) <= 37) or
                any(x in name for x in ['20260904', '20260908', '20260909', 'live_data_spec']))) or (
                    'research_results' in p.parts and p.suffix in ['.json', '.csv', '.md']):
            paths.append(p)
    records = []
    for p in sorted(paths):
        content = p.read_text(encoding='utf-8-sig')
        headings = [{'line': i, 'text': line} for i, line in enumerate(content.splitlines(), 1)
                    if line.startswith('#')]
        refs = sorted(set(re.findall(r'[A-Za-z0-9_./-]+\.py', content)))
        scripts = []
        for ref in refs:
            matches = list((ROOT / 'backtest/research').glob(Path(ref).name))
            scripts += [str(s.relative_to(ROOT)).replace('\\', '/') for s in matches]
        records.append({'path': str(p.relative_to(ROOT)).replace('\\', '/'),
                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                        'bytes': p.stat().st_size, 'headings': headings,
                        'referenced_existing_scripts': sorted(set(scripts))})
    status = subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True)
    result = {'indexed_utc': datetime.now(timezone.utc).isoformat(),
              'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'workspace_status_at_index': status, 'records': records,
              'scope_note': 'Inventory is not a claim that every historical script was rerun. Nearest OI/funding/spot/flow definitions were inspected directly.'}
    (OUT / 'history_index.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(records), 'versions': sorted({int(re.match(r'v(\d+)', Path(r['path']).name)[1])
          for r in records if re.match(r'v(\d+)', Path(r['path']).name)})}))


if __name__ == '__main__':
    main()
