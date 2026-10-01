#!/usr/bin/env python3
"""Read the live log only. No orders. No Databento."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
P = Path("/home/administrator/.openclaw/workspace/mnq_hybrid/logs/seven.jsonl")


def clock(ts):
    return datetime.fromtimestamp(ts / 1000, TZ)


rows = []
for ln in P.read_text().splitlines():
    if not ln.strip():
        continue
    try:
        o = json.loads(ln)
    except Exception:
        continue
    ts = o.get("ts")
    if not ts:
        continue
    dt = clock(ts)
    if dt.strftime("%Y-%m-%d") < "2026-09-28":
        continue
    rows.append((dt, o))

print("TODAY")
for dt, o in rows:
    if dt.strftime("%Y-%m-%d") != "2026-10-01":
        continue
    if o.get("event") not in ("struct40_submit", "struct40_fail", "trade_done", "eod_flat"):
        continue
    if o.get("event") == "struct40_fail" and not o.get("submit"):
        continue
    print(
        dt.strftime("%H:%M:%S"),
        o.get("event"),
        o.get("side"),
        o.get("poi"),
        "mid", o.get("mid"),
        "pts", o.get("pts"),
        "how", o.get("how"),
        "dist", o.get("dist"),
    )

print("WEEK")
book = []
for dt, o in rows:
    if o.get("event") == "struct40_submit" and o.get("submit"):
        book.append({
            "dt": dt, "side": o.get("side"), "poi": o.get("poi"),
            "mid": o.get("mid"), "pts": None, "how": None,
        })
    elif o.get("event") == "trade_done" and book and book[-1]["pts"] is None:
        book[-1]["pts"] = o.get("pts")
        book[-1]["how"] = o.get("how")

by_day = {}
for r in book:
    by_day.setdefault(r["dt"].strftime("%m-%d"), []).append(r)

for day, grp in by_day.items():
    wins = [r for r in grp if (r["pts"] or 0) > 0]
    losses = [r for r in grp if (r["pts"] or 0) < 0]
    net = sum(r["pts"] or 0 for r in grp)
    print(f"{day} n {len(grp)} W {len(wins)} L {len(losses)} {net:+.1f}")
    seen = {}
    for r in grp:
        key = (r["poi"], r["side"])
        seen[key] = seen.get(key, 0) + 1
        mark = "REPEAT" if seen[key] > 1 else "first"
        print(
            f"  {r['dt'].strftime('%H:%M')} {r['side']} {r['poi']} @{r['mid']} "
            f"{r['pts']} {r['how']} {mark}",
        )
