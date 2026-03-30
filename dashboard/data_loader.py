"""
Dashboard 資料載入模組
讀取回測交易紀錄、K線資料、即時持倉
"""
import os
import json
import pandas as pd

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "backtest", "results")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
STATE_FILE = os.path.join(PROJECT_ROOT, "live", "state.json")


def load_trades(csv_path: str = None) -> pd.DataFrame:
    """
    載入交易紀錄。預設讀最新的 *_trades.csv。
    """
    if csv_path is None:
        csv_path = _find_latest_trades_csv()
    if csv_path is None or not os.path.exists(csv_path):
        return pd.DataFrame()

    df = pd.read_csv(csv_path)
    for col in ["entry_time", "exit_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    return df


def _find_latest_trades_csv() -> str:
    """找 backtest/results/ 裡最新的 full trades CSV"""
    if not os.path.isdir(RESULTS_DIR):
        return None
    files = [f for f in os.listdir(RESULTS_DIR)
             if f.endswith("_trades.csv") and "full" in f.lower()]
    if not files:
        # 退而求其次，找任何 trades CSV
        files = [f for f in os.listdir(RESULTS_DIR) if f.endswith("_trades.csv")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(RESULTS_DIR, files[0])


def load_klines(symbol: str = "BTCUSDT", interval: str = "1h") -> pd.DataFrame:
    """
    從快取載入 K 線。找 data/ 裡最新的 CSV。
    """
    if not os.path.isdir(DATA_DIR):
        return pd.DataFrame()

    files = [f for f in os.listdir(DATA_DIR)
             if f.startswith(f"{symbol}_{interval}") and f.endswith(".csv")]
    if not files:
        return pd.DataFrame()

    files.sort(reverse=True)
    csv_path = os.path.join(DATA_DIR, files[0])
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def load_open_positions() -> list:
    """
    讀取即時持倉（Phase 1 live 用）。
    目前沒有 live 系統時回傳空列表。
    """
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        return state.get("positions", [])
    except (json.JSONDecodeError, IOError):
        return []


def list_trade_files() -> list:
    """列出所有可用的交易紀錄檔案"""
    if not os.path.isdir(RESULTS_DIR):
        return []
    files = [f for f in os.listdir(RESULTS_DIR) if f.endswith("_trades.csv")]
    files.sort(reverse=True)
    return files
