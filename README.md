# Tiny Bot — EMA(8, 21) crossover, long-only

Entry for make.fun **“Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana”**
([bounty](https://make.fun/bounty/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1)).

## Strategy in one line

Long-only EMA(8, 21) crossover on hourly bars, one position per symbol:
buy a symbol when its fast EMA crosses above its slow EMA, exit the whole
position on a cross down, a −20% stop-loss, or a +50% take-profit. While
everything trends down it sits in USDC.

## Why

The dev window (2026-10-29 → 2026-11-03, 136 hourly bars) is a broad down
market — all seven symbols end lower and buy-and-hold loses about $887 on
$10,000 — so a bot that sits in cash in a downtrend and only buys a fresh
uptrend beats buy-and-hold without needing to predict the final window
(2026-11-27 → 2026-11-30, recorded after the deadline). Parameters were
picked by offline backtest on the kit dev data across EMA(4,12) to
EMA(12,36): EMA(8,21) crossover-only was the most robust — mildly negative
in both halves of the window, not one lucky stretch.

## Files

- `main.py` — the entry point (repository root), Python 3.13, standard
  library only. Speaks `agent-fun/bars-replay/v1` on stdin/stdout.
- `vendor/makefun_bars.py` — the kit's official Python SDK, copied
  unchanged; it owns the wire protocol.
- `LICENSE` — MIT.

## How it trades

- Universe: SOL, WETH, FARTCOIN, JITOSOL, POPCAT, RAY, WIF (whatever the
  hello lists). Capital $10,000 USDC. No shorts, no leverage.
- Stake per buy: equity / min(symbols, max_open_positions), capped at 25%
  of equity per symbol, never below the $1 minimum notional; at most 16
  orders per bar. Sells are sent first; a sell's proceeds never fund a buy
  in the same reply.
- Fill price is capped at 100 bps past the close the order was decided on;
  a refused sell is re-sent on the next bar while its exit reason holds.
- EMAs are seeded as the mean of their first span of closes. A bar with
  volume `"0"` updates the EMAs but triggers no signal.
- Deterministic: no clock, no environment, no randomness — two runs give
  byte-identical replies.

## Local grade (dev data)

Official grader (needs Python 3.13 on PATH, or Docker):

```sh
node cli/makefun.mjs grade-local --kit . --bot <this directory> --driver process
```

Dev-window result with the protocol-level simulator in `../dev/proto_sim.py`
(same fill model as the harness: fee 10 bps, slippage 5 bps, pool impact,
1-bar latency, flatten at the last close):

| | Net PnL |
|---|---|
| This bot | **−$173.04** (28 trades, max drawdown 2.51%) |
| Buy-and-hold, SOL-only | −$934.10 |
| Buy-and-hold, equal-weight 7 | −$901.76 |

Beats buy-and-hold by **+$761.06** on the dev window and clears both
guardrails (≥ 8 trades, max drawdown ≤ 25%). The final window is a
different 3 days of prices, so the dev score is a check that the bot
behaves, not a promise about the final ranking.
