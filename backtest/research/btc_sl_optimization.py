"""
止損滑價優化：測試多種改善方案
找出在真實滑價下仍然賺錢的最佳配置

滑價模型：
  - "none": 無滑價（理想）
  - "pct_25": SL價 + 25% of (bar_extreme - SL)（樂觀）
  - "pct_50": SL價 + 50% of (bar_extreme - SL)（中性）
  - "pct_75": SL價 + 75% of (bar_extreme - SL)（悲觀）
  - "bar_extreme": 100% = bar 極值（最悲觀）

優化方向：
  1. SL 緩衝加大：0.3x → 0.4x / 0.5x / 0.7x ATR
  2. 槓桿降低：20x → 15x / 10x
  3. 只在低波動開倉（ATR pctile < 50）
  4. SL 不用結構止損，改用更寬的固定 ATR
  5. TP1 距離加大：1.0x → 1.5x ATR（減少頻繁開平）
"""
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from datetime import datetime, timedelta
import time as _time

mpl.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "SimHei", "Arial"]
mpl.rcParams["axes.unicode_minus"] = False

# ============================================================
# 資料（用上一個腳本的邏輯）
# ============================================================
def fetch_klines(interval, days_back=180):
    all_data = []
    end_ms = int(datetime.now().timestamp() * 1000)
    start_ms = int((datetime.now() - timedelta(days=days_back)).timestamp() * 1000)
    current = start_ms
    while current < end_ms:
        try:
            resp = requests.get("https://api.binance.com/api/v3/klines",
                                params={"symbol":"BTCUSDT","interval":interval,
                                        "startTime":current,"limit":1000}, timeout=10)
            data = resp.json()
            if not data: break
            all_data.extend(data)
            current = data[-1][0] + 1
            _time.sleep(0.1)
        except: break
    if not all_data: return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tbqv","ig"])
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms") + timedelta(hours=8)
    return df

def add_indicators(df):
    tr = pd.DataFrame({
        "hl":df["high"]-df["low"],
        "hc":abs(df["high"]-df["close"].shift(1)),
        "lc":abs(df["low"]-df["close"].shift(1))}).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    w=5; sh=pd.Series(np.nan,index=df.index); sl=pd.Series(np.nan,index=df.index)
    for i in range(w,len(df)-w):
        if df["high"].iloc[i]==df["high"].iloc[i-w:i+w+1].max(): sh.iloc[i]=df["high"].iloc[i]
        if df["low"].iloc[i]==df["low"].iloc[i-w:i+w+1].min(): sl.iloc[i]=df["low"].iloc[i]
    df["swing_high"]=sh.ffill(); df["swing_low"]=sl.ffill()
    d=df["close"].diff()
    g=d.clip(lower=0).ewm(alpha=1/14,min_periods=14).mean()
    l=(-d.clip(upper=0)).ewm(alpha=1/14,min_periods=14).mean()
    df["rsi"]=100-100/(1+g/l)
    df["bb_mid"]=df["close"].rolling(20).mean()
    df["bb_upper"]=df["bb_mid"]+2*df["close"].rolling(20).std()
    df["bb_lower"]=df["bb_mid"]-2*df["close"].rolling(20).std()
    return df.dropna().reset_index(drop=True)

print("Fetching data...")
raw = fetch_klines("5m", 180)
df = add_indicators(raw)
print(f"  {len(df)} bars")

