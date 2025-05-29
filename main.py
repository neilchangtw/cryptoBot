import datetime

def greet():
    now = datetime.datetime.now()
    print(f"Hello, Neil! 👋 現在時間是：{now.strftime('%Y-%m-%d %H:%M:%S')}")

greet()
