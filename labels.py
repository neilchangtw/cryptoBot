"""共用中文(英文)詞彙對照 — 出場原因 / 進場趨勢(regime) / 方向。

所有顯示層（analyze.py / run_backtest.py / check_health.py / Telegram 通知）統一從這裡取，
要改用詞只改這一個檔。輸出格式固定為「中文 (英文代號)」。

另含全形字（中日韓）終端對齊用的顯示寬度工具：CJK 字算 2 格，ljust_disp 依顯示寬度補空白，
讓含中文的欄位在等寬終端機仍能對齊。
"""

import unicodedata

# 出場原因：同時涵蓋「回測引擎短碼」與「實盤 trades.csv 的 exit_type 全名」→ 對到同一中文
_EXIT = {
    # 回測引擎短碼（v14_export_trades）
    "TP": "止盈",
    "SN": "安全網",
    "MFE": "浮盈回吐",
    "MH": "最長持倉",
    "MHx": "延長超時",
    "BE": "平保",
    # 實盤 exit_type / strategy.py reason 全名
    "SafeNet": "安全網",
    "MFE-trail": "浮盈回吐",
    "MaxHold": "最長持倉",
    "MH-ext": "延長超時",
}

# 進場趨勢 regime（V14+R SMA200 斜率分類）
_REGIME = {
    "UP": "多頭",
    "MILD_UP": "偏多",
    "SIDE": "盤整",
    "DOWN": "空頭",
    "NA": "未知",
}

# 方向
_SIDE = {
    "L": "做多",
    "S": "做空",
    "long": "做多",
    "short": "做空",
}


def _fmt(zh, code):
    """中文 (英文)；查無對照則只回原碼。"""
    code = str(code).strip()
    return f"{zh} ({code})" if zh else code


def exit_label(code):
    code = str(code or "").strip()
    if not code:
        return ""
    return _fmt(_EXIT.get(code, ""), code)


def regime_label(code):
    code = str(code or "").strip()
    if not code:
        return ""
    return _fmt(_REGIME.get(code, ""), code)


def side_label(code):
    code = str(code or "").strip()
    if not code:
        return ""
    return _fmt(_SIDE.get(code, ""), code)


# ── 終端對齊（全形字寬度）──
def disp_width(s) -> int:
    """字串在等寬終端的顯示寬度（中日韓全形 = 2 格）。"""
    w = 0
    for ch in str(s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def ljust_disp(s, width: int) -> str:
    """依顯示寬度靠左補空白（含中文欄位也能對齊）。"""
    s = str(s)
    pad = width - disp_width(s)
    return s + " " * pad if pad > 0 else s


def rjust_disp(s, width: int) -> str:
    """依顯示寬度靠右補空白。"""
    s = str(s)
    pad = width - disp_width(s)
    return " " * pad + s if pad > 0 else s