# ============================================================
# 通用回測引擎
# ============================================================
def run(data, sl_buffer=0.3, leverage=20, margin=100, sl_slip_pct=0.5,
        tp1_mult=1.0, max_c=3, vol_filter=None, sl_mode="structure"):
    """
    sl_buffer: 結構止損的 ATR 緩衝倍數
    leverage: 槓桿倍數
    sl_slip_pct: 滑價比例（0=無, 0.5=中性, 1.0=bar extreme）
    tp1_mult: TP1 距離（ATR 倍數）
    vol_filter: None=不過濾, 50=只在 ATR pctile<50 時開倉
    sl_mode: "structure" / "fixed_2x" / "fixed_3x"
    """
    long_pos=[]; short_pos=[]; trades=[]
    notional = margin * leverage

    for i in range(2, len(data)-1):
        row=data.iloc[i]; nxt=data.iloc[i+1]
        atr=row["atr"]; hi=row["high"]; lo=row["low"]
        ap=row["atr_pctile"] if not np.isnan(row["atr_pctile"]) else 50
        rsi=row["rsi"]; bm=1.0+(ap/100)*1.5

        # 更新多單
        nl=[]
        for p in long_pos:
            c=False
            if lo<=p["sl"]:
                # 滑價：SL 價 + slip_pct × (SL - bar_low)
                gap = p["sl"] - lo
                exit_p = p["sl"] - gap * sl_slip_pct
                pnl=(exit_p-p["entry"])*p["qty"]-exit_p*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"SL","dt":row["datetime"]}); c=True
            elif p["phase"]==1 and hi>=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["tp1"]-p["entry"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"long","type":"TP1","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_hi"]=hi
            elif p["phase"]==2:
                if hi>p["trail_hi"]: p["trail_hi"]=hi
                m=bm*0.6 if rsi>65 else bm
                sl=max(p["trail_hi"]-atr*m, p["entry"])
                if lo<=sl:
                    gap = sl - lo
                    exit_p = sl - gap * sl_slip_pct
                    exit_p = max(exit_p, p["entry"])  # trail 出場最差也保本
                    pnl=(exit_p-p["entry"])*p["qty"]-exit_p*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"long","type":"Trail","dt":row["datetime"]}); c=True
            if not c: nl.append(p)
        long_pos=nl

        ns=[]
        for p in short_pos:
            c=False
            if hi>=p["sl"]:
                gap = hi - p["sl"]
                exit_p = p["sl"] + gap * sl_slip_pct
                pnl=(p["entry"]-exit_p)*p["qty"]-exit_p*p["qty"]*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"SL","dt":row["datetime"]}); c=True
            elif p["phase"]==1 and lo<=p["tp1"]:
                out=p["oqty"]*0.1; pnl=(p["entry"]-p["tp1"])*out-p["tp1"]*out*0.0004*2
                trades.append({"pnl":pnl,"side":"short","type":"TP1","dt":row["datetime"]})
                p["qty"]=p["oqty"]*0.9; p["phase"]=2; p["sl"]=p["entry"]; p["trail_lo"]=lo
            elif p["phase"]==2:
                if lo<p["trail_lo"]: p["trail_lo"]=lo
                m=bm*0.6 if rsi<35 else bm
                sl=min(p["trail_lo"]+atr*m, p["entry"])
                if hi>=sl:
                    gap = hi - sl
                    exit_p = sl + gap * sl_slip_pct
                    exit_p = min(exit_p, p["entry"])
                    pnl=(p["entry"]-exit_p)*p["qty"]-exit_p*p["qty"]*0.0004*2
                    trades.append({"pnl":pnl,"side":"short","type":"Trail","dt":row["datetime"]}); c=True
            if not c: ns.append(p)
        short_pos=ns

        # 進場
        ep=nxt["open"]
        long_sig = row["rsi"]<30 and row["close"]<row["bb_lower"]
        short_sig = row["rsi"]>70 and row["close"]>row["bb_upper"]

        if vol_filter and ap > vol_filter:
            long_sig = False; short_sig = False

        if len(long_pos)<max_c and long_sig:
            qty=notional/ep
            if sl_mode == "structure":
                sl_price = row["swing_low"] - sl_buffer * atr
            elif sl_mode == "fixed_2x":
                sl_price = ep - 2.0 * atr
            elif sl_mode == "fixed_3x":
                sl_price = ep - 3.0 * atr
            long_pos.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":sl_price,"tp1":ep+tp1_mult*atr,"atr":atr,"phase":1,"trail_hi":ep})

        if len(short_pos)<max_c and short_sig:
            qty=notional/ep
            if sl_mode == "structure":
                sl_price = row["swing_high"] + sl_buffer * atr
            elif sl_mode == "fixed_2x":
                sl_price = ep + 2.0 * atr
            elif sl_mode == "fixed_3x":
                sl_price = ep + 3.0 * atr
            short_pos.append({"entry":ep,"qty":qty,"oqty":qty,
                "sl":sl_price,"tp1":ep-tp1_mult*atr,"atr":atr,"phase":1,"trail_lo":ep})

    tdf = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","side","type","dt"])
    return tdf

