# mnq77-golden

LOCKED snapshot. **Do not add files. Do not rewrite.**

| | |
|---|---|
| Version | `yesterday_shelf6_sticky_be_off` |
| Book | 5 MNQ DEMO, stop 20, TP 40, BE **off** |
| Dual | UNPLUGGED |
| Session | 04:00–11:30 CT M–F |
| Symbol | MNQZ6 |

## These three files only

- `live_77.py` — 7/7 bounce support / fade resistance
- `place_struct40.py` — 5×1 ATM 20/40
- `mnq_vol.py` — Databento tape (delta lives here)

No Dual. No `paper_rule`. No `manage_be20`. No `lock_side_close_through_skip`.

## Install on CFrias (this repo only)

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
