# fakeferrari458

These are the rules on the box. Frozen 2026-10-05. This is not Ferrari 458.

The live file is `apps/watcher7/live_77.py`, start note `fade_458_rail_close`, commit `e67402bece7c127ecd4500dfb6ad3c94b2b1dc81`. This shelf does not change that file.

| | |
|---|---|
| Window of the score | 2026-08-24 through 2026-10-02, Chicago, weekdays |
| Session | 10:00–16:00. No new trade at 15:59. Still open then, flatten at that close. |
| Book | 5 MNQ. Stop 20. Target 40. One position. No rail lock. |
| Contract | MNQU6 until MNQZ6 has more trades that day, then MNQZ6. |
| Tape | Databento trades. **B = buy, A = sell.** |
| Hold | The hold minute trades the rail. The next minute is the entry. Those two minutes are back to back. |
| Rail clock | A rail counts if the alert arrived before the entry minute closed. |
| Side | The hold close picks the side on every rail. A named low can be a short. A named high can be a long. |
| Tape gate | Long: hold close above the rail, sellers larger on the hold, next minute buyers larger, that close higher and above the rail. Short is the flip. |
| 15 points | On the **hold** close. The fill can be farther than 15. |
| ATR | Average true range of the 14 minutes ending on the entry. A gap in those minutes still allows the trade. Under 15. |
| Same minute | This score counts a stop if the stop and the target are in the same minute. |
| Skip | ONH, ONL, EMA, OPEN. |

The `hold_dist` column is the distance of the hold close to the rail. It is not the fill distance.