def s(tdf):
    if len(tdf)<1: return {"n":0,"pnl":0,"wr":0,"pf":0,"dd":0,"exp":0,"sl_n":0,"sl_avg":0}
    pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]; l=tdf[tdf["pnl"]<=0]
    pf=w["pnl"].sum()/abs(l["pnl"].sum()) if len(l)>0 and l["pnl"].sum()!=0 else 999
    cum=tdf["pnl"].cumsum(); dd=(cum-cum.cummax()).min()
    sl_trades = tdf[tdf["type"]=="SL"]
    sl_avg = sl_trades["pnl"].mean() if len(sl_trades)>0 else 0
    return {"n":len(tdf),"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "dd":round(dd,2),"exp":round(pnl/max(len(tdf),1),2),
            "sl_n":len(sl_trades),"sl_avg":round(sl_avg,2)}

# ============================================================
# 測試 1：滑價模型敏感度（到底多少滑價才合理？）
# ============================================================
print(f"\n{'='*120}")
print("Test 1: SL 滑價模型敏感度（用預設參數，只改滑價比例）")
print(f"{'='*120}")

print(f"\n  {'滑價模型':<25s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'止損次數':>8s} {'止損平均虧':>12s}")
print(f"  {'-'*105}")

for slip_pct, label in [(0, "0% 無滑價（理想）"),
                         (0.1, "10% 滑價"),
                         (0.25, "25% 滑價（樂觀）"),
                         (0.5, "50% 滑價（中性）"),
                         (0.75, "75% 滑價（悲觀）"),
                         (1.0, "100% bar extreme（最差）")]:
    tdf = run(df, sl_slip_pct=slip_pct)
    r = s(tdf)
    print(f"  {label:<25s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f} {r['sl_n']:>8d} ${r['sl_avg']:>+10.2f}")

# ============================================================
# 測試 2：SL 緩衝優化（50% 滑價下）
# ============================================================
print(f"\n{'='*120}")
print("Test 2: SL 緩衝 ATR 倍數優化（固定 50% 滑價）")
print(f"{'='*120}")

print(f"\n  {'SL緩衝':<20s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'止損次數':>8s}")
print(f"  {'-'*85}")

for buf in [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5]:
    tdf = run(df, sl_buffer=buf, sl_slip_pct=0.5)
    r = s(tdf)
    mark = " <-- 目前" if buf == 0.3 else ""
    print(f"  {buf:.1f}x ATR{mark:<14s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f} {r['sl_n']:>8d}")

# ============================================================
# 測試 3：槓桿優化（50% 滑價 + 最佳 SL 緩衝）
# ============================================================
print(f"\n{'='*120}")
print("Test 3: 槓桿優化（50% 滑價）")
print(f"{'='*120}")

print(f"\n  {'槓桿':<15s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'每單名義':>10s}")
print(f"  {'-'*80}")

for lev in [5, 10, 15, 20, 25]:
    tdf = run(df, leverage=lev, sl_slip_pct=0.5)
    r = s(tdf)
    notional = 100 * lev
    mark = " <-- 目前" if lev == 20 else ""
    print(f"  {lev}x{mark:<12s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f} ${notional:>9,d}")

# ============================================================
# 測試 4：SL 模式（結構 vs 固定 ATR）
# ============================================================
print(f"\n{'='*120}")
print("Test 4: 止損模式（50% 滑價）")
print(f"{'='*120}")

print(f"\n  {'止損模式':<25s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s} {'止損次數':>8s}")
print(f"  {'-'*90}")

for mode, label in [("structure", "結構 Swing+0.3ATR（目前）"),
                      ("fixed_2x", "固定 2.0x ATR"),
                      ("fixed_3x", "固定 3.0x ATR")]:
    tdf = run(df, sl_mode=mode, sl_slip_pct=0.5)
    r = s(tdf)
    print(f"  {label:<25s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f} {r['sl_n']:>8d}")

# 結構止損 + 更大緩衝
for buf in [0.5, 0.7, 1.0]:
    tdf = run(df, sl_buffer=buf, sl_slip_pct=0.5)
    r = s(tdf)
    print(f"  {"結構 Swing+" + str(buf) + "ATR":<25s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f} {r['sl_n']:>8d}")

