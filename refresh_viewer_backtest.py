"""Refresh the read-only viewer's backtest snapshot from the latest closed bars."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from trade_viewer import load_trades


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DEFAULT_OUTPUT = DATA_DIR / "backtest_trades.txt"


def main() -> int:
    output_path = Path(os.environ.get("VIEWER_BACKTEST_PATH", str(DEFAULT_OUTPUT))).expanduser()
    if not output_path.is_absolute():
        output_path = (ROOT / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(ROOT / "run_backtest.py"), "--refresh", "-t"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(f"回測快照更新失敗：run_backtest.py 結束碼 {result.returncode}", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode or 1
    if "實際資料：" not in result.stdout or "進出場明細" not in result.stdout:
        print("回測快照更新失敗：輸出缺少資料範圍或交易明細，保留舊快照。", file=sys.stderr)
        return 1

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(result.stdout)
            handle.flush()
            os.fsync(handle.fileno())

        trades = load_trades(temp_path)
        if not trades:
            raise ValueError("回測輸出沒有可解析的已平倉交易")
        os.replace(temp_path, output_path)
        temp_path = None
    except (OSError, ValueError) as exc:
        print(f"回測快照更新失敗：{exc}；保留舊快照。", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    print(f"回測快照已更新：{len(trades)} 筆；資料來源為最新已收盤 K 線。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
