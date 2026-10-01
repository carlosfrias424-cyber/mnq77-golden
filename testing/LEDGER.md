# Testing ledger

Paper only. Nothing here places an order.

The trade list is [data/fade_74.tsv](data/fade_74.tsv). Same 74 fades, 10:00-16:00 Chicago, Sep 14 through Oct 1, stop 20, target 40. Net +336.6 points.

`push` is the aggressor size hitting into the wick, the 20 seconds before the tip.
`f5` `f10` `f20` are the other side after the tip. Positive means buyers on a long, sellers on a short.
`o5` `o10` `o20` are the points price had already moved in the trade's favor by then.

## What we already closed

| Test | Result | Net |
|---|---|---|
| Wider stop, 23 / 25 / 30 | Rejected. The stops were real. A wider stop gave back more on the good weeks than it saved here. | not used |
| 15 points on the hold-bar close | Not the entry. The order is the next bar's close, and that bar can already be 20 points through the rail. | live rule, does not locate the fill |
| Which rail | Bare H4 is 57 of 74 trades and +402.6. PDL is 5 trades, 0 wins, -100. PDH is 5 trades, -6. H1 is 4 trades, -20. | PDL is dead on this sample. Too few to ban the rest. |
| Long a high, short a low | This week is not that. 26 of the 27 trades are a bare H4. Buys of it were +0. Sells of it were -40. Both sides lost. The week before, both sides of bare H4 paid. | not the split |
| Buyers showed up (`f20` > 0) | 50 trades, 23 wins, 27 losses, +376.8. The losers have follow-through too. | does not separate |
| Size hitting the wick (`push` > 0) | 71 of 74 trades. It is almost always true. | does not separate |

## What separated

Price already gone 20 seconds after the wick.

| Cut | Trades | W | L | Net |
|---|---:|---:|---:|---:|
| `o20` under 8 | 42 | 21 | 21 | +436.6 |
| `o20` 8 or more | 32 | 9 | 23 | -100.0 |

By week, keeping only `o20` under 8:

| Week | All trades | Kept | Dropped |
|---|---:|---:|---:|
| Sep 14 | +216.8 | +176.8 | +40.0 |
| Sep 21 | +179.8 | +219.8 | -40.0 |
| Sep 28 | -60.0 | +40.0 | -100.0 |

This week stops bleeding. It does not become a big week. One winner on Sep 28 had already run 17 points and would be skipped. The prior two weeks stay green.

## Saved to implement

Both cuts are in [CANDIDATES.md](CANDIDATES.md). Neither is in the live bot.

`o20` under 8 turns this week from -60 to +40, book +436.6.
`atr14` under 15 turns this week from -60 to +100, book +458.6. Putting them together is worse than ATR alone.

## Not a result

Entry-candle volume does not separate. VIX did not load. Do not treat either as a gate.
