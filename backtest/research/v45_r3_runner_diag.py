"""V45 第 3 輪診斷：現行策略 TP 出場後同向尾倉續抱（開發期 < 2024-07-01）。
訊號 = TP 出場那根；次根開盤同向進場，尾倉獨立帳，不改現行策略。"""

from __future__ import annotations

import numpy as np
import pandas as pd

import v45_engine as E

END = pd.Timestamp("2024-07-01")


def main():
    d = pd.read_pickle("data/v45/feat.pkl")
    b = pd.read_pickle("data/v45/base.pkl")
    o = d.open.values
    f = E.load_funding()
    for side, s in (("L", 1), ("S", -1)):
        x = b[(b.side == side) & (b.exit_reason == "TP") & (b.entry_dt < END)]
        idx = x.exit_bar.astype(int).values
        print(side, "TP 筆數", len(idx))
        for h in (6, 12, 24, 48, 72, 120):
            v = s * np.log(o[idx + 1 + h] / o[idx + 1]) * 1e4
            print(f"  h{h}: {v.mean():.0f}bp t {v.mean() / (v.std(ddof=1) / np.sqrt(len(v))):.1f}")
        sig = np.zeros(len(d), bool)
        sig[idx] = True
        for H in (12, 24, 48, 72, 120):
            for S in (.02, .03, .05):
                for tr in (None, (.03, .02), (.05, .03)):
                    t = E.sim(d, sig, s, H, S, trail=tr, cooldown=0, fund=f)
                    print(f"  H{H} S{S} tr{tr}", E.stats(t))


if __name__ == "__main__":
    main()
