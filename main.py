"""Pump.fun Paper Sprint bot — momentum rotation paper trader.

DRAFT: buys new pump.fun coins on early upward momentum, exits on
take-profit / stop-loss / trailing peak fall / old age, and rotates the
five open slots so the run touches many distinct mints.

Protocol: agent-fun/replay/v2, newline-delimited JSON on stdin/stdout.
The harness sends hello, then one batch per slot window, then done.
The bot answers every batch with one reply echoing its seq.
u64 values are decimal strings. No network, no clock, no randomness:
the same input always produces the same action log.
"""

import json
import sys

LAMPORTS_PER_SOL = 1_000_000_000

# --- strategy knobs (all in lamports / ratios, tuned on the dev window) ---
# Dev-window evidence (128k events, 1,222 new coins): buying the +12%
# momentum trigger and HOLDING loses (median -13% after 1,500 slots),
# but 63% of triggers spike at least +20% higher within 1,500 slots.
# Edge is a fast flip: small take-profit, tight stops, quick rotation.
STAKE_LAMPORTS = 300_000_000      # 0.30 SOL target per buy (size cap binds first)
MIN_BUY_LAMPORTS = 20_000_000     # 0.02 SOL: above the 0.01 SOL round-trip bar
MOMENTUM_RISE = 1.12              # buy once price is +12% over first mark
MOMENTUM_WINDOW_SLOTS = 1500     # momentum must fire soon after creation
TAKE_PROFIT = 1.20                # sell at +20% over entry
STOP_LOSS = 0.85                  # sell at -15% under entry
TRAIL_FALL = 0.90                 # sell at -10% under the peak since entry
MAX_HOLD_SLOTS = 600              # time exit: free the slot for rotation
MAX_SLIPPAGE_BPS = 500            # 5% slippage cap on buys
SELL_SLIPPAGE_BPS = 3000          # 30% on sells: exits must land, not reject


def price_of_reserves(virtual_quote, virtual_tokens):
    """Lamports of SOL per token from virtual reserves."""
    quote = int(virtual_quote)
    tokens = int(virtual_tokens)
    if tokens <= 0:
        return None
    return quote / tokens


def price_of(mark):
    """Lamports of SOL per token from a mark's virtual reserves."""
    return price_of_reserves(
        mark["virtual_quote_reserves"], mark["virtual_token_reserves"]
    )


