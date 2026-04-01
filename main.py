"""
v5 CryptoBot 主程式
同時啟動 strategy_runner + cryptobot_monitor

策略：5m RSI+BB 均值回歸 + 3 進場過濾
  進場：RSI+BB + ATR_pctile<=75 + EMA21<2% + 1h RSI 轉向
  SL：安全網 ±3%
  出場：TP1 全平 100% at 1.5×ATR + 8h 時間止損
"""
import threading
import strategy_runner
import cryptobot_monitor


def main():
    print("=" * 60)
    print("  v5 CryptoBot (RSI+BB + 3 Filters + TimeStop)")
    print("  Strategy Runner + Position Monitor")
    print("=" * 60)

    t1 = threading.Thread(target=strategy_runner.main, name="Runner", daemon=True)
    t2 = threading.Thread(target=cryptobot_monitor.main, name="Monitor", daemon=True)

    t1.start()
    t2.start()

    # 主執行緒等待，Ctrl+C 可中斷
    try:
        t1.join()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
