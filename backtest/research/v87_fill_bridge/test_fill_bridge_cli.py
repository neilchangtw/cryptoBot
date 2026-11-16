#!/usr/bin/env python3
"""fill_bridge.py 命令列端到端測試（只用合成資料；本測試檔才使用 subprocess，匯總器本身不用）。

每個案例都以獨立的 Python 行程執行 `python -I -c <守衛> fill_bridge.py ...`：守衛先把 socket 連線、DNS、
subprocess.Popen、os.system、os.posix_spawn 換成會記錄並報錯的函式，再以 runpy 執行匯總器，所以任何網路或子程序嘗試都會留下紀錄。
合成 CSV 存在 synthetic/（固定內容，可用 --write-synthetic 重建後以 git diff 比對）。

用法：python3 -I test_fill_bridge_cli.py [--write-synthetic]
"""
import hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(HERE, 'fill_bridge.py'); SYN = os.path.join(HERE, 'synthetic')
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
GUARD = r'''
import os, sys, runpy, socket, subprocess
LOG = sys.argv[1]
def blocked(*a, **k):
    with open(LOG, 'a') as f: f.write('blocked\n')
    raise RuntimeError('守衛攔下網路或子程序')
socket.socket.connect = blocked; socket.socket.connect_ex = blocked; socket.getaddrinfo = blocked; socket.create_connection = blocked
subprocess.Popen = blocked; os.system = blocked
if hasattr(os, 'posix_spawn'): os.posix_spawn = blocked
tool = sys.argv[2]; sys.argv = [tool] + sys.argv[3:]
runpy.run_path(tool, run_name='__main__')
'''


def sha(p):
    with open(p, 'rb') as f: return hashlib.sha256(f.read()).hexdigest()