# ============================================================
# 測試 5：TP1 距離優化
# ============================================================
print(f"\n{'='*120}")
print("Test 5: TP1 距離優化（50% 滑價）")
print(f"{'='*120}")

print(f"\n  {'TP1距離':<15s} {'交易數':>7s} {'損益(PnL)':>14s} {'勝率(WR)':>10s} {'獲利因子(PF)':>14s} {'回撤(DD)':>10s}")
print(f"  {'-'*70}")

for tp1 in [0.5, 0.75, 1.0, 1.5, 2.0]:
    tdf = run(df, tp1_mult=tp1, sl_slip_pct=0.5)
    r = s(tdf)
    mark = " <-- 目前" if tp1 == 1.0 else ""
    print(f"  {tp1:.1f}x ATR{mark:<8s} {r['n']:>7d} ${r['pnl']:>+12,.2f} {r['wr']:>9.1f}% {r['pf']:>13.2f} ${r['dd']:>9,.2f}")

# ============================================================
# 測試 6：最佳組合搜索
# ============================================================
print(f"\n{'='*120}")
print("Test 6: 最佳組合搜索（50% 滑價）")
print(f"{'='*120}")

best_combos = []
for buf in [0.3, 0.5, 0.7, 1.0]:
    for lev in [10, 15, 20]:
        for tp1 in [1.0, 1.5]:
            for mode in ["structure", "fixed_2x"]:
                tdf = run(df, sl_buffer=buf, leverage=lev, tp1_mult=tp1, sl_mode=mode, sl_slip_pct=0.5)
                r = s(tdf)
                if r["n"] > 50:
                    best_combos.append({
                        "buf":buf, "lev":lev, "tp1":tp1, "mode":mode, **r
                    })

best_combos.sort(key=lambda x: x["pnl"], reverse=True)

print(f"\n  Top 10 by PnL:")
print(f"  {'Rank':>4s} {'SL緩衝':>8s} {'槓桿':>5s} {'TP1':>5s} {'止損模式':>10s} {'交易':>6s} {'損益':>12s} {'勝率':>7s} {'PF':>7s} {'回撤':>10s}")
print(f"  {'-'*85}")

for i, c in enumerate(best_combos[:10]):
    print(f"  {i+1:>4d} {c['buf']:.1f}x     {c['lev']:>3d}x {c['tp1']:.1f}x {c['mode']:>10s} {c['n']:>6d} ${c['pnl']:>+10,.0f} {c['wr']:>6.1f}% {c['pf']:>6.2f} ${c['dd']:>9,.0f}")

# Walk-Forward for top 3
print(f"\n  Walk-Forward (Top 3, 50% slippage):")
split = int(len(df) * (4/6))
train = df.iloc[:split].reset_index(drop=True)
test = df.iloc[split:].reset_index(drop=True)

for i, c in enumerate(best_combos[:3]):
    tdf_oos = run(test, sl_buffer=c["buf"], leverage=c["lev"], tp1_mult=c["tp1"],
                  sl_mode=c["mode"], sl_slip_pct=0.5)
    r_oos = s(tdf_oos)
    print(f"    #{i+1} buf={c['buf']} lev={c['lev']} tp1={c['tp1']} {c['mode']}: "
          f"OOS ${r_oos['pnl']:>+10,.2f} WR {r_oos['wr']}% PF {r_oos['pf']}")

# ============================================================
# 圖表
# ============================================================
fig, axes = plt.subplots(2, 2, figsize=(20, 14))
fig.suptitle("SL 滑價優化：找出真實交易下的最佳參數", fontsize=14, fontweight="bold")

# 圖 1: 滑價敏感度
ax = axes[0][0]
slips = [0, 0.1, 0.25, 0.5, 0.75, 1.0]
pnls = [s(run(df, sl_slip_pct=sp))["pnl"] for sp in slips]
ax.plot([x*100 for x in slips], pnls, 'b-o', linewidth=2, markersize=8)
ax.axhline(0, color="red", linewidth=1, linestyle="--")
ax.fill_between([25, 75], [min(pnls)]*2, [max(pnls)]*2, alpha=0.1, color="green", label="Realistic range")
ax.set_xlabel("Slippage % (of SL-to-bar-extreme gap)"); ax.set_ylabel("PnL ($)")
ax.set_title("SL 滑價比例 vs 策略損益"); ax.legend(); ax.grid(True, alpha=0.3)
for x, y in zip([x*100 for x in slips], pnls):
    ax.annotate(f"${y:+,.0f}", (x, y), textcoords="offset points", xytext=(0, 10), ha="center", fontsize=8)

