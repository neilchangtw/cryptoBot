"""Bounded public archive acquisition; no credentials, trading APIs or retries."""
from pathlib import Path
from datetime import datetime, timezone
from io import BytesIO
import argparse
import hashlib
import json
import zipfile
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/new_information_20260910'
BASE = 'https://data.binance.vision/data/futures/um'
LIMIT = 50_000_000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def acquire(kind, date, manifest, session):
    if kind == 'metrics':
        key = f'daily/metrics/ETHUSDT/ETHUSDT-metrics-{date}.zip'
    elif kind == 'premium_daily':
        key = f'daily/premiumIndexKlines/ETHUSDT/1h/ETHUSDT-1h-{date}.zip'
    else:
        key = f'monthly/premiumIndexKlines/ETHUSDT/1h/ETHUSDT-1h-{date}.zip'
    path = OUT / 'raw' / kind / key.rsplit('/', 1)[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    url = BASE + '/' + key
    if url in manifest and path.exists():
        assert sha(path.read_bytes()) == manifest[url]['sha256']
        return path
    total = sum(r.get('bytes', 0) for r in manifest.values())
    if total >= LIMIT:
        raise RuntimeError('50MB cumulative download ceiling reached')
    now = datetime.now(timezone.utc).isoformat()
    response = session.get(url, timeout=30, stream=True)
    record = dict(url=url, retrieved_utc=now, status=response.status_code)
    manifest[url] = record
    if response.status_code == 404:
        record['bytes'] = len(response.content)
        return None
    response.raise_for_status()
    chunks = []
    size = 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if total + size > LIMIT or size > 5_000_000:
            raise RuntimeError('Download size guard exceeded')
        chunks.append(chunk)
    data = b''.join(chunks)
    check = session.get(url + '.CHECKSUM', timeout=30)
    check.raise_for_status()
    assert len(check.content) < 2048
    assert check.text.split()[0] == sha(data), 'Official checksum mismatch'
    path.write_bytes(data)
    path.with_suffix('.zip.CHECKSUM').write_bytes(check.content)
    record.update(bytes=len(data) + len(check.content), sha256=sha(data),
                  path=str(path.relative_to(ROOT)), last_modified=response.headers.get('Last-Modified'))
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = archive.namelist()
        assert len(names) == 1 and archive.getinfo(names[0]).file_size < 20_000_000
        frame = pd.read_csv(archive.open(names[0]))
        record.update(rows=len(frame), columns=frame.columns.tolist(),
                      first=frame.iloc[0].to_dict(), last=frame.iloc[-1].to_dict())
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['probe', 'metrics', 'premium'])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    mp = OUT / 'download_manifest.json'
    manifest = json.loads(mp.read_text()) if mp.exists() else {}
    jobs = ([('metrics', d) for d in ['2024-09-08', '2025-09-08', '2026-09-07']] +
            [('premium', d) for d in ['2024-09', '2025-09', '2026-08']])
    if args.mode == 'metrics':
        jobs = [('metrics', d.strftime('%Y-%m-%d')) for d in pd.date_range('2024-09-07', '2026-09-08')]
    elif args.mode == 'premium':
        jobs = [('premium', d.strftime('%Y-%m')) for d in pd.date_range('2024-09-01', '2026-08-01', freq='MS')]
        jobs += [('premium_daily', d.strftime('%Y-%m-%d')) for d in pd.date_range('2026-09-01', '2026-09-08')]
    session = requests.Session()
    try:
        for n, (kind, date) in enumerate(jobs):
            acquire(kind, date, manifest, session)
            mp.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            if args.mode == 'probe' or n % 50 == 0:
                print(json.dumps({'done': n + 1, 'total': len(jobs), 'kind': kind, 'date': date,
                                  'bytes': sum(r.get('bytes', 0) for r in manifest.values())}), flush=True)
        print(json.dumps({'requests': len(manifest), 'bytes': sum(r.get('bytes', 0) for r in manifest.values()),
                          'missing': [u for u, r in manifest.items() if r['status'] != 200]}), flush=True)
    finally:
        mp.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
