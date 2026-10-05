# 7/7 restore points

Dual stays UNPLUGGED. Book: 5×1, 20/40, BE off.

| Name | Commit | Fire | Tape |
|---|---|---|---|
| `shelf6` | `bca3cc8cb7290815c20275c40c82263d1b947b7b` | 1m **close** must sit within **6** of rail | 5m delta already our color |
| `sniper10` | `ae82b242d52e04a0ef0e4641401ca96a9f7ffc7e` | **live**, fill ≤10 off rail | same 5m |
| `sniper15` (current live) | `478b24416bc8285aba5563f9b0b613d9c98e902f` + session **04:00–16:00 CT** | **live**, fill ≤**15** off rail | same 5m |
| `tape_eye` PARKED | live_77 `e50cc5c` + mnq_vol `6170ccc` | same sniper15 | 30s flip / 5m dying; 5s never veto |

Parked spec: [pending/tape_eye.md](pending/tape_eye.md) · [issue #1](https://github.com/carlosfrias424-cyber/mnq77-golden/issues/1)
Review ~2026-09-26. Say **Ship pending tape_eye** to deploy. Dual stays off.

Branch freeze of sniper15: `backup/sniper15-20260918`

## Ferrari 458 shelf

Not live. Do not curl these files over `live_77.py`.

Frozen 2026-10-05. The pass that scored **60 trades, +747.8 points**. Rules, day card, and fills are in [shelf/ferrari_458](shelf/ferrari_458).

The box backtest, same dates, is `shelf/ferrari_458/score_box_live.py`. It does not start the bot.

## Restore on CFrias

Replace `COMMIT` with a hash from the table.

```bash
ROOT=/home/administrator/.openclaw/workspace/mnq_hybrid
COMMIT=478b24416bc8285aba5563f9b0b613d9c98e902f   # sniper15
# COMMIT=bca3cc8cb7290815c20275c40c82263d1b947b7b   # shelf6 (go back)
B=https://raw.githubusercontent.com/carlosfrias424-cyber/mnq77-golden/$COMMIT
curl -fsSL "$B/live_77.py" -o "$ROOT/apps/watcher7/live_77.py"
curl -fsSL "$B/mnq_vol.py" -o "$ROOT/apps/watcher7/mnq_vol.py"
curl -fsSL "$B/place_struct40.py" -o "$ROOT/apps/tradovate/place_struct40.py"
"$ROOT/.venv/bin/python" -m py_compile \
  "$ROOT/apps/watcher7/live_77.py" \
  "$ROOT/apps/watcher7/mnq_vol.py" \
  "$ROOT/apps/tradovate/place_struct40.py" && echo COMPILE_OK
pkill -f 'dual_live.py' 2>/dev/null || true
pkill -f 'watcher7/live_77.py' 2>/dev/null || true
pkill -f 'manage_be20.py' 2>/dev/null || true
sleep 1
nohup "$ROOT/.venv/bin/python" -u "$ROOT/apps/watcher7/live_77.py" >> /tmp/live_77.out 2>&1 &
sleep 1
grep seven_start /tmp/live_77.out | tail -n 1
```

Confirm the start line `note`:
- shelf6 → `yesterday_shelf6_rescore`
- sniper15 → `sniper_live_15`

## fakeferrari458

Not a second live file. These are the rules already on the box, start note `fade_458`.

Frozen 2026-10-05. **88 trades, 44 wins, 44 losses, profit factor 2.00, +847.8 points.** September 22 failed to pull. Card, fills, rules, and score are in [shelf/fakeferrari458](shelf/fakeferrari458). October 5 is separate: 5 trades, −100. Ferrari 458 stays in [shelf/ferrari_458](shelf/ferrari_458).
