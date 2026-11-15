"""V83：下載 Yahoo 日線（ETH=F、^VIX、ES=F、NQ=F）、ETH=F 小時線與 CFTC TFF 歷史檔到 data/v83/，並寫 SHA256SUMS.txt。公司電腦不可執行。"""
import os, subprocess, hashlib, time
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); D = os.path.join(ROOT, 'data', 'v83'); os.makedirs(D, exist_ok=True)
J = [(f'yahoo_{n}_1d.json', f'https://query1.finance.yahoo.com/v8/finance/chart/{s}?interval=1d&period1=1577836800&period2=1793491200') for s, n in (('ETH=F', 'ethf'), ('%5EVIX', 'vix'), ('ES=F', 'es'), ('NQ=F', 'nq'))]
J += [('yahoo_ethf_1h.json', 'https://query1.finance.yahoo.com/v8/finance/chart/ETH=F?interval=1h&range=730d')] + [(f'cot_fin_{y}.zip', f'https://www.cftc.gov/files/dea/history/fut_fin_txt_{y}.zip') for y in range(2021, 2027)]
for fn, url in J:
    p = os.path.join(D, fn)
    for k in range(4):
        if subprocess.run(['curl', '-sS', '-f', '-m', '60', '-H', 'User-Agent: Mozilla/5.0', '-o', p, url]).returncode == 0: break
        time.sleep(2 ** (k + 1))
    print(fn, os.path.getsize(p) if os.path.exists(p) else '失敗')
open(os.path.join(D, 'SHA256SUMS.txt'), 'w').write(''.join(f'{hashlib.sha256(open(os.path.join(D, f), "rb").read()).hexdigest()}  {f}\n' for f in sorted(os.listdir(D)) if f != 'SHA256SUMS.txt'))