class Bot:
    def __init__(self, hello):
        self.hello = hello
        self.caps = hello["caps"]
        self.fees = hello["fees"]
        self.cash = int(hello["capital"]["lamports"])
        self.slot = 0
        self.order_no = 0
        # mint -> {"first": price, "seen": slot, "peak": price}
        self.coins = {}
        # mint -> {"entry": price, "peak": price, "bought": slot}
        self.entries = {}
        # mints with a buy sent but not yet confirmed filled
        self.pending_buys = set()
        # mints already bought once: never re-buy after an exit (churn)
        self.decided = set()
        # mint -> tokens currently held (from the harness account)
        self.positions = {}

    def tx_fee(self):
        return int(self.fees["base_fee_lamports"]) + int(
            self.fees["priority_fee_lamports"]
        )

    def max_buy(self, real_quote):
        """Largest buy the size cap allows: bps of real quote reserves."""
        bps = int(self.caps["max_order_bps_of_real_reserve"])
        return real_quote * bps // 10000

    def next_client_id(self, prefix):
        self.order_no += 1
        return "%s%d" % (prefix, self.order_no)

    def on_batch(self, batch):
        self.slot = int(batch.get("slot", self.slot))
        marks = {}
        for mark in batch.get("marks", []):
            marks[mark["mint"]] = mark

        # Account is the source of truth for cash and positions.
        account = batch.get("account") or {}
        if "cash_lamports" in account:
            self.cash = int(account["cash_lamports"])
        self.positions = {}
        for pos in account.get("positions", []):
            self.positions[pos["mint"]] = int(pos["token_amount"])

        # Fills confirm buys (record entry price) and sells (drop entry).
        # A rejected/failed buy is decided: drop the coin, never retry it.
        # The buy's true cost basis is the fill itself (quote paid per
        # token received), not the market price around it: fills land two
        # slots after the decision, mid-spike, so a recorded market price
        # understates what was paid and skews every exit.
        for fill in batch.get("fills", []):
            mint = fill["mint"]
            if fill.get("status") == "filled":
                if fill["side"] == "buy":
                    self.pending_buys.discard(mint)
                    quote = int(fill.get("quote_lamports") or 0)
                    tokens = int(fill.get("token_amount") or 0)
                    entry = None
                    if quote > 0 and tokens > 0:
                        entry = quote / tokens
                    if entry is None:
                        entry = self.coins.get(mint, {}).get("last")
                    if entry is None:
                        entry = self.coins.get(mint, {}).get("first")
                    if entry:
                        self.entries[mint] = {
                            "entry": entry,
                            "peak": entry,
                            "bought": self.slot,
                        }
                else:
                    self.entries.pop(mint, None)
                    self.decided.add(mint)
            elif fill["side"] == "buy":
                self.pending_buys.discard(mint)
                self.coins.pop(mint, None)

        # Prices come from the EVENT stream, not from marks: the harness
        # only marks coins the bot already holds (or has pending), so a
        # candidate coin's price is visible only in its TradeEvents
        # (post-trade virtual reserves) and its CreateEvent (initial
        # reserves). Marks still refresh prices of held positions.
        for event in batch.get("events", []):
            mint = event["mint"]
            if event["event"] == "CreateEvent":
                if mint not in self.coins:
                    first = price_of_reserves(
                        event["virtual_sol_reserves"],
                        event["virtual_token_reserves"],
                    )
                    self.coins[mint] = {
                        "first": first,
                        "last": first,
                        "real": 0,
                        "seen": self.slot,
                        "peak": first,
                    }
            elif event["event"] == "TradeEvent":
                coin = self.coins.get(mint)
                if coin is None:
                    continue
                price = price_of_reserves(
                    event["virtual_sol_reserves"],
                    event["virtual_token_reserves"],
                )
                if price is not None:
                    coin["last"] = price
                    if coin["peak"] is None or price > coin["peak"]:
                        coin["peak"] = price
                real = event.get("real_sol_reserves")
                if real is not None:
                    coin["real"] = int(real)
            elif event["event"] == "CompleteEvent":
                self.coins.pop(mint, None)
                self.entries.pop(mint, None)

        # Marks (held/pending coins only): refresh last price and peaks.
        for mint in sorted(marks):
            price = price_of(marks[mint])
            if price is None:
                continue
            coin = self.coins.get(mint)
            if coin is not None:
                coin["last"] = price
                if coin["peak"] is None or price > coin["peak"]:
                    coin["peak"] = price
                real = marks[mint].get("real_quote_reserves")
                if real is not None:
                    coin["real"] = int(real)
            entry = self.entries.get(mint)
            if entry is not None and price > entry["peak"]:
                entry["peak"] = price

        orders = []
        max_orders = int(self.caps["max_orders_per_batch"])

        # --- exits first: free slots before spending them ---
        for mint in sorted(self.positions):
            tokens = self.positions[mint]
            if tokens <= 0:
                continue
            entry = self.entries.get(mint)
            coin = self.coins.get(mint)
            price = None
            if coin is not None and coin.get("last") is not None:
                price = coin["last"]
            elif mint in marks:
                price = price_of(marks[mint])
            # No entry price yet: hold unless the position is stale,
            # then exit blind rather than sit on it forever.
            if entry is None:
                continue
            if price is None:
                if self.slot - entry["bought"] >= MAX_HOLD_SLOTS:
                    reason = "time-exit"
                else:
                    continue
            elif price >= entry["entry"] * TAKE_PROFIT:
                reason = "take-profit"
            elif price <= entry["entry"] * STOP_LOSS:
                reason = "stop-loss"
            elif price <= entry["peak"] * TRAIL_FALL:
                reason = "trailing-stop"
            elif self.slot - entry["bought"] >= MAX_HOLD_SLOTS:
                reason = "time-exit"
            else:
                continue
            if len(orders) >= max_orders:
                break
            orders.append(
                {
                    "side": "sell",
                    "client_id": self.next_client_id("s"),
                    "mint": mint,
                    "token_amount": str(tokens),
                    "max_slippage_bps": SELL_SLIPPAGE_BPS,
                }
            )
            self.entries.pop(mint, None)

        # --- entries: early momentum on fresh coins ---
        open_slots = int(self.caps["max_open_positions"]) - len(
            self.positions
        ) - len(self.pending_buys)
        free_cash = self.cash
        for mint in sorted(self.coins):
            if open_slots <= 0 or len(orders) >= max_orders:
                break
            if mint in self.positions or mint in self.pending_buys:
                continue
            if mint in self.entries or mint in self.decided:
                continue
            coin = self.coins[mint]
            if coin["first"] is None or coin.get("last") is None:
                continue
            age = self.slot - coin["seen"]
            if age > MOMENTUM_WINDOW_SLOTS:
                continue
            price = coin["last"]
            if price < coin["first"] * MOMENTUM_RISE:
                continue
            cap = self.max_buy(coin.get("real", 0))
            amount = min(cap, STAKE_LAMPORTS)
            if amount < MIN_BUY_LAMPORTS:
                continue
            if free_cash < amount + self.tx_fee():
                continue
            orders.append(
                {
                    "side": "buy",
                    "client_id": self.next_client_id("b"),
                    "mint": mint,
                    "quote_lamports": str(amount),
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            )
            self.pending_buys.add(mint)
            free_cash -= amount + self.tx_fee()
            open_slots -= 1

        return orders


def main():
    hello = None
    bot = None
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            hello = msg
            bot = Bot(hello)
        elif kind == "batch":
            orders = bot.on_batch(msg)
            out.write(
                json.dumps(
                    {"type": "reply", "seq": msg["seq"], "orders": orders},
                    separators=(",", ":"),
                )
                + "\n"
            )
            out.flush()
        elif kind == "done":
            break


if __name__ == "__main__":
    main()
