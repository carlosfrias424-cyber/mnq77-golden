# mnq77-golden

7/7 bounce support / fade resistance. **Dual UNPLUGGED.**

| | |
|---|---|
| Version | `sniper_live_15` (see [VERSIONS.md](VERSIONS.md) to restore) |
| Book | 5 MNQ DEMO, stop 20, TP 40, BE **off** |
| Dual | UNPLUGGED |
| Session | 04:00–16:00 CT M–F |
| Symbol | MNQZ6 |
| Fire | live sniper: tag within 10, fill ≤15 off rail |

## Files

- `live_77.py` — 7/7 sniper
- `place_struct40.py` — 5×1 ATM 20/40
- `mnq_vol.py` — Databento tape (5m delta lives here)
- `VERSIONS.md` — restore older 77 by commit

No Dual. No `paper_rule`. No `manage_be20`.

## Install current (sniper15)

```bash
ROOT=/home/administrator/.openclaw/workspace/mnq_hybrid
B=https://raw.githubusercontent.com/carlosfrias424-cyber/mnq77-golden/main
curl -fsSL "$B/live_77.py" -o "$ROOT/apps/watcher7/live_77.py"
curl -fsSL "$B/place_struct40.py" -o "$ROOT/apps/tradovate/place_struct40.py"
curl -fsSL "$B/mnq_vol.py" -o "$ROOT/apps/watcher7/mnq_vol.py"
"$ROOT/.venv/bin/python" -m py_compile \
  "$ROOT/apps/watcher7/live_77.py" \
  "$ROOT/apps/tradovate/place_struct40.py" \
  "$ROOT/apps/watcher7/mnq_vol.py" && echo COMPILE_OK
```

Never curl `mnq77-sim-drop` or `mnq-77-drop` again.
Go back a version: [VERSIONS.md](VERSIONS.md).
