"""Q20W30＋固定風險下單：回測、逐事件重播與 paper 共用的決策邏輯（純函式，無副作用、不碰 API）。
規格：doc/research_results/v77/v77_plan.md 第 1 節。"""
import math

RISK_F = 0.03; LEV = 5.0; STOP = {'L': 0.035, 'S': 0.040}; STEP = 0.001; MIN_NTL = 20.0; MMR = 0.005; Q20_TH = 20.0; HC = 0.001125

def floor_qty(x, step=STEP): return math.floor(x / step + 1e-9) * step

def paused(vol_rank):
    """Q20W30：百分位 < 20 時不進場。NaN（暖機不足）視為不暫停，與研究引擎一致。"""
    return (vol_rank == vol_rank) and vol_rank < Q20_TH

def order_qty(wallet, side, price, snm=1.0, f=RISK_F):
    """數量＝f×錢包÷(止損距離×snm)÷價格，向下取整到 STEP。"""
    return floor_qty(f * wallet / (STOP[side] * snm) / price)

def check_order(qty, price, equity, used_margin, lev=LEV):
    """回傳 (可否下單, 原因, 需要保證金)。"""
    ntl = qty * price; need = ntl / lev
    if abs(qty / STEP - round(qty / STEP)) > 1e-6: return False, '數量精度不符', need
    if ntl < MIN_NTL: return False, '名目低於最小下單額', need
    if equity - used_margin < need: return False, '可用保證金不足', need
    return True, '', need

def stop_price(side, ep, snm=1.0): return ep * (1 - STOP[side] * snm) if side == 'L' else ep * (1 + STOP[side] * snm)

def liq_price(side, ep, lev=LEV, mmr=MMR):
    s = 1 if side == 'L' else -1; return ep * (1 - s / lev) / (1 - s * mmr)

def risk_at_stop(qty, side, ep, wallet, snm=1.0):
    """止損價成交時的價差損失占錢包比例（不含費用、穿價與資金費）。"""
    return qty * ep * STOP[side] * snm / wallet