def load_tool(path):
    spec = importlib.util.spec_from_file_location('fill_bridge_under_test', path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def write_synthetic():
    fb = load_tool(TOOL); fills, orders, local, kl = fb.synthetic(); os.makedirs(SYN, exist_ok=True)
    fills.to_csv(os.path.join(SYN, 'fills.csv'), index=False); orders.to_csv(os.path.join(SYN, 'orders.csv'), index=False)
    local.to_csv(os.path.join(SYN, 'local_trades.csv'), index=False); kl.to_csv(os.path.join(SYN, 'klines.csv'), index=False)
    fills.assign(commissionAsset=np.where(fills.orderId == 102, 'BNB', fills.commissionAsset)).to_csv(os.path.join(SYN, 'fills_bnb.csv'), index=False)
    kl.assign(open_time=kl.open_time + 60_000).to_csv(os.path.join(SYN, 'klines_bad_hour.csv'), index=False)


def cli(tool, args, log):
    if os.path.exists(log): os.remove(log)
    p = subprocess.run([sys.executable, '-I', '-c', GUARD, log, tool] + args, capture_output=True, text=True)
    guarded = os.path.exists(log)
    return p.returncode, p.stdout, p.stderr, guarded


def consistent(out, inputs, tool):
    legs = pd.read_csv(os.path.join(out, 'legs_deidentified.csv')); br = pd.read_csv(os.path.join(out, 'bridge_deidentified.csv'))
    S = json.load(open(os.path.join(out, 'summary.json'), encoding='utf-8'))
    c = []
    c.append(S['腿數'] == len(legs)); c.append(S['相容配對腿'] == int((legs.狀態 == '相容配對').sum())); c.append(S['未配對腿'] == int((legs.狀態 != '相容配對').sum()))
    c.append(S['橋接']['交易筆'] == len(br))
    for k in ('實際毛損益', '參考價反事實毛損益', '價差美元'):
        v = br[k].dropna(); c.append(S['橋接']['價格橋接'][k]['筆'] == len(v) and abs((S['橋接']['價格橋接'][k]['合計'] or 0) - v.sum()) < 1e-6)
    V = br[br.淨損益狀態 != '未驗證']; U = S['橋接']['USDT淨損益']
    c.append(U['已驗證筆'] == len(V) and U['未驗證筆'] == len(br) - len(V))
    for k in ('手續費USDT', '交易所淨_不含funding', '本機淨', '未拆分殘差'):
        v = V[k].dropna(); c.append(U[k]['筆'] == len(v) and abs((U[k]['合計'] or 0) - v.sum()) < 1e-6)
    c.append(br[br.淨損益狀態 == '未驗證'][['手續費USDT', '交易所淨_不含funding', '未拆分殘差']].isna().all().all())
    bl = legs[legs.序號.isin(br.序號)].groupby('序號').價差成本美元.sum()   # 進入橋接的交易：逐腿價差成本相加＝−橋接價差美元
    c.append(np.allclose(bl.reindex(br.序號).values, -br.價差美元.values))
    info = S['執行資訊']; c.append(info['工具']['sha256'] == sha(tool))
    c.append(all(info['輸入'][k]['sha256'] == sha(p) for k, p in inputs.items()))
    if os.name == 'posix': c.append(all((os.stat(os.path.join(out, n)).st_mode & 0o077) == 0 for n in os.listdir(out)) and (os.stat(out).st_mode & 0o077) == 0)
    return all(c), c, S, legs, br


def main():
    if '--write-synthetic' in sys.argv: write_synthetic(); print('已重建 synthetic/'); return
    tmp = tempfile.mkdtemp(prefix='fbcli_'); work = os.path.join(tmp, 'in'); shutil.copytree(SYN, work); log = os.path.join(tmp, 'guard.log')
    I = {k: os.path.join(work, f) for k, f in (('fills', 'fills.csv'), ('orders', 'orders.csv'), ('local', 'local_trades.csv'), ('klines', 'klines.csv'))}
    base = lambda d, **o: sum([['--' + k, o.get(k, v)] for k, v in d.items()], []) + ['--bar-time', 'open', '--klines-source', '合成資料（非實盤）']
    results = []

    def case(name, ok, detail=''):
        results.append(ok); print(('PASS ' if ok else 'FAIL ') + name + (('：' + detail) if detail else ''))

    print('平台：%s Python %s pandas %s numpy %s' % (os.name, sys.version.split()[0], pd.__version__, np.__version__))
    print('工具 sha256 %s' % sha(TOOL))
    for k, p in sorted(I.items()): print('輸入 %-6s sha256 %s  %s' % (k, sha(p), os.path.basename(p)))
    # A 正式執行
    out = os.path.join(tmp, 'out', 'A'); rc, so, se, g = cli(TOOL, base(I) + ['--out', out], log)
    okA = rc == 0 and not g and sorted(os.listdir(out)) == ['bridge_deidentified.csv', 'legs_deidentified.csv', 'summary.json'] and not os.path.lexists(out + '.incomplete')
    allc, c, S, legs, br = consistent(out, I, TOOL) if okA else (False, [], {}, None, None)
    case('A 命令列正式執行：結束碼 0、三檔齊全、無暫存目錄、無網路與子程序', okA, f'結束碼 {rc}')
    case('A summary.json 可重新讀取，且 stdout 與檔案內容相同', okA and json.loads(so) == S)
    case('A 摘要與逐腿、逐筆 CSV 的筆數與金額一致；執行資訊雜湊正確；600／700', allc, f'{sum(c)}/{len(c)} 項')
    if okA:
        print('  A 摘要節錄：' + json.dumps(dict(腿數=S['腿數'], 相容配對腿=S['相容配對腿'], 橋接=dict(交易筆=S['橋接']['交易筆'], 排除=S['橋接']['排除'], 價差美元=S['橋接']['價格橋接']['價差美元'],
                                                                                USDT淨損益=dict(已驗證筆=S['橋接']['USDT淨損益']['已驗證筆'], 手續費USDT=S['橋接']['USDT淨損益']['手續費USDT'])),
                                           K線覆蓋=S['K線覆蓋'], 版本=S['執行資訊']['版本']), ensure_ascii=False))
    # B 非 USDT 手續費
    IB = dict(I, fills=os.path.join(work, 'fills_bnb.csv')); outB = os.path.join(tmp, 'out', 'B'); rc, so, se, g = cli(TOOL, base(IB) + ['--out', outB], log)
    okB = rc == 0 and not g
    if okB:
        allcB, cB, SB, _, brB = consistent(outB, IB, TOOL); UB = SB['橋接']['USDT淨損益']; r1 = brB.set_index('序號').loc[1]
        fee2 = brB.set_index('序號').loc[2].手續費USDT
        okB = allcB and UB['已驗證筆'] == 1 and UB['未驗證筆'] == 1 and abs(UB['手續費USDT']['合計'] - fee2) < 1e-9 and r1.淨損益狀態 == '未驗證' and pd.isna(r1.交易所淨_不含funding) and np.isfinite(r1.價差美元)
    case('B 一筆出場手續費為 BNB：該筆 USDT 淨損益留空標未驗證、不進合計，價格橋接照列，摘要與 CSV 一致', okB, f'結束碼 {rc}')
    # C 不覆寫
    before = {n: sha(os.path.join(out, n)) for n in os.listdir(out)}; rc, so, se, g = cli(TOOL, base(I) + ['--out', out], log)
    case('C 目錄已存在：結束碼 4、既有三檔不變', rc == 4 and before == {n: sha(os.path.join(out, n)) for n in os.listdir(out)} and not g, f'結束碼 {rc}')
    # D URL 在任何網路 I/O 前拒絕
    for k in ('fills', 'orders', 'local', 'klines'):
        for u in ('https://example.invalid/x.csv', 'http://127.0.0.1:9/x.csv', 'file:///etc/hostname'):
            o = os.path.join(tmp, 'out', f'D_{k}'); rc, so, se, g = cli(TOOL, base(dict(I, **{k: u})) + ['--out', o], log)
            case(f'D --{k} {u}：結束碼 2、守衛未被觸發、未建立輸出', rc == 2 and not g and not os.path.lexists(o) and '拒絕輸入' in se, f'結束碼 {rc}')
    # E 其他輸入
    os.symlink(I['fills'], os.path.join(work, 'link.csv')) if os.name == 'posix' else None
    for nm, p in (('不存在', os.path.join(work, 'nope.csv')), ('目錄', work), ('符號連結', os.path.join(work, 'link.csv'))):
        if nm == '符號連結' and os.name != 'posix': continue
        o = os.path.join(tmp, 'out', 'E'); rc, so, se, g = cli(TOOL, base(dict(I, fills=p)) + ['--out', o], log)
        case(f'E --fills {nm}：結束碼 2、未建立輸出', rc == 2 and not os.path.lexists(o), f'結束碼 {rc}')
    # F 資料前提不成立
    o = os.path.join(tmp, 'out', 'F'); rc, so, se, g = cli(TOOL, base(dict(I, klines=os.path.join(work, 'klines_bad_hour.csv'))) + ['--out', o], log)
    case('F K 線不在整點：結束碼 3、未建立輸出與暫存目錄', rc == 3 and not os.path.lexists(o) and not os.path.lexists(o + '.incomplete') and not g, f'結束碼 {rc}')
    # G 輸出在 git 工作樹內
    o = os.path.join(REPO, 'fb_should_not_exist'); rc, so, se, g = cli(TOOL, base(I) + ['--out', o], log)
    case('G 輸出位於 git 工作樹：結束碼 4、未建立', rc == 4 and not os.path.lexists(o), f'結束碼 {rc}')
    # H 內建 selftest
    rc, so, se, g = cli(TOOL, ['--selftest'], log)
    case('H 內建 --selftest：結束碼 0', rc == 0 and not g, so.splitlines()[0][:60] if so else se[-200:])
    # I 對照：修正前版本（3c11426）在同一份合成資料上
    old = os.path.join(tmp, 'old_fill_bridge.py')
    try:
        src = subprocess.run(['git', '-C', REPO, 'show', '3c11426:backtest/research/v87_fill_bridge/fill_bridge.py'], capture_output=True, check=True).stdout
        open(old, 'wb').write(src); o = os.path.join(tmp, 'out', 'old')
        rc, so, se, g = cli(old, sum([['--' + k, v] for k, v in I.items()], []) + ['--bar-time', 'open', '--out', o], log)
        left = sorted(os.listdir(o)) if os.path.isdir(o) else []
        print(f'對照 修正前 3c11426（sha256 {hashlib.sha256(src).hexdigest()[:8]}…）：結束碼 {rc}，最後一行錯誤「{se.strip().splitlines()[-1] if se.strip() else ""}」，留下的檔案 {left}')
        o2 = os.path.join(tmp, 'out', 'old_url'); rc2, so2, se2, g2 = cli(old, sum([['--' + k, v] for k, v in dict(I, fills='https://example.invalid/x.csv').items()], []) + ['--bar-time', 'open', '--out', o2], log)
        print(f'對照 修正前版本 --fills https://example.invalid/x.csv：結束碼 {rc2}，守衛{"被觸發（pandas 嘗試連網，證明守衛有效）" if g2 else "未被觸發"}')
    except subprocess.CalledProcessError:
        print('對照 修正前版本：git show 失敗，未執行')
    print('結果：%d/%d 通過' % (sum(results), len(results)))
    shutil.rmtree(tmp)
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