# 圖 2: SL 緩衝優化
ax = axes[0][1]
bufs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0, 1.5]
pnls_buf = [s(run(df, sl_buffer=b, sl_slip_pct=0.5))["pnl"] for b in bufs]
ax.plot(bufs, pnls_buf, 'g-o', linewidth=2, markersize=8)
ax.axhline(0, color="red", linewidth=1, linestyle="--")
ax.axvline(0.3, color="orange", linewidth=1, linestyle="--", label="Current (0.3x)")
best_buf_idx = np.argmax(pnls_buf)
ax.axvline(bufs[best_buf_idx], color="green", linewidth=2, linestyle="--", label=f"Best ({bufs[best_buf_idx]}x)")
ax.set_xlabel("SL Buffer (ATR multiplier)"); ax.set_ylabel("PnL ($)")
ax.set_title("SL 緩衝倍數 vs 損益 (50% slippage)"); ax.legend(); ax.grid(True, alpha=0.3)

# 圖 3: 槓桿優化
ax = axes[1][0]
levs = [5, 10, 15, 20, 25]
pnls_lev = [s(run(df, leverage=l, sl_slip_pct=0.5))["pnl"] for l in levs]
dds_lev = [abs(s(run(df, leverage=l, sl_slip_pct=0.5))["dd"]) for l in levs]
ax2 = ax.twinx()
ax.bar(levs, pnls_lev, width=3, color=["#4CAF50" if p>0 else "#F44336" for p in pnls_lev], alpha=0.7, edgecolor="black")
ax2.plot(levs, dds_lev, "ro-", linewidth=2, markersize=8)
ax.set_xlabel("Leverage"); ax.set_ylabel("PnL ($)"); ax2.set_ylabel("Max DD ($)", color="red")
ax.set_title("Leverage vs PnL & Drawdown (50% slippage)"); ax.grid(True, alpha=0.3)

# 圖 4: 最佳組合表
ax = axes[1][1]; ax.axis("off")
table_h = ["Rank","SL\nBuffer","Leverage","TP1","SL Mode","Trades","PnL","WR","PF","DD"]
table_r = []
for i, c in enumerate(best_combos[:8]):
    table_r.append([str(i+1), f"{c['buf']}x", f"{c['lev']}x", f"{c['tp1']}x",
                    c["mode"][:8], str(c["n"]), f"${c['pnl']:+,.0f}",
                    f"{c['wr']}%", f"{c['pf']}", f"${c['dd']:,.0f}"])
table = ax.table(cellText=table_r, colLabels=table_h, cellLoc="center", loc="center")
table.auto_set_font_size(False); table.set_fontsize(8); table.scale(1.0, 1.7)
for j in range(10):
    table[0, j].set_facecolor("#4472C4"); table[0, j].set_text_props(color="white", fontweight="bold")
for j in range(10): table[1, j].set_facecolor("#FFF2CC")
ax.set_title("Top 8 Combos (50% slippage)", fontweight="bold", pad=20)

plt.tight_layout()
plt.savefig(r"C:\Users\neil.chang\workspace\btc-strategy-research\final\btc_sl_optimization.png",
            dpi=150, bbox_inches="tight")
print(f"\nChart saved.")
plt.show()

# ============================================================
# 結論
# ============================================================
print(f"\n{'='*120}")
print("CONCLUSION")
print(f"{'='*120}")

best = best_combos[0]
print(f"\n  50% 滑價下最佳組合：")
print(f"    SL 緩衝：{best['buf']}x ATR")
print(f"    槓桿：{best['lev']}x")
print(f"    TP1：{best['tp1']}x ATR")
print(f"    止損模式：{best['mode']}")
print(f"    損益：${best['pnl']:+,.2f}")
print(f"    勝率：{best['wr']}%  PF：{best['pf']}  DD：${best['dd']:,.2f}")
