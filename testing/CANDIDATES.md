# Candidate gates

Not live. The bot does not read this file.

Each row is one saved cut on the same 74 trades in [data/fade_74.tsv](data/fade_74.tsv).
Add the next factor as its own row. Do not replace this one.

## o20 under 8

Status: saved, not coded.

The wick is the tip of the hold bar. A buy uses the low. A sell uses the high.
`o20` is how far price has already moved in the trade's favor 20 seconds after that tip.
A buy is `price_at_tip_plus_20s - tip`. A sell is `tip - price_at_tip_plus_20s`.

Do not send the order when `o20` is 8 or more.
The 15-point hold-bar check stays. This is an extra gate, measured from the wick, not from the rail.

| Book | Trades | W | L | Net |
|---|---:|---:|---:|---:|
| All 74 | 74 | 30 | 44 | +336.6 |
| Keep `o20` under 8 | 42 | 21 | 21 | +436.6 |
| Drop `o20` 8 or more | 32 | 9 | 23 | -100.0 |

| Week | Kept | Dropped |
|---|---:|---:|
| Sep 14 | +176.8 | +40.0 |
| Sep 21 | +219.8 | -40.0 |
| Sep 28 | +40.0 | -100.0 |

Gives up one winner this week, the Sep 28 11:41 sell, which had already run 17 points.
Next factors get tested on top of this cut, and alone, and both numbers get written here.
