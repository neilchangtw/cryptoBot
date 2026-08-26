"""V36 R1 — V29 event-based recovery, research only.

Runs the production V14 engine with one narrowly injected change: the margin
scale is selected from the current V29 state.  The state is updated only when
a position closes.  Red state keeps trading at its floor, and may reset only
after N *completed* post-red trades have both positive normalized sum and PF.
No future trade, month, or equity value is consulted at entry time.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = Path(__file__).with_name("v14_export_trades.py")
DATA_PATH = ROOT / "data" / "ETHUSDT_1h_latest730d.csv"

IS_END = "2024-08-25 23:59:59"
OOS_END = "2026-07-30 23:59:59"
POLICIES = [
    ("500/200/100 N20", (500, 200, 100), 20),
    ("500/200/100 N30", (500, 200, 100), 30),
    ("500/200/100 N40", (500, 200, 100), 40),
    ("500/300/200 N20", (500, 300, 200), 20),
    ("500/300/200 N30", (500, 300, 200), 30),
    ("500/300/200 N40", (500, 300, 200), 40),
    ("300/200/100 N20", (300, 200, 100), 20),
    ("300/200/100 N30", (300, 200, 100), 30),
    ("300/200/100 N40", (300, 200, 100), 40),
]


def load_engine():
    source = ENGINE_PATH.read_text(encoding="utf-8")
    signature = (
        "def simulate_v14_detailed(ind, datetimes, start_bar=None,\n"
        "                          realistic=False, slip_bps=0.0, margin_schedule=None):"
    )
    source = source.replace(
        signature,
        signature[:-2] + ",\n                          recovery_policy=None):",
        1,
    )
    anchor = "    consec = 0\n    consec_end = -999\n"
    injected = anchor + """
    # V36 research state: all values are known at the current bar.
    policy_level = 'green'
    policy_cusum = 0.0
    post_red_r = []
    if recovery_policy is not None:
        green_margin, yellow_margin, red_margin, recovery_n = recovery_policy
"""
    assert source.count(anchor) == 1
    source = source.replace(anchor, injected, 1)
    old_scale = "        cbs = scale_arr[i] if scale_arr is not None else 1.0\n"
    new_scale = """        cbs = scale_arr[i] if scale_arr is not None else 1.0
        if recovery_policy is not None:
            margin_now = {'green': green_margin, 'yellow': yellow_margin,
                          'red': red_margin}[policy_level]
            cbs *= margin_now / 200.0
"""
    assert source.count(old_scale) == 1
    source = source.replace(old_scale, new_scale, 1)

    # Each update explicitly uses the exiting position's locked notional, so a
    # tier change cannot relabel an old trade's R result.
    l_anchor = """                l_m_pnl += pnl
                d_pnl += pnl
                if pnl < 0:
"""
    l_update = """                l_m_pnl += pnl
                d_pnl += pnl
                if recovery_policy is not None:
                    pnl_r = pnl * 4000.0 / lp_ntl
                    if policy_level == 'red':
                        post_red_r.append(pnl_r)
                        recent = post_red_r[-recovery_n:]
                        gp = sum(x for x in recent if x > 0)
                        gl = -sum(x for x in recent if x < 0)
                        if len(recent) >= recovery_n and sum(recent) > 0 and gp > gl:
                            policy_level = 'green'
                            policy_cusum = 0.0
                            post_red_r = []
                    else:
                        policy_cusum = max(0.0, policy_cusum + (14.3 - pnl_r))
                        policy_level = ('red' if policy_cusum > 800 else
                                        'yellow' if policy_cusum > 600 else 'green')
                        if policy_level == 'red':
                            post_red_r = []
                if pnl < 0:
"""
    assert source.count(l_anchor) == 1
    source = source.replace(l_anchor, l_update, 1)
    s_anchor = """                s_m_pnl += pnl
                d_pnl += pnl
                if pnl < 0:
