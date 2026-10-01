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

## atr14 under 15

Status: saved, not coded. Stronger than `o20` on this book. Not stacked on top of it.

`atr14` is the average 1-minute true range of the 14 bars ending at the entry bar.
15.05 is the median of these 74 trades, not a number picked in advance. The hot half is the losing cluster. The quiet half is the book.

Do not send when `atr14` is 15 or more.

| Book | Trades | W | L | Net | Sep 14 | Sep 21 | Sep 28 |
|---|---:|---:|---:|---:|---:|---:|---:|
| All 74 | 74 | 30 | 44 | +336.6 | +217 | +180 | -60 |
| Keep `atr14` under 15 | 37 | 20 | 17 | +458.6 | +219 | +140 | +100 |
| Drop `atr14` 15 or more | 37 | 10 | 27 | -122.0 | -2 | +40 | -160 |
| Top quarter, 20.7 or more | 19 | 5 | 14 | -80.0 | -20 | -40 | -20 |

On top of `o20` under 8, the quiet half falls to +378.6 and this week falls from +100 to +60. The combination is worse than the ATR cut alone. Do not stack them.

The last 30 minutes agrees. A 30-minute range under 81.75 is the same 20 wins and 17 losses, +458.6, but this week is only +40. ATR is the one that fixes this week.

## Looked at, not a gate

| Point | Result |
|---|---|
| Entry candle volume | No split around the median. A candle at least twice the prior 20 is 8 trades and -100, and it does not change this week. |
| Hold-candle size | Under 14.5 points is +416.6, but this week is still -20. A hold candle of 24 points or more is -140 and red every week. Real, and smaller than the ATR cut. |
| Range since 10:00 | The opposite shape. A session already 269 points wide is +297.8 on 20 trades, this week +100. A small session is where this week lost. Not the same thing as a wide minute. |
| VIX | Not tested. `VX.n.0` did not resolve. No number. |

