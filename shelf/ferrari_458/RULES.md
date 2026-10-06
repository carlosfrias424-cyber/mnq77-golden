# Ferrari 458

Frozen 2026-10-05. This is the book that scored **+747.8**. It is not the file running on the box.

| | |
|---|---|
| Window | 2026-08-24 through 2026-10-02, Chicago, weekdays |
| Session | 10:00–16:00. No new trade at 15:59. Still open then, flatten at that close. |
| Book | Stop 20, target 40. One position. No rail lock. |
| Contract | MNQU6 until MNQZ6 has more trades that day, then MNQZ6. |
| Tape | Databento trades. **B = buy, A = sell.** |
| Hold | The hold minute trades the rail. The next minute is the entry. Those two minutes are back to back. |
| Rail clock | A rail counts if the alert arrived before the entry minute closed. |
| Side | A named low is only a buy. A named high is only a sell. A bare H4 or H1 follows the hold close. |
| Tape gate | Long: sellers larger on the hold, next minute buyers larger, that close higher and above the rail. Short is the flip. |
| 15 points | On the **entry** close. Farther than 15, no trade. |
| ATR | Average true range of the 14 minutes ending on the entry. Those minutes must be consecutive. Under 15. |
| Same minute | If stop and target are both inside one minute, it is a stop. |
| Skip | ONH, ONL, EMA, OPEN, YEL, HEALTHCHECK. |

Result of this exact pass: **60 trades, 33 wins, 27 losses, profit factor 2.48, +747.8 points.**

The printed website card was +847.8. The 100-point gap is 2026-09-28 (0 vs +60) and 2026-10-02 (0 vs +40). Those fills were not saved with the graphic. This shelf does not add a rule to force them.

Fills: [fills.tsv](fills.tsv). Day path: [CARD.md](CARD.md). Scorer: [score_ferrari_458.py](score_ferrari_458.py).

The process on the box is a different card. Its backtest is [score_box_live.py](score_box_live.py). Do not curl that into `live_77.py`.
