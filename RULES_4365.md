# 4365 rules

Stored 2026-10-02. This is the card that printed 33 trades and +436.5 points, +$4,365, on Sep 14 through Sep 25. It is not the process that is running. It is not the file in live_77.py.

The reprint is `sanity` in flip_score.py and `monday` in score_brt.py. A rerun of that window has to print 33 trades and +436.5 before anything else is added.

## Book

- 5 MNQ. The dollar line is points times 10.
- Stop 20. Target 40.
- One position.
- Demo. No other size.

## When

- Weekdays, 10:00 through the 15:59 bar, Chicago.
- A trade still open on the 15:59 bar is closed at that bar's close.
- No ATR filter.

## Rails

- Use H4, H1, H4L, H1L, PDL, PWL, SUPPORT, H4H, H1H, PDH, PWH, RESISTANCE.
- Skip ONH, ONL, EMA, OPEN.
- A rail updates when a new alert for that name arrives.

## The trade

The hold bar has to trade the rail.

Side:

- A named low is a buy. A named high is a sell.
- A bare H4 or H1 is a buy if the hold close is above the rail, and a sell if the hold close is below it.

Tape, long:

- Hold close is above the rail.
- Sellers on the hold bar are larger than buyers.
- The next minute's buyers are larger than its sellers.
- That minute closes above the hold close and above the rail.

Tape, short: the same, flipped.

The 15 points is the entry. That minute's close has to be within 15 of the rail. If it is farther, do not take it. The fill is that close.

## After the trade

That rail is dead until a later close is 20 points away from the price that was traded. Then it can fire again.

## Not in this card

- No chase past 15.
- No sniper fill.
- No ATR gate.
- The 15 points is not measured on the hold close.
