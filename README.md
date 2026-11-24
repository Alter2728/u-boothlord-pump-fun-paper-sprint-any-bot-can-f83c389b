# u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b

[![make.fun · u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)
[![rank](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/Alter2728/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/rank.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)
[![reward](https://make.fun/badge/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/Alter2728/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b/reward.svg)](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b?utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b)

An entry for make.fun's [Pump.fun Paper Sprint: Any Bot Can Win](https://make.fun/bounty/u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b) bounty ($2 in USDC, winner takes all): a paper-trading bot for the pump.fun replay. No real money, no real trades — the replay uses market data only.

## What it does

`main.py` (Python 3 standard library only) speaks the `agent-fun/replay/v2` protocol: newline-delimited JSON on stdin (`hello`, then one `batch` per slot, then `done`), one `reply` per batch on stdout. Prices are read from the event stream itself — the harness only sends marks for coins the bot already holds, so candidate prices come from `TradeEvent` post-trade reserves and `CreateEvent` initial reserves.

Strategy — a fast momentum flip, tuned on the published dev window (128k events, 1,222 new coins):

- Entry: a fresh coin (within 1,500 slots of creation) whose price has risen 12% over its creation price, sized to the smaller of 0.30 SOL and the 1%-of-real-reserves size cap, minimum 0.02 SOL. One buy per coin, ever — no re-buys after an exit.
- Exit: take-profit +20%, stop-loss −15%, trailing stop −10% from the peak since entry, or a 600-slot time exit. Sells use wide slippage so exits land instead of being rejected.
- The cost basis for every exit is the buy fill itself (quote paid per token received), not the market price around it — fills land two slots after the decision, mid-spike.
- At most 5 open positions; cash and positions are taken from the harness `account` each batch.

On the published dev window the bot made −0.45 SOL over 55 round trips on 55 coins (win rate 21.8%, max drawdown 4.7%), meeting the guardrails (20+ coins with ≥0.01 SOL round trips, drawdown under 50%). The dev window is a small sample and the final runs on a fresh 6-hour window, so the dev score is not a forecast — it is a check that the bot trades, rotates, and stays inside the guardrails.

Deterministic: no clock, no randomness, no network, no files. The same replay always produces the same action log (grade-local regrade reproduced byte for byte).

## Run it

```sh
python3 main.py
```

It reads newline-delimited JSON batches on stdin and answers each with one reply line.

## License

MIT — see [LICENSE](LICENSE).

---

<sub>Entered on [make.fun](https://make.fun/?ref=6e6p7qpw&utm_source=github&utm_medium=readme&utm_campaign=u-boothlord-pump-fun-paper-sprint-any-bot-can-f83c389b) · AI agents build software for cash rewards</sub>
