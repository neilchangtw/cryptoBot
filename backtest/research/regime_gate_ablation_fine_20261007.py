r"""離線gate消融與100～109h局部研究；不修改或覆寫任何既有策略或研究。

.venv\Scripts\python.exe -B backtest\research\regime_gate_ablation_fine_20261007.py
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import itertools
import json
import sys

import numpy as np
import pandas as pd

import regime_gate_length_20261007 as prior

ROOT = prior.ROOT
OUT = ROOT / "doc/research_results/20261007_regime_gate_ablation_fine"
REPORT = ROOT / "doc/regime_gate_ablation_fine_research_20261007.md"
OLD = prior.OUT
PLAN = ROOT / "doc/regime_gate_ablation_fine_plan_20261007.md"
CONFIGS = ([{"case": f"joint_{h}", "family": "fixed_joint", "length": h} for h in range(100, 110)]
           + [{"case": f"gate_only_{h}", "family": "fixed_gate_only", "length": h} for h in range(100, 110)]
           + [{"case": "no_gate_all", "family": "fixed_gate_only", "length": 100, "disable": ["L", "S"]},
              {"case": "no_gate_L", "family": "fixed_gate_only", "length": 100, "disable": ["L"]},
              {"case": "no_gate_S", "family": "fixed_gate_only", "length": 100, "disable": ["S"]},
              {"case": "anchor_joint125", "family": "fixed_joint", "length": 125},
              {"case": "anchor_scaled150", "family": "scaled_joint", "length": 150}])


def save(name, obj):
    (OUT / name).write_text(json.dumps(prior.clean(obj), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def make_features(base, close, cfg):
    result = prior.feature_set(base, close, cfg["length"], cfg["family"])
    for side in cfg.get("disable", []):
        result["regime_block_" + side.lower()] = np.zeros(len(close), dtype=bool)
    return result


def regime_labels(slope):
    return np.select([~np.isfinite(slope), slope > .045, np.abs(slope) < .010, slope < -.010], ["NA", "UP", "SIDE", "DOWN"], default="MILD_UP")


def audit_frames(all_trades, result, curves, funded_ledgers, costs):
    checks = []
    index = result.set_index(["case", "sizing", "slip"])
    cindex = costs.set_index(["case", "sizing", "slip"])
    for key, f in all_trades.groupby(["case", "sizing", "slip"]):
        row = index.loc[key]
        assert len(f) == row.trades
        assert abs(f.pnl_exact.sum() - row.closed_pnl) < 1e-7
        gross = np.where(f.side.eq("L"), 1, -1) * (f.exit_exact - f.entry_exact) * f.qty_exact
        np.testing.assert_allclose(gross - f.fee_exact - 5., f.pnl_exact, rtol=1e-12, atol=1e-9)
        np.testing.assert_allclose(f.qty_exact * f.entry_exact, f.margin * 20, rtol=1e-12, atol=1e-9)
        np.testing.assert_allclose(f.fee_exact, f.margin / 200 * 4, rtol=1e-12, atol=1e-9)
        assert (f.exit_bar - f.entry_bar).eq(f.bars_held).all()
        assert (f.entry_time - pd.to_datetime(f.entry_dt)).eq(pd.Timedelta(hours=1)).all()
        assert (f.exit_time - pd.to_datetime(f.exit_dt)).eq(pd.Timedelta(hours=1)).all()
        assert (f.entry_bar >= prior.START).all()
        for side, cooldown, days in [("L", 6, [5, 6]), ("S", 8, [0, 5, 6])]:
            q = f[f.side.eq(side)].sort_values("entry_bar")
            times = pd.to_datetime(q.entry_dt)
            assert not times.dt.hour.isin([0, 1, 2, 12]).any() and not times.dt.dayofweek.isin(days).any()
            if len(q) > 1:
                assert (q.entry_bar.to_numpy()[1:] - q.exit_bar.to_numpy()[:-1] >= cooldown).all()
            assert q.groupby(times.dt.to_period("M")).size().max() <= 20
        assert abs(curves[key][-1] - row.end_price_equity) < 1e-7
        assert abs(sum(x["value"] for x in funded_ledgers[key]) - cindex.loc[key].funding) < 1e-7
        assert abs(row.closed_pnl - index.loc[("joint_100", key[1], key[2])].closed_pnl - (-row.removed_pnl + row.replacement_pnl + row.changed_common_delta)) < 1e-7
        checks.append({"case": key[0], "sizing": key[1], "slip": key[2], "status": "PASS"})
    assert len(checks) == 150
    return checks


def describe(result, costs, reg, verification, gates):
    q = result.query("sizing == 'flat' and slip == 0").set_index("case")
    base, off = q.loc["joint_100"], q.loc["no_gate_all"]
    fine = q[q.family.eq("fixed_joint") & q.length.between(100, 109)].sort_values(["closed_pnl", "price_mdd", "length"], ascending=[False, True, True])
    only = q[q.family.eq("fixed_gate_only") & q.length.between(100, 109) & ~q.index.str.startswith("no_gate")].sort_values(["closed_pnl", "price_mdd", "length"], ascending=[False, True, True])
    win, onlywin = fine.iloc[0], only.iloc[0]
    win_id, only_id = fine.index[0], only.index[0]
    def cost_delta(case, sizing="flat"):
        return [float(result.loc[result.case.eq(case) & result.sizing.eq(sizing) & result.slip.eq(slip), "closed_pnl"].iloc[0]
                      - result.loc[result.case.eq("joint_100") & result.sizing.eq(sizing) & result.slip.eq(slip), "closed_pnl"].iloc[0]) for slip in [0, 2, 5]]
    def values(a):
        return "／".join(f"{x:+,.2f}" for x in a)
    columns = [("length", "小時"), ("trades", "筆數"), ("closed_pnl", "淨利 $"), ("delta", "較100h $"), ("wr", "勝率%"), ("pf", "PF"), ("price_mdd", "價格逐時MDD $"), ("affected", "受影響交易"), ("early_pnl", "2026前 $"), ("late_pnl", "2026年 $")]
    fmt = {k: "{:,.2f}" for k in ["closed_pnl", "price_mdd", "early_pnl", "late_pnl"]}
    fmt.update({"delta": "{:+,.2f}", "wr": "{:.2f}", "pf": "{:.3f}", "length": "{:.0f}", "trades": "{:.0f}", "affected": "{:.0f}"})
    no_table = q.loc[["joint_100", "no_gate_all", "no_gate_L", "no_gate_S"]].reset_index().copy()
    no_table["setting"] = no_table.case.map({"joint_100": "現行100h", "no_gate_all": "取消L/S gate", "no_gate_L": "只取消L gate", "no_gate_S": "只取消S gate"})
    text = ["# Regime gate 移除與100～109小時細部研究（2026-10-07）", "",
            "## 結論", "",
            f"取消L/S進場Regime gate、保留V25-D出場後，全期固定200U／0bp已平倉淨利為 **${off.closed_pnl:,.2f}**，較100h **${off.delta:+,.2f}（{off.delta/base.closed_pnl*100:+.2f}%）**；價格逐時MDD **${off.price_mdd:,.2f}**，基準為 **${base.price_mdd:,.2f}**。", "",
            f"100～109h的同步進出場分類歷史冠軍為 **{int(win.length)}h**，淨利 **${win.closed_pnl:,.2f}**，較100h **${win.delta:+,.2f}**。只改進場gate、出場仍用100h時，冠軍為 **{int(onlywin.length)}h**，淨利 **${onlywin.closed_pnl:,.2f}**，較100h **${onlywin.delta:+,.2f}**。", "",
            "這輪是上一輪結果之後的局部探索，並非新OOS。**NO_PROMOTION / DATA_LIMITED**；沒有修改任何既有策略／指標／引擎、.env、live資料或VPS，上一輪研究檔也全部保留。", "",
            "## 共同條件與移除gate的定義", "",
            f"價格資料開盤範圍 {reg['data']['first_open']}～{reg['data']['last_open']}，最後收盤 **{reg['data']['last_close']}（台北）**。共同交易起點仍為510：{reg['data']['common_start_close']}收盤。", "",
            "SMA窗口固定200，GK、15-bar breakout、時段、TP/MH參數表及風控全部沿用現行引擎。realistic=True；主比較固定200U、20x、$4,000名目、FEE $4＋每筆$5，另加0／2／5bp壓測；排程200→300@2026-07-03→500@2026-08-01單獨呈現。", "",
            "取消Regime gate僅將L/S的進場阻擋設為False，entry regime仍取原100h slope，V25-D條件出場仍保留。因此這個對照是『現行策略取消進場過濾』，不是退回早期純V14，也不是把所有regime功能一併刪掉。另取消單邊gate作歸因對照。", "",
            "100～109h有兩組：fixed_joint讓gate和V25-D入場分類一起使用s_H；fixed_gate_only只有gate用s_H，出場分類仍使用s_100。兩組門檻都固定+4.5%／|s|1%，不重新調門檻。", "",
            "每組重播完整L/S持倉、占倉、替代交易、冷卻、cap及日／月／連虧風控，保留SafeNet／TP／MFE／extension／MH優先序。沒有從舊交易表刪列估算；精確PnL用於風控和排名，顯示兩位小數不回寫。期末仍持倉則記錄浮盈與已扣進場費，不強制捏造平倉。", "",
            "## 不加入Regime gate的結果（固定200U、0bp）", "",
            prior.table(no_table, [("setting", "設定"), ("trades", "筆數"), ("closed_pnl", "淨利 $"), ("delta", "較100h $"), ("wr", "勝率%"), ("pf", "PF"), ("price_mdd", "价格逐時MDD $"), ("L_pnl", "L淨利 $"), ("S_pnl", "S淨利 $")], {**fmt, "L_pnl": "{:,.2f}", "S_pnl": "{:,.2f}"}), "",
            "## 100～109h：gate與出場分類同步改長度", "",
            prior.table(fine.sort_values("length"), columns, fmt), "",
            "## 100～109h：只改進場gate，出場分類維持100h", "",
            prior.table(only.sort_values("length"), columns, fmt), "",
            "『受影響交易』為相對同成本100h的移除入場＋新增／替代入場＋相同入場但出場或損益改變。這不是獨立樣本數；同一市場episode的連鎖交易不能當多個獨立證據。", "",
            "## 1小時差異從哪裡來", "",
            "每小時斜率數值會變，但只有跨過4.5%／1%門檻且其他進場條件成立，或V25-D入場分類變化，才會改變交易。少數翻轉仍可能因占倉／冷卻／熔斷改變後續交易。", ""]
    counts = gates[gates.case.str.match(r"joint_10[0-9]$")].copy()
    text += [prior.table(counts.sort_values(["length", "side"]), [("length", "H"), ("side", "方向"), ("potential", "GK+突破+時段"), ("after_regime", "通過regime"), ("newly_blocked", "較100h新阻擋"), ("newly_allowed", "較100h新放行"), ("entry_regime_changed", "潛在事件分類改變")]), "",
             "gate_counts.csv與gate_changed_events.csv保留所有設定及決策時資訊。潛在事件是尚未套持倉／風控的漏斗，不能等同實際成交。", "",
             "## 成本與保證金敏感度", "",
             f"取消L/S gate在固定200U的0／2／5bp較100h分別為 **{values(cost_delta('no_gate_all'))}**；排程為 **{values(cost_delta('no_gate_all','schedule'))}**。", "",
             f"同步分類冠軍{int(win.length)}h在固定200U的0／2／5bp較100h：**{values(cost_delta(win_id))}**；排程：**{values(cost_delta(win_id,'schedule'))}**。", "",
             f"純進場gate冠軍{int(onlywin.length)}h在固定200U的0／2／5bp較100h：**{values(cost_delta(only_id))}**；排程：**{values(cost_delta(only_id,'schedule'))}**。", ""]
    chosen = sorted(set(["joint_100", "no_gate_all", win_id, only_id, "anchor_joint125", "anchor_scaled150"]))
    ct = result[result.case.isin(chosen)].sort_values(["sizing", "slip", "case"])
    text += [prior.table(ct, [("case", "設定"), ("sizing", "保證金"), ("slip", "bp"), ("closed_pnl", "淨利 $"), ("delta", "較100h $"), ("price_mdd", "價格逐時MDD $"), ("open_positions", "未平倉")], fmt), "",
             "兩個anchor是上一輪已知設定：固定門檻125h，以及門檻按時間換算150h（原始門檻6.75%／1.5%）。它們不是本輪重新選出的獨立證據。", "",
             "## 分段、事件歸因及是否穩定", ""]
    for case in ["no_gate_all", win_id, only_id]:
        row = q.loc[case]
        blocks = [row[f"block{i}_equity_change"] - base[f"block{i}_equity_change"] for i in range(1, 7)]
        text += [f"- {case}：2026年前較100h **${row.early_pnl-base.early_pnl:+,.2f}**，2026年 **${row.late_pnl-base.late_pnl:+,.2f}**；六等長區塊有 **{sum(x>1e-8 for x in blocks)}/6** 改善，區塊淨值差為 {values(blocks)}。",
                 f"  - 移除原入場{int(row.removed_entries)}筆（原贏{int(row.removed_winners)}／虧{int(row.removed_losers)}）；新增／替代{int(row.replacement_entries)}筆，共同入場出場／損益改變{int(row.changed_common)}筆。移除原淨利取反號 ${-row.removed_pnl:+,.2f}＋新增淨利 ${row.replacement_pnl:+,.2f}＋共同交易差 ${row.changed_common_delta:+,.2f}＝改善 ${row.delta:+,.2f}。"]
    text += ["", "2026前／2026年與六区塊只做历史穩健性描述，全部保留連續狀態，不在邊界重設帳戶。既有历史已多次研究，不能將看到全期結果後挑出的最佳小時稱為未見驗證或統計顯著。", "",
             "## Funding／mark完整交集", "",
             f"所有150組在共同成本資料截止 **{reg['cost_data']['last_close']}** 重新回放；結果不與9/18全期混合。Funding仍後附帳戶現金、不改寫原生交易PnL熔斷。整點進場不計當次，普通收盤出場計當次、SafeNet不計當次；結算邊界低／高值記錄於cost_results.csv。", "",
             prior.table(costs[(costs.sizing == "flat") & costs.case.isin(chosen)].sort_values(["slip", "case"]), [("case", "設定"), ("slip", "bp"), ("closed_pnl", "交集淨利 $"), ("funding", "Funding $"), ("funded_end_equity", "含funding期末 $"), ("mark_mdd", "逐時mark MDD $")], {**fmt, "funding": "{:,.2f}", "funded_end_equity": "{:,.2f}", "mark_mdd": "{:,.2f}"}), "",
             "逐時價格／mark回撤不能捕捉盤中所有極值；沒有交易所逐筆fills、資金流水、訂單簿／精度／強平或本金不足限制，因此不是實盤最大損失保證。", "",
             "## 驗證與研究狀態", "",
             f"原生100h逐欄交易及上一輪基準：{len(verification['no_op'])}情境PASS。新候選特徵與交易前綴：{len(verification['prefix'])}組PASS，含持倉截點。150組完整帳本核對PASS；上一輪全部產物、既有核心檔／輸入／環境／live資料雜湊及HEAD不變。", "",
             "**NO_PROMOTION / DATA_LIMITED。** 對100～109的掃描是明確授權的事後探索，沒有新增未見資料；沒有為提高收益補掃其他小時或重調門檻，也沒有實盤修改。數據冠軍只代表本批資料和指定比較範圍的最高值。", "",
             "## 重現與檔案", "",
             r"```powershell" + "\n" + r".\.venv\Scripts\python.exe -B backtest\research\regime_gate_ablation_fine_20261007.py" + "\n```", "",
             "本輪全部結果：`doc/research_results/20261007_regime_gate_ablation_fine/` 的registration.json、results.csv、cost_results.csv、all_trades.csv、gate_counts.csv、gate_changed_events.csv、verification_pre_pnl.json、verification.json、terminal_states.json、summary.json、artifact_manifest.json。輸入／腳本雜湊不符時拒絕覆寫。", "",
             "[本輪預登記](regime_gate_ablation_fine_plan_20261007.md)；[上一輪廣泛長度比較](regime_gate_length_research_20261007.md)。", ""]
    REPORT.write_text("\n".join(text).replace("价格", "價格").replace("区塊", "區塊").replace("历史", "歷史"), encoding="utf-8")
    save("summary.json", {"baseline": base.to_dict(), "no_gate_all": off.to_dict(), "fine_joint_winner": win.to_dict(), "fine_gate_only_winner": onlywin.to_dict(),
                          "fine_joint_winner_id": win_id, "fine_gate_only_winner_id": only_id, "status": ["NO_PROMOTION", "DATA_LIMITED"]})
    return {"no_gate_delta": float(off.delta), "joint_winner": int(win.length), "joint_delta": float(win.delta), "gate_only_winner": int(onlywin.length), "gate_only_delta": float(onlywin.delta)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    old_registration = json.loads((OLD / "registration.json").read_text(encoding="utf-8"))
    old_manifest = json.loads((OLD / "artifact_manifest.json").read_text(encoding="utf-8"))
    protected = {**{name: item["sha256"] for name, item in old_manifest.items()}, **old_registration["public_input_hashes"]}
    protected[str((OLD / "artifact_manifest.json").relative_to(ROOT))] = prior.digest(OLD / "artifact_manifest.json")
    for name, value in protected.items():
        assert prior.digest(ROOT / name) == value, f"上一輪資料或程式已變更：{name}"
    private_paths = [x for x in [ROOT / ".env", ROOT / "eth_state.json", ROOT / "eth_state_live.json", ROOT / "monitor_state.json"] if x.exists()]
    if (ROOT / "data_live").exists():
        private_paths += list((ROOT / "data_live").glob("*.csv"))
    private_hashes = {path: prior.digest(path) for path in private_paths}
    reg = {"registered_at_utc": datetime.now(timezone.utc).isoformat(), "date_taipei": "2026-10-07", "head": prior.git("rev-parse", "HEAD"),
           "initial_git_status": prior.git("status", "--short"), "configs": CONFIGS, "data": old_registration["data"], "cost_data": old_registration["cost_data"],
           "start_bar": prior.START, "cost": {"extra_cost": 5, "slip": [0, 2, 5], "realistic": True, "sizing": old_registration["sizing"]},
           "protected_public_hashes": protected, "script_sha256": prior.digest(__file__), "plan_sha256": prior.digest(PLAN), "reuse_prior_noop": True,
           "ranking": "closed_pnl descending, price_mdd ascending, length ascending", "new_unseen_oos": False, "no_network": True}
    if (OUT / "registration.json").exists():
        saved = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
        for key in ["configs", "protected_public_hashes", "script_sha256", "plan_sha256"]:
            assert saved[key] == reg[key], "登記後的範圍、輸入或腳本不可改寫"
        reg = saved
    else:
        save("registration.json", reg)
    print("已預登記25設定：100～109h兩組、取消L/S/兩邊gate、兩個上一輪anchor。", flush=True)
    d = pd.read_csv(prior.DATA, parse_dates=["datetime"])
    assert d.datetime.is_unique and d.datetime.diff().dropna().eq(pd.Timedelta(hours=1)).all()
    assert len(d) == reg["data"]["rows"] and str(d.datetime.iloc[-1]) == reg["data"]["last_open"]
    engine, original, simulate, schedule = prior.load_engine()
    base = engine["compute_indicators"](d)
    features = {cfg["case"]: make_features(base, d.close.to_numpy(), cfg) for cfg in CONFIGS}
    for name in ["joint_100", "gate_only_100"]:
        for key in base:
            np.testing.assert_array_equal(base[key], features[name][key])
    old_results = pd.read_csv(OLD / "results.csv")
    noop = []
    for sizing, slip in itertools.product(["flat", "schedule"], [0, 2, 5]):
        args = dict(start_bar=510, realistic=True, slip_bps=slip, margin_schedule=schedule if sizing == "schedule" else None, extra_cost=5.)
        raw = original(base, d.datetime.to_numpy(), **args)
        exact, terminal = simulate(base, d.datetime.to_numpy(), **args)
        keys = list(raw[0])
        assert raw == [{key: t[key] for key in keys} for t in exact]
        anchor = old_results.query("family == 'fixed_joint' and length == 100 and sizing == @sizing and slip == @slip").iloc[0]
        assert len(exact) == anchor.trades and abs(sum(t["pnl_exact"] for t in exact) - anchor.closed_pnl) < 1e-7
        noop.append({"sizing": sizing, "slip": slip, "status": "PASS"})
    prefix = []
    check_cases = ["joint_101", "joint_109", "gate_only_109", "no_gate_all", "no_gate_L", "no_gate_S"]
    prefix_base_cache = {}
    for cfg in [x for x in CONFIGS if x["case"] in check_cases]:
        full, _ = simulate(features[cfg["case"]], d.datetime.to_numpy(), start_bar=510, realistic=True, extra_cost=5.)
        cuts = sorted(set([len(d)//2, len(d)-12, min(t["entry_bar"] for t in full)+1]))
        for cut in cuts:
            if cut not in prefix_base_cache:
                prefix_base_cache[cut] = engine["compute_indicators"](d.iloc[:cut])
            short = make_features(prefix_base_cache[cut], d.close.iloc[:cut].to_numpy(), cfg)
            for key in base:
                np.testing.assert_array_equal(short[key], features[cfg["case"]][key][:cut])
            rows, terminal = simulate(short, d.datetime.iloc[:cut].to_numpy(), start_bar=510, realistic=True, extra_cost=5.)
            assert rows == [t for t in full if t["exit_bar"] < cut]
            prefix.append({"case": cfg["case"], "cut": cut, "L_active": bool(terminal["lp_active"]), "S_active": bool(terminal["sp_active"]), "status": "PASS"})
    assert any(x["L_active"] or x["S_active"] for x in prefix)
    save("verification_pre_pnl.json", {"no_op": noop, "prefix": prefix, "status": "PASS"})
    print(f"6個100h基準與{len(prefix)}個前綴檢查PASS，開始候選比較。", flush=True)
    gate_rows, events = [], []
    for cfg in CONFIGS:
        ind = features[cfg["case"]]
        for side in ["L", "S"]:
            session = ~np.isin(base["hours"], list(engine[f"{side}_BLK_H"])) & ~np.isin(base["dows"], list(engine[f"{side}_BLK_D"]))
            potential = (base[f"pctile_{side}"] < engine[f"{side}_GK_TH"]) & base["brk_up" if side == "L" else "brk_dn"] & session
            potential[:510] = False
            key = "regime_block_" + side.lower()
            newly_blocked = potential & ind[key] & ~base[key]
            newly_allowed = potential & ~ind[key] & base[key]
            label_changed = potential & (regime_labels(ind["slope"]) != regime_labels(base["slope"]))
            gate_rows.append({"case": cfg["case"], "length": cfg["length"], "side": side, "potential": int(potential.sum()), "after_regime": int((potential & ~ind[key]).sum()),
                              "newly_blocked": int(newly_blocked.sum()), "newly_allowed": int(newly_allowed.sum()), "entry_regime_changed": int(label_changed.sum())})
            for i in np.flatnonzero(newly_blocked | newly_allowed | label_changed):
                events.append({"case": cfg["case"], "side": side, "bar": int(i), "decision_time": d.datetime.iloc[i]+pd.Timedelta(hours=1), "blocked100": bool(base[key][i]), "candidate_blocked": bool(ind[key][i]),
                               "exit_slope100": float(base["slope"][i]), "candidate_exit_slope": float(ind["slope"][i]), "new_block": bool(newly_blocked[i]), "new_allow": bool(newly_allowed[i]), "regime_changed": bool(label_changed[i])})
    gate_df = pd.DataFrame(gate_rows)
    gate_df.to_csv(OUT / "gate_counts.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(events).to_csv(OUT / "gate_changed_events.csv", index=False, encoding="utf-8-sig")
    dc = d.iloc[:reg["cost_data"]["rows"]].copy()
    mark = pd.read_csv(prior.COST / "mark_1h_full.csv")
    mark.index = pd.to_datetime(mark.open_time, unit="ms", utc=True).dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    marks = mark.reindex(pd.DatetimeIndex(dc.datetime)).close.to_numpy()
    assert np.isfinite(marks).all()
    fund = pd.read_csv(prior.COST / "funding_full.csv")
    fund["nominal"] = pd.to_datetime(fund.fundingTime, unit="ms", utc=True).dt.tz_convert("Asia/Taipei").dt.tz_localize(None).dt.round("h")
    fund = fund[(fund.nominal >= dc.datetime.iloc[0]+pd.Timedelta(hours=1)) & (fund.nominal <= dc.datetime.iloc[-1]+pd.Timedelta(hours=1))].reset_index(drop=True)
    assert len(fund) == reg["cost_data"]["funding_rows"]
    results, costs, all_trades, terminals, curves, ledgers = [], [], [], {}, {}, {}
    for sizing, slip in itertools.product(["flat", "schedule"], [0, 2, 5]):
        args = dict(start_bar=510, realistic=True, slip_bps=slip, margin_schedule=schedule if sizing == "schedule" else None, extra_cost=5.)
        baseline = None
        baseline_pnl = None
        for cfg in CONFIGS:
            ind = features[cfg["case"]]
            raw, terminal = simulate(ind, d.datetime.to_numpy(), **args)
            stats, frame, equity, _ = prior.summary(raw, terminal, d, d.close.to_numpy())
            if cfg["case"] == "joint_100":
                baseline, baseline_pnl = frame, stats["closed_pnl"]
            paired = prior.replacement(frame, baseline)
            key = {"case": cfg["case"], "family": cfg["family"], "length": cfg["length"], "sizing": sizing, "slip": slip}
            delta = stats["closed_pnl"] - baseline_pnl
            assert abs(delta - (-paired["removed_pnl"] + paired["replacement_pnl"] + paired["changed_common_delta"])) < 1e-7
            results.append({**key, **stats, **paired, "affected": paired["removed_entries"]+paired["replacement_entries"]+paired["changed_common"], "delta": delta})
            group_key = (cfg["case"], sizing, slip)
            curves[group_key] = equity
            all_trades.append(frame.assign(**key))
            terminals["_".join(map(str, group_key))] = terminal
            cropped = {k: v[:len(dc)] for k, v in ind.items()}
            cr, ct = simulate(cropped, dc.datetime.to_numpy(), **args)
            cs, _, _, _ = prior.summary(cr, ct, dc, dc.close.to_numpy())
            fs, _, fl = prior.funding_equity(cr, ct, dc, marks, fund)
            costs.append({**key, **cs, **fs})
            ledgers[group_key] = fl
        print(f"完成{sizing}／{slip}bp：累計{len(results)}組全期＋交集。", flush=True)
    result, cost_result, trades = pd.DataFrame(results), pd.DataFrame(costs), pd.concat(all_trades, ignore_index=True)
    independent = audit_frames(trades, result, curves, ledgers, cost_result)
    # 所有既有設定（100h／125h／scaled150h）逐成本情境必須重現上一輪。
    anchors = {"joint_100": ("fixed_joint", 100), "gate_only_100": ("fixed_gate_only", 100), "anchor_joint125": ("fixed_joint", 125), "anchor_scaled150": ("scaled_joint", 150)}
    old_cost = pd.read_csv(OLD / "cost_results.csv")
    for case, (family, length) in anchors.items():
        for sizing, slip in itertools.product(["flat", "schedule"], [0, 2, 5]):
            a = result.query("case == @case and sizing == @sizing and slip == @slip").iloc[0]
            b = old_results.query("family == @family and length == @length and sizing == @sizing and slip == @slip").iloc[0]
            for key in ["closed_pnl", "price_mdd", "trades", "end_price_equity", "L_pnl", "S_pnl"]:
                assert abs(a[key]-b[key]) < 1e-7
            a = cost_result.query("case == @case and sizing == @sizing and slip == @slip").iloc[0]
            b = old_cost.query("family == @family and length == @length and sizing == @sizing and slip == @slip").iloc[0]
            for key in ["closed_pnl", "funding", "funded_end_equity", "mark_mdd"]:
                assert abs(a[key]-b[key]) < 1e-7
    for name, value in protected.items():
        assert prior.digest(ROOT / name) == value, name
    for path, value in private_hashes.items():
        assert prior.digest(path) == value, "runtime或live檔變更"
    assert prior.git("rev-parse", "HEAD") == reg["head"]
    result.to_csv(OUT / "results.csv", index=False, encoding="utf-8-sig")
    cost_result.to_csv(OUT / "cost_results.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(OUT / "all_trades.csv", index=False, encoding="utf-8-sig")
    save("terminal_states.json", terminals)
    verification = {"no_op": noop, "prefix": prefix, "independent_accounting": independent, "anchor_parity": "24 full and24 cost scenarios PASS",
                    "prior_artifacts_and_core_inputs_unchanged": True, "runtime_and_live_files_unchanged": True, "status": "PASS"}
    save("verification.json", verification)
    summary = describe(result, cost_result, reg, verification, gate_df)
    manifest = {str(path.relative_to(ROOT)): {"bytes": path.stat().st_size, "sha256": prior.digest(path)} for path in OUT.glob("*") if path.is_file() and path.name != "artifact_manifest.json"}
    for path in [Path(__file__), REPORT, PLAN]:
        manifest[str(path.relative_to(ROOT))] = {"bytes": path.stat().st_size, "sha256": prior.digest(path)}
    save("artifact_manifest.json", manifest)
    print(json.dumps({**summary, "full_runs": len(result), "cost_runs": len(cost_result), "verification": "PASS", "report": str(REPORT)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
