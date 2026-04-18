# V17 Research: Non-Breakout Alpha Search

> V17 goal: Find a genuinely new ETH 1h strategy that does NOT use any form of N-bar close breakout.
> V16 audit proved all V6-V16 strategies share the same core alpha (15-bar breakout).
> GK and TBR are quality filters, not independent edge sources.
>
> **Final conclusion: NON-BREAKOUT ALPHA DOES NOT EXIST on ETH 1h at $1K/20x/$4 fee.**

---

## Background

V16 10-Gate audit revealed:
- Pure 15-bar breakout: OOS $11,138 (strongest)
- V14 (GK + breakout): OOS $8,269 (filters trade PnL for MDD)
- S2 (TBR + breakout): OOS $8,252 (same tradeoff)
- Core alpha = breakout. GK/TBR = quality filters.

V17 question: **Does ETH 1h have ANY alpha beyond 15-bar close breakout?**

---

## R0: Non-Breakout Bar Analysis (`v17_r0_non_breakout_analysis.py`)

Comprehensive characterization of non-breakout bars (75.8% of all bars).

Key findings:
- Non-breakout bars have WR 50.3% — no directional bias
- Strongest conditional WR shifts: 1-bar return reversal +7.3pp, consec bars +6.5pp, EMA overext +6.2pp
- Mean reversion is the dominant non-breakout signal type
- BUT: best MFE-MAE advantage is only +0.387% (EMA dev > 1.5% Short)
- $4 fee = 0.1% of $4K notional → MFE must exceed fee + SafeNet risk

**Conclusion**: Mean reversion signals exist but MFE is marginal. S has slight structural advantage.

---

## R1: Mean Reversion Scan (`v17_r1_mean_reversion_scan.py`)

**30 signals x 14 exit configs = ALL IS NEGATIVE**

Signal types tested:
- EMA overextension (>1%, >1.5%, >2% from EMA20)
- Consecutive bars (3+, 4+ same direction, then reverse)
- CTR trend reversal (close-to-range)
- RSI extreme (>70/<30, >65/<35)
- 1-bar return reversal (ret_1 > 1.5%)
- EMA + CTR combined
- TBR + consecutive bars
- S-only EMA overextension

Exit configs: TP 0.5-2.0%, MH 2-6, all combinations.

Result: **ZERO IS-positive strategies with >= 20 trades.**
Best WR: 43%. Structurally impossible — MFE too small for $4 fee + $140 SafeNet hits.

---

## R2: Trend Following Without Breakout (`v17_r2_trend_non_breakout.py`)

**36 signals, 16 IS-positive, BUT breakout overlap reveals the truth.**

