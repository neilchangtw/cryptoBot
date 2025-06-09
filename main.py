from bybit_trade import place_order, close_all_position

if __name__ == "__main__":
    # 測試參數
    symbol = "ETHUSDT"
    side = "Buy"      # "Buy" or "Sell"
    price = 2528     # 測試價格，可任意填入現價
    stop_loss = 2500  # 可填入自訂停損，如 63000
    take_profit = 2600 # 可填入自訂停利，如 68000
    strategy = "ManualTest"
    interval = "15"

    print("===== 測試市價開倉功能 =====")
    place_order(
        symbol=symbol,
        side=side,
        price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        strategy=strategy,
        interval=interval
    )

    # # 如需測試 SELL 可取消下列註解
    # print("===== 測試市價開空 =====")
    # place_order(
    #     symbol=symbol,
    #     side="Sell",
    #     price=price,
    #     stop_loss=stop_loss,
    #     take_profit=take_profit,
    #     strategy=strategy,
    #     interval=interval
    # )

    # # 如需測試 EXIT 全部市價平倉可取消下列註解
    # print("===== 測試 EXIT 全平功能 =====")
    # close_all_position(symbol)
