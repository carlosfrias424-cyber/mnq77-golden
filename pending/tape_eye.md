# PARKED — tape_eye (do not deploy)

Logged: 2026-09-19 CT
Review: ~2026-09-26
Live stays: **sniper15** (`FIRE_NEAR=15`, `tape_5m`). Dual UNPLUGGED.
Issue: https://github.com/carlosfrias424-cyber/mnq77-golden/issues/1

## Frozen commits (code is in git, not the chat)

| What | SHA | Role |
|---|---|---|
| **sniper15** (run this) | `478b24416bc8285aba5563f9b0b613d9c98e902f` | live fill ≤15, 5m already our color |
| **tape_eye live_77** | `e50cc5ccd92b03117de5cf995804fcb6c5cb817b` | parked upgrade |
| **tape_eye mnq_vol** | `6170ccc0da2874b68e8f2c996542d58dc07a4e3a` | parked `tape_eye()` |

## Rule (simple)

Two doors. 5s never skips.

1. **Already flipped:** last 30s CVD net our way (≥3 prints).
2. **Still flipping:** last closed 5m weaker than the 5m before it, **and** 30s not slamming against.

No % drop. Sign of 30s sum / 5m delta compare only.

## Do not change

Arm 15, rails, 20/40, Dual off, BE off.

## Ship later

Say: **Ship pending tape_eye**

Then curl those two tape_eye SHAs onto `live_77.py` + `mnq_vol.py`, compile, restart. Confirm `note=sniper_live_15_tape_eye`.
