"""獨立核對已生成研究帳本，補上數據解讀；不重調任何候選。

執行本檔前先執行 regime_gate_length_20261007.py。
"""
from pathlib import Path
import hashlib
import json
import itertools
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "doc/research_results/20261007_regime_gate_length"
REPORT = ROOT / "doc/regime_gate_length_research_20261007.md"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    registration = json.loads((OUT / "registration.json").read_text(encoding="utf-8"))
    verification = json.loads((OUT / "verification.json").read_text(encoding="utf-8"))
    r = pd.read_csv(OUT / "results.csv")
    c = pd.read_csv(OUT / "cost_results.csv")
    t = pd.read_csv(OUT / "all_trades.csv", parse_dates=["entry_dt", "exit_dt", "entry_time", "exit_time"])
    keys = ["family", "length", "sizing", "slip"]
    expected = set(itertools.product(registration["families"], registration["lengths"], ["flat", "schedule"], registration["slip_bps"]))
    assert set(r[keys].itertuples(index=False, name=None)) == expected and len(r) == len(expected) == 234
    assert set(c[keys].itertuples(index=False, name=None)) == expected and len(c) == 234
    assert not r[keys].duplicated().any() and not c[keys].duplicated().any()
    grouped = t.groupby(keys, sort=False)
    count = 0
    for key, f in grouped:
        row = r.set_index(keys).loc[key]
        assert len(f) == row.trades
        assert abs(f.pnl_exact.sum() - row.closed_pnl) < 1e-7
        assert abs(f.pnl_usd.sum() - row.display_pnl) < 1e-7
        gross = np.where(f.side.eq("L"), 1, -1) * (f.exit_exact - f.entry_exact) * f.qty_exact
        np.testing.assert_allclose(gross - f.fee_exact - 5., f.pnl_exact, rtol=1e-12, atol=1e-9)
        np.testing.assert_allclose(f.qty_exact * f.entry_exact, f.margin * 20, rtol=1e-12, atol=1e-9)
        np.testing.assert_allclose(f.fee_exact, f.margin / 200 * 4, rtol=1e-12, atol=1e-9)
        assert (f.entry_time - f.entry_dt).eq(pd.Timedelta(hours=1)).all()
        assert (f.exit_time - f.exit_dt).eq(pd.Timedelta(hours=1)).all()
        assert (f.exit_bar - f.entry_bar).eq(f.bars_held).all()
        assert (f.entry_bar >= 510).all()
        for side, cooldown, blocked_days in [("L", 6, [5, 6]), ("S", 8, [0, 5, 6])]:
            q = f[f.side.eq(side)].sort_values("entry_bar")
            assert not q.entry_dt.dt.hour.isin([0, 1, 2, 12]).any()
            assert not q.entry_dt.dt.dayofweek.isin(blocked_days).any()
            if len(q) > 1:
                assert (q.entry_bar.to_numpy()[1:] - q.exit_bar.to_numpy()[:-1] >= cooldown).all()
            assert q.groupby(q.entry_dt.dt.to_period("M")).size().max() <= 20
        assert abs((f.pnl_exact > 0).mean() * 100 - row.wr) < 1e-8
        count += 1
    assert count == 234
    for sizing, slip in itertools.product(["flat", "schedule"], [0, 2, 5]):
        groups = [grouped.get_group((family, 100, sizing, slip)).drop(columns=keys).reset_index(drop=True) for family in registration["families"]]
        pd.testing.assert_frame_equal(groups[0], groups[1])
        pd.testing.assert_frame_equal(groups[0], groups[2])
    curves = pd.read_csv(OUT / "selected_equity.csv", parse_dates=["time"])
    for (family, length, slip), frame in curves.groupby(["family", "length", "slip"]):
        f = "fixed_joint" if family == "baseline" else family
        value = r.set_index(keys).loc[(f, length, "flat", slip)].end_price_equity
        assert abs(frame.price_equity_pnl.iloc[-1] - value) < 1e-7
    funding = pd.read_csv(OUT / "selected_funding.csv")
    for (family, length, slip), frame in funding.groupby(["family", "length", "slip"]):
        f = "fixed_joint" if family == "baseline" else family
        value = c.set_index(keys).loc[(f, length, "flat", slip)].funding
        assert abs(frame.value.sum() - value) < 1e-7
    for name, value in registration["public_input_hashes"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value, name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == registration["head"]
    # 事前固定分段的最高值無同分，選擇結果不依賴全期回撤作 tie-break。
    early_tie_free = all(r.query("sizing == 'flat' and slip == 0").groupby("family").early_pnl.nunique() == 13)
    assert early_tie_free
    b = r.query("family == 'fixed_joint' and length == 100 and sizing == 'flat' and slip == 0").iloc[0]
    winner = r.query("family == 'fixed_joint' and sizing == 'flat' and slip == 0").sort_values("closed_pnl", ascending=False).iloc[0]
    scaled = r.query("family == 'scaled_joint' and sizing == 'flat' and slip == 0").sort_values("closed_pnl", ascending=False).iloc[0]
    gate_only = r.query("family == 'fixed_gate_only' and sizing == 'flat' and slip == 0").sort_values("closed_pnl", ascending=False).iloc[0]
    at125_gate = r.query("family == 'fixed_gate_only' and length == 125 and sizing == 'flat' and slip == 0").iloc[0]
    old310 = next(x for x in verification["no_op"] if x["warmup"] == 310 and x["sizing"] == "flat" and x["slip"] == 0)
    conclusion = ["",
        f"**主要發現：**125h 只比100h多 **${winner.closed_pnl-b.closed_pnl:,.2f}（{(winner.closed_pnl/b.closed_pnl-1)*100:.2f}%）**。固定200U加2／5bp後，125h分別落後100h **$177.93／$117.60**；只改進場gate時125h也落後 **${b.closed_pnl-at125_gate.closed_pnl:,.2f}**。因此125h的小幅全期優勢包含V25-D出場分類的交互作用，不能認定回看125h本身有穩定改善。",
        "",
        f"**純進場gate的本次冠軍仍是100h。**若連門檻按時間換算，150h為另一個歷史候選：淨利 **${scaled.closed_pnl:,.2f}**，較100h **+${scaled.closed_pnl-b.closed_pnl:,.2f}（+{(scaled.closed_pnl/b.closed_pnl-1)*100:.2f}%）**。150h原始斜率的門檻此時是 L **6.75%**、S **1.5%**，並同步影響出場分類；這與『門檻維持4.5%／1%而只換長度』是不同設定。它未經全新資料／前瞻驗證，維持SHADOW_ONLY，沒有部署建議。",
        "",
        "[長度比較結果 CSV](research_results/20261007_regime_gate_length/results.csv) 保留所有候選及成本情境。",
        ""]
    text = REPORT.read_text(encoding="utf-8")
    if "**主要發現：**" not in text:
        before, after = text.split("## 定義與比較範圍", 1)
        text = before + "\n".join(conclusion) + "## 定義與比較範圍" + after
        native = f"\n原生310根起點的固定200U／0bp基準為 **{old310['trades']}筆、${old310['closed_pnl']:,.2f}**；共同510根起點的100h基準為 **{int(b.trades)}筆、${b.closed_pnl:,.2f}**。兩個起點差 **${b.closed_pnl-old310['closed_pnl']:,.2f}**，不可把起點差誤當長度改善。主要比較全部使用同一510起點。\n"
        text = text.replace("## 所有長度結果", native + "\n## 所有長度結果", 1)
        notes = ["### 怎麼解讀成本與近期結果", "",
            "- 固定門檻125h的優勢集中在2026：2026年前較100h **-$334.15**，2026年 **+$374.28**，全期只剩 **+$40.13**。只用2026年前選長度會選100h。六區塊為4/6改善，但前兩區塊惡化。",
            "- 完整funding／mark交集截至9/8，固定200U／0bp的125h含funding期末收益 **$6,322.20**，100h為 **$6,668.12**，125h落後 **$345.92**。9/8之後的少量交易使全期排名翻轉，缺乏跨期間一致性。",
            "- 保證金歷史排程下125h全期0／2／5bp均高於100h（+$637.39／+$414.41／+$467.37），但固定200U下並不成立。差異來自後期交易被放大及風控狀態變化，不能用排程加權的優勢宣稱長度具有普遍改善。",
            "- 時間換算門檻的150h在固定200U的0／2／5bp較100h分別 **+$446.60／+$327.78／+$358.84**，排程也同向；2026年前選出的同樣是150h。六區塊仍僅4/6改善，因此最多是歷史shadow候選。",
            "- 在原門檻下，30h的S潛在事件360個中只通過174個，50h通過235個，100h通過279個。較短跨度配同一1%門檻會更常把S擋為盤整；這是過濾行為的證據，實際收益仍以完整狀態交易帳本為準。",
            ""]
        text = text.replace("## 最佳長度的進出場歸因", "\n".join(notes) + "\n## 最佳長度的進出場歸因", 1)
        text = text.replace("verification.json、terminal_states.json、selected_equity.csv、selected_funding.csv、summary.json、regime_length_comparison.png", "verification.json、independent_audit.json、terminal_states.json、selected_equity.csv、selected_funding.csv、summary.json")
        text = text.replace("regime_gate_length_20261007.py\n```", "regime_gate_length_20261007.py\n.\\.venv\\Scripts\\python.exe -B backtest\\research\\audit_regime_gate_length_20261007.py\n```")
        text = text.replace("- 原生 simulate 的精確 PnL", "- 獨立帳本稽核234組PASS：精確損益恆等式、費用、數量、成交時間、占倉／冷卻／時段／月cap、100h三組對齊、選取淨值／funding加總及輸入雜湊。\n- 預檢第一次停止原因：抽查固定截點全为空倉；在看候選排名前加入確定持倉截點。這只修驗證覆蓋、不改候選或成交邏輯，完整保留於implementation_revision.json。\n- 原生 simulate 的精確 PnL")
    for old, new in {"线性": "線性", "另一个": "另一個", "改写": "改寫", "结果见": "結果見", "结果见": "結果見", "結果见": "結果見", "没有": "沒有", "全为空倉": "全為空倉"}.items():
        text = text.replace(old, new)
    REPORT.write_text(text, encoding="utf-8")
    audit = {"status": "PASS", "complete_scenarios": count, "exact_trade_accounting": "PASS", "quantity_fee_time": "PASS", "occupancy_cooldown_session_monthly_cap": "PASS",
             "100h_families_all_trades": "PASS", "selected_price_equity_and_funding": "PASS", "input_hashes_and_head": "PASS", "discovery_tie_free": early_tie_free,
             "no_scenario_or_ranking_rule_changes": True, "post_analysis_only": True,
             "historical_winners": {"fixed_joint": int(winner.length), "fixed_gate_only": int(gate_only.length), "scaled_joint": int(scaled.length)}}
    (OUT / "independent_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(OUT.glob("*")) if p.is_file() and p.name != "artifact_manifest.json"}
    for p in [REPORT, Path(__file__), ROOT / "backtest/research/regime_gate_length_20261007.py", ROOT / "doc/regime_gate_length_plan_20261007.md"]:
        manifest[str(p.relative_to(ROOT))] = {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    (OUT / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
