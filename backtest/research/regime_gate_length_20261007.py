r"""離線 Regime gate 長度研究；既有策略、引擎、資料及環境設定均不寫入。

執行：.venv\Scripts\python.exe -B backtest\research\regime_gate_length_20261007.py
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "doc/research_results/20261007_regime_gate_length"
REPORT = ROOT / "doc/regime_gate_length_research_20261007.md"
DATA = ROOT / "data/ETHUSDT_1h_latest730d.csv"
ENGINE = ROOT / "backtest/research/v14_export_trades.py"
COST = ROOT / "data/public_cost_history_20260908"
LENGTHS = [10, 20, 24, 30, 40, 50, 75, 100, 125, 150, 200, 250, 300]
FAMILIES = ["fixed_joint", "fixed_gate_only", "scaled_joint"]
START = 510
SPLIT = pd.Timestamp("2026-01-01")
EXTRA_COST = 5.0


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64, datetime)):
        return str(value)
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write_json(name, obj):
    (OUT / name).write_text(json.dumps(clean(obj), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def production_definitions():
    """只抽取純函式及 literal 常數；不 import strategy，不讀取 .env。"""
    tree = ast.parse((ROOT / "strategy.py").read_text(encoding="utf-8-sig"))
    constants = {}
    wanted = {"R_TH_UP", "R_TH_SIDE", "R_SMA_WIN", "R_SLOPE_WIN", "L_TP_BY_REGIME", "L_MH_BY_REGIME", "S_MH_BY_REGIME"}
    fn = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted:
            constants[node.targets[0].id] = ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "classify_regime":
            fn = copy.deepcopy(node)
    assert set(constants) == wanted and fn is not None
    scope = {"pd": pd, **constants}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<pure production classifier>", "exec"), scope)
    return constants, scope["classify_regime"]


def load_engine():
    """跳過會 import strategy 的 try 區塊，其他引擎來源原樣執行於隔離 namespace。"""
    constants, classifier = production_definitions()
    tree = ast.parse(ENGINE.read_text(encoding="utf-8-sig"))
    body = []
    skipped = 0
    for node in tree.body:
        if isinstance(node, ast.Try) and any(isinstance(x, ast.ImportFrom) and x.module == "strategy" for x in node.body):
            skipped += 1
            continue
        body.append(node)
    assert skipped == 1
    scope = {"__file__": str(ENGINE), "__name__": "isolated_regime_research", "_classify_regime": classifier,
             "_L_TP_BR": constants["L_TP_BY_REGIME"], "_L_MH_BR": constants["L_MH_BY_REGIME"],
             "_S_MH_BR": constants["S_MH_BY_REGIME"]}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ENGINE), "exec"), scope)
    for name in ["R_TH_UP", "R_TH_SIDE", "R_SMA_WIN", "R_SLOPE_WIN"]:
        assert scope[name] == constants[name]
    original = scope["simulate_v14_detailed"]
    node = copy.deepcopy(next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "simulate_v14_detailed"))
    additions = 0
    for call in ast.walk(node):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == "trades" and call.func.attr == "append":
            row = call.args[0]
            side = dict(zip([k.value for k in row.keys], row.values))["side"].value
            pref = "lp" if side == "L" else "sp"
            fields = {"entry_exact": "ep", "exit_exact": "ex_price", "pnl_exact": "pnl",
                      "qty_exact": f"{pref}_ntl / ep", "fee_exact": f"{pref}_fee"}
            for key, expression in fields.items():
                row.keys.append(ast.Constant(key))
                row.values.append(ast.parse(expression, mode="eval").body)
            additions += 1
    assert additions == 2
    names = ["lp_active", "lp_entry", "lp_ntl", "lp_fee", "lp_bar", "lp_held", "lp_mfe", "lp_mae", "lp_reduced", "lp_ext", "lp_ext_bars", "lp_regime",
             "sp_active", "sp_entry", "sp_ntl", "sp_fee", "sp_bar", "sp_held", "sp_mfe", "sp_mae", "sp_ext", "sp_ext_bars", "sp_regime",
             "l_last_exit", "s_last_exit", "cur_month", "l_m_entries", "s_m_entries", "l_m_pnl", "s_m_pnl", "cur_day", "d_pnl", "consec", "consec_end"]
    returns = [x for x in ast.walk(node) if isinstance(x, ast.Return)]
    assert len(returns) == 1 and isinstance(returns[0].value, ast.Name) and returns[0].value.id == "trades"
    returns[0].value = ast.Tuple(elts=[ast.Name(id="trades", ctx=ast.Load()), ast.Dict(keys=[ast.Constant(x) for x in names], values=[ast.Name(id=x, ctx=ast.Load()) for x in names])], ctx=ast.Load())
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    ledger_scope = dict(scope)
    exec(compile(module, "<research ledger instrumentation>", "exec"), ledger_scope)
    schedule_tree = ast.parse((ROOT / "run_backtest.py").read_text(encoding="utf-8-sig"))
    schedule = next(ast.literal_eval(x.value) for x in schedule_tree.body if isinstance(x, ast.Assign) and isinstance(x.targets[0], ast.Name) and x.targets[0].id == "MARGIN_SCHEDULE")
    return scope, original, ledger_scope["simulate_v14_detailed"], schedule


def feature_set(base, close, length, family):
    sma = pd.Series(close).rolling(200).mean()
    raw = ((sma - sma.shift(length)) / sma.shift(length)).shift(1).to_numpy()
    measured = raw * (100.0 / length) if family == "scaled_joint" else raw
    result = dict(base)
    result["regime_block_l"] = (measured > .045) & np.isfinite(measured)
    result["regime_block_s"] = (np.abs(measured) < .010) & np.isfinite(measured)
    result["slope"] = base["slope"] if family == "fixed_gate_only" else measured
    return result


def drawdown(values):
    x = np.r_[0., np.asarray(values, float)]
    return float(np.max(np.maximum.accumulate(x) - x))


def trade_frame(raw):
    f = pd.DataFrame(raw)
    assert len(f), "本研究預期應有交易"
    f["entry_time"] = pd.to_datetime(f.entry_dt) + pd.Timedelta(hours=1)
    f["exit_time"] = pd.to_datetime(f.exit_dt) + pd.Timedelta(hours=1)
    return f


def price_equity(raw, terminal, d, prices):
    """精確 entry/exit 與拆半 FEE；額外 $5 於平倉扣除，期末未實現部位保留。"""
    cash = np.zeros(len(d))
    floating = np.zeros(len(d))
    positions = []
    for t in raw:
        a, b = int(t["entry_bar"]), int(t["exit_bar"])
        cash[a] -= t["fee_exact"] / 2
        cash[b] += t["pnl_exact"] + t["fee_exact"] / 2
        sign = 1 if t["side"] == "L" else -1
        floating[a:b] += sign * t["qty_exact"] * (prices[a:b] - t["entry_exact"])
        positions.append({"side": t["side"], "a": a, "b": b, "entry": t["entry_exact"], "qty": t["qty_exact"], "reason": t["exit_reason"], "closed": True})
    open_count = 0
    for side, pref in [("L", "lp"), ("S", "sp")]:
        if terminal[pref + "_active"]:
            a = int(terminal[pref + "_bar"])
            entry = terminal[pref + "_entry"]
            qty = terminal[pref + "_ntl"] / entry
            cash[a] -= terminal[pref + "_fee"] / 2
            floating[a:] += (1 if side == "L" else -1) * qty * (prices[a:] - entry)
            positions.append({"side": side, "a": a, "b": len(d) - 1, "entry": entry, "qty": qty, "reason": "OPEN", "closed": False})
            open_count += 1
    cash = cash.cumsum()
    return cash + floating, cash, floating, positions, open_count


def summary(raw, terminal, d, prices):
    f = trade_frame(raw)
    equity, cash, floating, positions, opened = price_equity(raw, terminal, d, prices)
    pnl = f.pnl_exact.to_numpy()
    losses = -pnl[pnl < 0].sum()
    times = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    result = {"trades": len(f), "closed_pnl": float(pnl.sum()), "display_pnl": float(f.pnl_usd.sum()),
              "wr": float((pnl > 0).mean() * 100), "pf": float(pnl[pnl > 0].sum() / losses) if losses else None,
              "realized_mdd": drawdown(pnl.cumsum()), "price_mdd": drawdown(equity),
              "end_price_equity": float(equity[-1]), "end_floating": float(floating[-1]), "open_positions": opened,
              "worst30_price": float((equity[720:] - equity[:-720]).min()),
              "L_pnl": float(f.loc[f.side.eq("L"), "pnl_exact"].sum()), "S_pnl": float(f.loc[f.side.eq("S"), "pnl_exact"].sum()),
              "L_trades": int(f.side.eq("L").sum()), "S_trades": int(f.side.eq("S").sum()),
              "holding_hours": int(f.bars_held.sum()) + sum(int(terminal[p + "_held"]) for p in ["lp", "sp"] if terminal[p + "_active"]),
              "fee": float(f.fee_exact.sum()), "extra_cost": len(f) * EXTRA_COST,
              "exits": f.exit_reason.value_counts().to_dict()}
    for name, mask in [("early", times < SPLIT), ("late", times >= SPLIT), ("recent", times >= pd.Timestamp("2026-06-01"))]:
        indices = np.flatnonzero(mask)
        result[name + "_pnl"] = float(f.loc[mask[f.exit_bar.to_numpy(dtype=int)], "pnl_exact"].sum())
        result[name + "_trades"] = int(mask[f.exit_bar.to_numpy(dtype=int)].sum())
        result[name + "_equity_change"] = float(equity[indices[-1]] - (equity[indices[0]-1] if indices[0] else 0.)) if len(indices) else None
    edges = np.linspace(START, len(d), 7, dtype=int)
    for j, (a, b) in enumerate(zip(edges[:-1], edges[1:]), 1):
        result[f"block{j}_pnl"] = float(f.loc[(f.exit_bar >= a) & (f.exit_bar < b), "pnl_exact"].sum())
        result[f"block{j}_equity_change"] = float(equity[b-1] - (equity[a-1] if a else 0.))
    return result, f, equity, positions


def replacement(candidate, base):
    merged = candidate.merge(base, on=["side", "entry_bar"], suffixes=("_c", "_b"), how="outer", indicator=True)
    common = merged._merge.eq("both")
    removed = merged._merge.eq("right_only")
    added = merged._merge.eq("left_only")
    changed = common & ((merged.exit_bar_c != merged.exit_bar_b) | (abs(merged.pnl_exact_c - merged.pnl_exact_b) > 1e-8))
    return {"common_entries": int(common.sum()), "removed_entries": int(removed.sum()), "replacement_entries": int(added.sum()),
            "changed_common": int(changed.sum()), "removed_winners": int((removed & (merged.pnl_exact_b > 0)).sum()),
            "removed_losers": int((removed & (merged.pnl_exact_b < 0)).sum()),
            "removed_pnl": float(merged.loc[removed, "pnl_exact_b"].sum()), "replacement_pnl": float(merged.loc[added, "pnl_exact_c"].sum()),
            "changed_common_delta": float((merged.loc[common, "pnl_exact_c"] - merged.loc[common, "pnl_exact_b"]).sum())}


def funding_equity(raw, terminal, d, marks, fund):
    equity, cash, floating, positions, opened = price_equity(raw, terminal, d, marks)
    times = pd.DatetimeIndex(d.datetime + pd.Timedelta(hours=1))
    flows = np.zeros(len(d))
    ledger = []
    low = high = 0.
    ft = fund.nominal.to_numpy()
    for p in positions:
        a, b = p["a"], p["b"]
        entry, exit_ = times[a], times[b]
        for k in np.flatnonzero((ft >= entry.to_datetime64()) & (ft <= exit_.to_datetime64())):
            event = fund.iloc[k]
            nominal = event.nominal
            sign = 1 if p["side"] == "L" else -1
            possible = -sign * p["qty"] * float(event.markPrice) * float(event.fundingRate)
            boundary = nominal == entry or (p["closed"] and nominal == exit_)
            included = nominal > entry and (not p["closed"] or nominal < exit_ or p["reason"] != "SN")
            value = possible if included else 0.
            at = times.get_indexer([nominal])[0]
            assert at >= 0
            flows[at] += value
            low += min(0., possible) if boundary else value
            high += max(0., possible) if boundary else value
            ledger.append({"side": p["side"], "entry_bar": a, "time": nominal, "value": value, "possible": possible, "boundary": boundary, "included": included})
    funded = equity + flows.cumsum()
    return {"funding": float(flows.sum()), "funding_low": float(low), "funding_high": float(high),
            "funded_end_equity": float(funded[-1]), "mark_mdd": drawdown(funded),
            "worst30_mark": float((funded[720:] - funded[:-720]).min()), "open_positions": opened}, funded, ledger


def parity_and_prefix(engine, original, simulate, d, base, schedule):
    checks = []
    original_keys = None
    for start in [310, START]:
        for historical in [False, True]:
            for slip in [0, 2, 5]:
                args = dict(start_bar=start, realistic=True, slip_bps=slip, margin_schedule=schedule if historical else None, extra_cost=EXTRA_COST)
                raw = original(base, d.datetime.to_numpy(), **args)
                instrumented, terminal = simulate(base, d.datetime.to_numpy(), **args)
                original_keys = list(raw[0])
                assert raw == [{k: t[k] for k in original_keys} for t in instrumented]
                s, _, _, _ = summary(instrumented, terminal, d, d.close.to_numpy())
                checks.append({"check": "no_op_all_trade_columns", "warmup": start, "sizing": "schedule" if historical else "flat", "slip": slip, "trades": len(raw), "closed_pnl": s["closed_pnl"], "open_positions": s["open_positions"], "status": "PASS"})
    for family in FAMILIES:
        hundred = feature_set(base, d.close.to_numpy(), 100, family)
        for key in ["slope", "regime_block_l", "regime_block_s"]:
            np.testing.assert_array_equal(hundred[key], base[key])
    prefix_checks = []
    for family in ["fixed_joint", "scaled_joint"]:
        for length in [30, 50, 100, 150, 300]:
            ind = feature_set(base, d.close.to_numpy(), length, family)
            full_raw, _ = simulate(ind, d.datetime.to_numpy(), start_bar=START, realistic=True, extra_cost=EXTRA_COST)
            # 以最早已知入場的當棒收盤作確定持倉截點；僅供因果性測試，不選參數。
            cuts = sorted(set([len(d)//2, len(d)*3//4, len(d)-12, min(x["entry_bar"] for x in full_raw) + 1]))
            for cut in cuts:
                prefix_base = engine["compute_indicators"](d.iloc[:cut])
                independently = feature_set(prefix_base, d.close.iloc[:cut].to_numpy(), length, family)
                for key in base:
                    np.testing.assert_array_equal(ind[key][:cut], independently[key])
                rows, terminal = simulate(independently, d.datetime.iloc[:cut].to_numpy(), start_bar=START, realistic=True, extra_cost=EXTRA_COST)
                assert rows == [x for x in full_raw if x["exit_bar"] < cut]
                prefix_checks.append({"family": family, "length": length, "cut_bar": cut, "closed_trades": len(rows), "L_active": bool(terminal["lp_active"]), "S_active": bool(terminal["sp_active"]), "status": "PASS"})
    assert any(x["L_active"] or x["S_active"] for x in prefix_checks), "前綴稽核必須涵蓋仍持倉情況"
    return checks, prefix_checks


def table(frame, columns, formats=None):
    formats = formats or {}
    lines = ["| " + " | ".join(label for _, label in columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    for _, row in frame.iterrows():
        vals = []
        for key, _ in columns:
            v = row[key]
            vals.append(formats[key].format(v) if key in formats else str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def report(results, cost_results, registration, verification, stored, gates):
    primary = results.query("sizing == 'flat' and slip == 0")
    baseline = primary.query("family == 'fixed_joint' and length == 100").iloc[0]
    winners = {}
    for family in FAMILIES:
        q = primary[primary.family.eq(family)].sort_values(["closed_pnl", "price_mdd"], ascending=[False, True])
        winners[family] = int(q.iloc[0].length)
    out = ["# Regime gate 回看長度回測研究（2026-10-07）", "",
           "## 結論", "",
           f"在本次固定候選範圍及成本假設下，直接改回看長度、門檻維持現行值的全期歷史最高淨利為 **{winners['fixed_joint']} 小時**。這是歷史回測冠軍，不是已證明未來最佳；本次 **NO_PROMOTION / DATA_LIMITED**。所有既有策略與指標檔案均未修改。", "",
           f"資料 K 棒開盤範圍：**{registration['data']['first_open']}～{registration['data']['last_open']}（Asia/Taipei）**；最後已收盤時間為 **{registration['data']['last_close']}**。共同交易起點：{registration['data']['common_start_open']} K 棒、收盤 {registration['data']['common_start_close']}。", "",
           "## 定義與比較範圍", "",
           "H = SMA200 的相對變化回看小時，SMA 窗口始終為 200，不是把均線改成 SMA30／50／150。斜率 s_H(t)=SMA200(t-1)/SMA200(t-H-1)-1。候選固定為 " + "、".join(map(str, LENGTHS)) + " 小時。", "",
           "- fixed_joint：原始 s_H、L >4.5% 擋、S |s|<1% 擋；V25-D 入場 regime 同樣使用 s_H。這是主要答案。\n- fixed_gate_only：進場 gate 使用 s_H，但 V25-D 出場用原 s_100 的 entry regime，隔離進場效果。\n- scaled_joint：gate 和 regime 使用 s_H×100/H；門檻在原始斜率尺度等於 4.5%×H/100 和 1%×H/100。只做事前指定的线性尺度換算，沒有重新尋優門檻。", "",
           "完整狀態引擎保留 L/S 持倉、冷卻、月進場 cap、日／月熔斷、連虧冷卻、SafeNet→TP→MFE/extension→MH 優先序。沒有從舊交易表刪列；每組均重新回放，含新增／被移除／改變的後續交易。", "",
           "## 成交、成本與起點", "",
           "主比較固定 200U 保證金、20x、$4,000 名目，FEE $4＋每筆 $5 執行緩衝；realistic=True、市價進場與 TP/BE 等採收盤價格，SafeNet 沿用既有 25% 穿透模型。額外滑價 0／2／5bp 套用既有引擎，會影響後續風控和替代交易。另列 CLI 既有保證金排程，絕不與固定 200U 數字混用。", "",
           "共同 start_bar=510，讓 H=300 有完整有效資料；短 H 不提前啟動搶得額外交易。原 310 根起點另外做 no-op 重現。排名採精確未四捨五入的已平倉 PnL；與 CLI 按逐筆兩位小數相加會有小額差異。價格逐時 MDD 包含持倉浮動損益，已實現 MDD 是另一个數字。", "",
           "## 所有長度結果（固定 200U、0bp）", ""]
    for family in FAMILIES:
        q = primary[primary.family.eq(family)].sort_values("length").copy()
        q["delta"] = q.closed_pnl - baseline.closed_pnl
        out += [f"### {family}（歷史冠軍 {winners[family]}h）", "",
                table(q, [("length", "H 小時"), ("trades", "筆數"), ("closed_pnl", "已平倉淨利 $"), ("delta", "較100h $"), ("wr", "勝率 %"), ("pf", "PF"), ("price_mdd", "價格逐時MDD $"), ("early_pnl", "2026前 $"), ("late_pnl", "2026年 $")],
                      {"length": "{:.0f}", "trades": "{:.0f}", "closed_pnl": "{:,.2f}", "delta": "{:+,.2f}", "wr": "{:.2f}", "pf": "{:.3f}", "price_mdd": "{:,.2f}", "early_pnl": "{:,.2f}", "late_pnl": "{:,.2f}"}), ""]
    target_lengths = sorted(set([30, 50, 100, 150, *winners.values()]))
    out += ["## 基準、使用者指定長度與冠軍的成本／保證金對照", "",
            table(results[(results.family == "fixed_joint") & results.length.isin(target_lengths)].sort_values(["sizing", "slip", "length"]),
                  [("sizing", "保證金"), ("slip", "額外bp"), ("length", "H"), ("trades", "筆數"), ("closed_pnl", "已平倉淨利 $"), ("price_mdd", "價格逐時MDD $"), ("end_price_equity", "期末淨值損益 $"), ("open_positions", "未平倉")],
                  {"closed_pnl": "{:,.2f}", "price_mdd": "{:,.2f}", "end_price_equity": "{:,.2f}"}), "",
            "全部三組、13 長度、兩種保證金、三種滑價共 **234 組**結果見 results.csv；上述表僅節錄便於閱讀。期末未平倉不強制捏造平倉，未實現浮動及已扣進場半筆 FEE 反映在期末淨值中。", "",
            "## 歷史分段與穩健性", "",
            "2026-01-01 固定切分；只用 2026 年以前已平倉損益選長度，再查看 2026 年及六區塊表現。這些歷史已用於舊研究，稱『歷史驗證分段』，不能稱全新未見 OOS。各分段沿用全期狀態，不在邊界重設熔斷／倉位。", ""]
    selections = []
    for family in FAMILIES:
        discovery = primary[primary.family.eq(family)].sort_values(["early_pnl", "price_mdd"], ascending=[False, True]).iloc[0]
        selected = primary[(primary.family == family) & (primary.length == winners[family])].iloc[0]
        for label, row in [("全期選出", selected), ("2026前選出", discovery)]:
            deltas = [float(row[f"block{i}_equity_change"] - baseline[f"block{i}_equity_change"]) for i in range(1, 7)]
            selections.append({"family": family, "selection": label, "length": int(row.length), "early_pnl": row.early_pnl, "late_pnl": row.late_pnl,
                               "late_delta": row.late_pnl - baseline.late_pnl, "positive_blocks": sum(x > 1e-8 for x in deltas), "block_deltas": "/".join(f"{x:+.0f}" for x in deltas)})
    select_df = pd.DataFrame(selections)
    select_df.to_csv(OUT / "historical_selections.csv", index=False, encoding="utf-8-sig")
    out += [table(select_df, [("family", "對照"), ("selection", "選擇依據"), ("length", "H"), ("early_pnl", "2026前 $"), ("late_pnl", "2026年 $"), ("late_delta", "2026年較100h $"), ("positive_blocks", "改善區塊/6"), ("block_deltas", "六區塊淨值差 $")], {"early_pnl": "{:,.2f}", "late_pnl": "{:,.2f}", "late_delta": "{:+,.2f}"}), "",
            "六區塊依共同交易起點到資料截止等長切分，邊界詳見 registration.json。使用逐時價格淨值變動衡量區塊收益，因此跨區塊持倉不會把全部獲利錯分到平倉日。這是穩健性切片，沒有對每個區塊重選最佳 H，也不是正式完整 walk-forward 模型選擇。", "",
            "## Funding 與 mark 淨值交集稽核", "",
            f"本機公開成本資料與價格交集最後收盤：**{registration['cost_data']['last_close']}**。所有 234 組都在相同交集截止重新回放，不能將它的 funding／MDD 直接加到 9/18 全期排名。", "",
            "Funding 按持倉方向、精確數量、公開 fundingRate×markPrice 估算；整點進場不計當次，普通收盤出場計當次，SafeNet 出場不計當次。進出場恰逢結算另列低／高範圍。Funding 後附帳戶現金，不改写原生交易 PnL 熔斷。這保持回測引擎一致，但不等於交易所逐筆資金流水對帳。", ""]
    cq = cost_results[(cost_results.family == "fixed_joint") & (cost_results.sizing == "flat") & cost_results.length.isin(target_lengths)].sort_values(["slip", "length"])
    out += [table(cq, [("slip", "bp"), ("length", "H"), ("closed_pnl", "交集已平倉 $"), ("funding", "Funding $"), ("funded_end_equity", "含funding期末 $"), ("mark_mdd", "逐時mark MDD $"), ("funding_low", "Funding低 $"), ("funding_high", "Funding高 $")], {k: "{:,.2f}" for k in ["closed_pnl", "funding", "funded_end_equity", "mark_mdd", "funding_low", "funding_high"]}), "",
            "完整交集結果见 cost_results.csv。逐時 mark MDD 不包含每小時內極值、真實委託簿／成交、交易所精度／最小名目／強平與本金不足限制，不代表最大可能虧損。", "",
            "## 最佳長度的進出場歸因", ""]
    for family, h in winners.items():
        row = primary[(primary.family == family) & (primary.length == h)].iloc[0]
        delta = row.closed_pnl - baseline.closed_pnl
        out += [f"- {family} {h}h：較100h {delta:+,.2f}；L {row.L_pnl:,.2f}／S {row.S_pnl:,.2f}。移除原交易 {int(row.removed_entries)} 筆（贏 {int(row.removed_winners)}／虧 {int(row.removed_losers)}），新／替代 {int(row.replacement_entries)} 筆、相同入場但結果改變 {int(row.changed_common)} 筆。",
                f"  - 原交易移除淨利 {row.removed_pnl:+,.2f}（對改善的貢獻取反號）；替代交易淨利 {row.replacement_pnl:+,.2f}；共同交易改善 {row.changed_common_delta:+,.2f}。三者相加 = 總改善，已通過帳本恆等式。"]
    out += ["", "gate_counts.csv 列出 GK＋breakout＋session 潛在事件、Regime 過濾後事件與有效斜率覆蓋；其數量是進場之前的漏斗，實際成交仍受完整狀態限制。", "",
            "## 驗證", "",
            f"- 原生來源函式與研究帳本函式：**{len(verification['no_op'])} 組逐欄 no-op PASS**（兩暖機×兩保證金×三滑價）。\n- 三種100h指標／gate與原指標完全一致。\n- **{len(verification['prefix'])} 組特徵＋交易前綴 PASS**，包含期末持倉；截短資料不改變此前交易。\n- OHLC／時間連續性、mark逐根對齊、funding8h連續性、變更保護雜湊均通過。\n- 原生 simulate 的精確 PnL 用於風控；儀表／報表四捨五入不回寫。\n- git HEAD：`{registration['head']}`；研究前既有未追蹤文件保留。", "",
            "## 判定與資料限制", "",
            "**HISTORICAL_WINNER；NO_PROMOTION；DATA_LIMITED。** 用全部資料挑最高者會有多重比較及事後選擇偏誤；短／長尺度之間的收益差不能用單一近期交易解釋。未做真實全新 OOS、正式候選完整10-Gate或前瞻 shadow，也没有門檻最佳化。本次結果可回答『這批歷史、這些固定候選，哪個回測最高』，不能回答未來保證哪個最佳。", "",
            "既有主策略、指標程式、.env、狀態、live CSV 與 VPS 均未修改，沒有下單／部署／重啟／commit／push。研究程式也不 import 會讀取環境／交易連線的入口；只抽取純分類函式及常數，執行隔離的既有回測來源。", "",
            "## 重現與檔案", "",
            "```powershell\n.\\.venv\\Scripts\\python.exe -B backtest\\research\\regime_gate_length_20261007.py\n```", "",
            "輸入若和 registration.json 不同會拒絕覆蓋本次研究。文件與全部結果在 `doc/research_results/20261007_regime_gate_length/`：registration.json、results.csv、cost_results.csv、all_trades.csv、gate_counts.csv、historical_selections.csv、verification.json、terminal_states.json、selected_equity.csv、selected_funding.csv、summary.json、regime_length_comparison.png。", "",
            "研究設計見 [預登記](regime_gate_length_plan_20261007.md)，原始100h研究見 [V23](v23_research.md)。", ""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    write_json("summary.json", {"winners": winners, "baseline": baseline.to_dict(), "selections": selections, "status": ["HISTORICAL_WINNER", "NO_PROMOTION", "DATA_LIMITED"]})
    return winners


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    protected_paths = list(ROOT.glob("*.py")) + [ROOT / "AGENTS.md", ENGINE, DATA, COST / "mark_1h_full.csv", COST / "funding_full.csv", ROOT / "doc/maxhold_adaptive_learning_20260924.md"]
    protected_paths += [x for x in [ROOT / ".env", ROOT / "eth_state.json", ROOT / "eth_state_live.json", ROOT / "monitor_state.json"] if x.exists()]
    if (ROOT / "data_live").exists():
        protected_paths += list((ROOT / "data_live").glob("*.csv"))
    # 不把 secrets／runtime 雜湊發布到研究登記；仍在記憶體核對未變更。
    protected = {str(x.relative_to(ROOT)): digest(x) for x in protected_paths if x.is_file()}
    public_hashes = {k: v for k, v in protected.items() if not k.startswith("data_live/") and k not in [".env", "eth_state.json", "eth_state_live.json", "monitor_state.json"]}
    d = pd.read_csv(DATA, parse_dates=["datetime"])
    assert d.datetime.is_unique and d.datetime.is_monotonic_increasing
    assert d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert np.isfinite(d[["open", "high", "low", "close", "volume"]].to_numpy()).all()
    assert (d[["open", "high", "low", "close"]] > 0).all().all()
    assert (d.high >= d[["open", "close", "low"]].max(axis=1)).all() and (d.low <= d[["open", "close", "high"]].min(axis=1)).all()
    assert (d.datetime.iloc[-1] + pd.Timedelta(hours=1)) < pd.Timestamp("2026-10-07 00:00:00")
    engine, original, simulate, schedule = load_engine()
    base = engine["compute_indicators"](d)
    mark = pd.read_csv(COST / "mark_1h_full.csv")
    mark.index = pd.to_datetime(mark.open_time, unit="ms", utc=True).dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    assert mark.index.is_unique and mark.index.to_series().diff().dropna().eq(pd.Timedelta(hours=1)).all()
    cost_count = int((d.datetime <= mark.index[-1]).sum())
    dc = d.iloc[:cost_count].copy()
    marks = mark.reindex(pd.DatetimeIndex(dc.datetime)).close.to_numpy()
    assert np.isfinite(marks).all() and (marks > 0).all()
    fund = pd.read_csv(COST / "funding_full.csv")
    fund["nominal"] = pd.to_datetime(fund.fundingTime, unit="ms", utc=True).dt.tz_convert("Asia/Taipei").dt.tz_localize(None).dt.round("h")
    assert fund.nominal.is_unique and fund.nominal.diff().dropna().eq(pd.Timedelta(hours=8)).all()
    assert np.isfinite(fund[["fundingRate", "markPrice"]].to_numpy()).all() and (fund.markPrice > 0).all()
    fund = fund[(fund.nominal >= dc.datetime.iloc[0] + pd.Timedelta(hours=1)) & (fund.nominal <= dc.datetime.iloc[-1] + pd.Timedelta(hours=1))].reset_index(drop=True)
    edges = np.linspace(START, len(d), 7, dtype=int)
    registration = {"registered_at_utc": datetime.now(timezone.utc).isoformat(), "research_date_taipei": "2026-10-07", "head": git("rev-parse", "HEAD"),
                    "initial_git_status": git("status", "--short"), "lengths": LENGTHS, "families": FAMILIES, "common_start_bar": START,
                    "primary": "fixed_joint flat200 0bp full closed_pnl descending; tie price_mdd ascending", "extra_cost": EXTRA_COST,
                    "slip_bps": [0, 2, 5], "sizing": {"flat": 200, "schedule": schedule}, "data": {"rows": len(d), "first_open": str(d.datetime.iloc[0]), "last_open": str(d.datetime.iloc[-1]),
                    "last_close": str(d.datetime.iloc[-1] + pd.Timedelta(hours=1)), "common_start_open": str(d.datetime.iloc[START]), "common_start_close": str(d.datetime.iloc[START] + pd.Timedelta(hours=1))},
                    "cost_data": {"rows": cost_count, "funding_rows": len(fund), "last_close": str(dc.datetime.iloc[-1] + pd.Timedelta(hours=1))},
                    "blocks": [{"start_open": str(d.datetime.iloc[a]), "end_last_close": str(d.datetime.iloc[b-1] + pd.Timedelta(hours=1))} for a, b in zip(edges[:-1], edges[1:])],
                    "public_input_hashes": public_hashes, "script_sha256": digest(__file__), "plan_sha256": digest(ROOT / "doc/regime_gate_length_plan_20261007.md"),
                    "runtime": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__}, "no_network": True,
                    "status": "registered before candidate PnL"}
    if (OUT / "registration.json").exists():
        old = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
        assert old["public_input_hashes"] == public_hashes, "輸入或策略已改變，禁止覆寫本次研究"
        assert old["lengths"] == LENGTHS and old["families"] == FAMILIES
        if old["script_sha256"] != digest(__file__):
            revision = json.loads((OUT / "implementation_revision.json").read_text(encoding="utf-8"))
            assert revision["registered_sha256"] == old["script_sha256"]
            assert revision["current_sha256"] == digest(__file__)
            assert revision["candidate_pnl_inspected_before_revision"] is False
        registration = old
    else:
        write_json("registration.json", registration)
    print(f"已預登記：{len(d)} 根，最後收盤 {registration['data']['last_close']}，共同起點 {START}，13 長度 × 3 對照。", flush=True)
    features = {(family, h): feature_set(base, d.close.to_numpy(), h, family) for family in FAMILIES for h in LENGTHS}
    gate_rows = []
    for (family, length), ind in features.items():
        valid = np.isfinite(ind["slope"])
        for side in ["L", "S"]:
            session = ~np.isin(base["hours"], list(engine[f"{side}_BLK_H"])) & ~np.isin(base["dows"], list(engine[f"{side}_BLK_D"]))
            potential = (base[f"pctile_{side}"] < engine[f"{side}_GK_TH"]) & base["brk_up" if side == "L" else "brk_dn"] & session
            potential[:START] = False
            passed = potential & ~ind[f"regime_block_{side.lower()}"]
            gate_rows.append({"family": family, "length": length, "side": side, "potential": int(potential.sum()), "after_regime": int(passed.sum()), "blocked": int((potential & ~passed).sum()), "valid_after_common_start": int(valid[START:].sum())})
    gate_df = pd.DataFrame(gate_rows)
    gate_df.to_csv(OUT / "gate_counts.csv", index=False, encoding="utf-8-sig")
    print("開始 no-op 與特徵／交易前綴稽核。", flush=True)
    noop, prefixes = parity_and_prefix(engine, original, simulate, d, base, schedule)
    write_json("verification_pre_pnl.json", {"no_op": noop, "prefix": prefixes, "status": "PASS before candidate ranking"})
    print(f"no-op {len(noop)} 組、前綴 {len(prefixes)} 組全部 PASS；开始 234 組全期及234組成本交集回測。", flush=True)
    rows, costs, all_trades, terminals = [], [], [], {}
    stored, base_frames, base_cost_frames = {}, {}, {}
    # 100h先跑，用於完整替代交易歸因；同長度其他families仍逐組核對。
    ordered = [100] + [h for h in LENGTHS if h != 100]
    for sizing in ["flat", "schedule"]:
        for slip in [0, 2, 5]:
            args = dict(start_bar=START, realistic=True, slip_bps=slip, margin_schedule=schedule if sizing == "schedule" else None, extra_cost=EXTRA_COST)
            for family in FAMILIES:
                for length in ordered:
                    ind = features[family, length]
                    raw, terminal = simulate(ind, d.datetime.to_numpy(), **args)
                    stats, frame, equity, _ = summary(raw, terminal, d, d.close.to_numpy())
                    if length == 100 and family == "fixed_joint":
                        base_frames[sizing, slip] = frame
                    paired = replacement(frame, base_frames[sizing, slip])
                    key = {"family": family, "length": length, "sizing": sizing, "slip": slip}
                    baseline_pnl = float(base_frames[sizing, slip].pnl_exact.sum())
                    assert abs(stats["closed_pnl"] - baseline_pnl - (-paired["removed_pnl"] + paired["replacement_pnl"] + paired["changed_common_delta"])) < 1e-7
                    rows.append({**key, **stats, **paired})
                    run_id = f"{family}_{length}_{sizing}_{slip}"
                    terminals[run_id] = terminal
                    save = frame.assign(**key)
                    all_trades.append(save)
                    stored[family, length, sizing, slip] = (frame, equity)
                    ci = {k: v[:cost_count] for k, v in ind.items()}
                    craw, ct = simulate(ci, dc.datetime.to_numpy(), **args)
                    cs, _, _, _ = summary(craw, ct, dc, dc.close.to_numpy())
                    fs, funded, ledger = funding_equity(craw, ct, dc, marks, fund)
                    costs.append({**key, **cs, **fs})
                    stored["cost", family, length, sizing, slip] = (funded, ledger)
                print(f"完成 {sizing} / {slip}bp / {family}，累計 {len(rows)} 組全期＋交集。", flush=True)
    result = pd.DataFrame(rows)
    cost_result = pd.DataFrame(costs)
    result.to_csv(OUT / "results.csv", index=False, encoding="utf-8-sig")
    cost_result.to_csv(OUT / "cost_results.csv", index=False, encoding="utf-8-sig")
    pd.concat(all_trades, ignore_index=True).to_csv(OUT / "all_trades.csv", index=False, encoding="utf-8-sig")
    write_json("terminal_states.json", terminals)
    for path, value in protected.items():
        assert digest(ROOT / path) == value, f"受保護檔案變更: {path}"
    verification = {"no_op": noop, "prefix": prefixes, "data_quality": "PASS", "100h_features_all_families": "PASS", "protected_files_unchanged": True,
                    "protected_file_count": len(protected), "head_unchanged": git("rev-parse", "HEAD") == registration["head"], "status": "PASS"}
    assert verification["head_unchanged"]
    write_json("verification.json", verification)
    winners = report(result, cost_result, registration, verification, stored, gate_df)
    selected_curves, selected_funding = [], []
    for family, length in {"baseline": 100, **winners}.items():
        source_family = "fixed_joint" if family == "baseline" else family
        for slip in [0, 2, 5]:
            _, eq = stored[source_family, length, "flat", slip]
            selected_curves.append(pd.DataFrame({"time": d.datetime + pd.Timedelta(hours=1), "family": family, "length": length, "slip": slip, "price_equity_pnl": eq}))
            funded, ledger = stored["cost", source_family, length, "flat", slip]
            if ledger:
                selected_funding.append(pd.DataFrame(ledger).assign(family=family, length=length, slip=slip))
    pd.concat(selected_curves, ignore_index=True).to_csv(OUT / "selected_equity.csv", index=False, encoding="utf-8-sig")
    if selected_funding:
        pd.concat(selected_funding, ignore_index=True).to_csv(OUT / "selected_funding.csv", index=False, encoding="utf-8-sig")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
        primary = result.query("sizing == 'flat' and slip == 0")
        for family in FAMILIES:
            q = primary[primary.family == family].sort_values("length")
            axes[0].plot(q.length, q.closed_pnl, marker="o", label=family)
            axes[1].plot(q.length, q.price_mdd, marker="o", label=family)
        for ax in axes:
            ax.axvline(100, linestyle="--", color="grey", alpha=.6)
            ax.grid(alpha=.25)
            ax.legend()
        axes[0].set_ylabel("Closed net PnL (USD)")
        axes[0].set_title("ETH 1h regime lookback | flat 200U | FEE + $5 | extra slippage 0bp")
        axes[1].set_ylabel("Hourly price-equity MDD (USD)")
        axes[1].set_xlabel("SMA200 slope lookback (hours)")
        fig.tight_layout()
        fig.savefig(OUT / "regime_length_comparison.png", dpi=180)
        plt.close(fig)
    except ImportError:
        print("matplotlib不可用，保留完整CSV與Markdown表格。", flush=True)
    print(json.dumps({"winners": winners, "report": str(REPORT), "runs": len(rows), "cost_runs": len(costs), "verification": "PASS"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
