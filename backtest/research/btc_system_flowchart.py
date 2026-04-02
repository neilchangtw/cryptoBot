"""
BTC v3 自適應策略 - 實戰自動交易系統流程圖（中文版）
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib as mpl

mpl.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "SimHei", "Arial"]
mpl.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(1, 1, figsize=(24, 32))
ax.set_xlim(0, 24)
ax.set_ylim(0, 32)
ax.axis("off")

# 顏色定義
C_HEADER = "#1565C0"
C_PROCESS = "#E3F2FD"
C_DECISION = "#FFF9C4"
C_ACTION = "#E8F5E9"
C_ALERT = "#FFEBEE"
C_DATA = "#F3E5F5"
C_ARROW = "#424242"
C_TIMER = "#FF6F00"

def box(x, y, w, h, text, color, fontsize=9, bold=False, border="#333"):
    rect = mpatches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.15",
                                     facecolor=color, edgecolor=border, linewidth=1.5)
    ax.add_patch(rect)
    weight = "bold" if bold else "normal"
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fontsize,
            fontweight=weight, wrap=True, linespacing=1.4)

def diamond(x, y, w, h, text, color=C_DECISION, fontsize=8):
    pts = [(x+w/2, y+h), (x+w, y+h/2), (x+w/2, y), (x, y+h/2)]
    poly = plt.Polygon(pts, facecolor=color, edgecolor="#333", linewidth=1.5)
    ax.add_patch(poly)
    ax.text(x+w/2, y+h/2, text, ha="center", va="center", fontsize=fontsize, fontweight="bold")

def arrow(x1, y1, x2, y2, text="", color=C_ARROW):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5))
    if text:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx+0.15, my, text, fontsize=7, color="#666", fontstyle="italic")

def arrow_h(x1, y1, x2, y2, text="", color=C_ARROW):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.5))
    if text:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx, my+0.15, text, fontsize=7, color="#666", fontstyle="italic", ha="center")

# ============================================================
# 標題
# ============================================================
ax.text(12, 31.5, "BTC v3 自適應策略 - 實戰自動交易系統流程",
        ha="center", va="center", fontsize=16, fontweight="bold", color=C_HEADER)
ax.text(12, 31.1, "每小時 K 線收盤時觸發一次完整交易循環",
        ha="center", va="center", fontsize=10, color="#666")

# ============================================================
# 左側：時間軸
# ============================================================
ax.text(1, 30.5, "觸發時機", ha="center", fontsize=10, fontweight="bold", color=C_TIMER)
box(0.2, 29.5, 1.6, 0.7, "每小時\n1 次", "#FFF3E0", fontsize=9, bold=True, border=C_TIMER)
ax.text(1, 29.0, "xx:00:01", ha="center", fontsize=7, color=C_TIMER)
ax.text(1, 28.7, "(K 線收盤後 1 秒)", ha="center", fontsize=6, color="#999")

# ============================================================
# 主流程
# ============================================================
# Step 0: Scheduler
y = 30
box(4, y-0.5, 5, 0.8, "排程器 (cron / APScheduler)\n每小時 xx:00:01 觸發", C_HEADER, fontsize=9, bold=True, border=C_HEADER)

# Step 1: Fetch Data
y = 28.5
arrow(6.5, y+1.2, 6.5, y+0.8)
box(4, y, 5, 1.0, "1. 抓取最新 1h K 線\nBinance REST API\nGET /api/v3/klines?limit=1", C_DATA, fontsize=9)
box(10, y, 4.5, 1.0, "需要的資料：\n- 最新一根 OHLCV\n- 記憶體保留 100+ 根歷史", "#F5F5F5", fontsize=7)
arrow_h(9, y+0.5, 10, y+0.5)

# Step 2: Calculate Indicators
y = 26.8
arrow(6.5, y+1.0, 6.5, y+0.8)
box(4, y, 5, 1.2, "2. 更新技術指標\n- RSI(14)\n- BB(20,2)、ATR(14)\n- ATR 百分位(100)\n- Swing 高低點(5)", C_PROCESS, fontsize=8)
box(10, y, 4.5, 1.2, "滾動窗口需求：\n- ATR 百分位：100 根\n- 布林通道：20 根\n- RSI / ATR：14 根\n- Swing 高低點：11 根", "#F5F5F5", fontsize=7)
arrow_h(9, y+0.6, 10, y+0.6)

# Step 3: Fetch Positions
y = 25.0
arrow(6.5, y+1.1, 6.5, y+0.8)
box(4, y, 5, 0.8, "3. 查詢目前持倉\nBinance GET /fapi/v2/positionRisk", C_DATA, fontsize=9)

# Step 4: Update Existing Positions
y = 23.5
arrow(6.5, y+0.8, 6.5, y+0.7)
box(3, y-0.2, 7, 0.9, "4. 逐一更新現有持倉（迴圈）", C_HEADER, fontsize=10, bold=True, border=C_HEADER)

# Position update sub-flow
y = 22.3
diamond(4.5, y-0.5, 4, 0.8, "碰到止損？")
ax.text(9.3, y-0.1, "是", fontsize=8, color="red", fontweight="bold")
arrow_h(8.5, y-0.1, 10, y-0.1, "")
box(10, y-0.5, 4.5, 0.8, "市價平倉\nBinance 市價單", C_ALERT, fontsize=8)

y2 = 21.0
ax.text(6.3, y2+0.5, "否", fontsize=8, color="green", fontweight="bold")
arrow(6.5, y2+0.3, 6.5, y2+0.1)
diamond(4.5, y2-0.6, 4, 0.8, "Phase 1:\n碰到 TP1？")
ax.text(9.3, y2-0.2, "是", fontsize=8, color="red", fontweight="bold")
arrow_h(8.5, y2-0.2, 10, y2-0.2, "")
box(10, y2-0.6, 4.5, 0.8, "平掉 10% 倉位\n止損移至進場價（保本）\n進入 Phase 2", C_ACTION, fontsize=7)

y3 = 19.2
ax.text(6.3, y3+0.6, "否", fontsize=8, color="green", fontweight="bold")
arrow(6.5, y3+0.35, 6.5, y3+0.15)
diamond(4.5, y3-0.6, 4, 0.8, "Phase 2:\n碰到自適應\n追蹤止盈？")

box(10, y3-1.0, 4.5, 1.5, "自適應追蹤計算：\nmult = 1.0 + (ATR百分位/100)*1.5\n若 RSI > 65（多單）：mult *= 0.6\n若 RSI < 35（空單）：mult *= 0.6\n追蹤線 = 極值 +/- 當前ATR * mult\n不低/高於保本價", "#FFF9C4", fontsize=7)
arrow_h(8.5, y3-0.2, 10, y3-0.2, "")

y4 = 17.5
ax.text(9.3, y3-0.2, "是", fontsize=7, color="red", fontweight="bold")
arrow(6.5, y4+0.5, 6.5, y4+0.2)
box(4, y4-0.4, 5, 0.8, "平掉剩餘 90% 倉位\nBinance 市價單", C_ALERT, fontsize=8)

ax.text(3.5, y3-0.2, "否", fontsize=8, color="green", fontweight="bold")
arrow_h(4.5, y3-0.2, 2.5, y3-0.2, "")
box(0.5, y3-0.5, 2, 0.6, "繼續持有\n（不動作）", "#E8F5E9", fontsize=8, bold=True)

# Step 5: Check New Entries
y = 16.0
arrow(6.5, y+0.5, 6.5, y+0.2)
box(3, y-0.4, 7, 0.8, "5. 檢查新進場信號", C_HEADER, fontsize=10, bold=True, border=C_HEADER)

y = 14.8
diamond(2, y-0.5, 3.5, 0.8, "RSI < 30\n且多單 < 3？")
ax.text(6, y-0.1, "是", fontsize=8, color="green", fontweight="bold")
arrow_h(5.5, y-0.1, 6.5, y-0.1, "")
box(6.5, y-0.5, 4, 0.8, "開多單\n止損 = 前低 - 0.3*ATR\nTP1 = 進場價 + 1.0*ATR", C_ACTION, fontsize=7)
arrow_h(10.5, y-0.1, 11.5, y-0.1, "")
box(11.5, y-0.5, 3, 0.8, "Binance 下單\nPOST /fapi/v1/order\nside=BUY", C_DATA, fontsize=7)

y = 13.3
diamond(2, y-0.5, 3.5, 0.8, "收盤 > BB上軌\n且空單 < 3？")
ax.text(6, y-0.1, "是", fontsize=8, color="green", fontweight="bold")
arrow_h(5.5, y-0.1, 6.5, y-0.1, "")
box(6.5, y-0.5, 4, 0.8, "開空單\n止損 = 前高 + 0.3*ATR\nTP1 = 進場價 - 1.0*ATR", C_ACTION, fontsize=7)
arrow_h(10.5, y-0.1, 11.5, y-0.1, "")
box(11.5, y-0.5, 3, 0.8, "Binance 下單\nPOST /fapi/v1/order\nside=SELL", C_DATA, fontsize=7)

# Step 6: Risk Check
y = 11.5
arrow(6.5, y+1.0, 6.5, y+0.7)
box(4, y, 5, 1.0, "6. 風控檢查\n- 單日虧損 < 5%？\n- 總倉位 < 6 個？\n- 保證金使用 < 60%？", C_ALERT, fontsize=8)
box(10, y, 4.5, 1.0, "若超過限制：\n- 禁止新開倉\n- 發送 LINE/TG 警報\n- 寫入警告日誌", "#FFCDD2", fontsize=7)
arrow_h(9, y+0.5, 10, y+0.5)

# Step 7: Log & Monitor
y = 9.8
arrow(6.5, y+0.5, 6.5, y+0.3)
box(4, y-0.3, 5, 1.0, "7. 記錄 & 監控\n- 寫入所有操作日誌\n- 更新儀表板\n- 發送狀態到 LINE/TG", C_PROCESS, fontsize=8)

# Step 8: Sleep
y = 8.5
arrow(6.5, y+0.5, 6.5, y+0.3)
box(4, y-0.3, 5, 0.8, "8. 等待下一個整點\n（休眠至 xx+1:00:01）", "#FFF3E0", fontsize=9, bold=True, border=C_TIMER)

# Loop back arrow
ax.annotate("", xy=(2, 30.0), xytext=(2, 8.2),
            arrowprops=dict(arrowstyle="-|>", color=C_TIMER, lw=2,
                           connectionstyle="arc3,rad=0.3"))
ax.text(0.8, 20, "每小時\n循環", fontsize=9, color=C_TIMER, fontweight="bold", ha="center", rotation=90)

# ============================================================
# 右側：系統架構
# ============================================================
ax.text(19, 30.5, "系統架構", ha="center", fontsize=10, fontweight="bold", color=C_HEADER)

box(16, 29.0, 6, 1.5, "strategy_engine.py（新增）\n\n- 技術指標計算\n- 進出場信號判斷\n- 自適應追蹤計算\n- 持倉管理", "#E3F2FD", fontsize=8, bold=False, border=C_HEADER)

box(16, 27.0, 6, 1.0, "binance_trade.py（現有）\n\n- 下單執行\n- 持倉查詢", "#E8F5E9", fontsize=8, border="#2E7D32")

box(16, 25.5, 6, 1.0, "cryptobot_monitor.py（現有）\n\n- LINE / Telegram 通知\n- 健康檢查", "#FFF3E0", fontsize=8, border=C_TIMER)

arrow(19, 29.0, 19, 28.0)
arrow(19, 27.0, 19, 26.5)

# Data flow
box(16, 23.5, 6, 1.5, "資料流向：\n\nBinance API --> strategy_engine\n           （1h K 線、指標計算）\n\nstrategy_engine --> binance_trade\n           （信號、下單指令）\n\nbinance_trade --> Binance 交易所\n           （REST API 下單）", "#F5F5F5", fontsize=7)

# Timing detail
box(16, 21.0, 6, 2.0, "時間明細：\n\nxx:00:01  抓取 K 線（< 1 秒）\nxx:00:02  計算指標（< 1 秒）\nxx:00:03  更新持倉（< 2 秒）\nxx:00:05  檢查進場（< 1 秒）\nxx:00:06  送出訂單（< 2 秒）\nxx:00:08  記錄日誌、休眠\n\n完整循環：約 8 秒\n下次觸發：xx+1:00:01", "#E8EAF6", fontsize=7)

# Key numbers
box(16, 18.0, 6, 2.5, "關鍵參數（全部動態計算）：\n\n進場：\n  做多：RSI(14) < 30\n  做空：收盤 > BB上軌(20,2)\n\n止損：\n  做多：前低(5) - 0.3 * ATR(14)\n  做空：前高(5) + 0.3 * ATR(14)\n\n自適應出場：\n  mult = 1.0 + (ATR百分位/100)*1.5\n  RSI 回歸中性：mult *= 0.6\n  追蹤線 = 極值 +/- 當前ATR * mult", "#E8F5E9", fontsize=7)

# Order types
box(16, 15.3, 6, 2.2, "Binance 訂單類型：\n\n1. 進場：限價單 @ 收盤價\n   （若錯過改用市價單）\n\n2. 止損：條件單（stop order）\n   進場後立即設置\n\n3. TP1：條件止盈單\n   部分平倉 10%\n\n4. 追蹤出場：市價單\n   由 strategy_engine 觸發\n   （非 Binance 內建追蹤止損）", "#FFF9C4", fontsize=7)

# Error handling
box(16, 12.5, 6, 2.3, "異常處理：\n\n- API 超時：重試 3 次，間隔 2 秒\n- 訂單被拒：記錄日誌 + 發警報\n- 持倉不一致：與 Binance 持倉對帳\n- 資料中斷：跳過本次循環、發警報\n- 程式崩潰：systemd 自動重啟\n- 網路斷線：發警報、切換手動模式\n\n所有異常 --> LINE / TG 通知", "#FFEBEE", fontsize=7)

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_system_flowchart.png",
            dpi=150, bbox_inches="tight")
print("流程圖已儲存。")
plt.show()
