"""
v4 CryptoBot 主程式
同時啟動 strategy_runner + cryptobot_monitor

策略：Strategy H — 5m RSI+BB 均值回歸
  進場：RSI<30+BB_Lower (做多) / RSI>70+BB_Upper (做空)
  止損：結構止損 Swing ± 0.3×ATR
  出場：TP1 10% at 1.0×ATR + 自適應 ATR+RSI Trail
"""
import threading
import strategy_runner
import cryptobot_monitor


def main():
    print("=" * 60)
    print("  v4CryptoBot (Strategy H: RSI+BB Mean Reversion)")
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
