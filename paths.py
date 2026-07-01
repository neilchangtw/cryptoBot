"""多實例共用路徑解析。

單一 VPS 上跑多組機器人（給多人使用）時，用環境變數 INSTANCE_DIR 把每個實例的
「資料 / 日誌 / 狀態檔」分流到各自目錄，彼此不衝突。

不設 INSTANCE_DIR 時一律沿用程式目錄 → 單人跑法與原本完全相同（向後相容）。

systemd template 範例：
    Environment=INSTANCE_DIR=/home/cryptobot/instances/%i

注意：回測 K 線快取（data/ETHUSDT_1h_latest730d.csv）仍放在程式目錄、多實例共用，
      不受 INSTANCE_DIR 影響（run_backtest.py 自己指到程式目錄的 data/）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
# 沒設 INSTANCE_DIR 就用程式目錄（單人 = 原行為）
INSTANCE_DIR = os.getenv("INSTANCE_DIR", "").strip() or CODE_DIR


def data_dir(paper: bool) -> str:
    """交易資料目錄：paper→ data/，live→ data_live/（trades.csv/bar_snapshots.csv 等）。"""
    return os.path.join(INSTANCE_DIR, "data" if paper else "data_live")


def logs_dir() -> str:
    """日誌目錄（system.log / signal.log / alerts.log）。"""
    return os.path.join(INSTANCE_DIR, "logs")


def state_file(paper: bool) -> str:
    """持倉狀態檔：paper→ eth_state.json，live→ eth_state_live.json。"""
    return os.path.join(INSTANCE_DIR, "eth_state.json" if paper else "eth_state_live.json")