Signal categories:
- A: Range position (rpos — close's position within N-bar range, 0-1 scale)
- B: EMA slope + price position
- C: EMA convergence (ema20-ema50 divergence)
- D: Momentum percentile (return rank, NOT price breakout)
- E: Price cross EMA
- F: Multi-gate high-conviction
- G: Range position + trend filter
- H: EMA bias (close > fast > slow)
- I: Pullback to EMA in trend

Best result: **A8 (rpos15 > 0.90)** — IS $10,132, OOS $11,097, WR 72%

**CRITICAL FINDING**: A8 has **93% breakout overlap**.
rpos15 > 0.90 means close is in top 10% of 15-bar range = mathematically equivalent to breakout.

| Signal | IS PnL | OOS PnL | Breakout Overlap |
|--------|--------|---------|-----------------|
| A8 rpos15 >0.90 | $10,132 | $11,097 | **93%** |
| A4 rpos20 >0.80 | $8,556 | $8,319 | 68% |
| A2 rpos15 >0.80 | $8,156 | $8,788 | 66% |
| A7 rpos15 >0.60 | $6,844 | $6,353 | 28% |

Non-rpos signals (B, C, D, E, F, I): ALL IS-negative.
EMA bias (H): IS weak positive, OOS severe degradation.

---

## R3: Range Position WITHOUT Breakout (`v17_r3_rpos_without_breakout.py`)

**The definitive test: strip breakout bars from rpos signals.**

### Section 1: Full vs Breakout-only vs Non-breakout (IS)

| Signal | Full | Breakout-only | Non-breakout |
|--------|------|---------------|-------------|
| rpos15 >0.90 (A8) | -$921 | -$1,158 | -$1,160 |
| rpos15 >0.80 (A2) | -$640 | -$533 | -$1,448 |
| rpos15 >0.70 (A1) | -$1,780 | -$1,122 | -$1,902 |
| rpos15 >0.60 (A7) | -$543 | -$824 | -$1,478 |
| rpos20 >0.80 (A4) | -$1,675 | -$1,663 | -$364 |
| rpos20 >0.70 (A3) | -$1,261 | -$1,708 | -$1,342 |

**ALL non-breakout rpos = IS NEGATIVE.**

### Section 2: Extended scan (16 configs) — ALL IS negative
### Section 3: Enhanced with filters (32 configs) — ALL IS negative
- Volume filter: ALL negative
- GK compression: ALL negative
- BTC divergence: ALL negative
- EMA trend: ALL negative
- RSI zone: ALL negative
- Mid-zone rpos (0.55-0.85): ALL negative

**Conclusion: rpos = 100% breakout proxy. Zero independent alpha.**

---

## R4: Final Non-Breakout Scan (`v17_r4_final_scan.py`)

**36 signals, 1 IS-barely-positive ($+198), OOS -$2,253. FAIL.**

| Category | Signals | IS+ | Note |
|----------|---------|-----|------|
| Time-of-day | 4 | 1 ($+198) | OOS -$2,253 |
| Day-of-week | 2 | 0 | — |
| Candle patterns | 6 | 0 | Engulfing, pin bar, inside bar, doji |
| Volatility regime | 4 | 0 | GK/ATR expansion + direction |
| Volume anomaly | 7 | 0 | Vol spike, TBR extreme |
| Consecutive bars | 4 | 0 | Continuation + reversal |
| Range contract/expand | 3 | 0 | Without breakout |
| EMA cross events | 4 | 0 | 5x20, 10x20, 10x50, 20x50 |
| Combined multi-signal | 2 | 0 | Contract+cross, multi-gate 3+ |

---

## Final Summary

| Round | Approach | Configs Tested | IS+ | Viable |
|-------|----------|---------------|-----|--------|
| R0 | Bar characterization | — | — | Identified mean reversion bias |
| R1 | Mean reversion | 420 | 0 | 0 |
| R2 | Trend following (rpos) | 36 | 16 | 0 (93% breakout overlap) |
| R3 | rpos WITHOUT breakout | 80 | 0 | 0 |
| R4 | Time/candle/vol/EMA/multi | 36 | 1 ($+198) | 0 (OOS fail) |
| **Total** | | **572** | — | **0** |

## Definitive Conclusion

**ETH 1h non-breakout alpha does not exist at $1K / 20x / $4 fee account structure.**

The 15-bar close breakout is the **only** source of alpha on ETH 1h. Every other signal falls into one of three categories:

1. **Breakout proxies** — Range position, momentum percentile, consecutive new highs. These appear to work but 66-93% of their trades are on breakout bars. Strip the breakout and they become IS-negative.

2. **Structurally impossible** — Mean reversion signals have real statistical directional bias (+5-7 percentage points WR lift) but MFE is only 0.1-0.4%, insufficient to overcome $4 fee (0.1%) + SafeNet drawdown ($140/hit).

3. **Random noise** — Candle patterns, time-of-day, day-of-week, volume anomalies, EMA crosses, multi-gate filters. None achieve IS > $0.

**Implication for V14**: The current V14 strategy (GK compression breakout) is optimal. GK is the best quality filter for breakout trades (reduces PnL 26% but halves MDD). No replacement or supplement exists outside the breakout paradigm.

---

## Script Index

| Script | Description |
|--------|------------|
| `v17_r0_non_breakout_analysis.py` | Non-breakout bar characterization (19 features, 29 combos, MFE/MAE) |
| `v17_r1_mean_reversion_scan.py` | 30 mean reversion signals x 14 exits = ALL IS negative |
| `v17_r2_trend_non_breakout.py` | 36 trend signals, breakout overlap check (93% for best) |
| `v17_r3_rpos_without_breakout.py` | rpos WITHOUT breakout bars = ALL IS negative (80 configs) |
| `v17_r4_final_scan.py` | Final 36 signals (time/candle/vol/EMA/multi) = 0 viable |
