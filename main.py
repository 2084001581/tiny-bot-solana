"""Tiny Bot for make.fun: "Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana".

Strategy, in one line: long-only EMA(8, 21) crossover on hourly bars, one
position per symbol, exit on a cross down, a -20% stop-loss or a +50%
take-profit. It holds USDC while every symbol trends down and buys back in
when a symbol's fast EMA crosses above its slow EMA.

Why this shape: the dev window (2026-10-29 to 2026-11-03, 136 hourly bars)
is a broad down market -- all seven symbols end lower, buy-and-hold loses
about $887 on $10,000 -- so a bot that sits in cash in a downtrend and only
buys a fresh uptrend beats buy-and-hold without needing to predict the
final window (2026-11-27 to 2026-11-30, recorded after the deadline). The
parameters were picked by offline backtest on the kit dev data, testing
EMA(4,12) through EMA(12,36) and crossover-only vs hold-while-above:
EMA(8,21) crossover-only was the best robust choice -- official
grade-local net PnL -$173.04 on dev (vs -$934.10 buy-and-hold SOL-only),
28 trades, max drawdown 2.51%, and it stayed mildly negative in both
halves of the window, so it is not one lucky stretch. EMA(8,24) was close
behind (-$174.31, 22 trades).

Mechanics (from the kit's protocol agent-fun/bars-replay/v1 and
competition.yaml; no network, no outside data, no clock, no randomness):
  - entry point: this file at the repository root, run as
    `python3 -P -s main.py`; standard library only.
  - the vendor shim vendor/makefun_bars.py (the kit's official SDK, copied
    unchanged) owns the wire: one JSON line per batch on stdin, exactly one
    reply per batch on stdout echoing its seq.
  - orders decided on bar k fill at bar k+1's open (1-bar latency), fee
    10 bps, slippage 5 bps; sells never fund buys in the same reply.
  - stake per buy: equity / min(symbols, max_open_positions) -- about
    $1,428 on $10,000 -- capped at 25% of equity (caps.max_weight_bps),
    never below the $1 minimum notional; at most 16 orders per bar.
  - a sell the market refuses (e.g. price moved past the 100 bps slippage
    cap) is re-sent on the next bar while its exit reason still holds.
  - EMAs are seeded as the mean of their first span of closes, then move
    2 / (span + 1) of the way to each new close (float64, deterministic).
  - a bar with volume "0" updates the EMAs but triggers no signal.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

FAST_BARS = 8
SLOW_BARS = 21
# Take-profit +50%: exit when the position is worth 3/2 of its cost.
ROI_NUM, ROI_DEN = 3, 2
# Stop-loss -20%: exit when the position is worth 4/5 of its cost.
STOP_NUM, STOP_DEN = 4, 5
# Cap the fill price at 1% past the close it was decided on.
MAX_SLIPPAGE_BPS = 100


class Ema:
    """One EMA: the closes it has seen until it starts, then its value."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return self.value
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span
        return self.value


class Series:
    """Per-symbol EMA state."""

    def __init__(self):
        self.fast = Ema(FAST_BARS)
        self.slow = Ema(SLOW_BARS)
        # fast - slow after the previous bar, once both EMAs exist.
        self.gap = None
        # fast - slow after this bar.
        self.now = None
        # Sell signal: set on a cross of slow above fast, held while slow
        # stays above fast.
        self.down = False


_SERIES = {}


def _push(bar):
    series = _SERIES.get(bar["symbol"])
    if series is None:
        series = Series()
        _SERIES[bar["symbol"]] = series
    close = int(bar["c"])
    series.fast.push(close)
    series.slow.push(close)
    series.gap = series.now
    if series.fast.value is not None and series.slow.value is not None:
        series.now = series.fast.value - series.slow.value
    else:
        series.now = None
    if series.now is None or series.now >= 0:
        series.down = False
    return series


def _exits(state, symbol, held, signal):
    """True if a held position exits on this close."""
    if signal:
        return True
    value = state.value(symbol, held.qty)
    cost = held.cost
    return value * ROI_DEN >= cost * ROI_NUM or value * STOP_DEN <= cost * STOP_NUM


def on_batch(state, batch):
    cross_up = set()
    fresh = set()
    for bar in batch["bars"]:
        series = _push(bar)
        fresh.add(bar["symbol"])
        if series.gap is None or series.now is None or bar.get("v") == "0":
            continue
        if series.now > 0 and series.gap <= 0:
            cross_up.add(bar["symbol"])
        if series.now < 0 and series.gap >= 0:
            series.down = True

    if state.warming_up:
        return []

    caps = state.hello["caps"]
    fees = state.hello["fees"]
    sells = []
    entries = []
    for symbol_info in state.hello["symbols"]:
        symbol = symbol_info["symbol"]
        if symbol not in fresh:
            continue
        held = state.positions.get(symbol)
        if held is not None:
            if _exits(state, symbol, held, _SERIES[symbol].down):
                sells.append(
                    {
                        "side": "sell",
                        "symbol": symbol,
                        "qty": held.qty,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    }
                )
        elif symbol in cross_up:
            entries.append(symbol)

    orders = sells[: caps["max_orders_per_bar"]]
    slots = min(len(state.hello["symbols"]), caps["max_open_positions"])
    stake = state.equity // slots
    by_weight = caps["max_weight_bps"] * state.equity // makefun_bars.BPS
    # Positions being sold this reply still count against the slot cap:
    # their proceeds only land at the next open.
    room = slots - len(state.positions)
    cash = state.cash
    for symbol in entries:
        if room <= 0 or len(orders) >= caps["max_orders_per_bar"]:
            break
        slice_ = stake if stake < by_weight else by_weight
        if cash < slice_:
            slice_ = cash
        if slice_ <= 0 or slice_ < int(fees["min_notional_quote_units"]):
            break
        orders.append(
            {
                "side": "buy",
                "symbol": symbol,
                "notional_quote_units": slice_,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }
        )
        cash -= slice_
        room -= 1
    return orders


makefun_bars.run(on_batch)