"""
    s_update = """                s_m_pnl += pnl
                d_pnl += pnl
                if recovery_policy is not None:
                    pnl_r = pnl * 4000.0 / sp_ntl
                    if policy_level == 'red':
                        post_red_r.append(pnl_r)
                        recent = post_red_r[-recovery_n:]
                        gp = sum(x for x in recent if x > 0)
                        gl = -sum(x for x in recent if x < 0)
                        if len(recent) >= recovery_n and sum(recent) > 0 and gp > gl:
                            policy_level = 'green'
                            policy_cusum = 0.0
                            post_red_r = []
                    else:
                        policy_cusum = max(0.0, policy_cusum + (14.3 - pnl_r))
                        policy_level = ('red' if policy_cusum > 800 else
                                        'yellow' if policy_cusum > 600 else 'green')
                        if policy_level == 'red':
                            post_red_r = []
                if pnl < 0:
"""
    assert source.count(s_anchor) == 1
    source = source.replace(s_anchor, s_update, 1)
    source = source.split("if __name__ == '__main__':")[0]
    spec = importlib.util.spec_from_loader("v36_recovery_engine", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["__file__"] = str(ENGINE_PATH)
    exec(compile(source, str(ENGINE_PATH), "exec"), module.__dict__)
    return module


def metrics(trades: list[dict]) -> dict:
    x = pd.DataFrame(trades)
    pnl = float(x.pnl_usd.sum())
    curve = x.pnl_usd.cumsum()
    mdd = float((curve.cummax() - curve).max())
    gp = float(x.loc[x.pnl_usd > 0, "pnl_usd"].sum())
    gl = float(-x.loc[x.pnl_usd < 0, "pnl_usd"].sum())
    return {"pnl": pnl, "mdd": mdd, "n": len(x), "pf": gp / gl if gl else np.inf}


def run_trades(engine, df, policy=None, margin_schedule=None):
    return engine.simulate_v14_detailed(
        engine.compute_indicators(df), df.datetime.values,
        realistic=True, margin_schedule=margin_schedule,
        recovery_policy=policy,
    )


def split_metrics(trades):
    return (
        metrics(trades),
        metrics([t for t in trades if str(t["entry_dt"]) <= IS_END]),
        metrics([t for t in trades if str(t["entry_dt"]) > IS_END]),
    )


def main():
    df = pd.read_csv(DATA_PATH)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df[df.datetime <= pd.Timestamp(OOS_END)].reset_index(drop=True)
    engine = load_engine()
    baseline_trades = run_trades(engine, df)
    baseline = metrics(baseline_trades)
    # Parity: policy omitted is exactly the production research engine.
    raw_spec = importlib.util.spec_from_file_location("raw", ENGINE_PATH)
    raw = importlib.util.module_from_spec(raw_spec)
    raw_spec.loader.exec_module(raw)
    raw_trades = raw.simulate_v14_detailed(raw.compute_indicators(df), df.datetime.values, realistic=True)
    assert abs(baseline["pnl"] - metrics(raw_trades)["pnl"]) < 0.01

    print("V36 R1 V29 event recovery | realistic fills | 2000-day data through 2026-07")
    print(f"Baseline 200U: PnL ${baseline['pnl']:+.0f}, MDD ${baseline['mdd']:.0f}, n={baseline['n']}")
    rows = []
    for name, margins, n in POLICIES:
        policy = (*margins, n)
        trades = run_trades(engine, df, policy)
        full, ins, oos = split_metrics(trades)
        control = metrics(run_trades(
            engine, df, margin_schedule=[("2000-01-01", margins[0])]
        ))
        margin_counts = pd.Series([t["margin"] for t in trades]).value_counts().to_dict()
        rows.append({"name": name, "vs_constant_green": full["pnl"] - control["pnl"],
                     "control_pnl": control["pnl"], "control_mdd": control["mdd"],
                     "tier_entries": str({int(k): int(v) for k, v in sorted(margin_counts.items())}),
                     **{f"full_{k}": v for k, v in full.items()},
                     **{f"is_{k}": v for k, v in ins.items()},
                     **{f"oos_{k}": v for k, v in oos.items()}})
    out = pd.DataFrame(rows)
    pd.set_option("display.max_columns", None)
    print(out.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
